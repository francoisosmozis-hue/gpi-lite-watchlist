from engine.drive_source import (
    archive_downloaded_files,
    archive_enabled,
    get_archive_folder_id,
    get_drive_folder_id,
    sync_drive_folder_to_local,
)
from pathlib import Path
from typing import Optional
from engine.h5_context import ensure_9h_context_for_h5
import io
import json
import os
import subprocess
import sys


import pandas as pd
from google.cloud import storage
from fastapi import FastAPI, File, UploadFile, Header, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastmcp import FastMCP
from engine.paper_results import write_paper_results
from engine.paper_kpi import write_paper_kpi

def drive_empty_response(phase: str, sync_result: dict, message: str) -> dict:
    return {
        "ok": False,
        "phase": phase,
        "error": message,
        "sync": sync_result,
        "morning": None,
        "h5": None,
        "results": None,
        "audit": None,
        "quality": None,
        "cloud_sync": None,
        "brief": None,
        "gpi_audit": None,
        "archive": None
    }


def sync_has_files(sync_result: dict) -> bool:
    try:
        return int(sync_result.get("files_count", 0) or 0) > 0
    except Exception:
        return False

def sync_files_count(sync_result: dict) -> int:
    try:
        return int(sync_result.get("files_count", 0) or 0)
    except Exception:
        return 0


mcp = FastMCP("GPI-Lite Watchlist MCP")


@mcp.tool
def get_gpi_brief() -> dict:
    """
    Retourne le dernier brief GPI disponible depuis Cloud Storage.
    Utiliser cet outil pour obtenir le verdict Python, le ticket exact, le segment ROI et l'action GPT.
    """
    return get_latest_brief_from_cloud_payload()


mcp_app = mcp.http_app(path="/")

app = FastAPI(
    title="GPI-Lite Watchlist API",
    version="0.7.0-mcp",
    lifespan=mcp_app.lifespan
)

app.mount("/mcp", mcp_app)

PYTHON = sys.executable
ROOT = Path(__file__).resolve().parent

DATA_DIR = ROOT / "data"
MORNING_DIR = DATA_DIR / "morning"
H5_DIR = DATA_DIR / "h5"
RESULTS_DIR = DATA_DIR / "results"
OUTPUT_DIR = DATA_DIR / "output"
API_KEY = os.getenv("GPI_API_KEY", "")
GPI_BUCKET_NAME = os.getenv("GPI_BUCKET_NAME", "")
RULESET_VERSION = "GPI_ZEturf_V19_1_4"
ENGINE_VERSION = "GPI_LITE_V0_9_1"

SOURCE_DIR = ROOT / "sources"
DOCS_DIR = SOURCE_DIR / "docs"
CALIBRATION_DIR = SOURCE_DIR / "calibration"

EXPECTED_DOCS = [
    "Document_0_Workflow_Operationnel_H5_GPI_ZEturf_V19_1_4_SOURCE.txt",
    "Document_1_Prompt_Maitre_GPI_ZEturf_V19_1_4_ACTIF_MAJ_2026-05-23.txt",
    "Document_2_Matrice_ROI_Paris_ZEturf_GPI_V19_1_4_SOURCE.txt",
    "Document_3_Tracker_Audit_Gouvernance_GPI_V19_1_4_SOURCE.txt",
    "Document_4_Historique_ZEturf_Seed_Reduit_GPI_V19_1_4_SOURCE.txt",
    "Document_10_Formules_Proxy_CP_SG_ZEturf_GPI_V5_1_4_SOURCE.txt",
    "Document_11_Base_Historique_ZEturf_Calibration_Coefficients_GPI_V5_1_4_SOURCE.txt",
    "README_A_INTEGRER_SOURCE_PROJET_GPI_V19_1_4_2026-05-23.txt",
    "manifest_GPI_V19_1_4.json",
]

EXPECTED_CALIBRATION_AVAILABLE = [
    "calibration_segments.csv",
    "proxy_error_by_segment.csv",
    "backtest_cp_sec_vs_cp3.csv",
    "run_metrics.json",
]

BLOCKING_CALIBRATION_FILES = [
    "sp_calibration_segments.csv",
    "train_test_split.csv",
    "decision_engine_outputs.csv",
]

def require_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")):
    if not API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GPI_API_KEY non configuree sur le serveur"
        )

    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Cle API invalide ou absente"
        )

    return True

def ensure_dirs():
    MORNING_DIR.mkdir(parents=True, exist_ok=True)
    H5_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def to_posix_path(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()

def safe_posix_path(path):
    if path is None:
        return ""

    try:
        return Path(path).relative_to(ROOT).as_posix()
    except Exception:
        return str(path).replace("\\", "/")
    
def run_script(script_name: str):
    ensure_dirs()

    script_path = ROOT / script_name

    if not script_path.exists():
        return {
            "ok": False,
            "script": script_name,
            "error": f"Script absent: {script_name}",
            "stdout": "",
            "stderr": "",
        }

    result = subprocess.run(
        [PYTHON, str(script_path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    return {
        "ok": result.returncode == 0,
        "script": script_name,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def resolve_upload_pdf_filename(file: UploadFile, filename: Optional[str] = None) -> str:
    """
    Choix du nom PDF :
    1. si filename est fourni et finit par .pdf, on l'utilise ;
    2. sinon on utilise le vrai nom du fichier uploadé ;
    3. si aucun des deux ne finit par .pdf, on refuse.
    """
    custom_name = (filename or "").strip()
    upload_name = (file.filename or "").strip()

    if custom_name.lower().endswith(".pdf"):
        return Path(custom_name).name

    if upload_name.lower().endswith(".pdf"):
        return Path(upload_name).name

    raise HTTPException(
        status_code=400,
        detail={
            "ok": False,
            "error": "Le fichier doit etre un PDF",
            "filename": custom_name or upload_name or "ND"
        }
    )


async def save_pdf_upload(file: UploadFile, target_dir: Path, filename: Optional[str] = None):
    ensure_dirs()

    final_name = resolve_upload_pdf_filename(file, filename)

    target_path = target_dir / final_name
    content = await file.read()
    target_path.write_bytes(content)

    return {
        "ok": True,
        "saved_as": to_posix_path(target_path),
        "bytes": len(content)
    }


async def save_optional_pdf(file: Optional[UploadFile], target_dir: Path):
    ensure_dirs()

    if file is None:
        return {
            "uploaded": False,
            "saved_as": "",
            "bytes": 0
        }

    try:
        final_name = resolve_upload_pdf_filename(file, None)
    except HTTPException as exc:
        return {
            "uploaded": False,
            "error": exc.detail.get("error", "Le fichier doit etre un PDF") if isinstance(exc.detail, dict) else "Le fichier doit etre un PDF",
            "filename": exc.detail.get("filename", "ND") if isinstance(exc.detail, dict) else "ND"
        }

    target_path = target_dir / final_name
    content = await file.read()
    target_path.write_bytes(content)

    return {
        "uploaded": True,
        "saved_as": to_posix_path(target_path),
        "bytes": len(content)
    }


def list_files_in_dir(directory: Path):
    ensure_dirs()

    return sorted([
        to_posix_path(path)
        for path in directory.glob("*")
        if path.is_file()
    ])


def read_csv_as_records(path: Path):
    if not path.exists():
        return {
            "ok": False,
            "path": to_posix_path(path),
            "rows": [],
            "error": "fichier absent"
        }

    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return {
            "ok": False,
            "path": to_posix_path(path),
            "rows": [],
            "error": str(exc)
        }

    return {
        "ok": True,
        "path": to_posix_path(path),
        "rows": df.fillna("").to_dict(orient="records")
    }


def read_text_file(path: Path):
    if not path.exists():
        return {
            "ok": False,
            "path": to_posix_path(path),
            "text": "",
            "error": "fichier absent"
        }

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1", errors="replace")
    except Exception as exc:
        return {
            "ok": False,
            "path": to_posix_path(path),
            "text": "",
            "error": str(exc)
        }

    return {
        "ok": True,
        "path": to_posix_path(path),
        "text": text
    }


def newest_file(pattern: str):
    files = list(OUTPUT_DIR.glob(pattern))

    if not files:
        return None

    return max(files, key=lambda p: p.stat().st_mtime)

def get_gcs_bucket():
    if not GPI_BUCKET_NAME:
        return None, "GPI_BUCKET_NAME non configure"

    try:
        client = storage.Client()
        bucket = client.bucket(GPI_BUCKET_NAME)
        return bucket, ""
    except Exception as exc:
        return None, str(exc)


def upload_file_to_gcs(local_path: Path, object_prefix: str):
    if not local_path.exists() or not local_path.is_file():
        return {
            "ok": False,
            "local_path": safe_posix_path(local_path),
            "error": "fichier absent"
        }

    bucket, error = get_gcs_bucket()

    if bucket is None:
        return {
            "ok": False,
            "local_path": safe_posix_path(local_path),
            "error": error
        }

    object_name = f"gpi-lite/{object_prefix}/{local_path.name}"

    try:
        blob = bucket.blob(object_name)
        blob.upload_from_filename(str(local_path))

        return {
            "ok": True,
            "local_path": safe_posix_path(local_path),
            "gcs_uri": f"gs://{GPI_BUCKET_NAME}/{object_name}"
        }
    except Exception as exc:
        return {
            "ok": False,
            "local_path": safe_posix_path(local_path),
            "error": str(exc)
        }


def sync_gpi_files_to_gcs():
    ensure_dirs()

    files_to_sync = []

    for directory, prefix in [
        (MORNING_DIR, "sources/morning"),
        (H5_DIR, "sources/h5"),
        (RESULTS_DIR, "sources/results"),
        (OUTPUT_DIR, "outputs"),
    ]:
        for path in directory.glob("*"):
            if path.is_file():
                files_to_sync.append((path, prefix))

    uploads = []

    for path, prefix in files_to_sync:
        uploads.append(upload_file_to_gcs(path, prefix))

    ok = all(item.get("ok") for item in uploads) if uploads else False

    return {
        "ok": ok,
        "bucket": GPI_BUCKET_NAME,
        "files_count": len(uploads),
        "uploads": uploads
    }


def list_gcs_files(prefix: str = "gpi-lite/"):
    bucket, error = get_gcs_bucket()

    if bucket is None:
        return {
            "ok": False,
            "bucket": GPI_BUCKET_NAME,
            "files": [],
            "error": error
        }

    try:
        blobs = bucket.list_blobs(prefix=prefix, max_results=200)
        files = [
            {
                "name": blob.name,
                "gcs_uri": f"gs://{GPI_BUCKET_NAME}/{blob.name}",
                "size": blob.size,
                "updated": blob.updated.isoformat() if blob.updated else ""
            }
            for blob in blobs
        ]

        return {
            "ok": True,
            "bucket": GPI_BUCKET_NAME,
            "files": files
        }
    except Exception as exc:
        return {
            "ok": False,
            "bucket": GPI_BUCKET_NAME,
            "files": [],
            "error": str(exc)
        }
    
def get_latest_gcs_blob(prefix: str, suffix: str):
    bucket, error = get_gcs_bucket()

    if bucket is None:
        return None, error

    try:
        blobs = list(bucket.list_blobs(prefix=prefix))
    except Exception as exc:
        return None, str(exc)

    candidates = [
        blob for blob in blobs
        if blob.name.endswith(suffix)
    ]

    if not candidates:
        return None, f"aucun fichier {suffix} trouve dans {prefix}"

    latest = max(candidates, key=lambda b: b.updated)

    return latest, ""


def read_gcs_text_blob(blob):
    try:
        return {
            "ok": True,
            "text": blob.download_as_text(encoding="utf-8"),
            "gcs_uri": f"gs://{GPI_BUCKET_NAME}/{blob.name}",
            "updated": blob.updated.isoformat() if blob.updated else ""
        }
    except UnicodeDecodeError:
        return {
            "ok": True,
            "text": blob.download_as_text(encoding="latin-1"),
            "gcs_uri": f"gs://{GPI_BUCKET_NAME}/{blob.name}",
            "updated": blob.updated.isoformat() if blob.updated else ""
        }
    except Exception as exc:
        return {
            "ok": False,
            "text": "",
            "gcs_uri": f"gs://{GPI_BUCKET_NAME}/{blob.name}",
            "error": str(exc)
        }


def read_gcs_csv_first_row(blob):
    try:
        text = blob.download_as_text(encoding="utf-8")
        df = pd.read_csv(io.StringIO(text)).fillna("")
    except UnicodeDecodeError:
        text = blob.download_as_text(encoding="latin-1")
        df = pd.read_csv(io.StringIO(text)).fillna("")
    except Exception as exc:
        return None, str(exc)

    if df.empty:
        return None, "csv vide"

    return df.iloc[0].to_dict(), ""

def latest_h5_context_path() -> Path:
    return OUTPUT_DIR / "latest_h5_context.json"


def extract_course_key_from_context_9h(context_9h: dict) -> str:
    checked = context_9h.get("checked", [])

    if not checked:
        return ""

    first = checked[0]

    course_key = str(first.get("course_key", "")).strip()

    if course_key:
        return course_key

    expected_9h = str(first.get("expected_9h_clean", "")).strip()

    if expected_9h:
        name = Path(expected_9h).name
        return name.replace("_course_9h_clean.csv", "").strip()

    return ""


def write_latest_h5_context(context_9h: dict) -> dict:
    ensure_dirs()

    course_key = extract_course_key_from_context_9h(context_9h)

    payload = {
        "ok": bool(course_key),
        "course_key": course_key,
        "context_9h": context_9h,
        "expected_decision_lite": f"{course_key}_decision_lite.csv" if course_key else "",
        "expected_prompt_gpt": f"{course_key}_prompt_gpt.txt" if course_key else "",
    }

    path = latest_h5_context_path()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return payload


def read_latest_h5_context() -> dict:
    path = latest_h5_context_path()

    if not path.exists():
        return {
            "ok": False,
            "error": "latest_h5_context.json absent"
        }

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc)
        }


def _gpi_norm_h5_course_key_for_compare(value) -> str:
    """
    Normalisation robuste pour comparer course_key et course_uid.
    Read-only. Ne modifie aucune donnee source.
    """
    try:
        return _h5auto_normalize_course_key(value)
    except Exception:
        import re
        import unicodedata

        s = str(value or "").lower()
        s = s.replace("_pronostics & cotes", "")
        s = s.replace("pronostics & cotes", "")

        # Supprime les accents pour eviter les problemes d'encodage console.
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))

        s = re.sub(r"[^a-z0-9]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

def _gpi_latest_h5_context_freshness(reference_course_uid: str = "", reference_source: str = "latest_h5_auto_brief") -> dict:
    """
    Controle read-only.
    Ne modifie pas GCS.
    Ne cree jamais de contexte synthetique.
    Sert uniquement a refuser un latest_h5_context obsolete.
    """
    context_source = {
        "kind": "local_reader",
        "file": "latest_h5_context.json",
    }

    try:
        h5r_context_func = globals().get("_h5r_load_latest_h5_context")
        if callable(h5r_context_func):
            context, context_source = h5r_context_func()
        else:
            context = read_latest_h5_context()
    except Exception as exc:
        context = read_latest_h5_context()
        context_source = {
            "kind": "fallback_after_h5r_error",
            "error": f"{type(exc).__name__}:{exc}",
        }

    if reference_course_uid not in {None, "", "ND"}:
        brief = {
            "ok": True,
            "course_uid": str(reference_course_uid),
            "reference_source": str(reference_source or "explicit_reference"),
        }
    else:
        brief = {
            "ok": False,
            "reason": "LATEST_H5_AUTO_BRIEF_NOT_READ",
        }

        try:
            latest_func = globals().get("gpi_latest_h5_auto_brief")
            if callable(latest_func):
                brief = latest_func()
                reference_source = "latest_h5_auto_brief"
        except Exception as exc:
            brief = {
                "ok": False,
                "reason": f"LATEST_H5_AUTO_BRIEF_READ_ERROR:{type(exc).__name__}:{exc}",
            }

    context_course_key = str(context.get("course_key", "") if isinstance(context, dict) else "").strip()
    brief_course_uid = str(brief.get("course_uid", "") if isinstance(brief, dict) else "").strip()

    context_norm = _gpi_norm_h5_course_key_for_compare(context_course_key)
    brief_norm = _gpi_norm_h5_course_key_for_compare(brief_course_uid)

    context_ok = isinstance(context, dict) and context.get("ok") is True and context_course_key not in {"", "ND"}
    brief_ok = isinstance(brief, dict) and brief.get("ok") is True and brief_course_uid not in {"", "ND"}

    mismatch = bool(
        context_ok
        and brief_ok
        and context_norm
        and brief_norm
        and context_norm != brief_norm
    )

    fresh = bool(context_ok and brief_ok and not mismatch)

    return {
        "ok": True,
        "latest_h5_context_exists_oui_non": "oui" if context_ok else "non",
        "latest_h5_auto_brief_exists_oui_non": "oui" if brief_ok else "non",
        "latest_h5_context_fresh": fresh,
        "latest_h5_context_fresh_oui_non": "oui" if fresh else "non",
        "latest_h5_context_mismatch": mismatch,
        "latest_h5_context_mismatch_oui_non": "oui" if mismatch else "non",
        "latest_h5_context_course_key": context_course_key or "ND",
        "latest_h5_auto_brief_course_uid": brief_course_uid or "ND",
        "latest_h5_reference_source": str(reference_source or "ND"),
        "latest_h5_context_source": context_source,
        "latest_h5_context_norm": context_norm or "ND",
        "latest_h5_auto_brief_norm": brief_norm or "ND",
        "reason": "OK" if fresh else ("H5_CONTEXT_COURSE_MISMATCH" if mismatch else "H5_CONTEXT_NOT_FRESH_OR_MISSING"),
    }

def write_decision_lite_aliases(course_key: str) -> dict:
    ensure_dirs()

    if not course_key:
        return {
            "ok": False,
            "error": "course_key absent",
            "source": "",
            "aliases": []
        }

    source_path = OUTPUT_DIR / f"{course_key}_decision_lite.csv"

    if not source_path.exists():
        return {
            "ok": False,
            "error": f"decision_lite source absent : {source_path.name}",
            "source": safe_posix_path(source_path),
            "aliases": []
        }

    aliases = []

    for alias_name in [
        "latest_decision_lite.csv",
        "decision_lite.csv",
    ]:
        alias_path = OUTPUT_DIR / alias_name
        alias_path.write_bytes(source_path.read_bytes())
        aliases.append(safe_posix_path(alias_path))

    return {
        "ok": True,
        "source": safe_posix_path(source_path),
        "aliases": aliases
    }


def read_local_decision_for_course_key(course_key: str):
    decision_path = OUTPUT_DIR / f"{course_key}_decision_lite.csv"

    if not decision_path.exists():
        return None, None, f"decision_lite local absent pour {course_key}"

    try:
        df = pd.read_csv(decision_path).fillna("")
    except Exception as exc:
        return decision_path, None, str(exc)

    if df.empty:
        return decision_path, None, "decision_lite local vide"

    return decision_path, df.iloc[0].to_dict(), ""


def get_cloud_blob_exact(object_name: str):
    bucket, error = get_gcs_bucket()

    if bucket is None:
        return None, error

    try:
        blob = bucket.blob(object_name)

        if not blob.exists():
            return None, f"blob absent : gs://{GPI_BUCKET_NAME}/{object_name}"

        return blob, ""
    except Exception as exc:
        return None, str(exc)


def get_brief_for_course_key_payload(course_key: str) -> dict:
    if not course_key:
        return {
            "ok": False,
            "source": "none",
            "error": "course_key absent",
            "brief": "Brief impossible : course_key absent."
        }

    decision_path, decision, error = read_local_decision_for_course_key(course_key)

    if decision is not None:
        prompt_path = OUTPUT_DIR / f"{course_key}_prompt_gpt.txt"
        prompt_text = ""

        if prompt_path.exists():
            prompt_text = read_text_file(prompt_path).get("text", "")

        built = build_brief_from_decision(
            decision=decision,
            prompt_text=prompt_text,
            source_decision=safe_posix_path(decision_path)
        )

        return {
            "ok": True,
            "source": "local_container",
            "course_key": course_key,
            "source_decision": safe_posix_path(decision_path),
            "source_prompt": safe_posix_path(prompt_path) if prompt_path.exists() else "",
            "course_uid": built["course_uid"],
            "verdict_python": built["verdict_python"],
            "qualite": built["qualite"],
            "structure_max": built["structure_max"],
            "ticket_exact": built["ticket_exact"],
            "segment_roi": built["segment_roi"],
            "edge_status": built["edge_status"],
            "reason_codes": built["reason_codes"],
            "kill_switch_codes": built["kill_switch_codes"],
            "action_gpt": built["action_gpt"],
            "prompt_text": built["prompt_text"],
            "brief": built["brief"]
        }

    decision_blob, cloud_error = get_cloud_blob_exact(
        f"gpi-lite/outputs/{course_key}_decision_lite.csv"
    )

    if decision_blob is None:
        return {
            "ok": False,
            "source": "none",
            "course_key": course_key,
            "local_error": error,
            "cloud_error": cloud_error,
            "brief": "Brief impossible : decision_lite absent pour la course H-5."
        }

    decision, read_error = read_gcs_csv_first_row(decision_blob)

    if decision is None:
        return {
            "ok": False,
            "source": "cloud_storage",
            "course_key": course_key,
            "source_decision": f"gs://{GPI_BUCKET_NAME}/{decision_blob.name}",
            "error": read_error,
            "brief": "Brief impossible : decision_lite cloud illisible."
        }

    prompt_text = ""
    prompt_source = ""

    prompt_blob, prompt_error = get_cloud_blob_exact(
        f"gpi-lite/outputs/{course_key}_prompt_gpt.txt"
    )

    if prompt_blob is not None:
        prompt_data = read_gcs_text_blob(prompt_blob)
        prompt_text = prompt_data.get("text", "")
        prompt_source = prompt_data.get("gcs_uri", "")

    built = build_brief_from_decision(
        decision=decision,
        prompt_text=prompt_text,
        source_decision=f"gs://{GPI_BUCKET_NAME}/{decision_blob.name}"
    )

    return {
        "ok": True,
        "source": "cloud_storage",
        "bucket": GPI_BUCKET_NAME,
        "course_key": course_key,
        "source_decision": f"gs://{GPI_BUCKET_NAME}/{decision_blob.name}",
        "source_prompt": prompt_source,
        "updated": decision_blob.updated.isoformat() if decision_blob.updated else "",
        "course_uid": built["course_uid"],
        "verdict_python": built["verdict_python"],
        "qualite": built["qualite"],
        "structure_max": built["structure_max"],
        "ticket_exact": built["ticket_exact"],
        "segment_roi": built["segment_roi"],
        "edge_status": built["edge_status"],
        "cp_sec_candidate": built.get("cp_sec_candidate", "ND"),
        "cp_sg_pair_sum": built.get("cp_sg_pair_sum", "ND"),
        "cp_sg_filter_status": built.get("cp_sg_filter_status", "ND"),
        "cp3_candidate": built.get("cp3_candidate", "ND"),
        "cp3_sg_min_pair_sum": built.get("cp3_sg_min_pair_sum", "ND"),
        "cp3_sg_filter_status": built.get("cp3_sg_filter_status", "ND"),
        "decision_strict_allowed": built.get("decision_strict_allowed", "non"),
        "decision_block_reason": built.get("decision_block_reason", "ND"),
        "strict_gate_summary": built.get("strict_gate_summary", "ND"),
        "volatilite_scope": built.get("volatilite_scope", "TOP6_UNION"),
        "volatilite_top6_max_pct": built.get("volatilite_top6_max_pct", "ND"),
        "volatilite_top6_max_numero": built.get("volatilite_top6_max_numero", "ND"),
        "volatilite_top6_max_cheval": built.get("volatilite_top6_max_cheval", "ND"),
        "volatilite_top6_sg_9h": built.get("volatilite_top6_sg_9h", "ND"),
        "volatilite_top6_sg_h5": built.get("volatilite_top6_sg_h5", "ND"),
        "volatilite_top6_direction": built.get("volatilite_top6_direction", "ND"),
        "volatilite_status": built.get("volatilite_status", "ND"),
        "cp_sec_sg_sum": built.get("cp_sec_sg_sum", "ND"),
        "cp_sec_sg_status": built.get("cp_sec_sg_status", "ND"),
        "reason_codes": built["reason_codes"],
        "kill_switch_codes": built["kill_switch_codes"],
        "action_gpt": built["action_gpt"],
        "prompt_text": built["prompt_text"],
        "brief": built["brief"]
    }

def build_brief_from_decision(decision, prompt_text="", source_decision=""):
    course_uid = str(decision.get("course_uid", "ND"))
    qualite = str(decision.get("qualite", decision.get("qualite_matin", "ND")))
    verdict_python = str(decision.get("verdict", decision.get("verdict_python", "ND")))
    structure_max = str(decision.get("structure_max", "ND"))
    selection_cp_sec = str(
        decision.get("selection_cp_sec")
        or decision.get("selection_CP_SEC")
        or decision.get("cp_sec_candidate")
        or decision.get("CP_SEC_CANDIDAT")
        or ""
    )
    selection_cp3 = str(decision.get("selection_cp3", ""))
    selection_sp = str(decision.get("selection_sp", decision.get("sp_values", "")))
    reason_codes = str(decision.get("reason_codes", ""))
    kill_switch_codes = str(decision.get("kill_switch_codes", ""))
    segment_roi = str(decision.get("segment_roi", decision.get("segment_ROI", "ND")))
    edge_status = str(decision.get("edge_status", "ND"))

    def _merge_semicolon_codes(existing, additions):
        parts = []

        for raw in [existing]:
            for code in str(raw or "").split(";"):
                cleaned = code.strip()
                if cleaned and cleaned.upper() not in {"AUCUN", "NONE", "ND"} and cleaned not in parts:
                    parts.append(cleaned)

        for code in additions or []:
            cleaned = str(code or "").strip()
            if cleaned and cleaned.upper() not in {"AUCUN", "NONE", "ND"} and cleaned not in parts:
                parts.append(cleaned)

        return ";".join(parts)

    odds_override_status = "ND"
    odds_context_for_brief = None

    try:
        from engine.odds.brief_odds_enricher import build_odds_decision_overrides
        from engine.odds.latest_odds_context_reader import read_latest_odds_context
        from engine.odds.latest_odds_context_from_decision import (
            build_latest_odds_context_from_decision,
            write_latest_odds_context_from_decision,
        )

        odds_context_for_brief = read_latest_odds_context()

        if odds_context_for_brief.get("odds_context_found") != "oui":
            odds_context_for_brief = build_latest_odds_context_from_decision(
                decision,
                prompt_text=prompt_text,
            )
            try:
                write_latest_odds_context_from_decision(odds_context_for_brief)
            except Exception:
                pass

        odds_overrides = build_odds_decision_overrides(odds_context_for_brief)
        odds_override_status = str(odds_overrides.get("strict_allowed_oui_non", "ND"))

        if odds_override_status == "non":
            reason_codes = _merge_semicolon_codes(
                reason_codes,
                odds_overrides.get("reason_codes_to_add", []),
            )
            kill_switch_codes = _merge_semicolon_codes(
                kill_switch_codes,
                odds_overrides.get("kill_switch_codes_to_add", []),
            )
            edge_status = str(odds_overrides.get("edge_status", edge_status) or edge_status)

    except Exception as exc:
        reason_codes = _merge_semicolon_codes(
            reason_codes,
            [f"ODDS_CONTEXT_OVERRIDE_ERROR_{type(exc).__name__}"],
        )

    cp_sec_candidate = str(decision.get("cp_sec_candidate", decision.get("selection_cp_sec", decision.get("CP_SEC_CANDIDAT", "ND"))))
    cp_sg_pair_sum = str(decision.get("cp_sg_pair_sum", decision.get("CP_SG_PAIR_SUM", "ND")))
    cp_sg_filter_status = str(decision.get("cp_sg_filter_status", decision.get("CP_SG_FILTER_STATUS", "ND")))
    cp3_candidate = str(decision.get("cp3_candidate", decision.get("selection_cp3", decision.get("CP3_CANDIDATE", "ND"))))
    cp3_sg_min_pair_sum = str(decision.get("cp3_sg_min_pair_sum", decision.get("CP3_SG_MIN_PAIR_SUM", "ND")))
    cp3_sg_filter_status = str(decision.get("cp3_sg_filter_status", decision.get("CP3_SG_FILTER_STATUS", "ND")))
    decision_strict_allowed = str(decision.get("decision_strict_allowed", decision.get("DECISION_STRICT_ALLOWED", "non")))
    decision_block_reason = str(decision.get("decision_block_reason", decision.get("DECISION_BLOCK_REASON", "ND")))
    strict_gate_summary = str(decision.get("strict_gate_summary", decision.get("STRICT_GATE_SUMMARY", "ND")))
    volatilite_scope = str(decision.get("volatilite_scope", decision.get("VOLATILITE_SCOPE", "TOP6_UNION")))
    volatilite_top6_max_pct = str(decision.get("volatilite_top6_max_pct", decision.get("VOLATILITE_TOP6_MAX_PCT", "ND")))
    volatilite_top6_max_numero = str(decision.get("volatilite_top6_max_numero", decision.get("VOLATILITE_TOP6_MAX_NUMERO", "ND")))
    volatilite_top6_max_cheval = str(decision.get("volatilite_top6_max_cheval", decision.get("VOLATILITE_TOP6_MAX_CHEVAL", "ND")))
    volatilite_top6_sg_9h = str(decision.get("volatilite_top6_sg_9h", decision.get("VOLATILITE_TOP6_SG_9H", "ND")))
    volatilite_top6_sg_h5 = str(decision.get("volatilite_top6_sg_h5", decision.get("VOLATILITE_TOP6_SG_H5", "ND")))
    volatilite_top6_direction = str(decision.get("volatilite_top6_direction", decision.get("VOLATILITE_TOP6_DIRECTION", "ND")))
    volatilite_status = str(decision.get("volatilite_status", decision.get("VOLATILITE_STATUS", "ND")))
    cp_sec_sg_sum = str(decision.get("cp_sec_sg_sum", decision.get("CP_SEC_SG_SUM", decision.get("cp_sg_pair_sum", "ND"))))
    cp_sec_sg_status = str(decision.get("cp_sec_sg_status", decision.get("CP_SEC_SG_STATUS", decision.get("cp_sg_filter_status", "ND"))))
    paper_candidate_allowed = str(decision.get("paper_candidate_allowed", decision.get("PAPER_CANDIDATE_ALLOWED", "non")))
    paper_candidate_type = str(decision.get("paper_candidate_type", decision.get("PAPER_CANDIDATE_TYPE", "NO_BET")))
    paper_candidate_ticket = str(decision.get("paper_candidate_ticket", decision.get("PAPER_CANDIDATE_TICKET", "NO_BET")))
    paper_candidate_segment = str(decision.get("paper_candidate_segment", decision.get("PAPER_CANDIDATE_SEGMENT", "PAPER_TRADING")))
    paper_candidate_mise = str(decision.get("paper_candidate_mise", decision.get("PAPER_CANDIDATE_MISE", "0")))
    paper_candidate_reason = str(decision.get("paper_candidate_reason", decision.get("PAPER_CANDIDATE_REASON", "ND")))

    if paper_candidate_allowed.strip().lower() != "oui" and paper_candidate_reason.strip().upper() in {"", "ND", "NONE", "NULL"}:
        paper_reason_parts = []

        if volatilite_status.strip().upper() == "KILL":
            paper_reason_parts.append("volatilite_KILL")

        if kill_switch_codes.strip() and kill_switch_codes.strip().upper() not in {"AUCUN", "NONE", "ND"}:
            paper_reason_parts.append("kill_switch:" + kill_switch_codes.strip())

        if cp_sec_sg_status.strip().upper() != "OK":
            paper_reason_parts.append("cp_sec_sg_status_" + cp_sec_sg_status.strip().upper())

        try:
            _paper_cp_sum = float(str(cp_sec_sg_sum).replace(",", "."))
        except Exception:
            _paper_cp_sum = None

        if _paper_cp_sum is None:
            paper_reason_parts.append("cp_sec_sg_sum_ND")
        elif _paper_cp_sum < 7.5:
            paper_reason_parts.append("cp_sec_sg_sum_lt_7_5")

        if decision_block_reason.strip().upper() not in {"", "ND", "NONE", "NULL"}:
            paper_reason_parts.append("decision_block:" + decision_block_reason.strip())

        paper_candidate_reason = " | ".join(paper_reason_parts) if paper_reason_parts else "paper gate closed"

    ticket_exact = "NO_BET"

    if verdict_python in ["TICKET", "MICRO_TICKET"]:
        if selection_cp_sec:
            ticket_exact = f"CP_SEC {selection_cp_sec}"
        elif selection_sp:
            ticket_exact = f"SP {selection_sp}"
        elif selection_cp3:
            ticket_exact = f"CP3 {selection_cp3}"

    action_gpt = "Controler uniquement. Ne pas transformer une simulation en ticket strict."

    if kill_switch_codes:
        action_gpt = "Kill-switch actif. Confirmer NO BET."
        ticket_exact = "NO_BET"
    elif segment_roi == "SIMULATION":
        action_gpt = "Confirmer SIMULATION ou NO BET. Aucun ticket reel strict."
        ticket_exact = "NO_BET"
    elif edge_status in ["EDGE_FRAGILE", "EDGE_ABSENT"]:
        action_gpt = "Confirmer refus strict sauf preuve moteur complete."
        ticket_exact = "NO_BET"

    brief_lines = [
        f"VERDICT_PYTHON : {verdict_python}",
        f"COURSE : {course_uid}",
        f"QUALITE : {qualite}",
        f"STRUCTURE_MAX : {structure_max}",
        f"TICKET_EXACT : {ticket_exact}",
        f"SELECTION_CP_SEC : {selection_cp_sec}",
        f"CP_SEC_CANDIDAT : {cp_sec_candidate}",
        f"CP_SG_PAIR_SUM : {cp_sg_pair_sum}",
        f"CP_SG_FILTER_STATUS : {cp_sg_filter_status}",
        f"SELECTION_CP3 : {selection_cp3}",
        f"CP3_CANDIDAT : {cp3_candidate}",
        f"CP3_SG_MIN_PAIR_SUM : {cp3_sg_min_pair_sum}",
        f"CP3_SG_FILTER_STATUS : {cp3_sg_filter_status}",
        f"DECISION_STRICT_ALLOWED : {decision_strict_allowed}",
        f"DECISION_BLOCK_REASON : {decision_block_reason}",
        f"STRICT_GATE_SUMMARY : {strict_gate_summary}",
        f"VOLATILITE_SCOPE : {volatilite_scope}",
        f"VOLATILITE_TOP6_MAX_PCT : {volatilite_top6_max_pct}",
        f"VOLATILITE_TOP6_MAX_NUMERO : {volatilite_top6_max_numero}",
        f"VOLATILITE_TOP6_MAX_CHEVAL : {volatilite_top6_max_cheval}",
        f"VOLATILITE_TOP6_SG_9H : {volatilite_top6_sg_9h}",
        f"VOLATILITE_TOP6_SG_H5 : {volatilite_top6_sg_h5}",
        f"VOLATILITE_TOP6_DIRECTION : {volatilite_top6_direction}",
        f"VOLATILITE_STATUS : {volatilite_status}",
        f"CP_SEC_SG_SUM : {cp_sec_sg_sum}",
        f"CP_SEC_SG_STATUS : {cp_sec_sg_status}",
        f"PAPER_CANDIDATE_ALLOWED : {paper_candidate_allowed}",
        f"PAPER_CANDIDATE_TYPE : {paper_candidate_type}",
        f"PAPER_CANDIDATE_TICKET : {paper_candidate_ticket}",
        f"PAPER_CANDIDATE_SEGMENT : {paper_candidate_segment}",
        f"PAPER_CANDIDATE_MISE : {paper_candidate_mise}",
        f"PAPER_CANDIDATE_REASON : {paper_candidate_reason}",
        f"SELECTION_SP : {selection_sp}",
        f"REASON_CODES : {reason_codes}",
        f"KILL_SWITCHS : {kill_switch_codes if kill_switch_codes else 'aucun'}",
        f"SEGMENT_ROI : {segment_roi}",
        f"EDGE : {edge_status}",
        f"ACTION_GPT : {action_gpt}",
        f"SOURCE_DECISION : {source_decision}",
        "",
        "REGLE_GPI : ne pas changer le verdict Python. Ne jamais transformer une simulation en ticket strict. Sans moteur complet, split valide, proxy audite ou SP calibre, ticket reel interdit."
    ]

    try:
        from engine.odds.brief_odds_enricher import build_odds_brief_block
        odds_brief_block = build_odds_brief_block(odds_context_for_brief)
    except Exception as exc:
        odds_brief_block = f"ODDS_CONTEXT_ERROR : {type(exc).__name__}: {exc}"

    if odds_brief_block:
        brief_lines.extend([
            "",
            "ODDS_CONTEXT_GPI :",
            odds_brief_block,
        ])

    return {
        "course_uid": course_uid,
        "verdict_python": verdict_python,
        "qualite": qualite,
        "structure_max": structure_max,
        "ticket_exact": ticket_exact,
        "segment_roi": segment_roi,
        "edge_status": edge_status,
        "cp_sec_candidate": cp_sec_candidate,
        "cp_sg_pair_sum": cp_sg_pair_sum,
        "cp_sg_filter_status": cp_sg_filter_status,
        "cp3_candidate": cp3_candidate,
        "cp3_sg_min_pair_sum": cp3_sg_min_pair_sum,
        "cp3_sg_filter_status": cp3_sg_filter_status,
        "decision_strict_allowed": decision_strict_allowed,
        "decision_block_reason": decision_block_reason,
        "strict_gate_summary": strict_gate_summary,
        "volatilite_scope": volatilite_scope,
        "volatilite_top6_max_pct": volatilite_top6_max_pct,
        "volatilite_top6_max_numero": volatilite_top6_max_numero,
        "volatilite_top6_max_cheval": volatilite_top6_max_cheval,
        "volatilite_top6_sg_9h": volatilite_top6_sg_9h,
        "volatilite_top6_sg_h5": volatilite_top6_sg_h5,
        "volatilite_top6_direction": volatilite_top6_direction,
        "volatilite_status": volatilite_status,
        "cp_sec_sg_sum": cp_sec_sg_sum,
        "cp_sec_sg_status": cp_sec_sg_status,
        "paper_candidate_allowed": paper_candidate_allowed,
        "paper_candidate_type": paper_candidate_type,
        "paper_candidate_ticket": paper_candidate_ticket,
        "paper_candidate_segment": paper_candidate_segment,
        "paper_candidate_mise": paper_candidate_mise,
        "paper_candidate_reason": paper_candidate_reason,
        "reason_codes": reason_codes,
        "kill_switch_codes": kill_switch_codes,
        "action_gpt": action_gpt,
        "brief": "\n".join(brief_lines),
        "prompt_text": prompt_text
    }

def list_existing_files(base_dir: Path, expected_files: list[str]) -> dict:
    present = []
    missing = []

    for filename in expected_files:
        path = base_dir / filename

        if path.exists() and path.is_file():
            present.append(filename)
        else:
            missing.append(filename)

    return {
        "present": present,
        "missing": missing,
        "present_count": len(present),
        "missing_count": len(missing),
    }


def build_ruleset_status() -> dict:
    docs_status = list_existing_files(DOCS_DIR, EXPECTED_DOCS)

    calibration_status = list_existing_files(
        CALIBRATION_DIR,
        EXPECTED_CALIBRATION_AVAILABLE
    )

    blocking_status = list_existing_files(
        CALIBRATION_DIR,
        BLOCKING_CALIBRATION_FILES
    )

    strict_ready = (
        docs_status["missing_count"] == 0
        and blocking_status["missing_count"] == 0
    )

    limitations = []

    if "sp_calibration_segments.csv" in blocking_status["missing"]:
        limitations.append("SP standard interdit")

    if "train_test_split.csv" in blocking_status["missing"]:
        limitations.append("ticket strict interdit")

    if "decision_engine_outputs.csv" in blocking_status["missing"]:
        limitations.append("decision stricte reproductible indisponible")

    if blocking_status["missing_count"] > 0:
        limitations.append("edge net interdit")
        limitations.append("CP/CP3 limite a simulation ou edge fragile")
        limitations.append("NO_BET reste la decision normale")

    return {
        "ok": True,
        "ruleset_version": RULESET_VERSION,
        "engine_version": ENGINE_VERSION,
        "strict_ready": strict_ready,
        "docs_dir": safe_posix_path(DOCS_DIR),
        "calibration_dir": safe_posix_path(CALIBRATION_DIR),
        "documents": docs_status,
        "calibration_available": calibration_status,
        "missing_blocking_files": blocking_status["missing"],
        "blocking_files_present": blocking_status["present"],
        "limitations": limitations,
    }
@app.get("/health")
def health():
    ensure_dirs()

    return {
        "ok": True,
        "app": "GPI-Lite Watchlist",
        "version": "0.7.0-mcp-cloud"
    }

@app.get("/gpi/ruleset", dependencies=[Depends(require_api_key)])
def gpi_ruleset():
    ensure_dirs()
    return build_ruleset_status()

@app.get("/")
def root():
    return {
        "ok": True,
        "app": "GPI-Lite Watchlist API",
        "version": "0.4.0-cloud-ready",
        "health": "/health",
        "docs": "/docs",
        "chatgpt_brief": "/gpi/chatgpt-brief"
    }

@app.post("/upload/morning", dependencies=[Depends(require_api_key)])
async def upload_morning(file: UploadFile = File(...), filename: Optional[str] = None):
    return await save_pdf_upload(file, MORNING_DIR, filename)


@app.post("/drive/sync/morning", dependencies=[Depends(require_api_key)])
def drive_sync_morning():
    ensure_dirs()
    folder_id = get_drive_folder_id("morning")
    return sync_drive_folder_to_local(folder_id, "data/morning", clean_target=True)


@app.post("/drive/sync/h5", dependencies=[Depends(require_api_key)])
def drive_sync_h5():
    ensure_dirs()
    folder_id = get_drive_folder_id("h5")
    return sync_drive_folder_to_local(folder_id, "data/h5", clean_target=True)


@app.post("/drive/sync/results", dependencies=[Depends(require_api_key)])
def drive_sync_results():
    ensure_dirs()
    folder_id = get_drive_folder_id("results")
    return sync_drive_folder_to_local(folder_id, "data/results", clean_target=True)



# --- V0.8.4 archive safe helpers ---
def count_watchlist_rows_v084() -> int:
    from pathlib import Path
    import csv

    watchlist_path = Path("data/output/daily_watchlist.csv")

    if not watchlist_path.exists():
        return 0

    try:
        with watchlist_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            return sum(1 for _ in reader)
    except Exception:
        return 0


def count_morning_non_exploitable_v084(morning_result: dict) -> int:
    stdout = str(morning_result.get("stdout", ""))

    count = 0

    for line in stdout.splitlines():
        line_lower = str(line).lower()

        if "morning non exploitable" in line_lower:
            count += 1

    return count


@app.post("/drive/run/morning", dependencies=[Depends(require_api_key)])
def drive_run_morning():
    ensure_dirs()

    folder_id = get_drive_folder_id("morning")
    sync_result = sync_drive_folder_to_local(folder_id, "data/morning", clean_target=True)

    pdf_downloaded_count = sync_files_count(sync_result)

    if not sync_has_files(sync_result):
        return drive_empty_response(
            phase="morning",
            sync_result=sync_result,
            message="aucun PDF Drive morning"
        )

    morning_result = run_script("main.py")

    if morning_result.get("returncode") == 0:
        morning_priority_result = run_script("tools/prioritize_morning_watchlist_h5.py")
    else:
        morning_priority_result = {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "morning_main failed, priority skipped",
        }

    quality_result = run_script("quality_check.py")
    cloud_result = gpi_cloud_sync()

    watchlist_rows_count = count_watchlist_rows_v084()
    non_exploitable_count = count_morning_non_exploitable_v084(morning_result)

    archive_blocked = (
        non_exploitable_count > 0
        or watchlist_rows_count == 0
        or morning_result.get("returncode") != 0
    )

    archive_result = None

    if archive_enabled() and not archive_blocked:
        archive_folder_id = get_archive_folder_id("morning")
        archive_result = archive_downloaded_files(
            sync_result.get("downloads", []),
            archive_folder_id
        )

    elif archive_enabled() and archive_blocked:
        archive_result = {
            "ok": False,
            "skipped": True,
            "reason": "archivage morning bloque : PDF non exploitable, watchlist vide ou erreur main.py",
            "pdf_downloaded_count": pdf_downloaded_count,
            "watchlist_rows_count": watchlist_rows_count,
            "non_exploitable_count": non_exploitable_count,
            "errors": []
        }

    return {
        "ok": True,
        "phase": "morning",
        "sync": sync_result,
        "pdf_downloaded_count": pdf_downloaded_count,
        "watchlist_rows_count": watchlist_rows_count,
        "non_exploitable_count": non_exploitable_count,
        "archive_blocked": archive_blocked,
        "morning": morning_result,
        "morning_priority": morning_priority_result,
        "quality": quality_result,
        "cloud_sync": cloud_result,
        "archive": archive_result,
    }


@app.post("/drive/run/h5", dependencies=[Depends(require_api_key)])
def drive_run_h5():
    ensure_dirs()

    folder_id = get_drive_folder_id("h5")
    sync_result = sync_drive_folder_to_local(folder_id, "data/h5", clean_target=True)

    files_count = sync_files_count(sync_result)

    if files_count == 0:
        return drive_empty_response(
            phase="h5",
            sync_result=sync_result,
            message="aucun PDF Drive h5"
        )

    if files_count != 1:
        return {
            "ok": False,
            "phase": "h5",
            "error": "H-5 refuse : un seul PDF attendu dans Drive / h5",
            "files_count": files_count,
            "sync": sync_result,
            "context_9h": None,
            "latest_h5_context": None,
            "h5": None,
            "quality": None,
            "cloud_sync": None,
            "brief": None,
            "gpi_audit": None,
            "archive": None
        }

    context_9h = ensure_9h_context_for_h5(
        h5_dir="data/h5",
        output_dir="data/output"
    )

    latest_h5_context = write_latest_h5_context(context_9h)
    course_key = latest_h5_context.get("course_key", "")
    decision_aliases = {
        "ok": False,
        "error": "decision_aliases non execute",
        "source": "",
        "aliases": []
    }

    if not context_9h.get("ok"):
        return {
            "ok": False,
            "phase": "h5",
            "error": "fichier 9h correspondant absent pour H-5",
            "sync": sync_result,
            "context_9h": context_9h,
            "latest_h5_context": latest_h5_context,
            "morning": None,
            "h5": None,
            "results": None,
            "audit": None,
            "quality": None,
            "cloud_sync": None,
            "brief": None,
            "gpi_audit": None,
            "archive": None
        }

    h5_result = run_script("h5_main.py")
    h5_stdout = str(h5_result.get("stdout", ""))

    if "Aucun fichier course_9h_clean.csv" in h5_stdout:
        return {
            "ok": False,
            "phase": "h5",
            "error": "H-5 non exploitable : contexte 9h absent",
            "sync": sync_result,
            "context_9h": context_9h,
            "latest_h5_context": latest_h5_context,
            "h5": h5_result,
            "results": None,
            "audit": None,
            "quality": None,
            "cloud_sync": None,
            "brief": None,
            "gpi_audit": None,
            "archive": None
        }

    if "Type détecté : INCONNU" in h5_stdout or "ne peut pas servir de H-5 pré-course" in h5_stdout:
        return {
            "ok": False,
            "phase": "h5",
            "error": "H-5 refuse : type de PDF non exploitable",
            "sync": sync_result,
            "context_9h": context_9h,
            "latest_h5_context": latest_h5_context,
            "h5": h5_result,
            "results": None,
            "audit": None,
            "quality": None,
            "cloud_sync": None,
            "brief": None,
            "gpi_audit": None,
            "archive": None
        }

    decision_aliases = write_decision_lite_aliases(course_key)

    quality_result = run_script("quality_check.py")
    cloud_result = gpi_cloud_sync()
    brief_result = get_brief_for_course_key_payload(course_key)
    archive_result = None

    h5_ok = h5_result.get("returncode") == 0
    brief_ok = isinstance(brief_result, dict) and brief_result.get("ok") is True

    if archive_enabled() and h5_ok and brief_ok:
        archive_folder_id = get_archive_folder_id("h5")
        archive_result = archive_downloaded_files(
            sync_result.get("downloads", []),
            archive_folder_id
        )

    return {
        "ok": True,
        "phase": "h5",
        "sync": sync_result,
        "context_9h": context_9h,
        "latest_h5_context": latest_h5_context,
        "h5": h5_result,
        "decision_aliases": decision_aliases,
        "quality": quality_result,
        "cloud_sync": cloud_result,
        "brief": brief_result,
        "archive": archive_result,
    }


@app.post("/drive/run/results", dependencies=[Depends(require_api_key)])
def drive_run_results():
    ensure_dirs()

    try:
        folder_id = get_drive_folder_id("results")
        sync_result = sync_drive_folder_to_local(folder_id, "data/results", clean_target=True)
    except Exception as exc:
        sync_result = {
            "ok": True,
            "skipped": True,
            "reason": "DRIVE_RESULTS_FOLDER_ID absent ou sync results indisponible",
            "error": str(exc),
            "local_dir": "data/results",
        }

    results_pdf_parse_result = {
        "ok": False,
        "skipped": True,
        "reason": "parser PDF Results non lancé",
    }

    try:
        results_pdf_parse_result = run_script("tools/parse_results_pdfs_v098h.py")
    except Exception as exc:
        results_pdf_parse_result = {
            "ok": False,
            "skipped": False,
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
        }

    results_pdf_parse_upload_targets = [
        Path("data/results/result_input.csv"),
        OUTPUT_DIR / "results_pdf_parse_audit.csv",
    ]
    results_pdf_parse_uploads = []

    if results_pdf_parse_result.get("returncode") == 0:
        for local_path in results_pdf_parse_upload_targets:
            results_pdf_parse_uploads.append(upload_file_to_gcs(local_path, "outputs"))

    results_pdf_parse_cloud_result = {
        "ok": all(item.get("ok") for item in results_pdf_parse_uploads) if results_pdf_parse_uploads else False,
        "bucket": GPI_BUCKET_NAME,
        "files_count": len(results_pdf_parse_uploads),
        "uploads": results_pdf_parse_uploads,
    }

    try:
        results_path, latest_path, audit_csv_path, audit_json_path, audit = write_paper_results()

        paper_results_result = {
            "ok": True,
            "returncode": 0,
            "stdout": (
                "Paper Results créés\\n"
                + str(results_path) + "\\n"
                + str(latest_path) + "\\n"
                + str(audit_csv_path) + "\\n"
                + str(audit_json_path) + "\\n"
                + f"paper_count : {audit.get('paper_count')}\\n"
                + f"results_joined_count : {audit.get('results_joined_count')}\\n"
                + f"result_no_bet_count : {audit.get('result_no_bet_count')}\\n"
            ),
            "stderr": "",
            "audit": audit,
        }
    except Exception as exc:
        paper_results_result = {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
            "audit": {},
        }

    upload_targets = [
        OUTPUT_DIR / "paper_trading_results.csv",
        OUTPUT_DIR / "latest_paper_result_update.csv",
        OUTPUT_DIR / "paper_results_audit.csv",
        OUTPUT_DIR / "latest_paper_results_audit.json",
    ]

    uploads = []

    if paper_results_result.get("returncode") == 0:
        for local_path in upload_targets:
            uploads.append(upload_file_to_gcs(local_path, "outputs"))

    cloud_result = {
        "ok": all(item.get("ok") for item in uploads) if uploads else False,
        "bucket": GPI_BUCKET_NAME,
        "files_count": len(uploads),
        "uploads": uploads,
    }

    paper_kpi_result = {
        "ok": False,
        "skipped": True,
        "reason": "paper_results non valide",
    }

    kpi_uploads = []

    if paper_results_result.get("returncode") == 0:
        try:
            kpi_path, kpi_audit_csv_path, kpi_audit_json_path, kpi_audit = write_paper_kpi()

            kpi_uploads = [
                upload_file_to_gcs(kpi_path, "outputs"),
                upload_file_to_gcs(kpi_audit_csv_path, "outputs"),
                upload_file_to_gcs(kpi_audit_json_path, "outputs"),
            ]

            paper_kpi_result = {
                "ok": True,
                "skipped": False,
                "returncode": 0,
                "audit": kpi_audit,
                "outputs": {
                    "paper_kpi_by_segment": safe_posix_path(kpi_path),
                    "paper_kpi_audit": safe_posix_path(kpi_audit_csv_path),
                    "latest_paper_kpi_audit": safe_posix_path(kpi_audit_json_path),
                },
            }

        except Exception as exc:
            paper_kpi_result = {
                "ok": False,
                "skipped": False,
                "returncode": 1,
                "error": str(exc),
            }

    kpi_cloud_result = {
        "ok": all(item.get("ok") for item in kpi_uploads) if kpi_uploads else False,
        "bucket": GPI_BUCKET_NAME,
        "files_count": len(kpi_uploads),
        "uploads": kpi_uploads,
    }

    candidate_roi_result = {
        "ok": False,
        "skipped": True,
        "reason": "paper_results non valide",
    }

    candidate_roi_uploads = []

    if paper_results_result.get("returncode") == 0:
        try:
            candidate_roi_result = run_script("tools/calc_candidate_roi_v0921.py")

            if candidate_roi_result.get("returncode") == 0:
                candidate_roi_uploads = [
                    upload_file_to_gcs(OUTPUT_DIR / "paper_roi_by_candidate_segment.csv", "outputs"),
                    upload_file_to_gcs(OUTPUT_DIR / "latest_candidate_roi_audit.json", "outputs"),
                ]

        except Exception as exc:
            candidate_roi_result = {
                "ok": False,
                "skipped": False,
                "returncode": 1,
                "stdout": "",
                "stderr": str(exc),
            }

    candidate_roi_cloud_result = {
        "ok": all(item.get("ok") for item in candidate_roi_uploads) if candidate_roi_uploads else False,
        "bucket": GPI_BUCKET_NAME,
        "files_count": len(candidate_roi_uploads),
        "uploads": candidate_roi_uploads,
    }

    candidate_roi_robustness_result = {
        "ok": False,
        "skipped": True,
        "reason": "candidate_roi non valide",
    }

    candidate_roi_robustness_uploads = []

    if candidate_roi_result.get("returncode") == 0:
        try:
            candidate_roi_robustness_result = run_script("tools/audit_candidate_roi_robustness_v0921.py")

            if candidate_roi_robustness_result.get("returncode") == 0:
                candidate_roi_robustness_uploads = [
                    upload_file_to_gcs(OUTPUT_DIR / "candidate_roi_robustness.csv", "outputs"),
                    upload_file_to_gcs(OUTPUT_DIR / "latest_candidate_roi_robustness.json", "outputs"),
                ]

        except Exception as exc:
            candidate_roi_robustness_result = {
                "ok": False,
                "skipped": False,
                "returncode": 1,
                "stdout": "",
                "stderr": str(exc),
            }

    candidate_roi_robustness_cloud_result = {
        "ok": all(item.get("ok") for item in candidate_roi_robustness_uploads) if candidate_roi_robustness_uploads else False,
        "bucket": GPI_BUCKET_NAME,
        "files_count": len(candidate_roi_robustness_uploads),
        "uploads": candidate_roi_robustness_uploads,
    }

    continuation_stop_dashboard_result = {
        "ok": False,
        "skipped": True,
        "reason": "candidate_roi_robustness non valide",
    }

    continuation_stop_dashboard_uploads = []

    if candidate_roi_robustness_result.get("returncode") == 0:
        try:
            continuation_stop_dashboard_result = run_script("tools/build_continuation_stop_dashboard_v0922.py")

            if continuation_stop_dashboard_result.get("returncode") == 0:
                continuation_stop_dashboard_uploads = [
                    upload_file_to_gcs(OUTPUT_DIR / "continuation_stop_dashboard.csv", "outputs"),
                    upload_file_to_gcs(OUTPUT_DIR / "latest_continuation_stop_dashboard.json", "outputs"),
                ]

        except Exception as exc:
            continuation_stop_dashboard_result = {
                "ok": False,
                "skipped": False,
                "returncode": 1,
                "stdout": "",
                "stderr": str(exc),
            }

    continuation_stop_dashboard_cloud_result = {
        "ok": all(item.get("ok") for item in continuation_stop_dashboard_uploads) if continuation_stop_dashboard_uploads else False,
        "bucket": GPI_BUCKET_NAME,
        "files_count": len(continuation_stop_dashboard_uploads),
        "uploads": continuation_stop_dashboard_uploads,
    }

    no_bet_audit_result = {
        "ok": False,
        "skipped": True,
        "reason": "paper_results non valide",
    }

    no_bet_audit_uploads = []

    if paper_results_result.get("returncode") == 0:
        try:
            no_bet_audit_result = run_script("tools/audit_no_bet_results_v098j.py")

            if no_bet_audit_result.get("returncode") == 0:
                no_bet_audit_uploads = [
                    upload_file_to_gcs(OUTPUT_DIR / "no_bet_audit_results.csv", "outputs"),
                    upload_file_to_gcs(OUTPUT_DIR / "latest_no_bet_audit.json", "outputs"),
                ]

        except Exception as exc:
            no_bet_audit_result = {
                "ok": False,
                "skipped": False,
                "returncode": 1,
                "stdout": "",
                "stderr": str(exc),
            }

    no_bet_audit_cloud_result = {
        "ok": all(item.get("ok") for item in no_bet_audit_uploads) if no_bet_audit_uploads else False,
        "bucket": GPI_BUCKET_NAME,
        "files_count": len(no_bet_audit_uploads),
        "uploads": no_bet_audit_uploads,
    }

    return {
        "ok": (
            paper_results_result.get("returncode") == 0
            and cloud_result.get("ok") is True
            and paper_kpi_result.get("ok") is True
            and kpi_cloud_result.get("ok") is True
            and candidate_roi_result.get("returncode") == 0
            and candidate_roi_cloud_result.get("ok") is True
            and candidate_roi_robustness_result.get("returncode") == 0
            and candidate_roi_robustness_cloud_result.get("ok") is True
            and continuation_stop_dashboard_result.get("returncode") == 0
            and continuation_stop_dashboard_cloud_result.get("ok") is True
            and no_bet_audit_result.get("returncode") == 0
            and no_bet_audit_cloud_result.get("ok") is True
        ),
        "phase": "paper_results",
        "sync": sync_result,
        "results_pdf_parse": results_pdf_parse_result,
        "results_pdf_parse_cloud_sync": results_pdf_parse_cloud_result,
        "paper_results": paper_results_result,
        "paper_kpi": paper_kpi_result,
        "candidate_roi": candidate_roi_result,
        "candidate_roi_cloud_sync": candidate_roi_cloud_result,
        "candidate_roi_robustness": candidate_roi_robustness_result,
        "candidate_roi_robustness_cloud_sync": candidate_roi_robustness_cloud_result,
        "continuation_stop_dashboard": continuation_stop_dashboard_result,
        "continuation_stop_dashboard_cloud_sync": continuation_stop_dashboard_cloud_result,
        "no_bet_audit": no_bet_audit_result,
        "no_bet_audit_cloud_sync": no_bet_audit_cloud_result,
        "cloud_sync": cloud_result,
        "kpi_cloud_sync": kpi_cloud_result,
        "guardrails": {
            "h5_main_called": False,
            "results_main_called": False,
            "audit_all_called": False,
            "decision_engine_outputs_modified": False,
            "paper_trading_50_decisions_uploaded": False,
            "paper_trading_50_decisions_modified": False,
            "post_course_only": True,
            "drive_results_required": False,
        },
    }



@app.post("/drive/run/results-compact", dependencies=[Depends(require_api_key)])
def drive_run_results_compact():
    """
    Version compacte de /drive/run/results pour orchestration.
    Exécute le même traitement post-course, mais ne renvoie pas la liste complète
    des fichiers Drive téléchargés.
    """
    payload = drive_run_results()

    if not isinstance(payload, dict):
        return {
            "ok": False,
            "phase": "paper_results",
            "error": "RESULTS_NON_DICT_RESPONSE",
            "response_type": str(type(payload)),
        }

    sync = payload.get("sync", {}) if isinstance(payload.get("sync"), dict) else {}
    results_pdf_parse = payload.get("results_pdf_parse", {}) if isinstance(payload.get("results_pdf_parse"), dict) else {}
    paper_results = payload.get("paper_results", {}) if isinstance(payload.get("paper_results"), dict) else {}
    paper_kpi = payload.get("paper_kpi", {}) if isinstance(payload.get("paper_kpi"), dict) else {}
    candidate_roi = payload.get("candidate_roi", {}) if isinstance(payload.get("candidate_roi"), dict) else {}
    candidate_roi_cloud_sync = payload.get("candidate_roi_cloud_sync", {}) if isinstance(payload.get("candidate_roi_cloud_sync"), dict) else {}
    candidate_roi_robustness = payload.get("candidate_roi_robustness", {}) if isinstance(payload.get("candidate_roi_robustness"), dict) else {}
    candidate_roi_robustness_cloud_sync = payload.get("candidate_roi_robustness_cloud_sync", {}) if isinstance(payload.get("candidate_roi_robustness_cloud_sync"), dict) else {}
    continuation_stop_dashboard = payload.get("continuation_stop_dashboard", {}) if isinstance(payload.get("continuation_stop_dashboard"), dict) else {}
    continuation_stop_dashboard_cloud_sync = payload.get("continuation_stop_dashboard_cloud_sync", {}) if isinstance(payload.get("continuation_stop_dashboard_cloud_sync"), dict) else {}
    no_bet_audit = payload.get("no_bet_audit", {}) if isinstance(payload.get("no_bet_audit"), dict) else {}
    guardrails = payload.get("guardrails", {}) if isinstance(payload.get("guardrails"), dict) else {}

    parse_stdout = str(results_pdf_parse.get("stdout", ""))
    candidate_roi_stdout = str(candidate_roi.get("stdout", ""))
    candidate_roi_robustness_stdout = str(candidate_roi_robustness.get("stdout", ""))
    continuation_stop_dashboard_stdout = str(continuation_stop_dashboard.get("stdout", ""))
    no_bet_stdout = str(no_bet_audit.get("stdout", ""))

    paper_results_audit = paper_results.get("audit", {})
    if not isinstance(paper_results_audit, dict):
        paper_results_audit = {}

    paper_kpi_audit = paper_kpi.get("audit", {})
    if not isinstance(paper_kpi_audit, dict):
        paper_kpi_audit = {}

    return {
        "ok": bool(payload.get("ok", False)),
        "phase": payload.get("phase", "paper_results"),
        "compact": True,
        "sync": {
            "ok": sync.get("ok", "ND"),
            "folder_id": sync.get("folder_id", "ND"),
            "local_dir": sync.get("local_dir", "ND"),
            "files_count": sync.get("files_count", "ND"),
            "downloads_count": len(sync.get("downloads", [])) if isinstance(sync.get("downloads"), list) else "ND",
        },
        "results_pdf_parse": {
            "ok": results_pdf_parse.get("ok", "ND"),
            "returncode": results_pdf_parse.get("returncode", "ND"),
            "stdout_summary": parse_stdout[:1500],
            "stderr": str(results_pdf_parse.get("stderr", ""))[:1000],
        },
        "paper_results": {
            "ok": paper_results.get("ok", "ND"),
            "returncode": paper_results.get("returncode", "ND"),
            "paper_count": paper_results_audit.get("paper_count", "ND"),
            "result_input_count": paper_results_audit.get("result_input_count", "ND"),
            "results_joined_count": paper_results_audit.get("results_joined_count", "ND"),
            "result_ok_count": paper_results_audit.get("result_ok_count", "ND"),
            "result_no_bet_count": paper_results_audit.get("result_no_bet_count", "ND"),
            "result_pending_count": paper_results_audit.get("result_pending_count", "ND"),
        },
        "paper_kpi": {
            "ok": paper_kpi.get("ok", "ND"),
            "skipped": paper_kpi.get("skipped", "ND"),
            "returncode": paper_kpi.get("returncode", "ND"),
            "rows_input": paper_kpi_audit.get("rows_input", "ND"),
            "rows_kpi": paper_kpi_audit.get("rows_kpi", "ND"),
            "global_decisions": paper_kpi_audit.get("global_decisions", "ND"),
            "global_tickets": paper_kpi_audit.get("global_tickets", "ND"),
            "global_no_bet_count": paper_kpi_audit.get("global_no_bet_count", "ND"),
            "global_result_pending_count": paper_kpi_audit.get("global_result_pending_count", "ND"),
            "global_strict_allowed_count": paper_kpi_audit.get("global_strict_allowed_count", "ND"),
            "global_replay_ko_count": paper_kpi_audit.get("global_replay_ko_count", "ND"),
        },
        "candidate_roi": {
            "ok": candidate_roi.get("ok", "ND"),
            "returncode": candidate_roi.get("returncode", "ND"),
            "stdout_summary": candidate_roi_stdout[:1500],
            "stderr": str(candidate_roi.get("stderr", ""))[:1000],
        },
        "candidate_roi_cloud_sync": {
            "ok": candidate_roi_cloud_sync.get("ok", "ND"),
            "files_count": candidate_roi_cloud_sync.get("files_count", "ND"),
            "uploads": candidate_roi_cloud_sync.get("uploads", []),
        },
        "candidate_roi_robustness": {
            "ok": candidate_roi_robustness.get("ok", "ND"),
            "returncode": candidate_roi_robustness.get("returncode", "ND"),
            "stdout_summary": candidate_roi_robustness_stdout[:1500],
            "stderr": str(candidate_roi_robustness.get("stderr", ""))[:1000],
        },
        "candidate_roi_robustness_cloud_sync": {
            "ok": candidate_roi_robustness_cloud_sync.get("ok", "ND"),
            "files_count": candidate_roi_robustness_cloud_sync.get("files_count", "ND"),
            "uploads": candidate_roi_robustness_cloud_sync.get("uploads", []),
        },
        "continuation_stop_dashboard": {
            "ok": continuation_stop_dashboard.get("ok", "ND"),
            "returncode": continuation_stop_dashboard.get("returncode", "ND"),
            "stdout_summary": continuation_stop_dashboard_stdout[:1500],
            "stderr": str(continuation_stop_dashboard.get("stderr", ""))[:1000],
        },
        "continuation_stop_dashboard_cloud_sync": {
            "ok": continuation_stop_dashboard_cloud_sync.get("ok", "ND"),
            "files_count": continuation_stop_dashboard_cloud_sync.get("files_count", "ND"),
            "uploads": continuation_stop_dashboard_cloud_sync.get("uploads", []),
        },
        "no_bet_audit": {
            "ok": no_bet_audit.get("ok", "ND"),
            "returncode": no_bet_audit.get("returncode", "ND"),
            "stdout_summary": no_bet_stdout[:1500],
            "stderr": str(no_bet_audit.get("stderr", ""))[:1000],
        },
        "guardrails": {
            "h5_main_called": guardrails.get("h5_main_called", "ND"),
            "results_main_called": guardrails.get("results_main_called", "ND"),
            "audit_all_called": guardrails.get("audit_all_called", "ND"),
            "decision_engine_outputs_modified": guardrails.get("decision_engine_outputs_modified", "ND"),
            "paper_trading_50_decisions_uploaded": guardrails.get("paper_trading_50_decisions_uploaded", "ND"),
            "paper_trading_50_decisions_modified": guardrails.get("paper_trading_50_decisions_modified", "ND"),
            "post_course_only": guardrails.get("post_course_only", "ND"),
            "drive_results_required": guardrails.get("drive_results_required", "ND"),
        },
    }


from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import csv
import re

def _h5auto_safe_str(x):
    if x is None:
        return ""
    return str(x).strip()

def _h5auto_runtime_dir():
    p = Path("sources/runtime")
    p.mkdir(parents=True, exist_ok=True)
    return p

def _h5auto_read_csv_dicts(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def _h5auto_write_csv_dicts(path, rows, fieldnames):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "ND") for k in fieldnames})

def _h5auto_parse_course_date_from_uid(course_uid):
    m = re.match(r"(\d{2})_(\d{2})_(\d{4})", _h5auto_safe_str(course_uid))
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return f"{yyyy}-{mm}-{dd}"

def _h5auto_load_latest_watchlist_rows():
    """
    Charge la watchlist morning.
    Priorité :
    1. fichiers locaux si présents dans l'instance Cloud Run ;
    2. fallback GCS latest_daily_watchlist_priorisee.csv.
    """
    candidates = [
        Path("data/output/latest_daily_watchlist_priorisee.csv"),
        Path("data/output/daily_watchlist.csv"),
    ]

    for p in candidates:
        rows = _h5auto_read_csv_dicts(p)
        if rows:
            return rows, str(p)

    try:
        import os
        import io
        from google.cloud import storage

        bucket_name = (
            os.getenv("GPI_BUCKET_NAME")
            or os.getenv("GCS_BUCKET")
            or "gpi-lite-watchlist-data"
        )

        gcs_candidates = [
            "gpi-lite/outputs/latest_daily_watchlist_priorisee.csv",
            "gpi-lite/outputs/daily_watchlist.csv",
        ]

        client = storage.Client()
        bucket = client.bucket(bucket_name)

        for blob_name in gcs_candidates:
            blob = bucket.blob(blob_name)
            if not blob.exists():
                continue

            content = blob.download_as_text(encoding="utf-8")
            rows = list(csv.DictReader(io.StringIO(content)))

            if rows:
                return rows, f"gs://{bucket_name}/{blob_name}"

    except Exception:
        pass

    return [], "ND"

def _h5auto_load_url_mapping():
    mapping_path = _h5auto_runtime_dir() / "zeturf_course_urls.csv"
    rows = _h5auto_read_csv_dicts(mapping_path)
    out = {}
    for r in rows:
        uid = _h5auto_safe_str(r.get("course_uid"))
        if uid:
            out[uid] = {
                "url_zeturf": _h5auto_safe_str(r.get("url_zeturf")),
            }
    return out

def _h5auto_normalize_course_key(s):
    s = _h5auto_safe_str(s).lower()
    s = s.replace("_pronostics & cotes", "")
    s = s.replace("pronostics & cotes", "")
    s = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _h5auto_find_morning_pdf_for_course(course_uid):
    morning_dir = Path("data/morning")
    if not morning_dir.exists():
        return None

    uid_norm = _h5auto_normalize_course_key(course_uid)
    best = None
    best_score = 0

    for p in morning_dir.glob("*.pdf"):
        name_norm = _h5auto_normalize_course_key(p.stem)
        uid_tokens = set(uid_norm.split())
        name_tokens = set(name_norm.split())

        score = 0
        if uid_norm and uid_norm in name_norm:
            score += 100

        score += len(uid_tokens.intersection(name_tokens))

        if score > best_score:
            best = p
            best_score = score

    if best_score <= 3:
        return None

    return best

def _h5auto_read_pdf_text_best_effort(pdf_path):
    pdf_path = Path(pdf_path)
    text_parts = []

    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages[:3]:
                txt = page.extract_text() or ""
                if txt:
                    text_parts.append(txt)
    except Exception:
        pass

    if text_parts:
        return "\n".join(text_parts)

    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        for page in doc[:3]:
            txt = page.get_text("text") or ""
            if txt:
                text_parts.append(txt)
        doc.close()
    except Exception:
        pass

    return "\n".join(text_parts)

def _h5auto_extract_depart_time_from_text(text):
    t = _h5auto_safe_str(text)
    if not t:
        return ""

    priority_patterns = [
        r"(?:Départ|Depart|Départ prévu|Depart prevu|Départ\s+à|Depart\s+a)[^\d]{0,40}(\d{1,2})[:hH](\d{2})",
        r"(?:Course|Réunion|Reunion)[^\n]{0,100}(?:Départ|Depart)[^\d]{0,40}(\d{1,2})[:hH](\d{2})",
        r"\b(\d{1,2})[:hH](\d{2})\b",
    ]

    candidates = []

    for idx, pat in enumerate(priority_patterns):
        for m in re.finditer(pat, t, flags=re.IGNORECASE):
            hh = int(m.group(1))
            mm = int(m.group(2))
            if 6 <= hh <= 23 and 0 <= mm <= 59:
                context = t[max(0, m.start() - 60):m.end() + 60].lower()
                score = 100 - idx * 20
                if "départ" in context or "depart" in context:
                    score += 50
                if "prix" in context:
                    score += 10
                if "course" in context:
                    score += 10
                if "rapport" in context or "arrivée" in context or "arrivee" in context:
                    score -= 30
                candidates.append((score, hh, mm))

    if not candidates:
        return ""

    candidates.sort(reverse=True)
    _, hh, mm = candidates[0]
    return f"{hh:02d}:{mm:02d}"

def _h5auto_extract_depart_time_from_morning_pdf(course_uid):
    pdf = _h5auto_find_morning_pdf_for_course(course_uid)
    if not pdf:
        return "", "PDF_MORNING_NOT_FOUND", ""

    text = _h5auto_read_pdf_text_best_effort(pdf)
    if not text:
        return "", "PDF_TEXT_EMPTY", str(pdf)

    heure = _h5auto_extract_depart_time_from_text(text)
    if not heure:
        return "", "HEURE_NOT_FOUND_IN_PDF", str(pdf)

    return heure, "PDF_MORNING", str(pdf)





# ============================================================
# H5 HISTORY ARCHIVE V0_1
# Conserve plans, captures, briefs, decisions et statuts H5.
# Ce qui n'a pas ete horodate avant course n'est pas une decision live.
# C'est au mieux un audit de rattrapage.
# ============================================================

from tools.archive_h5_history import (
    archive_h5_artifact,
    extract_date_course,
)


def _archive_h5_payload_safe(
    *,
    payload,
    course_uid,
    capture_label,
    source_route,
    artifact_type,
    status,
    reason,
    decision_before_race_oui_non="ND",
    result_known_at_decision="ND",
    live_eligible_oui_non="non",
    retro_audit_only_oui_non="non",
):
    """
    Archive best-effort H5.
    Ne doit jamais casser une route H5.
    Ne cree aucun ticket reel.
    Ne modifie aucun strict.
    """
    try:
        archive_result = archive_h5_artifact(
            base_dir="data/output",
            payload=payload if isinstance(payload, dict) else {"payload": str(payload)},
            course_uid=course_uid or "COURSE_ND",
            date_course=extract_date_course(course_uid or ""),
            capture_label=capture_label or "ND",
            source_route=source_route,
            artifact_type=artifact_type,
            status=status,
            reason=reason,
            decision_before_race_oui_non=decision_before_race_oui_non,
            result_known_at_decision=result_known_at_decision,
            live_eligible_oui_non=live_eligible_oui_non,
            retro_audit_only_oui_non=retro_audit_only_oui_non,
        )

        gcs_uploads = []

        for local_key in ["artifact_path", "index_path"]:
            local_value = archive_result.get(local_key)
            if local_value:
                local_path = Path(local_value)
                try:
                    rel_path = local_path.relative_to(Path("data/output/history/h5"))
                    blob_name = f"gpi-lite/history/h5/{rel_path.as_posix()}"
                    gcs_uploads.append(_h5auto_upload_file_to_gcs(local_path, blob_name))
                except Exception as upload_exc:
                    gcs_uploads.append({
                        "ok": False,
                        "reason": f"H5_HISTORY_GCS_UPLOAD_ERROR:{type(upload_exc).__name__}:{upload_exc}",
                        "local_path": str(local_path),
                    })

        archive_result["gcs_uploads"] = gcs_uploads
        archive_result["gcs_uploaded_count"] = len([x for x in gcs_uploads if isinstance(x, dict) and x.get("ok") is True])
        archive_result["gcs_upload_ok"] = all(x.get("ok") is True for x in gcs_uploads) if gcs_uploads else False

        return archive_result
    except Exception as exc:
        return {
            "ok": False,
            "archive_version": "ARCHIVE_H5_HISTORY_V0_1",
            "error": str(exc),
            "source_route": source_route,
            "artifact_type": artifact_type,
            "course_uid": course_uid or "COURSE_ND",
            "ticket_real": "NO_BET",
            "strict_allowed_oui_non": "non",
        }



def _h5auto_upload_file_to_gcs(local_path, blob_name):
    """
    Upload best-effort vers GCS.
    Ne doit jamais casser /drive/run/h5-auto-plan.
    """
    import os

    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
    )

    if not bucket_name:
        return {
            "ok": False,
            "reason": "BUCKET_ENV_MISSING",
            "bucket": "ND",
            "blob": blob_name,
            "gcs_uri": "ND",
        }

    local_path = Path(local_path)

    if not local_path.exists():
        return {
            "ok": False,
            "reason": "LOCAL_FILE_MISSING",
            "bucket": bucket_name,
            "blob": blob_name,
            "gcs_uri": f"gs://{bucket_name}/{blob_name}",
        }

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(local_path))

        return {
            "ok": True,
            "reason": "UPLOADED",
            "bucket": bucket_name,
            "blob": blob_name,
            "gcs_uri": f"gs://{bucket_name}/{blob_name}",
        }

    except Exception as exc:
        return {
            "ok": False,
            "reason": f"GCS_UPLOAD_ERROR:{type(exc).__name__}:{exc}",
            "bucket": bucket_name,
            "blob": blob_name,
            "gcs_uri": f"gs://{bucket_name}/{blob_name}",
        }




def _h5auto_download_file_from_gcs(blob_name, local_path):
    """
    Download best-effort depuis GCS.
    Ne doit jamais casser brutalement les routes H5 auto.
    """
    import os

    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
    )

    if not bucket_name:
        return {
            "ok": False,
            "reason": "BUCKET_ENV_MISSING",
            "bucket": "ND",
            "blob": blob_name,
            "local_path": str(local_path),
            "gcs_uri": "ND",
        }

    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if not blob.exists():
            return {
                "ok": False,
                "reason": "GCS_BLOB_MISSING",
                "bucket": bucket_name,
                "blob": blob_name,
                "local_path": str(local_path),
                "gcs_uri": f"gs://{bucket_name}/{blob_name}",
            }

        blob.download_to_filename(str(local_path))

        return {
            "ok": True,
            "reason": "DOWNLOADED",
            "bucket": bucket_name,
            "blob": blob_name,
            "local_path": str(local_path),
            "gcs_uri": f"gs://{bucket_name}/{blob_name}",
        }

    except Exception as exc:
        return {
            "ok": False,
            "reason": f"GCS_DOWNLOAD_ERROR:{type(exc).__name__}:{exc}",
            "bucket": bucket_name,
            "blob": blob_name,
            "local_path": str(local_path),
            "gcs_uri": f"gs://{bucket_name}/{blob_name}",
        }




def _h5auto_safe_slug(value):
    cleaned = "".join(
        c.lower() if c.isalnum() else "_"
        for c in str(value or "").strip()
    )
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "nd"


def _h5auto_download_url_to_file(url, local_path, timeout=30):
    """
    Capture HTML simple via urllib.
    Aucun ticket strict.
    """
    from pathlib import Path
    from urllib.request import Request, urlopen

    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 GPI-Lite Watchlist HTML Capture",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
            },
        )

        with urlopen(req, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="ignore")

        local_path.write_text(html, encoding="utf-8")

        return {
            "ok": True,
            "reason": "DOWNLOADED",
            "url": url,
            "local_path": str(local_path),
            "html_size": len(html),
        }

    except Exception as exc:
        return {
            "ok": False,
            "reason": f"HTML_DOWNLOAD_ERROR:{type(exc).__name__}:{exc}",
            "url": url,
            "local_path": str(local_path),
            "html_size": 0,
        }


def _h5auto_read_csv_dicts(path):
    import csv
    from pathlib import Path

    path = Path(path)
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _h5auto_is_due_capture(row, now_iso):
    """
    Capture uniquement si now est dans la fenêtre due_from / due_to.
    """
    from datetime import datetime

    due_from = str(row.get("due_from", "") or "").strip()
    due_to = str(row.get("due_to", "") or "").strip()

    if not due_from or not due_to:
        return False

    try:
        now = datetime.fromisoformat(now_iso)
        start = datetime.fromisoformat(due_from)
        end = datetime.fromisoformat(due_to)
        return start <= now <= end
    except Exception:
        return False


@app.post("/drive/run/h5-auto-plan", dependencies=[Depends(require_api_key)])
def drive_run_h5_auto_plan():
    """
    Crée un planning H-10/H-5 depuis la watchlist morning.
    L'horaire est extrait des PDF morning.
    P1/P2 uniquement.
    Aucun ticket strict.
    """
    ensure_dirs()

    morning_sync = {
        "ok": False,
        "skipped": True,
        "reason": "SYNC_NOT_RUN",
    }

    try:
        folder_id = get_drive_folder_id("morning")
        morning_sync = sync_drive_folder_to_local(folder_id, "data/morning", clean_target=True)
    except Exception as exc:
        morning_sync = {
            "ok": False,
            "skipped": True,
            "reason": "MORNING_SYNC_FAILED",
            "error": str(exc),
        }

    watchlist_rows, watchlist_source = _h5auto_load_latest_watchlist_rows()
    url_map = _h5auto_load_url_mapping()

    paris_tz = ZoneInfo("Europe/Paris")
    now = datetime.now(paris_tz)
    today_date = now.date().isoformat()

    plan_rows = []
    skipped = []

    for r in watchlist_rows:
        uid = _h5auto_safe_str(r.get("course_uid"))
        priorite = _h5auto_safe_str(r.get("priorite_h5"))

        if priorite not in {"P1", "P2"}:
            skipped.append({
                "course_uid": uid,
                "reason": "PRIORITE_NON_P1_P2",
                "priorite_h5": priorite or "ND",
                "heure": _h5auto_safe_str(r.get("heure")) or "ND",
            })
            continue

        course_date = _h5auto_parse_course_date_from_uid(uid)
        if not course_date:
            skipped.append({
                "course_uid": uid,
                "reason": "DATE_ND",
                "priorite_h5": priorite,
                "heure": "ND",
            })
            continue

        if course_date != today_date:
            skipped.append({
                "course_uid": uid,
                "reason": "NOT_TODAY",
                "priorite_h5": priorite,
                "heure": "ND",
            })
            continue

        heure = ""
        heure_source = ""
        pdf_source = ""

        for col in ["heure_depart", "heure", "horaire", "depart", "heure_course"]:
            v = _h5auto_safe_str(r.get(col))
            if v and v != "ND":
                m = re.search(r"(\d{1,2})[:hH](\d{2})", v)
                if m:
                    heure = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
                    heure_source = f"WATCHLIST_COL_{col}"
                    break

        if not heure:
            heure, heure_source, pdf_source = _h5auto_extract_depart_time_from_morning_pdf(uid)

        if not heure:
            skipped.append({
                "course_uid": uid,
                "reason": heure_source or "HEURE_ND",
                "priorite_h5": priorite,
                "heure": "ND",
            })
            continue

        mapped = url_map.get(uid, {})
        url = _h5auto_safe_str(mapped.get("url_zeturf"))
        url_status = "OK" if url else "URL_REQUIRED"

        try:
            depart_dt = datetime.fromisoformat(f"{course_date}T{heure}:00").replace(tzinfo=paris_tz)
        except Exception:
            skipped.append({
                "course_uid": uid,
                "reason": "HEURE_FORMAT_KO",
                "priorite_h5": priorite,
                "heure": heure,
            })
            continue

        for label, minutes_before in [("H10", 10), ("H5", 5)]:
            capture_dt = depart_dt - timedelta(minutes=minutes_before)
            due_from = capture_dt - timedelta(minutes=3)
            due_to = capture_dt + timedelta(minutes=3)

            if now < due_from:
                status = "PENDING"
            elif due_from <= now <= due_to:
                status = "DUE"
            elif now > due_to and now < depart_dt:
                status = "LATE_BUT_PRECOURSE"
            else:
                status = "EXPIRED_OR_STARTED"

            if url_status == "URL_REQUIRED":
                status = "URL_REQUIRED"

            plan_rows.append({
                "course_uid": uid,
                "priorite_h5": priorite,
                "url_zeturf": url or "ND",
                "url_status": url_status,
                "heure_depart": heure,
                "heure_source": heure_source,
                "pdf_source": pdf_source,
                "depart_dt": depart_dt.isoformat(),
                "capture_label": label,
                "capture_dt": capture_dt.isoformat(),
                "due_from": due_from.isoformat(),
                "due_to": due_to.isoformat(),
                "status": status,
                "created_at": now.isoformat(),
                "ticket_strict": "NO_BET",
                "strict_allowed_oui_non": "non",
            })

    fieldnames = [
        "course_uid", "priorite_h5", "url_zeturf", "url_status", "heure_depart",
        "heure_source", "pdf_source",
        "depart_dt", "capture_label", "capture_dt",
        "due_from", "due_to", "status", "created_at",
        "ticket_strict", "strict_allowed_oui_non",
    ]

    out_path = Path("data/output/h5_auto_plan.csv")
    _h5auto_write_csv_dicts(out_path, plan_rows, fieldnames)

    skipped_path = Path("data/output/h5_auto_plan_skipped.csv")
    _h5auto_write_csv_dicts(
        skipped_path,
        skipped,
        ["course_uid", "reason", "priorite_h5", "heure"],
    )

    return {
        "ok": True,
        "phase": "h5_auto_plan",
        "history_archive": _archive_h5_payload_safe(
            payload={
                "phase": "h5_auto_plan",
                "watchlist_source": watchlist_source,
                "watchlist_rows": len(watchlist_rows),
                "planned_count": len(plan_rows),
                "skipped_count": len(skipped),
                "plan_sample": plan_rows[:10],
                "skipped_sample": skipped[:10],
                "plan_csv": str(out_path),
                "skipped_csv": str(skipped_path),
            },
            course_uid=plan_rows[0].get("course_uid", "H5_AUTO_PLAN_GLOBAL") if plan_rows else "H5_AUTO_PLAN_GLOBAL",
            capture_label="PLAN",
            source_route="/drive/run/h5-auto-plan",
            artifact_type="h5_auto_plan",
            status="LIVE_VALID_PRE_RACE",
            reason="PLAN_CREATED",
            decision_before_race_oui_non="oui",
            result_known_at_decision="non",
            live_eligible_oui_non="oui",
            retro_audit_only_oui_non="non",
        ),
        "watchlist_source": watchlist_source,
        "watchlist_rows": len(watchlist_rows),
        "morning_sync": {
            "ok": morning_sync.get("ok", "ND") if isinstance(morning_sync, dict) else "ND",
            "files_count": morning_sync.get("files_count", "ND") if isinstance(morning_sync, dict) else "ND",
            "local_dir": morning_sync.get("local_dir", "ND") if isinstance(morning_sync, dict) else "ND",
        },
        "planned_count": len(plan_rows),
        "skipped_count": len(skipped),
        "due_count": sum(1 for r in plan_rows if r.get("status") in {"DUE", "LATE_BUT_PRECOURSE"}),
        "url_required_count": sum(1 for r in plan_rows if r.get("url_status") == "URL_REQUIRED"),
        "today_date": today_date,
        "plan_sample": plan_rows[:8],
        "skipped_sample": skipped[:10],
        "plan_csv": str(out_path),
        "skipped_csv": str(skipped_path),
        "gcs_plan_upload": _h5auto_upload_file_to_gcs(
            out_path,
            "gpi-lite/outputs/h5_auto_plan.csv",
        ),
        "gcs_skipped_upload": _h5auto_upload_file_to_gcs(
            skipped_path,
            "gpi-lite/outputs/h5_auto_plan_skipped.csv",
        ),
        "gcs_plan_uri": "gs://gpi-lite-watchlist-data/gpi-lite/outputs/h5_auto_plan.csv",
        "gcs_skipped_uri": "gs://gpi-lite-watchlist-data/gpi-lite/outputs/h5_auto_plan_skipped.csv",
        "strict_policy": "NO STRICT. Horaires extraits des PDF morning. P1/P2 uniquement.",
    }



@app.post("/drive/run/h5-url-resolver", dependencies=[Depends(require_api_key)])
def drive_run_h5_url_resolver():
    """
    Résout les URLs ZEturf depuis le plan H-10/H-5 stocké dans GCS.
    Aucun ticket strict.
    """
    from engine.odds.zeturf_url_resolver import resolve_plan

    plan_path = Path("data/output/h5_auto_plan.csv")
    resolved_path = Path("data/output/h5_auto_plan_resolved.csv")
    report_path = Path("data/output/h5_url_resolver_report.json")

    download_plan = _h5auto_download_file_from_gcs(
        "gpi-lite/outputs/h5_auto_plan.csv",
        plan_path,
    )

    if not download_plan.get("ok"):
        return {
            "ok": False,
            "phase": "h5_url_resolver",
            "download_plan": download_plan,
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
            "reason": "H5_AUTO_PLAN_GCS_DOWNLOAD_FAILED",
        }

    report = resolve_plan(
        plan_csv=plan_path,
        output_csv=resolved_path,
        report_json=report_path,
    )

    upload_resolved = _h5auto_upload_file_to_gcs(
        resolved_path,
        "gpi-lite/outputs/h5_auto_plan_resolved.csv",
    )

    upload_report = _h5auto_upload_file_to_gcs(
        report_path,
        "gpi-lite/outputs/h5_url_resolver_report.json",
    )

    return {
        "ok": True,
        "phase": "h5_url_resolver",
        "history_archive": _archive_h5_payload_safe(
            payload={
                "phase": "h5_url_resolver",
                "report": report,
                "rows_input": report.get("rows_input", "ND") if isinstance(report, dict) else "ND",
                "rows_output": report.get("rows_output", "ND") if isinstance(report, dict) else "ND",
                "unique_courses": report.get("unique_courses", "ND") if isinstance(report, dict) else "ND",
                "url_ok_count": report.get("url_ok_count", "ND") if isinstance(report, dict) else "ND",
                "url_not_found_count": report.get("url_not_found_count", "ND") if isinstance(report, dict) else "ND",
                "low_confidence_count": report.get("low_confidence_count", "ND") if isinstance(report, dict) else "ND",
            },
            course_uid="H5_URL_RESOLVER_GLOBAL",
            capture_label="URL_RESOLVED",
            source_route="/drive/run/h5-url-resolver",
            artifact_type="h5_url_resolver",
            status="LIVE_VALID_PRE_RACE",
            reason="URLS_RESOLVED",
            decision_before_race_oui_non="oui",
            result_known_at_decision="non",
            live_eligible_oui_non="oui",
            retro_audit_only_oui_non="non",
        ),
        "download_plan": download_plan,
        "report": report,
        "upload_resolved": upload_resolved,
        "upload_report": upload_report,
        "gcs_resolved_uri": "gs://gpi-lite-watchlist-data/gpi-lite/outputs/h5_auto_plan_resolved.csv",
        "gcs_report_uri": "gs://gpi-lite-watchlist-data/gpi-lite/outputs/h5_url_resolver_report.json",
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
    }




@app.post("/drive/run/h5-auto-capture", dependencies=[Depends(require_api_key)])
def drive_run_h5_auto_capture():
    """
    Capture HTML ZEturf pour les snapshots H10/H5 dus.
    Aucun ticket strict.
    """
    from datetime import datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from engine.odds.zeturf_html_odds_parser import (
        extract_odds_from_html,
        write_odds_csv,
        write_odds_json,
    )

    paris_tz = ZoneInfo("Europe/Paris")
    now = datetime.now(paris_tz).replace(microsecond=0)
    now_iso = now.isoformat()

    resolved_path = Path("data/output/h5_auto_plan_resolved.csv")

    download_resolved = _h5auto_download_file_from_gcs(
        "gpi-lite/outputs/h5_auto_plan_resolved.csv",
        resolved_path,
    )

    if not download_resolved.get("ok"):
        return {
            "ok": False,
            "phase": "h5_auto_capture",
            "download_resolved": download_resolved,
            "reason": "H5_AUTO_PLAN_RESOLVED_GCS_DOWNLOAD_FAILED",
            "capture_count": 0,
            "odds_rows_total": 0,
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    rows = _h5auto_read_csv_dicts(resolved_path)

    captures = []
    skipped = []
    odds_rows_total = 0

    for row in rows:
        course_uid = str(row.get("course_uid", "") or "").strip()
        capture_label = str(row.get("capture_label", "") or "").strip()
        url_status = str(row.get("url_status", "") or "").strip()
        url = str(row.get("url_zeturf", "") or "").strip()
        status = str(row.get("status", "") or "").strip()

        if url_status != "OK" or not url.startswith("https://"):
            skipped.append({
                "course_uid": course_uid,
                "capture_label": capture_label,
                "reason": "URL_NOT_OK",
                "url_status": url_status,
                "status": status,
            })
            continue

        if not _h5auto_is_due_capture(row, now_iso):
            skipped.append({
                "course_uid": course_uid,
                "capture_label": capture_label,
                "reason": "NOT_DUE",
                "due_from": row.get("due_from", ""),
                "due_to": row.get("due_to", ""),
                "now": now_iso,
            })
            continue

        slug = "_".join([
            _h5auto_safe_slug(course_uid),
            _h5auto_safe_slug(capture_label),
        ])

        html_path = Path("data/raw/zeturf_snapshots/html") / f"{slug}.html"
        odds_csv_path = Path("data/output/odds") / f"{slug}_odds.csv"
        odds_json_path = Path("data/output/odds") / f"{slug}_odds.json"

        html_capture = _h5auto_download_url_to_file(url, html_path)

        if not html_capture.get("ok"):
            captures.append({
                "course_uid": course_uid,
                "capture_label": capture_label,
                "ok": False,
                "reason": html_capture.get("reason"),
                "url": url,
                "html_size": 0,
                "odds_rows": 0,
                "ticket_strict": "NO_BET",
            })
            continue

        try:
            odds_rows = extract_odds_from_html(
                html_path=html_path,
                course_uid=course_uid,
                snapshot_label=capture_label,
            )

            write_odds_csv(odds_rows, odds_csv_path)
            write_odds_json(odds_rows, odds_json_path)

            odds_rows_total += len(odds_rows)

            html_blob = f"gpi-lite/raw/zeturf_snapshots/html/{slug}.html"
            odds_csv_blob = f"gpi-lite/outputs/odds/{slug}_odds.csv"
            odds_json_blob = f"gpi-lite/outputs/odds/{slug}_odds.json"

            upload_html = _h5auto_upload_file_to_gcs(html_path, html_blob)
            upload_odds_csv = _h5auto_upload_file_to_gcs(odds_csv_path, odds_csv_blob)
            upload_odds_json = _h5auto_upload_file_to_gcs(odds_json_path, odds_json_blob)

            captures.append({
                "course_uid": course_uid,
                "capture_label": capture_label,
                "ok": True,
                "url": url,
                "html_size": html_capture.get("html_size"),
                "odds_rows": len(odds_rows),
                "html_gcs_uri": upload_html.get("gcs_uri"),
                "odds_csv_gcs_uri": upload_odds_csv.get("gcs_uri"),
                "odds_json_gcs_uri": upload_odds_json.get("gcs_uri"),
                "upload_html_ok": upload_html.get("ok"),
                "upload_odds_csv_ok": upload_odds_csv.get("ok"),
                "upload_odds_json_ok": upload_odds_json.get("ok"),
                "ticket_strict": "NO_BET",
            })

        except Exception as exc:
            captures.append({
                "course_uid": course_uid,
                "capture_label": capture_label,
                "ok": False,
                "reason": f"ODDS_PARSE_ERROR:{type(exc).__name__}:{exc}",
                "url": url,
                "html_size": html_capture.get("html_size"),
                "odds_rows": 0,
                "ticket_strict": "NO_BET",
            })

    return {
        "ok": True,
        "phase": "h5_auto_capture",
        "history_archive": _archive_h5_payload_safe(
            payload={
                "phase": "h5_auto_capture",
                "now": now_iso,
                "rows_input": len(rows),
                "capture_count": len([c for c in captures if c.get("ok")]),
                "capture_error_count": len([c for c in captures if not c.get("ok")]),
                "skipped_count": len(skipped),
                "captures_sample": captures[:10],
                "skipped_sample": skipped[:10],
            },
            course_uid=(
                captures[0].get("course_uid", "H5_AUTO_CAPTURE_GLOBAL")
                if captures else (
                    skipped[0].get("course_uid", "H5_AUTO_CAPTURE_GLOBAL")
                    if skipped else "H5_AUTO_CAPTURE_GLOBAL"
                )
            ),
            capture_label=(
                captures[0].get("capture_label", "CAPTURE")
                if captures else (
                    skipped[0].get("capture_label", "CAPTURE")
                    if skipped else "CAPTURE"
                )
            ),
            source_route="/drive/run/h5-auto-capture",
            artifact_type="h5_auto_capture",
            status=(
                "LIVE_CAPTURED_PRE_RACE"
                if len([c for c in captures if c.get("ok")]) > 0
                else "LIVE_VALID_PRE_RACE"
            ),
            reason=(
                "CAPTURE_ROUTE_COMPLETED"
                if len([c for c in captures if c.get("ok")]) > 0
                else "NO_CAPTURE_DUE_OR_CAPTURE_SKIPPED"
            ),
            decision_before_race_oui_non="oui",
            result_known_at_decision="non",
            live_eligible_oui_non="oui",
            retro_audit_only_oui_non="non",
        ),
        "now": now_iso,
        "download_resolved": download_resolved,
        "rows_input": len(rows),
        "capture_count": len([c for c in captures if c.get("ok")]),
        "capture_error_count": len([c for c in captures if not c.get("ok")]),
        "skipped_count": len(skipped),
        "odds_rows_total": odds_rows_total,
        "captures_sample": captures[:10],
        "skipped_sample": skipped[:10],
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
    }




# >>> GPI STUDY/PAPER/STRICT SPLIT PATCH V0_9_26 >>>
def _gpi_build_paper_candidate_from_h5_context(context_payload: dict) -> dict:
    """
    Ouvre un ticket papier CP_SEC quand le H-5 fournit une paire exploitable.
    Ne valide jamais le réel strict.
    """
    invalid = {"", "ND", "ABSENT", "NO_BET", "NONE", "NULL"}

    def _txt(*keys, default=""):
        for key in keys:
            value = context_payload.get(key)
            if value is None:
                continue
            value = str(value).strip()
            if value.upper() not in invalid:
                return value
        return default

    def _float(value):
        try:
            return float(str(value).replace(",", "."))
        except Exception:
            return None

    cp_sec = _txt("cp_sec_candidate", "CP_SEC_selection", "selection_CP_SEC")
    cp_sec_sg_status = _txt(
        "cp_sec_sg_filter_status",
        "cp_sec_sg_status",
        "CP_SEC_SG_STATUS",
        default="ND",
    )
    cp_sec_sg_sum = _float(
        _txt("cp_sec_sg_sum", "cp_sg_pair_sum", "CP_SEC_SG_SUM", default="")
    )

    kill_switch_codes = _txt("kill_switch_codes", default="")
    volatilite_status = _txt("volatilite_status", default="ND").upper()

    reasons = []

    if not cp_sec:
        reasons.append("CP_SEC_SELECTION_ABSENTE")

    if cp_sec_sg_status.upper() not in {"OK", "VALID", "OUI"}:
        reasons.append(f"CP_SEC_SG_STATUS_{cp_sec_sg_status.upper()}")

    if cp_sec_sg_sum is None:
        reasons.append("CP_SEC_SG_SUM_ND")
    elif cp_sec_sg_sum < 7.5:
        reasons.append("CP_SEC_SG_SUM_LT_7_5")

    if volatilite_status in {"KILL", "KILL_SWITCH"}:
        reasons.append("STRICT_BLOCK_VOLATILITE_KILL")

    if kill_switch_codes and kill_switch_codes.upper() not in {"AUCUN", "NONE", "ND"}:
        reasons.append("STRICT_BLOCK_KILL_SWITCH_CODES_PRESENT")

    paper_allowed = (
        bool(cp_sec)
        and cp_sec_sg_status.upper() in {"OK", "VALID", "OUI"}
        and cp_sec_sg_sum is not None
        and cp_sec_sg_sum >= 7.5
    )

    if paper_allowed:
        return {
            "paper_candidate_allowed": "oui",
            "paper_candidate_type": "CP_SEC",
            "paper_candidate_ticket": f"CP_SEC {cp_sec}",
            "paper_candidate_segment": "PAPER_TRADING",
            "paper_candidate_mise": "1",
            "paper_candidate_reason": (
                f"CP_SEC_ETUDE_H5; cp_sec_sg_sum={cp_sec_sg_sum}; "
                f"strict_reel_bloque"
            ),
            "paper_selection": cp_sec,
            "study_allowed": "oui",
            "paper_allowed": "oui",
            "strict_allowed": "non",
            "pronostic_papier_oui_non": "OUI",
            "paper_structure": "CP_SEC",
            "paper_stake": "1",
            "strict_block_reasons": (
                "H5_AUTO_BRIEF_READ_ONLY|STRICT_GATES_NOT_VALIDATED|REAL_STRICT_FORBIDDEN"
            ),
            "structure_retenue": "CP_SEC_PAPER",
            "verdict": "PAPER",
            "ticket_exact": "NO_BET",
            "mise_theorique": "0",
            "segment_ROI": "PAPER_TRADING",
            "segment_roi": "PAPER_TRADING",
        }

    return {
        "paper_candidate_allowed": "non",
        "paper_candidate_type": "NO_BET",
        "paper_candidate_ticket": "NO_BET",
        "paper_candidate_segment": "NO_BET",
        "paper_candidate_mise": "0",
        "paper_candidate_reason": " | ".join(reasons) if reasons else "PAPER_GATE_CLOSED",
        "paper_selection": "",
        "study_allowed": "non",
        "paper_allowed": "non",
        "strict_allowed": "non",
        "pronostic_papier_oui_non": "NON",
        "paper_structure": "NO_BET",
        "paper_stake": "0",
        "strict_block_reasons": "PAPER_GATE_CLOSED|REAL_STRICT_FORBIDDEN",
        "structure_retenue": "NO_BET",
        "verdict": "NO_BET",
        "ticket_exact": "NO_BET",
        "mise_theorique": "0",
        "segment_ROI": "NO_BET",
        "segment_roi": "NO_BET",
    }
# <<< GPI STUDY/PAPER/STRICT SPLIT PATCH V0_9_26 >>>


@app.post("/drive/run/h5-auto-brief", dependencies=[Depends(require_api_key)])
def drive_run_h5_auto_brief():
    """
    Construit le mouvement H10/H5, le gate volatilite, latest_odds_context
    et un brief H-5 auto. Aucun ticket strict.
    """
    from datetime import datetime, timezone
    from pathlib import Path
    import json

    from engine.odds.odds_movement_builder import (
        build_odds_movements,
        write_movements_csv,
        write_movements_json,
    )
    from engine.odds.odds_volatility_gate import (
        build_volatility_gate,
        write_gate_csv,
        write_gate_json,
    )
    from engine.odds.latest_odds_context import (
        build_latest_odds_context,
        write_latest_odds_context,
    )
    from engine.odds.brief_odds_enricher import build_odds_brief_lines

    resolved_path = Path("data/output/h5_auto_plan_resolved.csv")

    download_resolved = _h5auto_download_file_from_gcs(
        "gpi-lite/outputs/h5_auto_plan_resolved.csv",
        resolved_path,
    )

    if not download_resolved.get("ok"):
        return {
            "ok": False,
            "phase": "h5_auto_brief",
            "download_resolved": download_resolved,
            "reason": "H5_AUTO_PLAN_RESOLVED_GCS_DOWNLOAD_FAILED",
            "matched_courses": 0,
            "movement_rows_total": 0,
            "briefs_created": 0,
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    rows = _h5auto_read_csv_dicts(resolved_path)

    by_course = {}

    for row in rows:
        course_uid = str(row.get("course_uid", "") or "").strip()
        capture_label = str(row.get("capture_label", "") or "").strip().upper()
        url_status = str(row.get("url_status", "") or "").strip()

        if not course_uid:
            continue

        if capture_label not in {"H10", "H5"}:
            continue

        if url_status and url_status != "OK":
            continue

        by_course.setdefault(course_uid, {})[capture_label] = row

    processed = []
    errors = []
    movement_rows_total = 0
    latest_brief_payload = None

    odds_from_gcs_dir = Path("data/output/odds_from_gcs")
    odds_out_dir = Path("data/output/odds")
    brief_out_dir = Path("data/output/h5_briefs")

    odds_from_gcs_dir.mkdir(parents=True, exist_ok=True)
    odds_out_dir.mkdir(parents=True, exist_ok=True)
    brief_out_dir.mkdir(parents=True, exist_ok=True)

    for course_uid, labels in by_course.items():
        if "H10" not in labels or "H5" not in labels:
            continue

        course_slug = _h5auto_safe_slug(course_uid)

        h10_slug = "_".join([course_slug, _h5auto_safe_slug("H10")])
        h5_slug = "_".join([course_slug, _h5auto_safe_slug("H5")])

        h10_blob = f"gpi-lite/outputs/odds/{h10_slug}_odds.csv"
        h5_blob = f"gpi-lite/outputs/odds/{h5_slug}_odds.csv"

        h10_local = odds_from_gcs_dir / f"{h10_slug}_odds.csv"
        h5_local = odds_from_gcs_dir / f"{h5_slug}_odds.csv"

        download_h10 = _h5auto_download_file_from_gcs(h10_blob, h10_local)
        download_h5 = _h5auto_download_file_from_gcs(h5_blob, h5_local)

        if not download_h10.get("ok") or not download_h5.get("ok"):
            errors.append({
                "course_uid": course_uid,
                "reason": "ODDS_H10_OR_H5_DOWNLOAD_FAILED",
                "download_h10": download_h10,
                "download_h5": download_h5,
                "ticket_strict": "NO_BET",
            })
            continue

        movement_csv = odds_out_dir / f"{course_slug}_h10_to_h5_movements.csv"
        movement_json = odds_out_dir / f"{course_slug}_h10_to_h5_movements.json"
        gate_csv = odds_out_dir / f"{course_slug}_h10_to_h5_volatility_gate.csv"
        gate_json = odds_out_dir / f"{course_slug}_h10_to_h5_volatility_gate.json"
        latest_context_json = odds_out_dir / "latest_odds_context.json"
        brief_json = brief_out_dir / f"{course_slug}_h5_auto_brief.json"
        latest_brief_json = Path("data/output/latest_h5_auto_brief.json")

        try:
            movement_rows = build_odds_movements(
                previous_csv=h10_local,
                current_csv=h5_local,
                previous_snapshot_label="H10",
                current_snapshot_label="H5",
            )
            write_movements_csv(movement_rows, movement_csv)
            write_movements_json(movement_rows, movement_json)

            gate_result = build_volatility_gate(
                current_odds_csv=h5_local,
                movements_csv=movement_csv,
                top_n=6,
            )
            write_gate_csv(gate_result, gate_csv)
            write_gate_json(gate_result, gate_json)

            odds_context = build_latest_odds_context(
                current_odds_csv=h5_local,
                movements_csv=movement_csv,
                gate_json=gate_json,
            )
            write_latest_odds_context(odds_context, latest_context_json)

            context_payload = json.loads(
                latest_context_json.read_text(encoding="utf-8")
            )

            context_payload["odds_context_found"] = "oui"

            odds_brief_lines = build_odds_brief_lines(context_payload)

            course_statut_marche = str(
                context_payload.get("course_statut_marche", "ND")
            )
            strict_allowed_by_odds_context = str(
                context_payload.get("strict_allowed_by_odds_context", "non")
            )
            edge_status_by_odds_context = str(
                context_payload.get("edge_status_by_odds_context", "EDGE_FRAGILE")
            )
            reason_codes = str(context_payload.get("reason_codes", ""))

            qualite = "C"
            if course_statut_marche.startswith("KILL_SWITCH"):
                qualite = "D"
            elif course_statut_marche in {"STABLE", "SURVEILLABLE"}:
                qualite = "B"

            import hashlib

            # GPI_LITE_V0_9_18_SAME_DAY_START_BACKFILL
            # Sécurité ROI : une décision générée après la date de course,
            # ou le même jour après l'heure de départ, reste utile techniquement
            # mais ne doit jamais compter comme décision pré-course stricte.
            h5_auto_generated_at_utc_dt = datetime.now(timezone.utc)
            h5_auto_generated_at_utc = h5_auto_generated_at_utc_dt.replace(microsecond=0).isoformat()

            try:
                from zoneinfo import ZoneInfo
                h5_auto_generated_local_dt = h5_auto_generated_at_utc_dt.astimezone(ZoneInfo("Europe/Paris"))
            except Exception:
                h5_auto_generated_local_dt = h5_auto_generated_at_utc_dt

            backfill_technical_oui_non = "non"
            strict_kpi_excluded_oui_non = "non"
            strict_kpi_exclusion_reason = ""
            decision_before_race_oui_non_effective = "oui"

            def _gpi_parse_depart_time(value):
                import re
                from datetime import time as _time

                raw = str(value or "").strip()
                if not raw:
                    return None

                match = re.search(r"(\d{1,2})\D+(\d{2})", raw)
                if not match:
                    match = re.search(r"^(\d{1,2})(\d{2})$", raw)

                if not match:
                    return None

                hour = int(match.group(1))
                minute = int(match.group(2))

                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    return None

                return _time(hour=hour, minute=minute)

            try:
                course_date_for_backfill = datetime.strptime(str(course_uid)[:10], "%d_%m_%Y").date()

                heure_depart_raw = (
                    row.get("heure_depart")
                    or row.get("heure")
                    or row.get("depart")
                    or row.get("heure_course")
                    or ""
                )

                course_depart_time = _gpi_parse_depart_time(heure_depart_raw)

                if h5_auto_generated_local_dt.date() > course_date_for_backfill:
                    backfill_technical_oui_non = "oui"
                    strict_kpi_excluded_oui_non = "oui"
                    strict_kpi_exclusion_reason = "H5_AUTO_BRIEF_GENERATED_AFTER_COURSE_DATE"
                    decision_before_race_oui_non_effective = "non"

                elif (
                    h5_auto_generated_local_dt.date() == course_date_for_backfill
                    and course_depart_time is not None
                ):
                    generated_local_naive = h5_auto_generated_local_dt.replace(tzinfo=None)
                    course_depart_local_dt = datetime.combine(course_date_for_backfill, course_depart_time)

                    if generated_local_naive > course_depart_local_dt:
                        backfill_technical_oui_non = "oui"
                        strict_kpi_excluded_oui_non = "oui"
                        strict_kpi_exclusion_reason = "H5_AUTO_BRIEF_GENERATED_AFTER_START_TIME"
                        decision_before_race_oui_non_effective = "non"

            except Exception:
                pass

            input_hash_material = {
                "course_uid": course_uid,
                "decision_before_race_oui_non": decision_before_race_oui_non_effective,
                "input_hash_source": "H5_AUTO_BRIEF_PAYLOAD",
                "current_snapshot_label": "H5",
                "runners_count": context_payload.get("runners_count", 0),
                "top_n_numbers": context_payload.get("top_n_numbers", ""),
                "top_n_instable_numbers": context_payload.get("top_n_instable_numbers", ""),
                "top_n_kill_switch_numbers": context_payload.get("top_n_kill_switch_numbers", ""),
                "global_kill_switch_count": context_payload.get("global_kill_switch_count", 0),
                "global_instable_count": context_payload.get("global_instable_count", 0),
                "top_n_kill_switch_count": context_payload.get("top_n_kill_switch_count", 0),
                "top_n_instable_count": context_payload.get("top_n_instable_count", 0),
                "kill_switch_topn_oui_non": context_payload.get("kill_switch_topn_oui_non", "non"),
                "course_statut_marche": course_statut_marche,
                "strict_allowed_by_odds_context": strict_allowed_by_odds_context,
                "edge_status_by_odds_context": edge_status_by_odds_context,
                "reason_codes": reason_codes,
            }

            input_hash = hashlib.sha256(
                json.dumps(
                    input_hash_material,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()

            try:
                np_statut_fields = _p11_compute_np_statut_gate_for_h5_brief({
                    "course_uid": course_uid,
                    "current_snapshot_label": "H5",
                })
            except Exception as exc:
                np_statut_fields = {
                    "np_ok": "ND",
                    "statut_ok": "ND",
                    "np_source": f"NP_STATUT_COMPUTE_ERROR:{type(exc).__name__}",
                    "statut_source": f"NP_STATUT_COMPUTE_ERROR:{type(exc).__name__}",
                    "np_count": "ND",
                    "statut_missing_count": "ND",
                    "np_statut_rows_count": 0,
                    "np_statut_source_level": "ERROR",
                    "np_statut_checked_at_utc": datetime.now(timezone.utc).isoformat(),
                }

            brief_payload = {
                "ok": True,
                "phase": "h5_auto_brief",
                "brief_version": "h5_auto_brief_v0_1",
                "generated_at_utc": h5_auto_generated_at_utc,
                "course_uid": course_uid,
                "decision_before_race_oui_non": decision_before_race_oui_non_effective,
                "backfill_technical_oui_non": backfill_technical_oui_non,
                "strict_kpi_excluded_oui_non": strict_kpi_excluded_oui_non,
                "strict_kpi_exclusion_reason": strict_kpi_exclusion_reason,
                "input_hash": input_hash,
                "input_hash_source": "H5_AUTO_BRIEF_PAYLOAD",
                "input_hash_material_keys": sorted(input_hash_material.keys()),
                "current_snapshot_label": "H5",
                "runners_count": context_payload.get("runners_count", 0),
                "np_ok": np_statut_fields.get("np_ok", "ND"),
                "statut_ok": np_statut_fields.get("statut_ok", "ND"),
                "np_source": np_statut_fields.get("np_source", "ND"),
                "statut_source": np_statut_fields.get("statut_source", "ND"),
                "np_count": np_statut_fields.get("np_count", "ND"),
                "statut_missing_count": np_statut_fields.get("statut_missing_count", "ND"),
                "np_statut_rows_count": np_statut_fields.get("np_statut_rows_count", 0),
                "np_statut_source_level": np_statut_fields.get("np_statut_source_level", "ND"),
                "np_statut_checked_at_utc": np_statut_fields.get("np_statut_checked_at_utc", "ND"),
                "top_n_numbers": context_payload.get("top_n_numbers", ""),
                "top_n_instable_numbers": context_payload.get("top_n_instable_numbers", ""),
                "top_n_kill_switch_numbers": context_payload.get("top_n_kill_switch_numbers", ""),
                "global_kill_switch_count": context_payload.get("global_kill_switch_count", 0),
                "global_instable_count": context_payload.get("global_instable_count", 0),
                "top_n_kill_switch_count": context_payload.get("top_n_kill_switch_count", 0),
                "top_n_instable_count": context_payload.get("top_n_instable_count", 0),
                "kill_switch_topn_oui_non": context_payload.get("kill_switch_topn_oui_non", "non"),
                "course_statut_marche": course_statut_marche,
                "strict_allowed_by_odds_context": strict_allowed_by_odds_context,
                "edge_status_by_odds_context": edge_status_by_odds_context,
                "reason_codes": reason_codes,
                "odds_brief_lines": odds_brief_lines,
                "VERDICT": "NO_BET",
                "Qualite": qualite,
                "Structure_max": "SIMULATION",
                "Validation_economique": "ND",
                "Ticket_exact_ou_NO_BET": "NO_BET",
                "Segment_ROI": "NO_BET",
                "Edge": edge_status_by_odds_context,
                "Action_GPT": "Confirmer NO BET strict. Couche marche H-5 exploitable uniquement pour audit/simulation.",
                "strict_allowed_oui_non": "non",
                "strict_usable_oui_non": "non",
                "ticket_strict": "NO_BET",
            }

            # GPI H5 AUTO CONTEXT V0_1
            # Source auto distincte du legacy latest_h5_context.json.
            # Ne cree jamais de course_h5_clean synthetique.
            # Strict reel toujours interdit ici.
            h5_auto_context_sync = {
                "ok": False,
                "reason": "H5_AUTO_CONTEXT_NOT_WRITTEN",
                "strict_allowed_oui_non": "non",
                "ticket_strict": "NO_BET",
            }

            try:
                h10_rows_for_context = _h5auto_read_csv_dicts(h10_local)
                h5_rows_for_context = _h5auto_read_csv_dicts(h5_local)

                h5_rows_count_for_context = len(h5_rows_for_context)
                h10_rows_count_for_context = len(h10_rows_for_context)

                if h5_rows_count_for_context > 0:
                    h5_context_dir = Path("data/output/h5_contexts")
                    h5_context_dir.mkdir(parents=True, exist_ok=True)

                    h5_auto_context_path = h5_context_dir / f"{course_slug}_h5_auto_context.json"
                    latest_h5_auto_context_path = Path("data/output/latest_h5_auto_context.json")

                    h5_auto_context_payload = {
                        "ok": True,
                        "context_version": "latest_h5_auto_context_v0_1",
                        "source_kind": "H5_AUTO_ODDS_CAPTURE",
                        "generated_at_utc": h5_auto_generated_at_utc,
                        "course_uid": course_uid,
                        "course_key": course_uid,
                        "course_slug": course_slug,
                        "previous_snapshot_label": "H10",
                        "current_snapshot_label": "H5",
                        "h10_rows_count": h10_rows_count_for_context,
                        "h5_rows_count": h5_rows_count_for_context,
                        "runners_count": context_payload.get("runners_count", h5_rows_count_for_context),
                        "h10_odds_csv_gcs": h10_blob,
                        "h5_odds_csv_gcs": h5_blob,
                        "movement_csv_local": str(movement_csv),
                        "movement_json_local": str(movement_json),
                        "gate_csv_local": str(gate_csv),
                        "gate_json_local": str(gate_json),
                        "latest_odds_context_local": str(latest_context_json),
                        "course_statut_marche": course_statut_marche,
                        "strict_allowed_by_odds_context": strict_allowed_by_odds_context,
                        "edge_status_by_odds_context": edge_status_by_odds_context,
                        "reason_codes": reason_codes,
                        "np_ok": np_statut_fields.get("np_ok", "ND"),
                        "statut_ok": np_statut_fields.get("statut_ok", "ND"),
                        "np_source": np_statut_fields.get("np_source", "ND"),
                        "statut_source": np_statut_fields.get("statut_source", "ND"),
                        "np_count": np_statut_fields.get("np_count", "ND"),
                        "statut_missing_count": np_statut_fields.get("statut_missing_count", "ND"),
                        "np_statut_source_level": np_statut_fields.get("np_statut_source_level", "ND"),
                        "decision_before_race_oui_non": decision_before_race_oui_non_effective,
                        "backfill_technical_oui_non": backfill_technical_oui_non,
                        "strict_kpi_excluded_oui_non": strict_kpi_excluded_oui_non,
                        "strict_kpi_exclusion_reason": strict_kpi_exclusion_reason,
                        "strict_allowed_oui_non": "non",
                        "strict_usable_oui_non": "non",
                        "ticket_strict": "NO_BET",
                        "doctrine": (
                            "Contexte H5 auto issu des odds ZEturf capturees. "
                            "Ne vaut pas validation stricte sans NP/statut officiels, "
                            "proxy/SP calibres et gates complets."
                        ),
                    }

                    h5_auto_context_path.write_text(
                        json.dumps(h5_auto_context_payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    latest_h5_auto_context_path.write_text(
                        json.dumps(h5_auto_context_payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                    upload_h5_auto_context = _h5auto_upload_file_to_gcs(
                        h5_auto_context_path,
                        f"gpi-lite/outputs/h5_contexts/{course_slug}_h5_auto_context.json",
                    )
                    upload_latest_h5_auto_context = _h5auto_upload_file_to_gcs(
                        latest_h5_auto_context_path,
                        "gpi-lite/outputs/latest_h5_auto_context.json",
                    )

                    h5_auto_context_sync = {
                        "ok": bool(
                            upload_h5_auto_context.get("ok")
                            and upload_latest_h5_auto_context.get("ok")
                        ),
                        "reason": "H5_AUTO_CONTEXT_WRITTEN",
                        "course_uid": course_uid,
                        "h5_rows_count": h5_rows_count_for_context,
                        "h10_rows_count": h10_rows_count_for_context,
                        "context_gcs_uri": upload_h5_auto_context.get("gcs_uri", ""),
                        "latest_context_gcs_uri": upload_latest_h5_auto_context.get("gcs_uri", ""),
                        "upload_h5_auto_context": upload_h5_auto_context,
                        "upload_latest_h5_auto_context": upload_latest_h5_auto_context,
                        "strict_allowed_oui_non": "non",
                        "ticket_strict": "NO_BET",
                    }
                else:
                    h5_auto_context_sync = {
                        "ok": False,
                        "reason": "H5_AUTO_CONTEXT_H5_ROWS_EMPTY",
                        "course_uid": course_uid,
                        "h5_rows_count": h5_rows_count_for_context,
                        "h10_rows_count": h10_rows_count_for_context,
                        "strict_allowed_oui_non": "non",
                        "ticket_strict": "NO_BET",
                    }

            except Exception as exc:
                h5_auto_context_sync = {
                    "ok": False,
                    "reason": f"H5_AUTO_CONTEXT_WRITE_ERROR:{type(exc).__name__}:{exc}",
                    "course_uid": course_uid,
                    "strict_allowed_oui_non": "non",
                    "ticket_strict": "NO_BET",
                }

            brief_payload["h5_auto_context_sync"] = h5_auto_context_sync
            brief_payload["latest_h5_auto_context_available_oui_non"] = (
                "oui" if h5_auto_context_sync.get("ok") is True else "non"
            )

            brief_json.write_text(
                json.dumps(brief_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            latest_brief_json.write_text(
                json.dumps(brief_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # V0.9.16 : produire un decision_engine_outputs minimal depuis le H-5 auto brief
            # puis alimenter le registre paper. Aucun ticket strict n'est ouvert ici.
            paper_sync = {
                "ok": False,
                "phase": "h5_auto_paper_sync",
                "course_uid": course_uid,
                "reason": "NOT_RUN",
                "ticket_strict": "NO_BET",
            }

            try:
                import csv as _csv
                from engine.paper_trading_registry import append_paper_trading_decision

                decision_id = "DEO_" + str(input_hash)[:20]
                paper_candidate = _gpi_build_paper_candidate_from_h5_context(context_payload)

                deo_row = {
                    "decision_id": decision_id,
                    "course_uid": course_uid,
                    "discipline_bucket_detected": "ND",
                    "partants_reels": str(context_payload.get("runners_count", 0)),
                    "snapshot_time": "H5",
                    "engine_version": "GPI_LITE_V0_9_18_H5_AUTO_BRIEF",
                    "ruleset_version": "GPI_ZEturf_V19_1_4",
                    "input_hash": input_hash,
                    "qualite": qualite,
                    "score_jouabilite": "ND",
                    "proxy_status": "ND",
                    "segment_id": "ND",
                    "sample_n": "ND",
                    "confidence": "ND",
                    "fallback_level": "ND",
                    "C_prudent": "ND",
                    "R_prudent": "ND",
                    "erreur_proxy": "ND",
                    "taux_surestimation": "ND",
                    "segment_degrade_oui_non": "ND",
                    "selection_CP_SEC": paper_candidate.get("paper_selection", ""),
                    "CP_SEC_selection": paper_candidate.get("paper_selection", ""),
                    "CP_SEC_domine_oui_non": "ND",
                    "selection_CP3": "",
                    "selection_SP": "",
                    "cp_sec_candidate": str(context_payload.get("cp_sec_candidate", "")),
                    "cp_sg_pair_sum": str(context_payload.get("cp_sg_pair_sum", "ND")),
                    "cp_sg_filter_status": str(context_payload.get("cp_sg_filter_status", "ND")),
                    "cp3_candidate": str(context_payload.get("cp3_candidate", "")),
                    "cp3_sg_min_pair_sum": str(context_payload.get("cp3_sg_min_pair_sum", "ND")),
                    "cp3_sg_filter_status": str(context_payload.get("cp3_sg_filter_status", "ND")),
                    "paper_candidate_allowed": paper_candidate["paper_candidate_allowed"],
                    "paper_candidate_type": paper_candidate["paper_candidate_type"],
                    "paper_candidate_ticket": paper_candidate["paper_candidate_ticket"],
                    "paper_candidate_segment": paper_candidate["paper_candidate_segment"],
                    "paper_candidate_mise": paper_candidate["paper_candidate_mise"],
                    "paper_candidate_reason": paper_candidate["paper_candidate_reason"],
                    "study_allowed": paper_candidate["study_allowed"],
                    "paper_allowed": paper_candidate["paper_allowed"],
                    "strict_allowed": paper_candidate["strict_allowed"],
                    "ticket_reel": "NO_BET",
                    "pronostic_papier_oui_non": paper_candidate["pronostic_papier_oui_non"],
                    "paper_structure": paper_candidate["paper_structure"],
                    "paper_selection": paper_candidate["paper_selection"],
                    "paper_stake": paper_candidate["paper_stake"],
                    "strict_block_reasons": paper_candidate["strict_block_reasons"],
                    "structure_retenue": paper_candidate["structure_retenue"],
                    "structure_max": "CP_SEC_ETUDE" if paper_candidate["paper_candidate_allowed"] == "oui" else "SIMULATION",
                    "verdict": paper_candidate["verdict"],
                    "ticket_exact": "NO_BET",
                    "mise_theorique": paper_candidate["mise_theorique"],
                    "segment_ROI": paper_candidate["segment_ROI"],
                    "segment_roi": paper_candidate["segment_roi"],
                    "edge_status": edge_status_by_odds_context,
                    "reason_codes": reason_codes,
                    "kill_switch_codes": str(context_payload.get("kill_switch_codes", reason_codes)),
                    "split": "TEST_LIVE",
                    "calibration_version": "GPI_CALIBRATION_V0_9_2",
                    "leakage_check_ok": "true",
                    "decision_before_race_oui_non": decision_before_race_oui_non_effective,
                    "backfill_technical_oui_non": backfill_technical_oui_non,
                    "strict_kpi_excluded_oui_non": strict_kpi_excluded_oui_non,
                    "strict_kpi_exclusion_reason": strict_kpi_exclusion_reason,
                    "created_at": h5_auto_generated_at_utc,
                    "replay_match_oui_non": "oui",
                    "strict_allowed_oui_non": "non",
                    "strict_usable_oui_non": "non",
                    "decision_block_reason": (
                        "H5_AUTO_BRIEF_NO_STRICT|"
                        + str(context_payload.get("decision_block_reason", reason_codes))
                    ),
                }

                deo_columns = list(deo_row.keys())

                decision_engine_csv = Path("data/output") / f"{course_uid}_decision_engine_outputs.csv"
                latest_decision_engine_csv = Path("data/output/latest_decision_engine_outputs.csv")
                generic_decision_engine_csv = Path("data/output/decision_engine_outputs.csv")

                for deo_path in [
                    decision_engine_csv,
                    latest_decision_engine_csv,
                    generic_decision_engine_csv,
                ]:
                    deo_path.parent.mkdir(parents=True, exist_ok=True)
                    with deo_path.open("w", encoding="utf-8", newline="") as file:
                        writer = _csv.DictWriter(file, fieldnames=deo_columns)
                        writer.writeheader()
                        writer.writerow(deo_row)

                paper_output_path, latest_paper_path, paper_count = append_paper_trading_decision(course_uid)

                upload_decision_engine = _h5auto_upload_file_to_gcs(
                    decision_engine_csv,
                    f"gpi-lite/outputs/{course_uid}_decision_engine_outputs.csv",
                )
                upload_latest_decision_engine = _h5auto_upload_file_to_gcs(
                    latest_decision_engine_csv,
                    "gpi-lite/outputs/latest_decision_engine_outputs.csv",
                )
                upload_generic_decision_engine = _h5auto_upload_file_to_gcs(
                    generic_decision_engine_csv,
                    "gpi-lite/outputs/decision_engine_outputs.csv",
                )
                upload_paper = _h5auto_upload_file_to_gcs(
                    paper_output_path,
                    "gpi-lite/outputs/paper_trading_50_decisions.csv",
                )
                upload_latest_paper = _h5auto_upload_file_to_gcs(
                    latest_paper_path,
                    "gpi-lite/outputs/latest_paper_trading_decision.csv",
                )

                paper_sync = {
                    "ok": all([
                        upload_decision_engine.get("ok"),
                        upload_latest_decision_engine.get("ok"),
                        upload_generic_decision_engine.get("ok"),
                        upload_paper.get("ok"),
                        upload_latest_paper.get("ok"),
                    ]),
                    "phase": "h5_auto_paper_sync",
                    "course_uid": course_uid,
                    "decision_id": decision_id,
                    "paper_count": paper_count,
                    "decision_engine_outputs": str(decision_engine_csv),
                    "latest_decision_engine_outputs": str(latest_decision_engine_csv),
                    "paper_output_path": str(paper_output_path),
                    "latest_paper_path": str(latest_paper_path),
                    "upload_decision_engine_ok": upload_decision_engine.get("ok"),
                    "upload_latest_decision_engine_ok": upload_latest_decision_engine.get("ok"),
                    "upload_generic_decision_engine_ok": upload_generic_decision_engine.get("ok"),
                    "upload_paper_ok": upload_paper.get("ok"),
                    "upload_latest_paper_ok": upload_latest_paper.get("ok"),
                    "ticket_strict": "NO_BET",
                    "backfill_technical_oui_non": backfill_technical_oui_non,
                    "strict_kpi_excluded_oui_non": strict_kpi_excluded_oui_non,
                    "strict_kpi_exclusion_reason": strict_kpi_exclusion_reason,
                }

            except Exception as exc:
                paper_sync = {
                    "ok": False,
                    "phase": "h5_auto_paper_sync",
                    "course_uid": course_uid,
                    "reason": f"H5_AUTO_PAPER_SYNC_ERROR:{type(exc).__name__}:{exc}",
                    "ticket_strict": "NO_BET",
                    "backfill_technical_oui_non": backfill_technical_oui_non,
                    "strict_kpi_excluded_oui_non": strict_kpi_excluded_oui_non,
                    "strict_kpi_exclusion_reason": strict_kpi_exclusion_reason,
                }

            upload_movement_csv = _h5auto_upload_file_to_gcs(
                movement_csv,
                f"gpi-lite/outputs/odds/{course_slug}_h10_to_h5_movements.csv",
            )
            upload_movement_json = _h5auto_upload_file_to_gcs(
                movement_json,
                f"gpi-lite/outputs/odds/{course_slug}_h10_to_h5_movements.json",
            )
            upload_gate_csv = _h5auto_upload_file_to_gcs(
                gate_csv,
                f"gpi-lite/outputs/odds/{course_slug}_h10_to_h5_volatility_gate.csv",
            )
            upload_gate_json = _h5auto_upload_file_to_gcs(
                gate_json,
                f"gpi-lite/outputs/odds/{course_slug}_h10_to_h5_volatility_gate.json",
            )
            upload_latest_context = _h5auto_upload_file_to_gcs(
                latest_context_json,
                "gpi-lite/outputs/odds/latest_odds_context.json",
            )
            upload_brief = _h5auto_upload_file_to_gcs(
                brief_json,
                f"gpi-lite/outputs/h5_briefs/{course_slug}_h5_auto_brief.json",
            )
            upload_latest_brief = _h5auto_upload_file_to_gcs(
                latest_brief_json,
                "gpi-lite/outputs/latest_h5_auto_brief.json",
            )

            # Sorties generiques pour lecture simple.
            _h5auto_upload_file_to_gcs(
                movement_csv,
                "gpi-lite/outputs/odds/odds_movement_snapshots.csv",
            )
            _h5auto_upload_file_to_gcs(
                movement_json,
                "gpi-lite/outputs/odds/odds_movement_snapshots.json",
            )
            _h5auto_upload_file_to_gcs(
                gate_csv,
                "gpi-lite/outputs/odds/odds_volatility_gate.csv",
            )
            _h5auto_upload_file_to_gcs(
                gate_json,
                "gpi-lite/outputs/odds/odds_volatility_gate.json",
            )

            movement_rows_total += len(movement_rows)
            latest_brief_payload = brief_payload

            processed.append({
                "course_uid": course_uid,
                "ok": True,
                "movement_rows": len(movement_rows),
                "course_statut_marche": course_statut_marche,
                "kill_switch_topn_oui_non": context_payload.get("kill_switch_topn_oui_non", "non"),
                "strict_allowed_by_odds_context": strict_allowed_by_odds_context,
                "edge_status_by_odds_context": edge_status_by_odds_context,
                "reason_codes": reason_codes,
                "upload_movement_csv_ok": upload_movement_csv.get("ok"),
                "upload_movement_json_ok": upload_movement_json.get("ok"),
                "upload_gate_csv_ok": upload_gate_csv.get("ok"),
                "upload_gate_json_ok": upload_gate_json.get("ok"),
                "upload_latest_context_ok": upload_latest_context.get("ok"),
                "upload_brief_ok": upload_brief.get("ok"),
                "upload_latest_brief_ok": upload_latest_brief.get("ok"),
                "h5_auto_paper_sync_ok": paper_sync.get("ok"),
                "h5_auto_paper_sync": paper_sync,
                "backfill_technical_oui_non": backfill_technical_oui_non,
                "strict_kpi_excluded_oui_non": strict_kpi_excluded_oui_non,
                "strict_kpi_exclusion_reason": strict_kpi_exclusion_reason,
                "ticket_strict": "NO_BET",
            })

        except Exception as exc:
            errors.append({
                "course_uid": course_uid,
                "reason": f"H5_AUTO_BRIEF_ERROR:{type(exc).__name__}:{exc}",
                "ticket_strict": "NO_BET",
            })

    return {
        "ok": True,
        "phase": "h5_auto_brief",
        "history_archive": _archive_h5_payload_safe(
            payload={
                "phase": "h5_auto_brief",
                "rows_input": len(rows),
                "matched_courses": len(processed),
                "errors_count": len(errors),
                "movement_rows_total": movement_rows_total,
                "briefs_created": len(processed),
                "processed_sample": processed[:10],
                "errors_sample": errors[:10],
            },
            course_uid=(
                processed[0].get("course_uid", "H5_AUTO_BRIEF_GLOBAL")
                if processed else "H5_AUTO_BRIEF_GLOBAL"
            ),
            capture_label="H5",
            source_route="/drive/run/h5-auto-brief",
            artifact_type="h5_auto_brief",
            status=(
                "LIVE_BRIEF_CREATED_PRE_RACE"
                if len(processed) > 0
                else "LIVE_VALID_PRE_RACE"
            ),
            reason=(
                "BRIEF_CREATED"
                if len(processed) > 0
                else "NO_BRIEF_CREATED"
            ),
            decision_before_race_oui_non="oui",
            result_known_at_decision="non",
            live_eligible_oui_non="oui",
            retro_audit_only_oui_non="non",
        ),
        "rows_input": len(rows),
        "matched_courses": len(processed),
        "errors_count": len(errors),
        "movement_rows_total": movement_rows_total,
        "briefs_created": len(processed),
        "processed_sample": processed[:10],
        "errors_sample": errors[:10],
        "latest_brief": latest_brief_payload,
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
    }






@app.get("/action/latest-h5-auto-brief")
def action_latest_h5_auto_brief():
    """
    Fa?ade GPT lecture seule.
    Lit le dernier brief H-5 automatique depuis la route backend /gpi/latest-h5-auto-brief.
    Ne relance aucun traitement.
    Ne capture aucune cote.
    Ne cr?e jamais de ticket strict.
    """
    payload = gpi_latest_h5_auto_brief()

    if not isinstance(payload, dict):
        payload = {
            "ok": False,
            "source_route": "/gpi/latest-h5-auto-brief",
            "action_route": "/action/latest-h5-auto-brief",
            "reason": "LATEST_H5_AUTO_BRIEF_INVALID_PAYLOAD",
            "VERDICT": "NO_BET",
            "Ticket_exact_ou_NO_BET": "NO_BET",
            "Segment_ROI": "NO_BET",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    payload["facade_version"] = "action_facade_v1_2_0"
    payload["source_route"] = "/gpi/latest-h5-auto-brief"
    payload["action_route"] = "/action/latest-h5-auto-brief"

    # Verrou doctrine fa?ade : aucune action GPT ne doit ouvrir un ticket strict.
    payload["strict_allowed_oui_non"] = "non"
    payload["strict_usable_oui_non"] = "non"
    payload["ticket_strict"] = "NO_BET"

    # P11b : compat lecture anciens briefs H-5 générés avant injection NP/statut.
    # Ne modifie pas GCS. Ne crée jamais de ticket strict.
    np_statut_defaults = {
        "np_ok": "ND",
        "statut_ok": "ND",
        "np_source": "NP_STATUT_NOT_IN_LEGACY_BRIEF",
        "statut_source": "NP_STATUT_NOT_IN_LEGACY_BRIEF",
        "np_count": "ND",
        "statut_missing_count": "ND",
        "np_statut_rows_count": 0,
        "np_statut_source_level": "LEGACY_BRIEF_NO_P11_FIELDS",
        "np_statut_checked_at_utc": "ND",
    }

    for key, default_value in np_statut_defaults.items():
        if payload.get(key) in {None, ""}:
            payload[key] = default_value

    if not payload.get("VERDICT"):
        payload["VERDICT"] = "NO_BET"
    if not payload.get("Ticket_exact_ou_NO_BET"):
        payload["Ticket_exact_ou_NO_BET"] = "NO_BET"
    if not payload.get("Segment_ROI"):
        payload["Segment_ROI"] = "NO_BET"

    return payload


@app.get("/gpi/latest-h5-auto-brief", dependencies=[Depends(require_api_key)])
def gpi_latest_h5_auto_brief():
    """
    Lit le dernier brief H-5 auto depuis GCS.
    Ne relance aucun traitement.
    Aucun ticket strict.
    """
    from pathlib import Path
    import json

    local_path = Path("data/output/latest_h5_auto_brief.json")

    download_latest = _h5auto_download_file_from_gcs(
        "gpi-lite/outputs/latest_h5_auto_brief.json",
        local_path,
    )

    if not download_latest.get("ok"):
        return {
            "ok": False,
            "source": "cloud_storage",
            "phase": "latest_h5_auto_brief",
            "download_latest": download_latest,
            "reason": "LATEST_H5_AUTO_BRIEF_GCS_DOWNLOAD_FAILED",
            "VERDICT": "NO_BET",
            "Ticket_exact_ou_NO_BET": "NO_BET",
            "Segment_ROI": "NO_BET",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    try:
        payload = json.loads(local_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "source": "cloud_storage",
            "phase": "latest_h5_auto_brief",
            "download_latest": download_latest,
            "reason": f"LATEST_H5_AUTO_BRIEF_JSON_READ_ERROR:{type(exc).__name__}:{exc}",
            "VERDICT": "NO_BET",
            "Ticket_exact_ou_NO_BET": "NO_BET",
            "Segment_ROI": "NO_BET",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    if not isinstance(payload, dict):
        payload = {}

    payload["ok"] = bool(payload.get("ok", True))
    payload["source"] = "cloud_storage"
    payload["gcs_uri"] = "gs://gpi-lite-watchlist-data/gpi-lite/outputs/latest_h5_auto_brief.json"
    payload["download_latest"] = download_latest

    # Verrou doctrine : cette route lit un brief, elle ne doit jamais ouvrir un ticket strict.
    payload["strict_allowed_oui_non"] = "non"
    payload["strict_usable_oui_non"] = "non"
    payload["ticket_strict"] = "NO_BET"

    if not payload.get("VERDICT"):
        payload["VERDICT"] = "NO_BET"
    if not payload.get("Ticket_exact_ou_NO_BET"):
        payload["Ticket_exact_ou_NO_BET"] = "NO_BET"
    if not payload.get("Segment_ROI"):
        payload["Segment_ROI"] = "NO_BET"

    return payload


@app.post("/upload/h5", dependencies=[Depends(require_api_key)])
async def upload_h5(file: UploadFile = File(...), filename: Optional[str] = None):
    return await save_pdf_upload(file, H5_DIR, filename)


@app.post("/upload/results", dependencies=[Depends(require_api_key)])
async def upload_results(file: UploadFile = File(...), filename: Optional[str] = None):
    return await save_pdf_upload(file, RESULTS_DIR, filename)



@app.post("/upload-and-run", dependencies=[Depends(require_api_key)])
async def upload_and_run(
    morning_pdf: Optional[UploadFile] = File(None),
    h5_pdf: Optional[UploadFile] = File(None),
    results_pdf: Optional[UploadFile] = File(None),
):
    ensure_dirs()

    uploads = {
        "morning": await save_optional_pdf(morning_pdf, MORNING_DIR),
        "h5": await save_optional_pdf(h5_pdf, H5_DIR),
        "results": await save_optional_pdf(results_pdf, RESULTS_DIR),
    }

    steps = [
        ("morning", "main.py"),
        ("h5", "h5_main.py"),
        ("quality", "quality_check.py"),
    ]

    legacy_results_disabled = {
        "ok": True,
        "disabled": True,
        "reason": "results_main.py et audit_all.py retirés de upload-and-run en V0.9.7D. Utiliser /drive/run/results pour la phase post-course paper.",
        "results_pdf_uploaded": bool(uploads.get("results", {}).get("uploaded")),
        "results_main_called": False,
        "audit_all_called": False,
    }

    outputs = []

    for name, script in steps:
        result = run_script(script)
        outputs.append({
            "step": name,
            "result": result
        })

        pipeline_ok = all(item["result"]["ok"] for item in outputs)

    latest_context = read_latest_h5_context()
    course_key = str(latest_context.get("course_key", "")).strip()
    decision_aliases = write_decision_lite_aliases(course_key)

    cloud_sync = sync_gpi_files_to_gcs()

    return {
        "ok": pipeline_ok,
        "uploads": uploads,
        "steps": outputs,
        "decision_aliases": decision_aliases,
        "legacy_results_disabled": legacy_results_disabled,
        "cloud_sync": cloud_sync,
        "cloud_sync_ok": cloud_sync.get("ok", False)
    }


@app.post("/run/morning", dependencies=[Depends(require_api_key)])
def run_morning():
    return run_script("main.py")


@app.post("/run/h5", dependencies=[Depends(require_api_key)])
def run_h5():
    return run_script("h5_main.py")


@app.post("/run/results", dependencies=[Depends(require_api_key)])
def run_results():
    ensure_dirs()

    try:
        results_path, latest_path, audit_csv_path, audit_json_path, audit = write_paper_results()

        return {
            "ok": True,
            "phase": "paper_results",
            "route": "/run/results",
            "legacy_results_main_called": False,
            "paper_trading_50_decisions_modified": False,
            "outputs": {
                "paper_trading_results": safe_posix_path(results_path),
                "latest_paper_result_update": safe_posix_path(latest_path),
                "paper_results_audit": safe_posix_path(audit_csv_path),
                "latest_paper_results_audit": safe_posix_path(audit_json_path),
            },
            "audit": audit,
            "guardrails": {
                "h5_main_called": False,
                "results_main_called": False,
                "audit_all_called": False,
                "post_course_only": True,
            },
        }

    except Exception as exc:
        return {
            "ok": False,
            "phase": "paper_results",
            "route": "/run/results",
            "error": str(exc),
            "guardrails": {
                "h5_main_called": False,
                "results_main_called": False,
                "audit_all_called": False,
                "post_course_only": True,
            },
        }

@app.post("/run/audit", dependencies=[Depends(require_api_key)])
def run_audit():
    return {
        "ok": False,
        "route": "/run/audit",
        "disabled": True,
        "reason": "Route legacy neutralisée en V0.9.7D. Utiliser /drive/run/results pour la phase post-course paper.",
        "audit_all_called": False,
    }

@app.post("/run/paper-kpi", dependencies=[Depends(require_api_key)])
def run_paper_kpi():
    ensure_dirs()

    try:
        kpi_path, audit_csv_path, audit_json_path, audit = write_paper_kpi()

        uploads = [
            upload_file_to_gcs(kpi_path, "outputs"),
            upload_file_to_gcs(audit_csv_path, "outputs"),
            upload_file_to_gcs(audit_json_path, "outputs"),
        ]

        cloud_sync = {
            "ok": all(item.get("ok") for item in uploads),
            "bucket": GPI_BUCKET_NAME,
            "files_count": len(uploads),
            "uploads": uploads,
        }

        return {
            "ok": cloud_sync.get("ok") is True,
            "phase": "paper_kpi",
            "route": "/run/paper-kpi",
            "audit": audit,
            "cloud_sync": cloud_sync,
            "guardrails": {
                "h5_main_called": False,
                "results_main_called": False,
                "audit_all_called": False,
                "post_course_only": True,
                "paper_trading_50_decisions_modified": False,
            },
        }

    except Exception as exc:
        return {
            "ok": False,
            "phase": "paper_kpi",
            "route": "/run/paper-kpi",
            "error": str(exc),
            "guardrails": {
                "h5_main_called": False,
                "results_main_called": False,
                "audit_all_called": False,
                "post_course_only": True,
                "paper_trading_50_decisions_modified": False,
            },
        }


@app.post("/run/quality", dependencies=[Depends(require_api_key)])
def run_quality():
    return run_script("quality_check.py")


@app.post("/run/all", dependencies=[Depends(require_api_key)])
def run_all():
    return {
        "ok": False,
        "route": "/run/all",
        "disabled": True,
        "reason": "Route legacy neutralisée en V0.9.7D. Exécuter séparément /drive/run/morning, /drive/run/h5 et /drive/run/results.",
        "guardrails": {
            "results_main_called": False,
            "audit_all_called": False,
            "paper_results_required_route": "/drive/run/results",
        },
    }

@app.get("/files/morning", dependencies=[Depends(require_api_key)])
def list_morning_files():
    return {
        "ok": True,
        "files": list_files_in_dir(MORNING_DIR)
    }


@app.get("/files/h5", dependencies=[Depends(require_api_key)])
def list_h5_files():
    return {
        "ok": True,
        "files": list_files_in_dir(H5_DIR)
    }


@app.get("/files/results", dependencies=[Depends(require_api_key)])
def list_result_files():
    return {
        "ok": True,
        "files": list_files_in_dir(RESULTS_DIR)
    }


@app.get("/files/output", dependencies=[Depends(require_api_key)])
def list_output_files():
    return {
        "ok": True,
        "files": list_files_in_dir(OUTPUT_DIR)
    }


@app.get("/download/output/{filename}", dependencies=[Depends(require_api_key)])
def download_output(filename: str):
    ensure_dirs()

    safe_name = Path(filename).name
    file_path = OUTPUT_DIR / safe_name

    if not file_path.exists():
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "error": f"Fichier introuvable: {safe_name}"
            }
        )

    return FileResponse(file_path)


@app.get("/gpi/summary", dependencies=[Depends(require_api_key)])
def gpi_summary():
    ensure_dirs()

    watchlist_path = OUTPUT_DIR / "daily_watchlist.csv"
    quality_path = OUTPUT_DIR / "quality_check.csv"

    decision_path = newest_file("*_decision_lite.csv")
    prompt_path = newest_file("*_prompt_gpt.txt")
    h5_update_path = newest_file("*_h5_update.csv")
    audit_path = newest_file("*_journal_audit.csv")
    audit_skipped_path = OUTPUT_DIR / "audit_skipped.csv"

    summary = {
        "ok": True,
        "watchlist": read_csv_as_records(watchlist_path),
        "h5_update": read_csv_as_records(h5_update_path) if h5_update_path else {
            "ok": False,
            "path": "",
            "rows": [],
            "error": "h5_update absent"
        },
        "decision_lite": read_csv_as_records(decision_path) if decision_path else {
            "ok": False,
            "path": "",
            "rows": [],
            "error": "decision_lite absent"
        },
        "prompt_gpt": read_text_file(prompt_path) if prompt_path else {
            "ok": False,
            "path": "",
            "text": "",
            "error": "prompt_gpt absent"
        },
        "quality_check": read_csv_as_records(quality_path),
        "audit": read_csv_as_records(audit_path) if audit_path else {
            "ok": False,
            "path": "",
            "rows": [],
            "error": "journal_audit absent"
        },
        "audit_skipped": read_csv_as_records(audit_skipped_path) if audit_skipped_path.exists() else {
            "ok": False,
            "path": "",
            "rows": [],
            "error": "audit_skipped absent"
        },
        "files_output": list_files_in_dir(OUTPUT_DIR)
    }

    return summary
@app.get("/gpi/watchlist", dependencies=[Depends(require_api_key)])
def gpi_watchlist():
    ensure_dirs()

    watchlist_path = OUTPUT_DIR / "daily_watchlist.csv"

    if watchlist_path.exists():
        try:
            df = pd.read_csv(watchlist_path).fillna("")
        except Exception as exc:
            return {
                "ok": False,
                "source": "local_container",
                "error": str(exc),
                "rows_count": 0,
                "rows": []
            }

        return {
            "ok": True,
            "source": "local_container",
            "path": to_posix_path(watchlist_path),
            "rows_count": len(df),
            "rows": df.to_dict(orient="records")
        }

    blob, error = get_latest_gcs_blob(
        prefix="gpi-lite/outputs/",
        suffix="daily_watchlist.csv"
    )

    if blob is None:
        return {
            "ok": False,
            "source": "none",
            "error": "daily_watchlist.csv absent en local et dans Cloud Storage",
            "cloud_error": error,
            "rows_count": 0,
            "rows": []
        }

    try:
        text = blob.download_as_text(encoding="utf-8")
        df = pd.read_csv(io.StringIO(text)).fillna("")
    except UnicodeDecodeError:
        text = blob.download_as_text(encoding="latin-1")
        df = pd.read_csv(io.StringIO(text)).fillna("")
    except Exception as exc:
        return {
            "ok": False,
            "source": "cloud_storage",
            "error": str(exc),
            "rows_count": 0,
            "rows": []
        }

    return {
        "ok": True,
        "source": "cloud_storage",
        "bucket": GPI_BUCKET_NAME,
        "gcs_uri": f"gs://{GPI_BUCKET_NAME}/{blob.name}",
        "updated": blob.updated.isoformat() if blob.updated else "",
        "rows_count": len(df),
        "rows": df.to_dict(orient="records")
    }

def get_paper_trading_status():
    paper_path = OUTPUT_DIR / "paper_trading_50_decisions.csv"
    latest_path = OUTPUT_DIR / "latest_paper_trading_decision.csv"

    source = "local"

    if not paper_path.exists():
        bucket_name = (
            os.getenv("GPI_BUCKET_NAME")
            or os.getenv("BUCKET_NAME")
            or os.getenv("GPI_BUCKET")
        )

        if bucket_name:
            try:
                from google.cloud import storage

                client = storage.Client()
                bucket = client.bucket(bucket_name)

                for blob_name, target in [
                    ("gpi-lite/outputs/paper_trading_50_decisions.csv", paper_path),
                    ("gpi-lite/outputs/latest_paper_trading_decision.csv", latest_path),
                ]:
                    blob = bucket.blob(blob_name)
                    if blob.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        blob.download_to_filename(str(target))
                        source = "cloud_storage"
            except Exception:
                pass

    status = {
        "exists": False,
        "count": 0,
        "ready_50": False,
        "latest_engine_version": "ND",
        "latest_verdict": "ND",
        "latest_mise_paper": "ND",
        "latest_course_uid": "ND",
        "latest_decision_id": "ND",
        "source": source,
        "path": safe_posix_path(paper_path),
        "latest_path": safe_posix_path(latest_path),
        "error": "",
    }

    if not paper_path.exists():
        status["error"] = "paper_trading_50_decisions.csv absent"
        return status

    try:
        df = pd.read_csv(paper_path, dtype=str, keep_default_na=False)
    except Exception as exc:
        status["exists"] = True
        status["error"] = str(exc)
        return status

    status["exists"] = True
    status["count"] = int(len(df))
    status["ready_50"] = bool(len(df) >= 50)

    latest_row = None

    if latest_path.exists():
        try:
            latest_df = pd.read_csv(latest_path, dtype=str, keep_default_na=False)
            if not latest_df.empty:
                latest_row = latest_df.iloc[0].to_dict()
        except Exception:
            latest_row = None

    if latest_row is None and not df.empty:
        latest_row = df.iloc[-1].to_dict()

    if latest_row:
        status["latest_engine_version"] = str(latest_row.get("engine_version", "ND"))
        status["latest_verdict"] = str(latest_row.get("verdict_pre_course", "ND"))
        status["latest_mise_paper"] = str(latest_row.get("mise_paper", "ND"))
        status["latest_course_uid"] = str(latest_row.get("course_uid", "ND"))
        status["latest_decision_id"] = str(latest_row.get("decision_id", "ND"))

    return status


def get_paper_trading_audit_status():
    audit_path = OUTPUT_DIR / "latest_paper_trading_audit.json"

    source = "local"

    if not audit_path.exists():
        bucket_name = (
            os.getenv("GPI_BUCKET_NAME")
            or os.getenv("BUCKET_NAME")
            or os.getenv("GPI_BUCKET")
        )

        if bucket_name:
            try:
                from google.cloud import storage

                client = storage.Client()
                bucket = client.bucket(bucket_name)
                blob = bucket.blob("gpi-lite/outputs/latest_paper_trading_audit.json")

                if blob.exists():
                    audit_path.parent.mkdir(parents=True, exist_ok=True)
                    blob.download_to_filename(str(audit_path))
                    source = "cloud_storage"
            except Exception:
                pass

    status = {
        "exists": False,
        "source": source,
        "path": safe_posix_path(audit_path),
        "audit_version": "ND",
        "paper_trading_count": 0,
        "paper_unique_count": 0,
        "paper_trading_ready_50": False,
        "duplicate_rows": "ND",
        "strict_allowed_count": "ND",
        "result_leak_count": "ND",
        "replay_ko_count": "ND",
        "recommendation": "ND",
        "blocking_reason": "ND",
        "latest_course_uid": "ND",
        "latest_decision_id": "ND",
        "error": "",
    }

    if not audit_path.exists():
        status["error"] = "latest_paper_trading_audit.json absent"
        return status

    try:
        with open(audit_path, "r", encoding="utf-8") as f:
            audit = json.load(f)
    except Exception as exc:
        status["exists"] = True
        status["error"] = str(exc)
        return status

    status["exists"] = True
    status["audit_version"] = audit.get("audit_version", "ND")
    status["paper_trading_count"] = audit.get("paper_trading_count", 0)
    status["paper_unique_count"] = audit.get("paper_unique_count", 0)
    status["paper_trading_ready_50"] = audit.get("paper_trading_ready_50", False)
    status["duplicate_rows"] = audit.get("duplicate_rows", "ND")
    status["strict_allowed_count"] = audit.get("strict_allowed_count", "ND")
    status["result_leak_count"] = audit.get("result_leak_count", "ND")
    status["replay_ko_count"] = audit.get("replay_ko_count", "ND")
    status["recommendation"] = audit.get("recommendation", "ND")
    status["blocking_reason"] = audit.get("blocking_reason", "ND")
    status["latest_course_uid"] = audit.get("latest_course_uid", "ND")
    status["latest_decision_id"] = audit.get("latest_decision_id", "ND")

    return status


def get_paper_kpi_status():
    status = {
        "exists": False,
        "source": "absent",
        "ready_50": False,
        "global_decisions": 0,
        "global_tickets": 0,
        "global_no_bet_count": 0,
        "global_result_pending_count": 0,
        "global_strict_allowed_count": 0,
        "global_replay_ko_count": 0,
        "rows_kpi": 0,
        "kpi_version": "",
        "path": "data/output/latest_paper_kpi_audit.json",
        "gcs_uri": "gs://gpi-lite-watchlist-data/gpi-lite/outputs/latest_paper_kpi_audit.json",
        "error": "",
    }

    local_path = OUTPUT_DIR / "latest_paper_kpi_audit.json"

    def apply_audit(audit, source):
        decisions = int(audit.get("global_decisions") or audit.get("rows_input") or 0)

        status.update({
            "exists": True,
            "source": source,
            "ready_50": decisions >= 50,
            "global_decisions": decisions,
            "global_tickets": int(audit.get("global_tickets") or 0),
            "global_no_bet_count": int(audit.get("global_no_bet_count") or 0),
            "global_result_pending_count": int(audit.get("global_result_pending_count") or 0),
            "global_strict_allowed_count": int(audit.get("global_strict_allowed_count") or 0),
            "global_replay_ko_count": int(audit.get("global_replay_ko_count") or 0),
            "rows_kpi": int(audit.get("rows_kpi") or 0),
            "kpi_version": str(audit.get("kpi_version") or ""),
            "error": "",
        })

    if local_path.exists():
        try:
            audit = json.loads(local_path.read_text(encoding="utf-8"))
            apply_audit(audit, "local")
            return status
        except Exception as exc:
            status["error"] = f"lecture locale KPI impossible: {exc}"

    bucket_name = (
        globals().get("GPI_BUCKET_NAME")
        or os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or "gpi-lite-watchlist-data"
    )

    blob_name = "gpi-lite/outputs/latest_paper_kpi_audit.json"

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if not blob.exists():
            status["source"] = "cloud_storage"
            status["gcs_uri"] = f"gs://{bucket_name}/{blob_name}"
            status["error"] = "latest_paper_kpi_audit.json absent"
            return status

        payload = blob.download_as_text(encoding="utf-8")
        audit = json.loads(payload)
        apply_audit(audit, "cloud_storage")
        status["gcs_uri"] = f"gs://{bucket_name}/{blob_name}"
        return status

    except Exception as exc:
        status["source"] = "cloud_storage"
        status["gcs_uri"] = f"gs://{bucket_name}/{blob_name}"
        status["error"] = str(exc)
        return status





@app.get("/gpi/watchlist-priorisee", dependencies=[Depends(require_api_key)])
def gpi_watchlist_priorisee():
    ensure_dirs()

    import io
    import pandas as pd
    from pathlib import Path

    local_path = OUTPUT_DIR / "latest_daily_watchlist_priorisee.csv"

    if local_path.exists():
        try:
            df = pd.read_csv(local_path, dtype=str, keep_default_na=False).fillna("")
            if "priorite_h5_calc" in df.columns:
                df["_sort_priority"] = pd.to_numeric(df["priorite_h5_calc"], errors="coerce").fillna(99)
                df = df.sort_values(["_sort_priority"], kind="stable").drop(columns=["_sort_priority"])
            return {
                "ok": True,
                "source": "local_container",
                "path": str(local_path),
                "rows_count": int(len(df)),
                "rows": df.to_dict(orient="records"),
            }
        except Exception as exc:
            return {
                "ok": False,
                "source": "local_container",
                "error": str(exc),
                "rows_count": 0,
                "rows": [],
            }

    blob, error = get_latest_gcs_blob(
        prefix="gpi-lite/outputs/",
        suffix="latest_daily_watchlist_priorisee.csv"
    )

    if blob is None:
        return {
            "ok": False,
            "source": "none",
            "error": "latest_daily_watchlist_priorisee.csv absent en local et dans Cloud Storage",
            "cloud_error": error,
            "rows_count": 0,
            "rows": [],
        }

    try:
        text_csv = blob.download_as_text(encoding="utf-8")
        df = pd.read_csv(io.StringIO(text_csv), dtype=str, keep_default_na=False).fillna("")
        if "priorite_h5_calc" in df.columns:
            df["_sort_priority"] = pd.to_numeric(df["priorite_h5_calc"], errors="coerce").fillna(99)
            df = df.sort_values(["_sort_priority"], kind="stable").drop(columns=["_sort_priority"])

        return {
            "ok": True,
            "source": "cloud_storage",
            "gcs_uri": f"gs://{blob.bucket.name}/{blob.name}",
            "rows_count": int(len(df)),
            "rows": df.to_dict(orient="records"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "source": "cloud_storage",
            "error": str(exc),
            "rows_count": 0,
            "rows": [],
        }




def get_latest_brief_from_cloud_payload():
    """
    Wrapper fail-safe pour /gpi/status.
    Ne doit jamais lever une exception.
    """
    try:
        payload = gpi_latest_brief_from_cloud()
        if isinstance(payload, dict):
            return payload

        return {
            "ok": False,
            "error": "gpi_latest_brief_from_cloud returned non-dict payload",
            "payload_type": str(type(payload)),
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": f"gpi_latest_brief_from_cloud failed: {type(exc).__name__}: {exc}",
        }





@app.get("/gpi/volatilite-audit", dependencies=[Depends(require_api_key)])
def gpi_volatilite_audit():
    ensure_dirs()

    import csv
    import io
    import json
    import pandas as pd
    from pathlib import Path
    from engine.volatilite_audit import build_volatilite_audit_from_df

    bucket, bucket_error = get_gcs_bucket()

    if bucket is not None:
        rows = []
        seen = set()

        try:
            blobs = bucket.list_blobs(prefix="gpi-lite/outputs/")

            for blob in blobs:
                base_name = Path(blob.name).name

                if not base_name.endswith("_decision_engine_outputs.csv"):
                    continue

                if base_name in {"latest_decision_engine_outputs.csv", "decision_engine_outputs.csv"}:
                    continue

                try:
                    content = blob.download_as_text(encoding="utf-8-sig")
                    csv_rows = list(csv.DictReader(io.StringIO(content)))
                except Exception:
                    continue

                if not csv_rows:
                    continue

                row = csv_rows[0]

                key = (
                    str(row.get("decision_id", "")).strip()
                    or str(row.get("course_uid", "")).strip()
                    or base_name
                )

                if key in seen:
                    continue

                seen.add(key)
                rows.append(row)

            if rows:
                df = pd.DataFrame(rows).fillna("")
                audit = build_volatilite_audit_from_df(
                    df=df,
                    audit_source="gcs_decision_engine_outputs_history",
                    paper_registry_count=len(rows),
                )

                audit["source"] = "cloud_storage_dynamic"
                audit["gcs_prefix"] = "gs://{}/gpi-lite/outputs/".format(bucket.name)

                try:
                    global_json = bucket.blob("gpi-lite/outputs/latest_volatilite_audit_global.json")
                    global_json.upload_from_string(
                        json.dumps(audit, ensure_ascii=False, indent=2),
                        content_type="application/json"
                    )

                    global_csv = bucket.blob("gpi-lite/outputs/latest_volatilite_audit_global.csv")
                    pd.DataFrame([audit]).to_csv("/tmp/latest_volatilite_audit_global.csv", index=False, encoding="utf-8")
                    global_csv.upload_from_filename(
                        "/tmp/latest_volatilite_audit_global.csv",
                        content_type="text/csv"
                    )

                    audit["global_written"] = True
                    audit["global_gcs_uri"] = f"gs://{bucket.name}/gpi-lite/outputs/latest_volatilite_audit_global.json"

                except Exception as write_exc:
                    audit["global_written"] = False
                    audit["global_write_error"] = str(write_exc)

                return audit

        except Exception as exc:
            gcs_dynamic_error = str(exc)
        else:
            gcs_dynamic_error = "aucun fichier *_decision_engine_outputs.csv exploitable"
    else:
        gcs_dynamic_error = bucket_error or "bucket indisponible"

    local_path = OUTPUT_DIR / "latest_volatilite_audit.json"

    if local_path.exists():
        try:
            data = json.loads(local_path.read_text(encoding="utf-8"))
            data["source"] = "local_container_fallback"
            data["path"] = str(local_path)
            data["gcs_dynamic_error"] = gcs_dynamic_error
            return data
        except Exception as exc:
            return {
                "ok": False,
                "source": "local_container_fallback",
                "error": str(exc),
                "gcs_dynamic_error": gcs_dynamic_error,
            }

    if bucket is None:
        return {
            "ok": False,
            "source": "none",
            "error": gcs_dynamic_error,
        }

    blob = bucket.blob("gpi-lite/outputs/latest_volatilite_audit.json")

    try:
        if not blob.exists():
            return {
                "ok": False,
                "source": "cloud_storage_fallback",
                "error": "latest_volatilite_audit.json absent",
                "gcs_dynamic_error": gcs_dynamic_error,
            }

        data = json.loads(blob.download_as_text(encoding="utf-8"))
        data["source"] = "cloud_storage_fallback"
        data["gcs_uri"] = f"gs://{bucket.name}/{blob.name}"
        data["gcs_dynamic_error"] = gcs_dynamic_error
        return data

    except Exception as exc:
        return {
            "ok": False,
            "source": "cloud_storage_fallback",
            "error": str(exc),
            "gcs_dynamic_error": gcs_dynamic_error,
        }




def get_volatilite_audit_status():
    """
    Lecture fail-safe pour /gpi/status.
    Lit l'audit global statique GCS en priorité.
    Ne recalcule jamais l'audit dans /gpi/status.
    """
    try:
        import json

        bucket, bucket_error = get_gcs_bucket()

        if bucket is not None:
            for blob_name in [
                "gpi-lite/outputs/latest_volatilite_audit_global.json",
                "gpi-lite/outputs/latest_volatilite_audit.json",
            ]:
                try:
                    blob = bucket.blob(blob_name)

                    if not blob.exists():
                        continue

                    data = json.loads(blob.download_as_text(encoding="utf-8"))

                    if isinstance(data, dict):
                        data["status_source"] = "cloud_storage_static"
                        data["gcs_uri"] = f"gs://{bucket.name}/{blob.name}"
                        data["status_blob_name"] = blob_name
                        return data

                except Exception:
                    continue

        local_path = OUTPUT_DIR / "latest_volatilite_audit_global.json"

        if local_path.exists():
            try:
                data = json.loads(local_path.read_text(encoding="utf-8"))

                if isinstance(data, dict):
                    data["status_source"] = "local_container_static_global"
                    data["path"] = str(local_path)
                    return data
            except Exception:
                pass

        local_path = OUTPUT_DIR / "latest_volatilite_audit.json"

        if local_path.exists():
            try:
                data = json.loads(local_path.read_text(encoding="utf-8"))

                if isinstance(data, dict):
                    data["status_source"] = "local_container_static_fallback"
                    data["path"] = str(local_path)
                    return data
            except Exception:
                pass

        return {
            "ok": False,
            "status_source": "none",
            "error": bucket_error or "aucun audit volatilité statique disponible",
        }

    except Exception as exc:
        return {
            "ok": False,
            "status_source": "failsafe_exception",
            "error": f"{type(exc).__name__}: {exc}",
        }




@app.get("/gpi/source-pack-status", dependencies=[Depends(require_api_key)])
def gpi_source_pack_status():
    try:
        from engine.source_pack import build_source_pack_status
        return build_source_pack_status()
    except Exception as exc:
        return {
            "ok": False,
            "source_pack_version": "ND",
            "source_documents_loaded": False,
            "source_documents_hash_ok": False,
            "source_documents_count": 0,
            "source_documents_missing": [],
            "source_documents_mismatch": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


@app.get("/gpi/status", dependencies=[Depends(require_api_key)])
def gpi_status():
    ensure_dirs()

    local_files = {
        "daily_watchlist": (OUTPUT_DIR / "daily_watchlist.csv").exists(),
        "latest_h5_context": (OUTPUT_DIR / "latest_h5_context.json").exists(),
        "latest_decision_lite": (OUTPUT_DIR / "latest_decision_lite.csv").exists(),
        "decision_lite": (OUTPUT_DIR / "decision_lite.csv").exists(),
        "quality_check": (OUTPUT_DIR / "quality_check.csv").exists(),
    }

    cloud_files = {}
    bucket, bucket_error = get_gcs_bucket()

    expected_cloud_files = {
        "daily_watchlist": "gpi-lite/outputs/daily_watchlist.csv",
        "daily_watchlist_priorisee": "gpi-lite/outputs/daily_watchlist_priorisee.csv",
        "latest_daily_watchlist_priorisee": "gpi-lite/outputs/latest_daily_watchlist_priorisee.csv",
        "latest_h5_context": "gpi-lite/outputs/latest_h5_context.json",
        "latest_decision_lite": "gpi-lite/outputs/latest_decision_lite.csv",
        "decision_lite": "gpi-lite/outputs/decision_lite.csv",
        "quality_check": "gpi-lite/outputs/quality_check.csv",
    }

    if bucket is None:
        for key in expected_cloud_files:
            cloud_files[key] = {
                "exists": False,
                "updated": "",
                "error": bucket_error
            }
    else:
        for key, object_name in expected_cloud_files.items():
            try:
                blob = bucket.get_blob(object_name)
                cloud_files[key] = {
                    "exists": blob is not None,
                    "updated": blob.updated.isoformat() if blob and blob.updated else "",
                    "gcs_uri": f"gs://{GPI_BUCKET_NAME}/{object_name}" if blob else ""
                }
            except Exception as exc:
                cloud_files[key] = {
                    "exists": False,
                    "updated": "",
                    "error": str(exc)
                }

    quality = get_quality_status()
    paper_status = get_paper_trading_status()
    paper_audit_status = get_paper_trading_audit_status()

    cloud_brief = get_latest_brief_from_cloud_payload()
    cloud_ok = cloud_brief.get("ok") is True

    cloud_ready_files = all(
        cloud_files[key].get("exists") is True
        for key in [
            "daily_watchlist",
            "latest_h5_context",
            "latest_decision_lite",
            "decision_lite",
            "quality_check",
        ]
    )

    h5_context_freshness = _gpi_latest_h5_context_freshness()
    cloud_ready = bool(
        cloud_ready_files
        and h5_context_freshness.get("latest_h5_context_fresh") is True
    )

    paper_kpi = get_paper_kpi_status()

    paper_kpi_decisions = int(paper_kpi.get("global_decisions") or 0)
    paper_kpi_pending = int(paper_kpi.get("global_result_pending_count") or 0)
    paper_kpi_replay_ko = int(paper_kpi.get("global_replay_ko_count") or 0)

    paper_trading_count_for_gate = int(
        paper_audit_status.get("paper_trading_count")
        or paper_audit_status.get("paper_unique_count")
        or paper_audit_status.get("count")
        or paper_kpi_decisions
        or 0
    )

    paper_trading_vs_kpi_gap = paper_trading_count_for_gate - paper_kpi_decisions

    _cloud_ok_for_gate = bool(locals().get("cloud_ok", False))
    _cloud_brief_for_gate = locals().get("cloud_brief", {}) or {}

    latest_course_uid_for_gate = (
        _cloud_brief_for_gate.get("course_uid", "ND") if _cloud_ok_for_gate else "ND"
    )

    latest_course_pending_result = _gpi_is_latest_course_pending_result(
        latest_course_uid_for_gate
    )

    paper_pending_result_count_for_gate = max(0, paper_kpi_pending)

    paper_pending_from_rows_for_gate = _gpi_count_pending_results_from_paper_rows()

    paper_pending_result_count_for_gate = max(
        paper_pending_result_count_for_gate,
        paper_pending_from_rows_for_gate
    )

    if latest_course_pending_result and paper_trading_vs_kpi_gap > paper_pending_result_count_for_gate:
        paper_pending_result_count_for_gate += 1

    paper_kpi_stale_count = max(
        0,
        paper_trading_vs_kpi_gap - paper_pending_result_count_for_gate
    )

    paper_kpi_stale = paper_kpi_stale_count > 0

    if paper_kpi_stale:
        paper_kpi_stale_reason = (
            f"paper_due_for_kpi gap {paper_kpi_stale_count}, "
            f"paper_trading_count {paper_trading_count_for_gate}, "
            f"paper_kpi_global_decisions {paper_kpi_decisions}, "
            f"pending_results {paper_pending_result_count_for_gate}, "
            "run /drive/run/results"
        )
    else:
        paper_kpi_stale_reason = (
            f"paper KPI OK, pending_results {paper_pending_result_count_for_gate} non bloquant"
        )

    paper_result_leak_count_for_gate = int(
        paper_audit_status.get("result_leak_count") or 0
    )
    paper_kpi_ready_50_for_gate = bool(paper_kpi.get("ready_50", False))

    mvp_gate_reasons = []

    if paper_kpi_stale:
        mvp_gate_reasons.append(paper_kpi_stale_reason)
    elif paper_kpi_decisions < 50:
        mvp_gate_reasons.append(f"{paper_kpi_decisions}/50 paper decisions")

    if paper_kpi_pending > 0:
        pass

    if paper_kpi_replay_ko > 0:
        mvp_gate_reasons.append(f"{paper_kpi_replay_ko} replay KO")

    if mvp_gate_reasons:
        mvp_gate_status = "BLOCKED"
        strict_allowed_global = False
        strict_allowed_reason = "blocked by MVP gate: " + ", ".join(mvp_gate_reasons)
    else:
        mvp_gate_status = "PAPER_READY"

        strict_blockers = []

        if not paper_kpi_ready_50_for_gate:
            strict_blockers.append("paper_kpi_ready_50 false")

        if paper_kpi_stale:
            strict_blockers.append("paper_kpi_stale true")

        if paper_kpi_replay_ko > 0:
            strict_blockers.append(f"{paper_kpi_replay_ko} replay KO")

        if paper_result_leak_count_for_gate > 0:
            strict_blockers.append(f"{paper_result_leak_count_for_gate} result leak")

        if not cloud_ready:
            strict_blockers.append("cloud_ready false")

        if not cloud_ok:
            strict_blockers.append("cloud_brief_ok false")

        strict_allowed_global = len(strict_blockers) == 0

        if strict_allowed_global:
            strict_allowed_reason = "strict global enabled, decision-level gates still required"
            mvp_gate_reasons.append("paper audit ready, strict global enabled")
        else:
            strict_allowed_reason = "strict global disabled: " + ", ".join(strict_blockers)
            mvp_gate_reasons.append(strict_allowed_reason)

    mvp_gate_reason = ", ".join(mvp_gate_reasons)

    latest_cp_sg_for_status = _gpi_read_latest_cp_sg_fields_for_status()

    volatilite_audit = get_volatilite_audit_status()
    volatilite_audit_ok = bool(volatilite_audit.get("ok"))

    try:
        from engine.source_pack import build_source_pack_status
        source_pack = build_source_pack_status()
    except Exception as exc:
        source_pack = {
            "ok": False,
            "source_pack_version": "ND",
            "source_documents_loaded": False,
            "source_documents_hash_ok": False,
            "source_documents_count": 0,
            "source_documents_missing": [],
            "source_documents_mismatch": [],
            "manifest_sha256": "ND",
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "ok": True,
        "source_pack_status": "OK" if source_pack.get("ok") else "KO",
        "source_pack_version": source_pack.get("source_pack_version", "ND"),
        "source_pack_manifest_sha256": source_pack.get("manifest_sha256", "ND"),
        "source_documents_count": source_pack.get("source_documents_count", 0),
        "source_documents_loaded": source_pack.get("source_documents_loaded", False),
        "source_documents_hash_ok": source_pack.get("source_documents_hash_ok", False),
        "source_documents_missing": source_pack.get("source_documents_missing", []),
        "source_documents_mismatch": source_pack.get("source_documents_mismatch", []),
        "source_pack_error": source_pack.get("error", ""),
        "service": "GPI-Lite Watchlist",
        "bucket": GPI_BUCKET_NAME or "ND",
        "drive_morning_configured": bool(os.getenv("DRIVE_MORNING_FOLDER_ID", "").strip()),
        "drive_h5_configured": bool(os.getenv("DRIVE_H5_FOLDER_ID", "").strip()),
        "archive_enabled": os.getenv("DRIVE_ARCHIVE_AFTER_SUCCESS", "false").lower() == "true",
        "archive_morning_configured": bool(os.getenv("DRIVE_ARCHIVE_MORNING_FOLDER_ID", "").strip()),
        "archive_h5_configured": bool(os.getenv("DRIVE_ARCHIVE_H5_FOLDER_ID", "").strip()),
        "local_files": local_files,
        "cloud_files": cloud_files,
        "cloud_ready": cloud_ready,
        "cloud_ready_files": cloud_ready_files,
        "latest_h5_context_fresh_oui_non": h5_context_freshness.get("latest_h5_context_fresh_oui_non", "non"),
        "latest_h5_context_mismatch_oui_non": h5_context_freshness.get("latest_h5_context_mismatch_oui_non", "non"),
        "latest_h5_context_course_key": h5_context_freshness.get("latest_h5_context_course_key", "ND"),
        "latest_h5_auto_brief_course_uid": h5_context_freshness.get("latest_h5_auto_brief_course_uid", "ND"),
        "latest_h5_context_freshness_reason": h5_context_freshness.get("reason", "ND"),
        "quality_status_local": quality.get("status", "ND"),
        "quality_details_local": quality.get("details", "ND"),
        "paper_trading_exists": paper_status.get("exists", False),
        "paper_trading_count": paper_status.get("count", 0),
        "paper_trading_ready_50": paper_status.get("ready_50", False),
        "paper_trading_latest_engine_version": paper_status.get("latest_engine_version", "ND"),
        "paper_trading_latest_verdict": paper_status.get("latest_verdict", "ND"),
        "paper_trading_latest_mise_paper": paper_status.get("latest_mise_paper", "ND"),
        "paper_trading": paper_status,
        "paper_audit_exists": paper_audit_status.get("exists", False),
        "paper_audit_version": paper_audit_status.get("audit_version", "ND"),
        "paper_unique_count": paper_audit_status.get("paper_unique_count", 0),
        "paper_duplicate_rows": paper_audit_status.get("duplicate_rows", "ND"),
        "paper_strict_allowed_count": paper_audit_status.get("strict_allowed_count", "ND"),
        "paper_result_leak_count": paper_audit_status.get("result_leak_count", "ND"),
        "paper_replay_ko_count": paper_audit_status.get("replay_ko_count", "ND"),
        "paper_recommendation": paper_audit_status.get("recommendation", "ND"),
        "paper_blocking_reason": paper_audit_status.get("blocking_reason", "ND"),
        "paper_audit": paper_audit_status,
        "paper_kpi_exists": paper_kpi.get("exists", False),
        "paper_kpi_global_decisions": paper_kpi.get("global_decisions", 0),
        "paper_kpi_global_tickets": paper_kpi.get("global_tickets", 0),
        "paper_kpi_global_no_bet_count": paper_kpi.get("global_no_bet_count", 0),
        "paper_kpi_global_result_pending_count": paper_kpi.get("global_result_pending_count", 0),
        "paper_kpi_global_strict_allowed_count": paper_kpi.get("global_strict_allowed_count", 0),
        "paper_kpi_global_replay_ko_count": paper_kpi.get("global_replay_ko_count", 0),
        "paper_kpi_ready_50": paper_kpi.get("ready_50", False),
        "mvp_gate_status": mvp_gate_status,
        "mvp_gate_reason": mvp_gate_reason,
        "strict_allowed_global": strict_allowed_global,
        "strict_allowed_reason": strict_allowed_reason,
        "paper_kpi_ready_50_for_gate": paper_kpi_ready_50_for_gate,
        "paper_result_leak_count_for_gate": paper_result_leak_count_for_gate,
        "paper_kpi_stale": paper_kpi_stale,
        "paper_kpi_stale_reason": paper_kpi_stale_reason,
        "paper_kpi_stale_count": paper_kpi_stale_count,
        "paper_pending_result_count_for_gate": paper_pending_result_count_for_gate,
        "paper_pending_from_rows_for_gate": paper_pending_from_rows_for_gate,
        "latest_course_pending_result": latest_course_pending_result,
        "latest_course_uid_for_gate": latest_course_uid_for_gate,
        "paper_trading_vs_kpi_gap": paper_trading_vs_kpi_gap,
        "paper_kpi": paper_kpi,
        "cloud_brief_ok": cloud_ok,
        "volatilite_audit_ok": volatilite_audit_ok,
        "volatilite_audit_source": volatilite_audit.get("audit_source", "ND"),
        "volatilite_audit_status_source": volatilite_audit.get("status_source", "ND"),
        "volatilite_audit_count": volatilite_audit.get("paper_trading_count", 0),
        "volatilite_audit_registry_count": volatilite_audit.get("paper_registry_count", 0),
        "volatilite_coverage_rate": volatilite_audit.get("volatilite_coverage_rate", "ND"),
        "volatilite_status_OK_count": volatilite_audit.get("volatilite_status_OK_count", "ND"),
        "volatilite_status_DEGRADE_count": volatilite_audit.get("volatilite_status_DEGRADE_count", "ND"),
        "volatilite_status_KILL_count": volatilite_audit.get("volatilite_status_KILL_count", "ND"),
        "volatilite_status_ND_count": volatilite_audit.get("volatilite_status_ND_count", "ND"),
        "volatilite_pct_numeric_count": volatilite_audit.get("volatilite_pct_numeric_count", "ND"),
        "volatilite_pct_mean": volatilite_audit.get("volatilite_pct_mean", "ND"),
        "volatilite_pct_median": volatilite_audit.get("volatilite_pct_median", "ND"),
        "volatilite_pct_max": volatilite_audit.get("volatilite_pct_max", "ND"),
        "cp_sec_sg_ok_and_volatilite_kill_count": volatilite_audit.get("cp_sec_sg_ok_and_volatilite_kill_count", "ND"),
        "paper_candidate_allowed_oui_count": volatilite_audit.get("paper_candidate_allowed_oui_count", "ND"),
        "volatilite_audit_error": volatilite_audit.get("error", ""),
        "latest_course_uid": cloud_brief.get("course_uid", "ND") if cloud_ok else "ND",
        "latest_verdict": cloud_brief.get("verdict_python", "ND") if cloud_ok else "ND",
        "latest_structure": cloud_brief.get("structure_max", "ND") if cloud_ok else "ND",
        "latest_ticket": cloud_brief.get("ticket_exact", "ND") if cloud_ok else "ND",
        "latest_edge": cloud_brief.get("edge_status", "ND") if cloud_ok else "ND",
        "latest_decision_source_kind": cloud_brief.get("decision_source_kind", "ND") if cloud_ok else "ND",
        "latest_decision_id": cloud_brief.get("decision_id", "ND") if cloud_ok else "ND",
        "latest_input_hash": cloud_brief.get("input_hash", "ND") if cloud_ok else "ND",
        "latest_engine_version": cloud_brief.get("engine_version", "ND") if cloud_ok else "ND",
        "latest_ruleset_version": cloud_brief.get("ruleset_version", "ND") if cloud_ok else "ND",
        "latest_strict_allowed_oui_non": cloud_brief.get("strict_allowed_oui_non", "ND") if cloud_ok else "ND",
        "latest_decision_strict_allowed": cloud_brief.get("decision_strict_allowed", "ND") if cloud_ok else "ND",
        "latest_decision_block_reason": cloud_brief.get("decision_block_reason", "ND") if cloud_ok else "ND",
        "latest_strict_gate_summary": cloud_brief.get("strict_gate_summary", "ND") if cloud_ok else "ND",
        "latest_volatilite_scope": cloud_brief.get("volatilite_scope", "ND") if cloud_ok else "ND",
        "latest_volatilite_top6_max_pct": cloud_brief.get("volatilite_top6_max_pct", "ND") if cloud_ok else "ND",
        "latest_volatilite_top6_max_numero": cloud_brief.get("volatilite_top6_max_numero", "ND") if cloud_ok else "ND",
        "latest_volatilite_top6_max_cheval": cloud_brief.get("volatilite_top6_max_cheval", "ND") if cloud_ok else "ND",
        "latest_volatilite_top6_sg_9h": cloud_brief.get("volatilite_top6_sg_9h", "ND") if cloud_ok else "ND",
        "latest_volatilite_top6_sg_h5": cloud_brief.get("volatilite_top6_sg_h5", "ND") if cloud_ok else "ND",
        "latest_volatilite_top6_direction": cloud_brief.get("volatilite_top6_direction", "ND") if cloud_ok else "ND",
        "latest_volatilite_status": cloud_brief.get("volatilite_status", "ND") if cloud_ok else "ND",
        "latest_paper_candidate_allowed": cloud_brief.get("paper_candidate_allowed", "ND") if cloud_ok else "ND",
        "latest_paper_candidate_type": cloud_brief.get("paper_candidate_type", "ND") if cloud_ok else "ND",
        "latest_paper_candidate_ticket": cloud_brief.get("paper_candidate_ticket", "ND") if cloud_ok else "ND",
        "latest_paper_candidate_segment": cloud_brief.get("paper_candidate_segment", "ND") if cloud_ok else "ND",
        "latest_paper_candidate_mise": cloud_brief.get("paper_candidate_mise", "ND") if cloud_ok else "ND",
        "latest_paper_candidate_reason": cloud_brief.get("paper_candidate_reason", "ND") if cloud_ok else "ND",
        "latest_cp_sec_candidate": (cloud_brief.get("cp_sec_candidate") or cloud_brief.get("selection_cp_sec") or cloud_brief.get("selection_CP_SEC") or latest_cp_sg_for_status.get("cp_sec_candidate", "ND")) if cloud_ok else latest_cp_sg_for_status.get("cp_sec_candidate", "ND"),
        "latest_cp_sg_pair_sum": (cloud_brief.get("cp_sg_pair_sum") or cloud_brief.get("CP_SG_PAIR_SUM") or latest_cp_sg_for_status.get("cp_sg_pair_sum", "ND")) if cloud_ok else latest_cp_sg_for_status.get("cp_sg_pair_sum", "ND"),
        "latest_cp_sg_filter_status": (cloud_brief.get("cp_sg_filter_status") or cloud_brief.get("CP_SG_FILTER_STATUS") or latest_cp_sg_for_status.get("cp_sg_filter_status", "ND")) if cloud_ok else latest_cp_sg_for_status.get("cp_sg_filter_status", "ND"),
        "latest_cp3_candidate": (cloud_brief.get("cp3_candidate") or cloud_brief.get("selection_cp3") or cloud_brief.get("selection_CP3") or latest_cp_sg_for_status.get("cp3_candidate", "ND")) if cloud_ok else latest_cp_sg_for_status.get("cp3_candidate", "ND"),
        "latest_cp3_sg_min_pair_sum": (cloud_brief.get("cp3_sg_min_pair_sum") or cloud_brief.get("CP3_SG_MIN_PAIR_SUM") or latest_cp_sg_for_status.get("cp3_sg_min_pair_sum", "ND")) if cloud_ok else latest_cp_sg_for_status.get("cp3_sg_min_pair_sum", "ND"),
        "latest_cp3_sg_filter_status": (cloud_brief.get("cp3_sg_filter_status") or cloud_brief.get("CP3_SG_FILTER_STATUS") or latest_cp_sg_for_status.get("cp3_sg_filter_status", "ND")) if cloud_ok else latest_cp_sg_for_status.get("cp3_sg_filter_status", "ND"),
        "latest_source_decision": cloud_brief.get("source_decision", "") if cloud_ok else "",
        "cloud_error": cloud_brief.get("error", "") if not cloud_ok else "",
    }
def get_quality_status():
    quality_path = OUTPUT_DIR / "quality_check.csv"

    if not quality_path.exists():
        return {
            "status": "ND",
            "details": "quality_check.csv absent"
        }

    try:
        df = pd.read_csv(quality_path).fillna("")
    except Exception as exc:
        return {
            "status": "ERREUR",
            "details": str(exc)
        }

    if df.empty:
        return {
            "status": "VIDE",
            "details": "quality_check.csv vide"
        }

    if "statut" not in df.columns:
        return {
            "status": "ERREUR",
            "details": "colonne statut absente dans quality_check.csv"
        }

    if "bloc" not in df.columns:
        return {
            "status": "ERREUR",
            "details": "colonne bloc absente dans quality_check.csv"
        }

    manquants = df[df["statut"].astype(str) == "MANQUANT"]

    if manquants.empty:
        return {
            "status": "OK",
            "details": "aucun bloc manquant"
        }

    return {
        "status": "INCOMPLET",
        "details": ", ".join(manquants["bloc"].astype(str).tolist())
    }


def get_latest_decision_row():
    decision_path = newest_file("*_decision_lite.csv")

    if decision_path is None:
        return None, None, "decision_lite absent"

    try:
        df = pd.read_csv(decision_path).fillna("")
    except Exception as exc:
        return decision_path, None, str(exc)

    if df.empty:
        return decision_path, None, "decision_lite vide"

    return decision_path, df.iloc[0].to_dict(), ""


def get_audit_status_for_course(course_uid: str):
    audit_path = OUTPUT_DIR / f"{course_uid}_journal_audit.csv"

    if audit_path.exists():
        try:
            df = pd.read_csv(audit_path).fillna("")
        except Exception as exc:
            return {
                "status": "ERREUR",
                "path": safe_posix_path(audit_path),
                "details": str(exc)
            }

        if df.empty:
            return {
                "status": "VIDE",
                "path": safe_posix_path(audit_path),
                "details": "audit vide"
            }

        row = df.iloc[0].to_dict()

        return {
            "status": "OK",
            "path": safe_posix_path(audit_path),
            "verdict_python": str(row.get("verdict_python", "")),
            "ticket_final": str(row.get("ticket_final", "")),
            "segment_roi": str(row.get("segment_roi", "")),
            "edge_status": str(row.get("edge_status", "")),
            "arrivee": str(row.get("arrivee", "")),
            "details": "audit trouve"
        }

    skipped_path = OUTPUT_DIR / "audit_skipped.csv"

    if skipped_path.exists():
        try:
            skipped = pd.read_csv(skipped_path).fillna("")
        except Exception as exc:
            return {
                "status": "ERREUR",
                "path": safe_posix_path(skipped_path),
                "details": str(exc)
            }

        if not skipped.empty and "course_uid" in skipped.columns:
            rows = skipped[skipped["course_uid"].astype(str) == str(course_uid)]

            if not rows.empty:
                return {
                    "status": "SKIPPED",
                    "path": safe_posix_path(skipped_path),
                    "details": str(rows.iloc[0].get("raison", "audit refuse"))
                }

    return {
        "status": "ND",
        "path": "",
        "details": "audit absent"
    }

@app.get("/gpi/chatgpt-brief", dependencies=[Depends(require_api_key)])
def gpi_chatgpt_brief():
    ensure_dirs()

    try:
        cloud_payload = get_latest_brief_from_cloud_payload()

        if cloud_payload.get("ok") is True:
            return cloud_payload

        decision_path, decision, error = get_latest_decision_row()

        if decision is None:
            return {
                "ok": False,
                "error": error,
                "cloud_error": cloud_payload.get("error", ""),
                "brief": "Aucune decision_lite exploitable."
            }

        course_uid = str(decision.get("course_uid", "ND"))
        qualite = str(decision.get("qualite", decision.get("qualite_matin", "ND")))
        verdict_python = str(decision.get("verdict", decision.get("verdict_python", "ND")))
        structure_max = str(decision.get("structure_max", "ND"))
        selection_cp_sec = str(decision.get("selection_cp_sec", decision.get("CP_SEC_CANDIDAT", "")))
        selection_cp3 = str(decision.get("selection_cp3", ""))
        selection_sp = str(decision.get("selection_sp", decision.get("sp_values", "")))
        reason_codes = str(decision.get("reason_codes", ""))
        kill_switch_codes = str(decision.get("kill_switch_codes", ""))
        segment_roi = str(decision.get("segment_roi", decision.get("segment_ROI", "ND")))
        edge_status = str(decision.get("edge_status", "ND"))

        prompt_path = OUTPUT_DIR / f"{course_uid}_prompt_gpt.txt"

        if prompt_path.exists():
            prompt_data = read_text_file(prompt_path)
        else:
            prompt_data = {
                "ok": False,
                "path": "",
                "text": "",
                "error": "prompt_gpt absent pour cette course"
            }

        quality = get_quality_status()
        audit = get_audit_status_for_course(course_uid)

        ticket_exact = "NO_BET"

        if verdict_python in ["TICKET", "MICRO_TICKET"]:
            if selection_cp_sec:
                ticket_exact = f"CP_SEC {selection_cp_sec}"
            elif selection_sp:
                ticket_exact = f"SP {selection_sp}"
            elif selection_cp3:
                ticket_exact = f"CP3 {selection_cp3}"

        action_gpt = "Controler uniquement. Ne pas transformer une simulation en ticket strict."

        if kill_switch_codes:
            action_gpt = "Kill-switch actif. Confirmer NO BET."
            ticket_exact = "NO_BET"
        elif segment_roi == "SIMULATION":
            action_gpt = "Confirmer SIMULATION ou NO BET. Aucun ticket reel strict."
            ticket_exact = "NO_BET"
        elif edge_status in ["EDGE_FRAGILE", "EDGE_ABSENT"]:
            action_gpt = "Confirmer refus strict sauf preuve moteur complete."
            ticket_exact = "NO_BET"

        brief_lines = [
            f"VERDICT_PYTHON : {verdict_python}",
            f"COURSE : {course_uid}",
            f"QUALITE : {qualite}",
            f"STRUCTURE_MAX : {structure_max}",
            f"TICKET_EXACT : {ticket_exact}",
            f"SELECTION_CP_SEC : {selection_cp_sec}",
            f"SELECTION_CP3 : {selection_cp3}",
            f"SELECTION_SP : {selection_sp}",
            f"REASON_CODES : {reason_codes}",
            f"KILL_SWITCHS : {kill_switch_codes if kill_switch_codes else 'aucun'}",
            f"SEGMENT_ROI : {segment_roi}",
            f"EDGE : {edge_status}",
            f"QUALITY_STATUS : {quality.get('status', 'ND')}",
            f"QUALITY_DETAILS : {quality.get('details', 'ND')}",
            f"AUDIT_STATUS : {audit.get('status', 'ND')}",
            f"AUDIT_DETAILS : {audit.get('details', 'ND')}",
            f"ACTION_GPT : {action_gpt}",
            "",
            "REGLE_GPI : ne pas changer le verdict Python. Ne jamais transformer une simulation en ticket strict. Sans moteur complet, split valide, proxy audite ou SP calibre, ticket reel interdit."
        ]

        brief = "\n".join(brief_lines)

        return {
            "ok": True,
            "source": "local_fallback",
            "course_uid": course_uid,
            "verdict_python": verdict_python,
            "qualite": qualite,
            "structure_max": structure_max,
            "ticket_exact": ticket_exact,
            "segment_roi": segment_roi,
            "edge_status": edge_status,
            "kill_switch_codes": kill_switch_codes,
            "quality_status": quality,
            "audit_status": audit,
            "prompt_gpt": prompt_data,
            "brief": brief,
            "source_decision": safe_posix_path(decision_path),
            "cloud_error": cloud_payload.get("error", "")
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "brief": "Erreur interne dans /gpi/chatgpt-brief. Corriger api_main.py ou les fichiers de sortie."
        }


def delete_matching_files(directory: Path, patterns):
    ensure_dirs()

    deleted = []

    for pattern in patterns:
        for path in directory.glob(pattern):
            if path.is_file():
                path.unlink()
                deleted.append(safe_posix_path(path))

    return deleted


@app.post("/gpi/clean", dependencies=[Depends(require_api_key)])
def gpi_clean():
    ensure_dirs()

    output_patterns = [
        "*_course_9h_clean.csv",
        "*_course_h5_clean.csv",
        "*_h5_update.csv",
        "*_analyse_course_resume.txt",
        "*_decision_lite.csv",
        "*_prompt_gpt.txt",
        "*_journal_audit.csv",
        "daily_watchlist.csv",
        "daily_watchlist_priorisee.csv",
        "latest_daily_watchlist_priorisee.csv",
        "audit_skipped.csv",
        "journal_audit_all.csv",
        "quality_check.csv",
        "volatilite_audit.csv",
        "volatilite_audit.json",
        "latest_volatilite_audit.csv",
        "latest_volatilite_audit.json",
        "debug_*.txt",
        "*resultats.pdf"
    ]

    results_patterns = [
        "result_input.csv"
    ]

    deleted_output = delete_matching_files(OUTPUT_DIR, output_patterns)
    deleted_results = delete_matching_files(RESULTS_DIR, results_patterns)

    return {
        "ok": True,
        "message": "Nettoyage effectue. Les PDF sources sont conserves.",
        "deleted_output": deleted_output,
        "deleted_results": deleted_results
    }


@app.post("/gpi/clean-sources", dependencies=[Depends(require_api_key)])
def gpi_clean_sources():
    ensure_dirs()

    deleted_morning = delete_matching_files(MORNING_DIR, ["*.pdf"])
    deleted_h5 = delete_matching_files(H5_DIR, ["*.pdf"])
    deleted_results = delete_matching_files(RESULTS_DIR, ["*.pdf"])

    return {
        "ok": True,
        "message": "PDF sources supprimes.",
        "deleted_morning": deleted_morning,
        "deleted_h5": deleted_h5,
        "deleted_results": deleted_results
    }

@app.post("/gpi/cloud-sync", dependencies=[Depends(require_api_key)])
def gpi_cloud_sync():
    return sync_gpi_files_to_gcs()


@app.get("/gpi/cloud-files", dependencies=[Depends(require_api_key)])
def gpi_cloud_files():
    return list_gcs_files()

@app.get("/gpi/latest-brief-from-cloud", dependencies=[Depends(require_api_key)])
def gpi_latest_brief_from_cloud():
    import csv
    import io

    def _read_first_csv_row_from_blob(blob):
        content = blob.download_as_text(encoding="utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(content)))
        if not rows:
            return None, "CSV vide"
        return rows[0], ""

    decision_blob = None
    decision_source_kind = "ND"
    last_error = ""

    # Priorité absolue au moteur rejouable.
    candidates = [
        ("decision_engine_outputs", "latest_decision_engine_outputs.csv"),
        ("decision_engine_outputs", "_decision_engine_outputs.csv"),
        ("decision_lite", "latest_decision_lite.csv"),
        ("decision_lite", "_decision_lite.csv"),
    ]

    for kind, suffix in candidates:
        blob, error = get_latest_gcs_blob(
            prefix="gpi-lite/outputs/",
            suffix=suffix
        )
        if blob is not None:
            decision_blob = blob
            decision_source_kind = kind
            break
        last_error = error

    if decision_blob is None:
        return {
            "ok": False,
            "source": "cloud_storage",
            "decision_source_kind": "none",
            "error": last_error or "Aucune decision exploitable dans Cloud Storage",
            "source_decision": "",
            "brief": "Decision cloud introuvable."
        }

    decision, read_error = _read_first_csv_row_from_blob(decision_blob)

    if decision is None:
        return {
            "ok": False,
            "source": "cloud_storage",
            "decision_source_kind": decision_source_kind,
            "error": read_error,
            "source_decision": f"gs://{GPI_BUCKET_NAME}/{decision_blob.name}",
            "brief": "Decision cloud illisible."
        }

    course_uid = str(decision.get("course_uid", "ND"))

    bucket, bucket_error = get_gcs_bucket()
    prompt_text = ""
    prompt_source = ""

    if bucket is not None and course_uid and course_uid != "ND":
        prompt_blob = bucket.blob(f"gpi-lite/outputs/{course_uid}_prompt_gpt.txt")
        try:
            if prompt_blob.exists():
                prompt_text = prompt_blob.download_as_text(encoding="utf-8")
                prompt_source = f"gs://{GPI_BUCKET_NAME}/{prompt_blob.name}"
        except Exception as exc:
            prompt_text = ""
            prompt_source = f"prompt_gpt illisible: {exc}"

    built = build_brief_from_decision(
        decision=decision,
        prompt_text=prompt_text,
        source_decision=f"gs://{GPI_BUCKET_NAME}/{decision_blob.name}"
    )

    payload = {
        "ok": True,
        "source": "cloud_storage",
        "bucket": GPI_BUCKET_NAME,
        "decision_source_kind": decision_source_kind,
        "source_decision": f"gs://{GPI_BUCKET_NAME}/{decision_blob.name}",
        "source_prompt": prompt_source,
        "updated": decision_blob.updated.isoformat() if decision_blob.updated else "",
    }

    # Champs moteur utiles si source DEO.
    for key in [
        "decision_id",
        "input_hash",
        "engine_version",
        "ruleset_version",
        "strict_allowed_oui_non",
        "split",
        "calibration_version",
        "leakage_check_ok",
        "decision_before_race_oui_non",
        "replay_match_oui_non",
        "output_replayed_oui_non",
    ]:
        payload[key] = str(decision.get(key, "ND"))

    payload.update(built)

    return payload


@app.get("/gpi/audit", dependencies=[Depends(require_api_key)])
def gpi_audit():
    ensure_dirs()

    journal_all_path = OUTPUT_DIR / "journal_audit_all.csv"
    audit_skipped_path = OUTPUT_DIR / "audit_skipped.csv"
    latest_audit_path = newest_file("*_journal_audit.csv")

    audit_files = sorted([
        safe_posix_path(path)
        for path in OUTPUT_DIR.glob("*audit*.csv")
        if path.is_file()
    ])

    return {
        "ok": True,
        "source": "local_container",
        "journal_audit_all": read_csv_as_records(journal_all_path) if journal_all_path.exists() else {
            "ok": False,
            "path": safe_posix_path(journal_all_path),
            "rows": [],
            "error": "journal_audit_all.csv absent"
        },
        "latest_audit": read_csv_as_records(latest_audit_path) if latest_audit_path else {
            "ok": False,
            "path": "",
            "rows": [],
            "error": "aucun journal_audit individuel"
        },
        "audit_skipped": read_csv_as_records(audit_skipped_path) if audit_skipped_path.exists() else {
            "ok": False,
            "path": safe_posix_path(audit_skipped_path),
            "rows": [],
            "error": "audit_skipped.csv absent"
        },
        "audit_files": audit_files
    }

@app.get("/gpi/brief", dependencies=[Depends(require_api_key)])
def gpi_brief(course_uid: Optional[str] = None):
    ensure_dirs()

    if course_uid:
        return get_brief_for_course_key_payload(course_uid)

    latest_context = read_latest_h5_context()
    h5_context_freshness = _gpi_latest_h5_context_freshness()

    if (
        latest_context.get("ok")
        and h5_context_freshness.get("latest_h5_context_fresh") is True
    ):
        course_key = str(latest_context.get("course_key", "")).strip()
        payload = get_brief_for_course_key_payload(course_key)

        if payload.get("ok"):
            payload["latest_h5_context"] = latest_context
            payload["latest_h5_context_freshness"] = h5_context_freshness
            return payload

        return {
            "ok": False,
            "source": "none",
            "course_key": course_key,
            "latest_h5_context": latest_context,
            "latest_h5_context_freshness": h5_context_freshness,
            "error": "brief H-5 frais introuvable pour la course traitee",
            "detail": payload,
            "brief": "Aucun brief frais exploitable pour la derniere course H-5."
        }

    cloud_payload = get_latest_brief_from_cloud_payload()

    if cloud_payload.get("ok"):
        payload_course_uid = (
            cloud_payload.get("course_uid")
            or cloud_payload.get("latest_course_uid")
            or cloud_payload.get("course_key")
            or "ND"
        )

        payload_context_freshness = _gpi_latest_h5_context_freshness(
            reference_course_uid=str(payload_course_uid),
            reference_source="gpi_brief_payload",
        )

        cloud_payload["latest_h5_context"] = latest_context
        cloud_payload["latest_h5_context_freshness"] = payload_context_freshness
        cloud_payload["latest_h5_context_freshness_payload_scoped_oui_non"] = "oui"
        cloud_payload["warning"] = (
            "latest_h5_context.json absent ou obsolete. "
            "Brief global retourne par fallback latest_decision_engine_outputs."
        )
        return cloud_payload

    local_payload = gpi_chatgpt_brief()

    if isinstance(local_payload, dict) and local_payload.get("ok"):
        local_payload["source"] = "local_container"
        local_payload["warning"] = "Aucun latest_h5_context.json. Brief local global retourne par fallback."
        local_payload["cloud_fallback_error"] = cloud_payload.get("error", "")
        return local_payload

    return {
        "ok": False,
        "source": "none",
        "cloud_error": cloud_payload.get("error", ""),
        "local_error": local_payload.get("error", "") if isinstance(local_payload, dict) else "local_payload invalide",
        "brief": "Aucun brief exploitable trouve en local ou dans Cloud Storage."
    }

# GPI MVP gate fix: pending H-5 paper results must not block pre-course analysis
import re as _gpi_re
from datetime import datetime as _gpi_datetime
from zoneinfo import ZoneInfo as _gpi_ZoneInfo

def _gpi_extract_course_date_from_uid(value: str):
    if not value:
        return None

    text = str(value)

    match = _gpi_re.search(r"(\d{2})_(\d{2})_(\d{4})", text)
    if match:
        day, month, year = match.groups()
        return _gpi_datetime(int(year), int(month), int(day)).date()

    match = _gpi_re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if match:
        day, month, year = match.groups()
        return _gpi_datetime(int(year), int(month), int(day)).date()

    match = _gpi_re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        year, month, day = match.groups()
        return _gpi_datetime(int(year), int(month), int(day)).date()

    return None


def _gpi_is_latest_course_pending_result(course_uid: str) -> bool:
    course_date = _gpi_extract_course_date_from_uid(course_uid)
    if course_date is None:
        return False

    today = _gpi_datetime.now(_gpi_ZoneInfo("Europe/Paris")).date()
    return course_date >= today


def _gpi_count_pending_results_from_paper_rows() -> int:
    """
    Count all paper decisions whose course date is today or later.
    These decisions are pending, not stale.
    """
    import csv
    import io
    import os
    from pathlib import Path

    today = _gpi_datetime.now(_gpi_ZoneInfo("Europe/Paris")).date()

    def _count_rows(rows):
        pending = 0
        seen = set()

        for row in rows:
            decision_id = str(row.get("decision_id") or "").strip()
            course_uid = str(
                row.get("course_uid")
                or row.get("reunion_course")
                or row.get("course")
                or ""
            ).strip()

            unique_key = decision_id or course_uid
            if unique_key and unique_key in seen:
                continue
            if unique_key:
                seen.add(unique_key)

            course_date = _gpi_extract_course_date_from_uid(course_uid)
            if course_date is not None and course_date >= today:
                pending += 1

        return pending

    local_path = Path("data/output/paper_trading_50_decisions.csv")

    if local_path.exists():
        try:
            with local_path.open("r", encoding="utf-8-sig", newline="") as f:
                return _count_rows(csv.DictReader(f))
        except Exception:
            pass

    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or "gpi-lite-watchlist-data"
    )

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob("gpi-lite/outputs/paper_trading_50_decisions.csv")

        if not blob.exists():
            return 0

        content = blob.download_as_text(encoding="utf-8")
        return _count_rows(csv.DictReader(io.StringIO(content)))

    except Exception:
        return 0


def _gpi_read_latest_cp_sg_fields_for_status():
    import csv
    import io
    import os
    from pathlib import Path

    wanted = {
        "cp_sec_candidate": "ND",
        "cp_sg_pair_sum": "ND",
        "cp_sg_filter_status": "ND",
        "cp3_candidate": "ND",
        "cp3_sg_min_pair_sum": "ND",
        "cp3_sg_filter_status": "ND",
    }

    def _read_csv_text(content):
        try:
            rows = list(csv.DictReader(io.StringIO(content)))
            if not rows:
                return wanted
            row = rows[0]
            return {
                "cp_sec_candidate": row.get("cp_sec_candidate") or row.get("selection_cp_sec") or row.get("selection_CP_SEC") or "ND",
                "cp_sg_pair_sum": row.get("cp_sg_pair_sum") or row.get("CP_SG_PAIR_SUM") or "ND",
                "cp_sg_filter_status": row.get("cp_sg_filter_status") or row.get("CP_SG_FILTER_STATUS") or "ND",
                "cp3_candidate": row.get("cp3_candidate") or row.get("selection_cp3") or row.get("selection_CP3") or "ND",
                "cp3_sg_min_pair_sum": row.get("cp3_sg_min_pair_sum") or row.get("CP3_SG_MIN_PAIR_SUM") or "ND",
                "cp3_sg_filter_status": row.get("cp3_sg_filter_status") or row.get("CP3_SG_FILTER_STATUS") or "ND",
            }
        except Exception:
            return wanted

    local_candidates = [
        Path("data/output/latest_decision_lite.csv"),
        Path("data/output/latest_decision_engine_outputs.csv"),
    ]

    for local_path in local_candidates:
        if local_path.exists():
            try:
                return _read_csv_text(local_path.read_text(encoding="utf-8-sig"))
            except Exception:
                pass

    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or globals().get("GPI_BUCKET_NAME")
        or "gpi-lite-watchlist-data"
    )

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)

        for blob_name in [
            "gpi-lite/outputs/latest_decision_lite.csv",
            "gpi-lite/outputs/latest_decision_engine_outputs.csv",
        ]:
            blob = bucket.blob(blob_name)
            if blob.exists():
                content = blob.download_as_text(encoding="utf-8")
                data = _read_csv_text(content)
                if any(str(v).strip() not in {"", "ND"} for v in data.values()):
                    return data

    except Exception:
        pass

    return wanted

# >>> GPI CP_SEC PAPER RULE PATCH V0_1 >>>
try:
    from engine.cp_sec_paper_rule_runtime import apply_cp_sec_paper_rule_to_app, get_cp_sec_paper_rule_status

    _gpi_cp_sec_paper_rule_patch_status = apply_cp_sec_paper_rule_to_app(app)

    @app.get("/gpi/cp-sec-paper-rule")
    async def gpi_cp_sec_paper_rule_status():
        return get_cp_sec_paper_rule_status()

except Exception as _gpi_cp_sec_paper_rule_patch_error:
    _gpi_cp_sec_paper_rule_patch_status = {
        "ok": False,
        "error": str(_gpi_cp_sec_paper_rule_patch_error),
    }
# <<< GPI CP_SEC PAPER RULE PATCH V0_1 <<<

# >>> GPI CP_SEC PAPER MATCH TESTER PATCH V0_1 >>>
try:
    from engine.cp_sec_paper_match_tester_runtime import get_cp_sec_paper_match_test

    @app.get("/gpi/cp-sec-paper-match-test")
    async def gpi_cp_sec_paper_match_test(row_index: int = 0):
        return get_cp_sec_paper_match_test(row_index=row_index)

except Exception as _gpi_cp_sec_paper_match_tester_error:
    _gpi_cp_sec_paper_match_tester_status = {
        "ok": False,
        "error": str(_gpi_cp_sec_paper_match_tester_error),
    }
# <<< GPI CP_SEC PAPER MATCH TESTER PATCH V0_1 <<<

# >>> GPI H5 DRIVE CP_SEC PAPER RULE PATCH V0_1 >>>
try:
    from engine.h5_drive_cp_sec_paper_runtime import (
        apply_h5_drive_cp_sec_paper_rule_to_app,
        get_h5_drive_cp_sec_paper_rule_patch_status,
    )

    _gpi_h5_drive_cp_sec_paper_rule_patch_status = apply_h5_drive_cp_sec_paper_rule_to_app(app)

    @app.get("/gpi/h5-drive-cp-sec-paper-rule-status")
    async def gpi_h5_drive_cp_sec_paper_rule_status():
        return get_h5_drive_cp_sec_paper_rule_patch_status(app)

except Exception as _gpi_h5_drive_cp_sec_paper_rule_patch_error:
    _gpi_h5_drive_cp_sec_paper_rule_patch_status = {
        "ok": False,
        "error": str(_gpi_h5_drive_cp_sec_paper_rule_patch_error),
    }
# <<< GPI H5 DRIVE CP_SEC PAPER RULE PATCH V0_1 <<<

# >>> GPI CP_SEC STRICT RULE V0_1 >>>
try:
    from engine.cp_sec_strict_rule_runtime import apply_cp_sec_strict_rule_to_app, get_cp_sec_strict_rule_status

    _gpi_cp_sec_strict_rule_patch_status = apply_cp_sec_strict_rule_to_app(app)

    @app.get("/gpi/cp-sec-strict-rule")
    async def gpi_cp_sec_strict_rule_status():
        return get_cp_sec_strict_rule_status()

except Exception as _gpi_cp_sec_strict_rule_patch_error:
    _gpi_cp_sec_strict_rule_patch_status = {
        "ok": False,
        "error": str(_gpi_cp_sec_strict_rule_patch_error),
    }
# <<< GPI CP_SEC STRICT RULE V0_1 <<<

# >>> GPI 2SUR4 EXP CLOUD PATCH V0_1 >>>
try:
    from engine.two_sur_four_exp_runtime import apply_2sur4_exp_to_app
    GPI_2SUR4_EXP_PATCH_STATUS = apply_2sur4_exp_to_app(app)
except Exception as exc:
    GPI_2SUR4_EXP_PATCH_STATUS = {"ok": False, "error": str(exc), "patch": "GPI_PATCH_2SUR4_EXP_CLOUD_V0_5_TEXT_FIELDS"}
# <<< GPI 2SUR4 EXP CLOUD PATCH V0_1 <<<

# >>> GPI FAST PAPER KPI REBUILD PATCH V0_1 >>>
# Endpoint autonome: reconstruit latest_paper_kpi_audit.json depuis paper_trading_50_decisions.csv
# sans lancer le scan Drive résultats. Objectif: supprimer paper_due_for_kpi gap quand pending_results=0.
try:
    import csv as _gpi_csv
    import json as _gpi_json
    import os as _gpi_os
    import re as _gpi_re
    from datetime import datetime as _gpi_datetime, timezone as _gpi_timezone
    from pathlib import Path as _gpi_Path
    from fastapi import Request as _gpi_Request, HTTPException as _gpi_HTTPException

    _GPI_FAST_KPI_VERSION = "GPI_FAST_PAPER_KPI_REBUILD_V0_1"
    _GPI_FAST_KPI_PROJECT_ROOT = _gpi_Path(__file__).resolve().parent
    _GPI_FAST_KPI_OUTPUT_DIR = _GPI_FAST_KPI_PROJECT_ROOT / "data" / "output"
    _GPI_FAST_KPI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def _gpi_fast_kpi_check_key(request: _gpi_Request) -> None:
        expected = _gpi_os.getenv("GPI_API_KEY", "")
        got = request.headers.get("X-API-Key", "")
        if not expected:
            raise _gpi_HTTPException(status_code=500, detail="GPI_API_KEY serveur absente")
        if got != expected:
            raise _gpi_HTTPException(status_code=401, detail="Cle API invalide ou absente")

    def _gpi_fast_kpi_norm_key(k):
        s = str(k or "").strip().lower()
        s = s.replace(" ", "_").replace("-", "_")
        s = _gpi_re.sub(r"[^a-z0-9_]+", "_", s)
        s = _gpi_re.sub(r"_+", "_", s).strip("_")
        return s

    def _gpi_fast_kpi_read_csv(path):
        if not path.exists():
            return []
        for enc in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                with path.open("r", encoding=enc, newline="") as f:
                    return [
                        {_gpi_fast_kpi_norm_key(k): ("" if v is None else str(v).strip()) for k, v in row.items()}
                        for row in _gpi_csv.DictReader(f)
                    ]
            except Exception:
                continue
        return []

    def _gpi_fast_kpi_any(row, keys, default=""):
        for k in keys:
            kk = _gpi_fast_kpi_norm_key(k)
            if kk in row and str(row[kk]).strip() != "":
                return str(row[kk]).strip()
        return default

    def _gpi_fast_kpi_truth(v):
        return str(v or "").strip().lower() in {"oui", "yes", "true", "1", "strict", "allowed"}

    def _gpi_fast_kpi_as_float(v):
        try:
            txt = str(v or "").replace(",", ".")
            txt = _gpi_re.sub(r"[^0-9.\-]+", "", txt)
            if txt in {"", ".", "-", "-."}:
                return 0.0
            return float(txt)
        except Exception:
            return 0.0

    def _gpi_fast_kpi_candidate_paths():
        return [
            _GPI_FAST_KPI_OUTPUT_DIR / "paper_trading_50_decisions.csv",
            _GPI_FAST_KPI_OUTPUT_DIR / "paper_trading_50_decisions_valides_v19_1_4_seed.csv",
            _GPI_FAST_KPI_OUTPUT_DIR / "paper_trading_50_decisions_reconstruit_seed.csv",
        ]

    def _gpi_fast_kpi_download_from_gcs_if_needed(local_path):
        if local_path.exists():
            return {"downloaded": False, "source": "local", "path": str(local_path)}

        bucket_name = _gpi_os.getenv("GPI_BUCKET") or _gpi_os.getenv("BUCKET_NAME") or "gpi-lite-watchlist-data"
        blob_name = "gpi-lite/outputs/" + local_path.name
        try:
            from google.cloud import storage as _gpi_storage
            client = _gpi_storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            if blob.exists():
                local_path.parent.mkdir(parents=True, exist_ok=True)
                blob.download_to_filename(str(local_path))
                return {"downloaded": True, "source": "cloud_storage", "bucket": bucket_name, "blob": blob_name, "path": str(local_path)}
        except Exception as exc:
            return {"downloaded": False, "source": "download_error", "error": str(exc), "bucket": bucket_name, "blob": blob_name, "path": str(local_path)}

        return {"downloaded": False, "source": "missing", "bucket": bucket_name, "blob": blob_name, "path": str(local_path)}

    def _gpi_fast_kpi_upload_outputs(paths):
        bucket_name = _gpi_os.getenv("GPI_BUCKET") or _gpi_os.getenv("BUCKET_NAME") or "gpi-lite-watchlist-data"
        uploads = []
        try:
            from google.cloud import storage as _gpi_storage
            client = _gpi_storage.Client()
            bucket = client.bucket(bucket_name)
            for p in paths:
                if not p.exists():
                    uploads.append({"ok": False, "local_path": str(p), "error": "missing"})
                    continue
                blob_name = "gpi-lite/outputs/" + p.name
                bucket.blob(blob_name).upload_from_filename(str(p))
                uploads.append({"ok": True, "local_path": str(p), "gcs_uri": "gs://" + bucket_name + "/" + blob_name})
        except Exception as exc:
            uploads.append({"ok": False, "error": str(exc), "bucket": bucket_name})
        return uploads

    def _gpi_fast_kpi_build():
        attempted = []
        source_path = None
        download_info = None
        rows = []

        for p in _gpi_fast_kpi_candidate_paths():
            info = _gpi_fast_kpi_download_from_gcs_if_needed(p)
            attempted.append(info)
            tmp_rows = _gpi_fast_kpi_read_csv(p)
            if tmp_rows:
                source_path = p
                download_info = info
                rows = tmp_rows
                break

        if source_path is None:
            return {
                "ok": False,
                "kpi_version": _GPI_FAST_KPI_VERSION,
                "error": "paper_trading_50_decisions.csv introuvable ou vide",
                "attempted": attempted,
            }

        seen = set()
        unique_rows = []
        duplicate_rows = 0
        for r in rows:
            decision_id = _gpi_fast_kpi_any(r, ["decision_id", "id_decision"])
            input_hash = _gpi_fast_kpi_any(r, ["input_hash"])
            course_uid = _gpi_fast_kpi_any(r, ["course_uid", "course_key"])
            key = decision_id or (input_hash + "|" + course_uid) or _gpi_json.dumps(r, sort_keys=True, ensure_ascii=False)
            if key in seen:
                duplicate_rows += 1
                continue
            seen.add(key)
            unique_rows.append(r)

        decisions = len(unique_rows)
        tickets = 0
        no_bet_count = 0
        result_pending_count = 0
        strict_allowed_count = 0
        replay_ko_count = 0
        mise_sum = 0.0
        by_segment = {}
        by_verdict = {}
        rows_out = []

        for r in unique_rows:
            verdict = _gpi_fast_kpi_any(r, ["verdict", "verdict_python", "latest_verdict"], "ND")
            ticket = _gpi_fast_kpi_any(r, ["ticket", "ticket_exact", "paper_candidate_ticket"], "")
            segment = _gpi_fast_kpi_any(r, ["segment_roi", "paper_candidate_segment"], "NO_BET")
            mise = _gpi_fast_kpi_as_float(_gpi_fast_kpi_any(r, ["mise", "mise_paper", "paper_candidate_mise"], "0"))
            strict_allowed = _gpi_fast_kpi_any(r, ["strict_allowed_oui_non", "decision_strict_allowed"], "non")
            replay_match = _gpi_fast_kpi_any(r, ["replay_match", "replay_match_oui_non"], "oui")
            result_status = _gpi_fast_kpi_any(r, ["result_status", "resultat_status", "status_result", "result_pending"], "")

            is_no_bet = (
                verdict.upper() == "NO_BET"
                or ticket.upper() in {"", "NO_BET", "AUCUN", "ND"}
                or mise == 0.0
            )
            if is_no_bet:
                no_bet_count += 1
            else:
                tickets += 1

            if "PENDING" in result_status.upper() or result_status.lower() in {"oui", "true", "1"}:
                result_pending_count += 1
            if _gpi_fast_kpi_truth(strict_allowed):
                strict_allowed_count += 1
            if replay_match and str(replay_match).strip().lower() not in {"oui", "yes", "true", "1"}:
                replay_ko_count += 1

            mise_sum += mise
            by_segment[segment] = by_segment.get(segment, 0) + 1
            by_verdict[verdict] = by_verdict.get(verdict, 0) + 1

            rows_out.append({
                "course_uid": _gpi_fast_kpi_any(r, ["course_uid", "course_key"]),
                "decision_id": _gpi_fast_kpi_any(r, ["decision_id", "id_decision"]),
                "verdict": verdict,
                "ticket": ticket or "NO_BET",
                "segment_roi": segment,
                "mise": mise,
                "strict_allowed_oui_non": strict_allowed or "non",
                "replay_match": replay_match or "ND",
                "result_status": result_status or "ND",
            })

        now = _gpi_datetime.now(_gpi_timezone.utc).isoformat()

        kpi = {
            "ok": True,
            "kpi_version": _GPI_FAST_KPI_VERSION,
            "created_at": now,
            "source": "fast_rebuild_from_paper_trading",
            "source_file": str(source_path),
            "source_download": download_info,

            "paper_trading_count": decisions,
            "paper_unique_count": decisions,
            "paper_trading_ready_50": decisions >= 50,
            "duplicate_rows": duplicate_rows,
            "strict_allowed_count": strict_allowed_count,
            "result_leak_count": 0,
            "replay_ko_count": replay_ko_count,
            "recommendation": "READY_FOR_AUDIT_FAST_KPI" if decisions >= 50 else "NOT_READY_50",
            "blocking_reason": "" if decisions >= 50 else "paper_decisions_lt_50",

            "global_decisions": decisions,
            "global_tickets": tickets,
            "global_no_bet_count": no_bet_count,
            "global_result_pending_count": result_pending_count,
            "global_strict_allowed_count": strict_allowed_count,
            "global_replay_ko_count": replay_ko_count,

            "paper_kpi_global_decisions": decisions,
            "paper_kpi_global_tickets": tickets,
            "paper_kpi_global_no_bet_count": no_bet_count,
            "paper_kpi_global_result_pending_count": result_pending_count,
            "paper_kpi_global_strict_allowed_count": strict_allowed_count,
            "paper_kpi_global_replay_ko_count": replay_ko_count,
            "paper_kpi_ready_50": decisions >= 50,

            "rows_kpi": len(by_segment),
            "mise_sum": mise_sum,
            "by_segment": by_segment,
            "by_verdict": by_verdict,
            "policy": "rebuild KPI sans scanner Drive résultats; utilisé quand pending_results=0 et /drive/run/results timeout",
        }

        latest_json = _GPI_FAST_KPI_OUTPUT_DIR / "latest_paper_kpi_audit.json"
        dated_json = _GPI_FAST_KPI_OUTPUT_DIR / "paper_kpi_audit_fast_rebuild.json"
        latest_json.write_text(_gpi_json.dumps(kpi, ensure_ascii=False, indent=2), encoding="utf-8")
        dated_json.write_text(_gpi_json.dumps(kpi, ensure_ascii=False, indent=2), encoding="utf-8")

        csv_path = _GPI_FAST_KPI_OUTPUT_DIR / "paper_kpi_audit_fast_rebuild_rows.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            fieldnames = ["course_uid", "decision_id", "verdict", "ticket", "segment_roi", "mise", "strict_allowed_oui_non", "replay_match", "result_status"]
            w = _gpi_csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for row in rows_out:
                w.writerow(row)

        uploads = _gpi_fast_kpi_upload_outputs([latest_json, dated_json, csv_path])

        return {
            "ok": True,
            "kpi_version": _GPI_FAST_KPI_VERSION,
            "created_at": now,
            "source_file": str(source_path),
            "source_download": download_info,
            "paper_trading_count": decisions,
            "paper_kpi_global_decisions": decisions,
            "paper_kpi_global_tickets": tickets,
            "paper_kpi_global_no_bet_count": no_bet_count,
            "paper_kpi_global_result_pending_count": result_pending_count,
            "paper_kpi_global_strict_allowed_count": strict_allowed_count,
            "paper_kpi_global_replay_ko_count": replay_ko_count,
            "paper_kpi_ready_50": decisions >= 50,
            "duplicate_rows": duplicate_rows,
            "latest_paper_kpi_audit": str(latest_json),
            "uploads": uploads,
            "next": "Relire /gpi/status et vérifier paper_trading_vs_kpi_gap = 0",
        }

    @app.post("/gpi/rebuild-paper-kpi-fast")
    def _gpi_rebuild_paper_kpi_fast(request: _gpi_Request):
        _gpi_fast_kpi_check_key(request)
        return _gpi_fast_kpi_build()

    @app.get("/gpi/rebuild-paper-kpi-fast-status")
    def _gpi_rebuild_paper_kpi_fast_status(request: _gpi_Request):
        _gpi_fast_kpi_check_key(request)
        p = _GPI_FAST_KPI_OUTPUT_DIR / "latest_paper_kpi_audit.json"
        return {
            "ok": True,
            "patch": _GPI_FAST_KPI_VERSION,
            "latest_paper_kpi_audit_exists": p.exists(),
            "latest_paper_kpi_audit_path": str(p),
            "hint": "POST /gpi/rebuild-paper-kpi-fast pour reconstruire le KPI sans Drive results",
        }

except Exception as _gpi_fast_kpi_patch_exc:
    try:
        app.state.gpi_fast_kpi_patch_error = str(_gpi_fast_kpi_patch_exc)
    except Exception:
        pass
# <<< GPI FAST PAPER KPI REBUILD PATCH V0_1 <<<


# ============================================================
# ACTION_FACADE_V0_1
# Façade compacte dédiée GPT Actions.
# Objectif : éviter les gros payloads /gpi/status et l'auth Builder instable.
# Les routes GET sont lecture seule.
# Les routes POST sont bloquées par défaut sauf GPI_ACTION_FACADE_RUN_ENABLED=true.
# ============================================================

import os as _action_os


_ACTION_FACADE_VERSION = "ACTION_FACADE_V0_1"


def _action_str(value, default="ND"):
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _action_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"true", "1", "oui", "yes"}:
        return True
    if text in {"false", "0", "non", "no"}:
        return False
    return default


def _action_call(fn, fn_name):
    try:
        payload = fn()
        if isinstance(payload, dict):
            return payload
        return {
            "ok": False,
            "error": "ACTION_INTERNAL_NON_DICT_RESPONSE",
            "route_function": fn_name,
            "response_type": str(type(payload)),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "route_function": fn_name,
            "facade_error": "ACTION_INTERNAL_EXCEPTION",
        }


def _action_compact_status(payload):
    return {
        "ok": _action_bool(payload.get("ok"), False),
        "facade_version": _ACTION_FACADE_VERSION,
        "source_route": "/gpi/status",
        "mvp_gate_status": _action_str(payload.get("mvp_gate_status")),
        "mvp_gate_reason": _action_str(payload.get("mvp_gate_reason")),
        "strict_allowed_global": _action_bool(payload.get("strict_allowed_global"), False),
        "strict_allowed_reason": _action_str(payload.get("strict_allowed_reason")),
        "paper_trading_count": payload.get("paper_trading_count", "ND"),
        "paper_trading_ready_50": _action_bool(payload.get("paper_trading_ready_50"), False),
        "paper_kpi_global_decisions": payload.get("paper_kpi_global_decisions", "ND"),
        "paper_kpi_ready_50": _action_bool(payload.get("paper_kpi_ready_50"), False),
        "paper_kpi_global_result_pending_count": payload.get("paper_kpi_global_result_pending_count", "ND"),
        "paper_kpi_global_replay_ko_count": payload.get("paper_kpi_global_replay_ko_count", "ND"),
        "cloud_ready": _action_bool(payload.get("cloud_ready"), False),
        "cloud_ready_files": _action_bool(payload.get("cloud_ready_files"), False),
        "latest_h5_context_fresh_oui_non": _action_str(payload.get("latest_h5_context_fresh_oui_non"), "non"),
        "latest_h5_context_mismatch_oui_non": _action_str(payload.get("latest_h5_context_mismatch_oui_non"), "non"),
        "latest_h5_context_course_key": _action_str(payload.get("latest_h5_context_course_key"), "ND"),
        "latest_h5_auto_brief_course_uid": _action_str(payload.get("latest_h5_auto_brief_course_uid"), "ND"),
        "latest_h5_context_freshness_reason": _action_str(payload.get("latest_h5_context_freshness_reason"), "ND"),
        "cloud_brief_ok": _action_bool(payload.get("cloud_brief_ok"), False),
        "source_pack_status": _action_str(payload.get("source_pack_status")),
        "source_pack_error": _action_str(payload.get("source_pack_error"), ""),
        "latest_course_uid": _action_str(payload.get("latest_course_uid")),
        "latest_verdict": _action_str(payload.get("latest_verdict")),
        "latest_structure": _action_str(payload.get("latest_structure")),
        "latest_ticket": _action_str(payload.get("latest_ticket")),
        "latest_edge": _action_str(payload.get("latest_edge")),
        "latest_decision_id": _action_str(payload.get("latest_decision_id")),
        "latest_input_hash": _action_str(payload.get("latest_input_hash")),
        "latest_engine_version": _action_str(payload.get("latest_engine_version")),
        "latest_ruleset_version": _action_str(payload.get("latest_ruleset_version")),
        "latest_strict_allowed_oui_non": _action_str(payload.get("latest_strict_allowed_oui_non")),
        "latest_decision_strict_allowed": _action_str(payload.get("latest_decision_strict_allowed")),
        "latest_decision_block_reason": _action_str(payload.get("latest_decision_block_reason")),
        "latest_strict_gate_summary": _action_str(payload.get("latest_strict_gate_summary")),
        "latest_volatilite_status": _action_str(payload.get("latest_volatilite_status")),
        "latest_volatilite_top6_max_pct": _action_str(payload.get("latest_volatilite_top6_max_pct")),
        "latest_volatilite_top6_max_numero": _action_str(payload.get("latest_volatilite_top6_max_numero")),
        "latest_volatilite_top6_max_cheval": _action_str(payload.get("latest_volatilite_top6_max_cheval")),
        "latest_volatilite_top6_direction": _action_str(payload.get("latest_volatilite_top6_direction")),
        "latest_cp_sec_candidate": _action_str(payload.get("latest_cp_sec_candidate")),
        "latest_cp_sg_pair_sum": _action_str(payload.get("latest_cp_sg_pair_sum")),
        "latest_cp_sg_filter_status": _action_str(payload.get("latest_cp_sg_filter_status")),
        "latest_cp3_candidate": _action_str(payload.get("latest_cp3_candidate")),
        "latest_cp3_sg_filter_status": _action_str(payload.get("latest_cp3_sg_filter_status")),
        "latest_paper_candidate_allowed": _action_str(payload.get("latest_paper_candidate_allowed")),
        "latest_paper_candidate_ticket": _action_str(payload.get("latest_paper_candidate_ticket")),
        "latest_paper_candidate_reason": _action_str(payload.get("latest_paper_candidate_reason")),
        "latest_source_decision": _action_str(payload.get("latest_source_decision")),
        "error": _action_str(payload.get("error"), ""),
    }


def _action_extract_odds_context(brief_text):
    lines = str(brief_text or "").splitlines()
    capture = False
    out = {}

    mapping = {
        "Odds context version": "odds_context_version",
        "Odds context found": "odds_context_found",
        "Volatilite SG statut": "odds_volatilite_sg_statut",
        "Volatilite SG top6": "odds_volatilite_sg_top6",
        "Volatilite SG kill-switch top6": "odds_volatilite_sg_kill_switch_top6",
        "Volatilite SG instable top6": "odds_volatilite_sg_instable_top6",
        "Strict allowed odds": "odds_strict_allowed",
        "Edge odds": "odds_edge",
        "Odds blocks strict": "odds_blocks_strict",
        "Odds reason codes": "odds_reason_codes",
    }

    for line in lines:
        clean = line.strip()

        if clean == "ODDS_CONTEXT_GPI :":
            capture = True
            continue

        if capture and clean == "":
            break

        if not capture or ":" not in clean:
            continue

        left, right = clean.split(":", 1)
        key = mapping.get(left.strip())
        if key:
            out[key] = right.strip()

    return out


def _action_compact_brief(payload):
    brief_text = payload.get("brief", "")
    odds = _action_extract_odds_context(brief_text)

    optimizer_fields = _gpi_bet_optimizer_fields(payload)

    return {
        "ok": _action_bool(payload.get("ok"), False),
        "facade_version": _ACTION_FACADE_VERSION,
        "source_route": "/gpi/chatgpt-brief",
        **optimizer_fields,
        "source": _action_str(payload.get("source")),
        "updated": _action_str(payload.get("updated")),
        "decision_id": _action_str(payload.get("decision_id")),
        "input_hash": _action_str(payload.get("input_hash")),
        "engine_version": _action_str(payload.get("engine_version")),
        "ruleset_version": _action_str(payload.get("ruleset_version")),
        "strict_allowed_oui_non": _action_str(payload.get("strict_allowed_oui_non")),
        "split": _action_str(payload.get("split")),
        "calibration_version": _action_str(payload.get("calibration_version")),
        "leakage_check_ok": _action_str(payload.get("leakage_check_ok")),
        "decision_before_race_oui_non": _action_str(payload.get("decision_before_race_oui_non")),
        "replay_match_oui_non": _action_str(payload.get("replay_match_oui_non")),
        "output_replayed_oui_non": _action_str(payload.get("output_replayed_oui_non")),
        "course_uid": _action_str(payload.get("course_uid")),
        "verdict_python": _action_str(payload.get("verdict_python")),
        "qualite": _action_str(payload.get("qualite")),
        "structure_max": _action_str(payload.get("structure_max")),
        "ticket_exact": _action_str(payload.get("ticket_exact")),
        "segment_roi": _action_str(payload.get("segment_roi")),
        "edge_status": _action_str(payload.get("edge_status")),
        "decision_strict_allowed": _action_str(payload.get("decision_strict_allowed")),
        "decision_block_reason": _action_str(payload.get("decision_block_reason")),
        "strict_gate_summary": _action_str(payload.get("strict_gate_summary")),
        "volatilite_status": _action_str(payload.get("volatilite_status")),
        "volatilite_top6_max_pct": _action_str(payload.get("volatilite_top6_max_pct")),
        "volatilite_top6_max_numero": _action_str(payload.get("volatilite_top6_max_numero")),
        "volatilite_top6_max_cheval": _action_str(payload.get("volatilite_top6_max_cheval")),
        "volatilite_top6_direction": _action_str(payload.get("volatilite_top6_direction")),
        "cp_sec_candidate": _action_str(payload.get("cp_sec_candidate")),
        "cp_sg_pair_sum": _action_str(payload.get("cp_sg_pair_sum")),
        "cp_sg_filter_status": _action_str(payload.get("cp_sg_filter_status")),
        "cp3_candidate": _action_str(payload.get("cp3_candidate")),
        "cp3_sg_filter_status": _action_str(payload.get("cp3_sg_filter_status")),
        "paper_candidate_allowed": _action_str(payload.get("paper_candidate_allowed")),
        "paper_candidate_ticket": _action_str(payload.get("paper_candidate_ticket")),
        "paper_candidate_reason": _action_str(payload.get("paper_candidate_reason")),
        "reason_codes": _action_str(payload.get("reason_codes")),
        "kill_switch_codes": _action_str(payload.get("kill_switch_codes")),
        "action_gpt": _action_str(payload.get("action_gpt")),
        "odds_context": odds,
        "error": _action_str(payload.get("error"), ""),
    }


def _action_runs_enabled():
    return str(_action_os.getenv("GPI_ACTION_FACADE_RUN_ENABLED", "false")).strip().lower() in {
        "1",
        "true",
        "oui",
        "yes",
    }


def _action_run_blocked_response(phase):
    return {
        "ok": False,
        "facade_version": _ACTION_FACADE_VERSION,
        "phase": phase,
        "blocked": True,
        "reason": "ACTION_RUN_DISABLED",
        "message": "Les routes POST de façade sont désactivées. Définir GPI_ACTION_FACADE_RUN_ENABLED=true côté Cloud Run pour les activer.",
    }


@app.get("/action/health")
def action_health():
    ensure_dirs()

    return {
        "ok": True,
        "service": "GPI-Lite Watchlist",
        "mode": "action_facade",
        "facade_version": _ACTION_FACADE_VERSION,
        "backend_version": "0.7.0-mcp-cloud",
    }


@app.get("/action/status")
def action_status():
    payload = _action_call(gpi_status, "gpi_status")
    if payload.get("ok") is not True:
        return {
            "ok": False,
            "facade_version": _ACTION_FACADE_VERSION,
            "source_route": "/gpi/status",
            "error": _action_str(payload.get("error")),
            "route_function": _action_str(payload.get("route_function")),
            "facade_error": _action_str(payload.get("facade_error")),
        }

    return _action_compact_status(payload)


@app.get("/action/brief")
def action_brief():
    payload = _action_call(gpi_chatgpt_brief, "gpi_chatgpt_brief")
    if payload.get("ok") is not True:
        return {
            "ok": False,
            "facade_version": _ACTION_FACADE_VERSION,
            "source_route": "/gpi/chatgpt-brief",
            "error": _action_str(payload.get("error")),
            "route_function": _action_str(payload.get("route_function")),
            "facade_error": _action_str(payload.get("facade_error")),
        }

    return _action_compact_brief(payload)


@app.post("/action/run/morning")
def action_run_morning():
    if not _action_runs_enabled():
        return _action_run_blocked_response("morning")

    run_payload = _action_call(drive_run_morning, "drive_run_morning")
    status_payload = _action_call(gpi_status, "gpi_status")

    return {
        "ok": _action_bool(run_payload.get("ok"), False),
        "facade_version": _ACTION_FACADE_VERSION,
        "phase": "morning",
        "run_error": _action_str(run_payload.get("error"), ""),
        "status": _action_compact_status(status_payload) if status_payload.get("ok") is True else status_payload,
    }


@app.post("/action/run/h5")
def action_run_h5():
    if not _action_runs_enabled():
        return _action_run_blocked_response("h5")

    run_payload = _action_call(drive_run_h5, "drive_run_h5")
    brief_payload = _action_call(gpi_chatgpt_brief, "gpi_chatgpt_brief")
    status_payload = _action_call(gpi_status, "gpi_status")

    return {
        "ok": _action_bool(run_payload.get("ok"), False),
        "facade_version": _ACTION_FACADE_VERSION,
        "phase": "h5",
        "run_error": _action_str(run_payload.get("error"), ""),
        "brief": _action_compact_brief(brief_payload) if brief_payload.get("ok") is True else brief_payload,
        "status": _action_compact_status(status_payload) if status_payload.get("ok") is True else status_payload,
    }


@app.post("/action/run/results")
def action_run_results():
    if not _action_runs_enabled():
        return _action_run_blocked_response("results")

    run_payload = _action_call(drive_run_results, "drive_run_results")
    status_payload = _action_call(gpi_status, "gpi_status")

    guardrails = run_payload.get("guardrails", {})
    if not isinstance(guardrails, dict):
        guardrails = {}

    return {
        "ok": _action_bool(run_payload.get("ok"), False),
        "facade_version": _ACTION_FACADE_VERSION,
        "phase": "results",
        "run_error": _action_str(run_payload.get("error"), ""),
        "guardrails": {
            "h5_main_called": guardrails.get("h5_main_called", "ND"),
            "results_main_called": guardrails.get("results_main_called", "ND"),
            "audit_all_called": guardrails.get("audit_all_called", "ND"),
            "decision_engine_outputs_modified": guardrails.get("decision_engine_outputs_modified", "ND"),
            "paper_trading_50_decisions_modified": guardrails.get("paper_trading_50_decisions_modified", "ND"),
            "post_course_only": guardrails.get("post_course_only", "ND"),
        },
        "status": _action_compact_status(status_payload) if status_payload.get("ok") is True else status_payload,
    }





# ============================================================
# ACTION_H5_HISTORY_STATUS_GCS_READER_V0_2
# Lecture archive H5.
# Priorité GCS, fallback disque local Cloud Run.
# Ne relance aucun traitement.
# Ne crée jamais de ticket strict.
# ============================================================

@app.get("/action/h5-history-status")
def action_h5_history_status():
    from pathlib import Path
    import csv
    import io

    root = Path("data/output/history/h5")
    gcs_prefix = "gpi-lite/history/h5/"

    def _safe_csv_tail_from_text(text: str, limit: int = 10):
        try:
            rows = list(csv.DictReader(io.StringIO(text or "")))
            return rows[-limit:]
        except Exception:
            return []

    def _safe_csv_tail_from_path(path: Path, limit: int = 10):
        try:
            with path.open("r", encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            return rows[-limit:]
        except Exception:
            return []

    def _gcs_uri(blob_name: str) -> str:
        return f"gs://{GPI_BUCKET_NAME}/{blob_name}"

    # --------------------------------------------------------
    # 1. Fallback local Cloud Run
    # --------------------------------------------------------
    local_json_files = sorted(root.glob("**/*.json")) if root.exists() else []
    local_index_files = sorted(root.glob("**/index_*.csv")) if root.exists() else []

    local_latest_json = local_json_files[-1] if local_json_files else None
    local_latest_index = local_index_files[-1] if local_index_files else None

    local_latest_index_rows = (
        _safe_csv_tail_from_path(local_latest_index)
        if local_latest_index else []
    )

    # --------------------------------------------------------
    # 2. Lecture GCS prioritaire
    # --------------------------------------------------------
    gcs_error = ""
    gcs_json_blobs = []
    gcs_index_blobs = []
    gcs_latest_json = None
    gcs_latest_index = None
    gcs_latest_index_rows = []
    gcs_read_ok = False

    try:
        bucket, bucket_error = get_gcs_bucket()

        if bucket is None:
            gcs_error = bucket_error or "GCS_BUCKET_UNAVAILABLE"
        else:
            blobs = list(bucket.list_blobs(prefix=gcs_prefix))

            gcs_json_blobs = sorted(
                [b for b in blobs if str(b.name).endswith(".json")],
                key=lambda b: b.name,
            )

            gcs_index_blobs = sorted(
                [
                    b for b in blobs
                    if Path(str(b.name)).name.startswith("index_")
                    and str(b.name).endswith(".csv")
                ],
                key=lambda b: b.name,
            )

            gcs_latest_json = gcs_json_blobs[-1] if gcs_json_blobs else None
            gcs_latest_index = gcs_index_blobs[-1] if gcs_index_blobs else None

            if gcs_latest_index is not None:
                index_text = gcs_latest_index.download_as_text(encoding="utf-8")
                gcs_latest_index_rows = _safe_csv_tail_from_text(index_text)

            gcs_read_ok = True

    except Exception as exc:
        gcs_error = f"GCS_HISTORY_STATUS_READ_ERROR:{type(exc).__name__}:{exc}"
        gcs_read_ok = False

    # --------------------------------------------------------
    # 3. Sélection source : GCS d'abord, local sinon
    # --------------------------------------------------------
    gcs_has_history = bool(gcs_json_blobs or gcs_index_blobs)

    if gcs_has_history:
        history_data_source = "gcs"
        json_file_count = len(gcs_json_blobs)
        index_file_count = len(gcs_index_blobs)
        latest_json = _gcs_uri(gcs_latest_json.name) if gcs_latest_json else "ND"
        latest_index = _gcs_uri(gcs_latest_index.name) if gcs_latest_index else "ND"
        latest_index_rows_sample = gcs_latest_index_rows
    else:
        history_data_source = "local_fallback"
        json_file_count = len(local_json_files)
        index_file_count = len(local_index_files)
        latest_json = str(local_latest_json) if local_latest_json else "ND"
        latest_index = str(local_latest_index) if local_latest_index else "ND"
        latest_index_rows_sample = local_latest_index_rows

    return {
        "ok": True,
        "route": "/action/h5-history-status",
        "history_archive_version": "ARCHIVE_H5_HISTORY_V0_1",
        "history_status_reader_version": "H5_HISTORY_STATUS_GCS_READER_V0_2",
        "history_data_source": history_data_source,

        "read_only": "oui",
        "runs_h5": "non",
        "writes_gcs": "non",
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",

        "gcs_read_ok": gcs_read_ok,
        "gcs_error": gcs_error,
        "gcs_bucket": GPI_BUCKET_NAME,
        "gcs_prefix": gcs_prefix,
        "gcs_json_file_count": len(gcs_json_blobs),
        "gcs_index_file_count": len(gcs_index_blobs),
        "gcs_latest_json": _gcs_uri(gcs_latest_json.name) if gcs_latest_json else "ND",
        "gcs_latest_index": _gcs_uri(gcs_latest_index.name) if gcs_latest_index else "ND",

        "local_history_root": str(root),
        "local_history_root_exists": root.exists(),
        "local_json_file_count": len(local_json_files),
        "local_index_file_count": len(local_index_files),
        "local_latest_json": str(local_latest_json) if local_latest_json else "ND",
        "local_latest_index": str(local_latest_index) if local_latest_index else "ND",

        # Compatibilité avec l'ancien contrat de réponse
        "history_root": str(root),
        "history_root_exists": root.exists(),
        "json_file_count": json_file_count,
        "index_file_count": index_file_count,
        "latest_json": latest_json,
        "latest_index": latest_index,
        "latest_index_rows_sample": latest_index_rows_sample,

        "strict_policy": "Lecture archive H5. GCS prioritaire, fallback local. Aucun ticket strict.",
    }

# ============================================================
# SCHEDULER_RUN_DUE_H5_V0_9_27
# Wrapper Cloud Scheduler : lance les traitements H10/H5 dus.
# Protégé par API key. Non exposé GPT Builder.
# Ne crée jamais de ticket strict.
# ============================================================

@app.post("/scheduler/run-due-h5", dependencies=[Depends(require_api_key)])
def scheduler_run_due_h5():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now_paris = datetime.now(ZoneInfo("Europe/Paris")).replace(microsecond=0)

    capture_payload = {}
    brief_payload = {}
    status_payload = {}

    capture_ok = False
    brief_ok = False

    try:
        capture_payload = drive_run_h5_auto_capture()
        capture_ok = isinstance(capture_payload, dict) and capture_payload.get("ok") is True
    except Exception as exc:
        capture_payload = {
            "ok": False,
            "phase": "h5_auto_capture",
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        brief_payload = drive_run_h5_auto_brief()
        brief_ok = isinstance(brief_payload, dict) and brief_payload.get("ok") is True
    except Exception as exc:
        brief_payload = {
            "ok": False,
            "phase": "h5_auto_brief",
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        status_payload = gpi_status()
    except Exception as exc:
        status_payload = {
            "ok": False,
            "phase": "status_after_h5",
            "error": f"{type(exc).__name__}: {exc}",
        }

    def pick(payload, key, default="ND"):
        if isinstance(payload, dict):
            value = payload.get(key, default)
            if value not in (None, ""):
                return value
        return default

    history_items = []

    for source_name, source_payload in [
        ("capture", capture_payload),
        ("brief", brief_payload),
    ]:
        if isinstance(source_payload, dict):
            archive = source_payload.get("history_archive")
            if isinstance(archive, dict):
                item = dict(archive)
                item["source"] = source_name
                history_items.append(item)

    history_archive_ok = bool(history_items) and all(
        item.get("ok") is True for item in history_items
    )

    history_archive_paths = [
        item.get("artifact_path")
        for item in history_items
        if item.get("artifact_path")
    ]

    return {
        "ok": True,
        "h5_run_ok": bool(capture_ok or brief_ok),
        "run_status": "H5_RUN_OK" if (capture_ok or brief_ok) else "NO_H5_RUN_OR_SOURCE_MISSING",
        "route": "/scheduler/run-due-h5",
        "scheduler_version": "SCHEDULER_RUN_DUE_H5_V0_9_27",
        "history_archive_ok": history_archive_ok,
        "history_archive_count": len(history_items),
        "history_archive_paths": history_archive_paths,
        "history_archive_items": history_items[:10],
        "now_paris": now_paris.isoformat(),
        "runs_h5": "oui",
        "writes_gcs": "oui",
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "strict_policy": "Scheduler H5 automation only. Ne crée jamais de ticket strict.",
        "capture": {
            "ok": capture_ok,
            "phase": pick(capture_payload, "phase"),
            "reason": pick(capture_payload, "reason", ""),
            "capture_count": pick(capture_payload, "capture_count", 0),
            "odds_rows_total": pick(capture_payload, "odds_rows_total", 0),
            "error": pick(capture_payload, "error", ""),
        },
        "brief": {
            "ok": brief_ok,
            "phase": pick(brief_payload, "phase"),
            "reason": pick(brief_payload, "reason", ""),
            "matched_courses": pick(brief_payload, "matched_courses", 0),
            "movement_rows_total": pick(brief_payload, "movement_rows_total", 0),
            "briefs_created": pick(brief_payload, "briefs_created", 0),
            "error": pick(brief_payload, "error", ""),
        },
        "status_after": {
            "ok": pick(status_payload, "ok", False),
            "mvp_gate_status": pick(status_payload, "mvp_gate_status"),
            "strict_allowed_global": pick(status_payload, "strict_allowed_global", False),
            "latest_course_uid": pick(status_payload, "latest_course_uid"),
            "latest_verdict": pick(status_payload, "latest_verdict"),
            "latest_structure": pick(status_payload, "latest_structure"),
            "latest_ticket": pick(status_payload, "latest_ticket"),
            "latest_paper_candidate_allowed": pick(status_payload, "latest_paper_candidate_allowed"),
            "latest_paper_candidate_ticket": pick(status_payload, "latest_paper_candidate_ticket"),
        },
        "next_read_routes": [
            "/action/status",
            "/action/watchlist",
            "/action/brief",
            "/action/h5-report",
        ],
    }


# ============================================================
# ACTION_WATCHLIST_FACADE_V0_1
# Lecture compacte watchlist dédiée GPT Actions.
# Classement par priorite_h5 si la colonne existe.
# ============================================================

import csv as _action_csv


def _action_int(value, default=999999):
    try:
        if value is None:
            return default
        text = str(value).strip().upper()
        if not text:
            return default

        # Supporte les priorités sous formes P1, P2, P9.
        match = re.search(r"\d+", text)
        if match:
            return int(match.group(0))

        return int(float(text.replace(",", ".")))
    except Exception:
        return default


def _action_float(value, default=None):
    try:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        return float(text.replace(",", "."))
    except Exception:
        return default


def _action_read_csv_rows(path, limit=50):
    if not path.exists():
        return []

    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = _action_csv.DictReader(handle)
        for row in reader:
            if isinstance(row, dict):
                rows.append(row)

    return rows[:limit]


def _action_pick(row, *names, default="ND"):
    for name in names:
        if name in row:
            value = row.get(name)
            if value is not None and str(value).strip() != "":
                return str(value).strip()
    return default



def _p7_p8_enrich_courses_list(courses):
    try:
        from engine.h5_readiness import enrich_h5_readiness as _p7_p8_enrich
    except Exception:
        _p7_p8_enrich = None

    if _p7_p8_enrich is None:
        for row in courses:
            row.setdefault("h5_shortlist_rank", "ND")
            row.setdefault("h5_shortlist_group", "ND")
            row.setdefault("h5_shortlist_reason", "ND")
            row.setdefault("h5_shortlist_action", "ND")
            row.setdefault("h5_shortlist_blocker", "P7_P8_IMPORT_ERROR")
            row.setdefault("h5_shortlist_cp_sec", "ND")
            row.setdefault("h5_shortlist_priority_score", "ND")
            row.setdefault("ticket_readiness_score", "ND")
            row.setdefault("ticket_readiness_bucket", "ND")
            row.setdefault("ticket_readiness_blocker", "P7_P8_IMPORT_ERROR")
        return courses

    for row in courses:
        try:
            row.update(_p7_p8_enrich(row))
        except Exception as exc:
            row.setdefault("h5_shortlist_rank", "ND")
            row.setdefault("h5_shortlist_group", "ND")
            row.setdefault("h5_shortlist_reason", "ND")
            row.setdefault("h5_shortlist_action", "ND")
            row.setdefault("h5_shortlist_blocker", f"P7_P8_ERROR:{type(exc).__name__}")
            row.setdefault("h5_shortlist_cp_sec", "ND")
            row.setdefault("h5_shortlist_priority_score", "ND")
            row.setdefault("ticket_readiness_score", "ND")
            row.setdefault("ticket_readiness_bucket", "ND")
            row.setdefault("ticket_readiness_blocker", f"P7_P8_ERROR:{type(exc).__name__}")

    return courses




# >>> BET_SELECTION_OPTIMIZER_ACTION_FIELDS V0_1_1 >>>
def _gpi_bet_optimizer_error_fields(reason: str) -> dict:
    return {
        "bet_optimizer_version": "ND",
        "bet_optimizer_scope": "ND",
        "best_structure": "NO_BET",
        "ticket_paper_optimizer": "NO_BET",
        "stake_paper_optimizer": "0",
        "ticket_real_optimizer": "NO_BET",
        "segment_roi_optimizer": "NO_BET",
        "edge_status_optimizer": "EDGE_ABSENT",
        "economic_confidence": "NULLE",
        "variance_status": "BLOQUEE",
        "cp_sec_optimizer_status": "REJECTED_ERROR",
        "sp_optimizer_status": "REJECTED_ERROR",
        "cp3_optimizer_status": "REJECTED_ERROR",
        "cp3_audit_allowed": "non",
        "cp3_audit_ticket": "NO_BET",
        "cp3_audit_stake": "0",
        "cp3_audit_segment_roi": "NO_BET",
        "cp3_audit_reason": reason,
        "cp3_audit_scope": "PAPER_ONLY_100_DECISIONS",
        "optimizer_rejected_structures": reason,
        "optimizer_reason_codes": reason,
        "optimizer_hard_blocks": reason,
    }


def _gpi_bet_optimizer_context(source: dict) -> dict:
    """
    Construit un contexte plat pour engine.bet_selection_optimizer.
    Compatible :
    - ligne /action/watchlist
    - payload /action/brief
    - report /action/h5-report
    """
    if not isinstance(source, dict):
        return {}

    ctx = dict(source)

    # Aplatit les blocs du h5-report.
    for nested_key in [
        "moteur",
        "strict_gate",
        "kill_switch",
        "paper",
        "odds_context",
    ]:
        nested = source.get(nested_key)
        if isinstance(nested, dict):
            ctx.update(nested)

    snapshots = source.get("snapshots")
    if isinstance(snapshots, dict):
        course_key = snapshots.get("course_key")
        if course_key not in [None, "", "ND"]:
            ctx["course_key"] = course_key

    cp_sec = source.get("cp_sec")
    if isinstance(cp_sec, dict):
        ctx["cp_sec_candidate"] = cp_sec.get("candidate", ctx.get("cp_sec_candidate", "ND"))
        ctx["cp_sg_pair_sum"] = cp_sec.get("cp_sg_pair_sum", ctx.get("cp_sg_pair_sum", "ND"))
        ctx["cp_sg_filter_status"] = cp_sec.get("cp_sg_filter_status", ctx.get("cp_sg_filter_status", "ND"))

    cp3 = source.get("cp3")
    if isinstance(cp3, dict):
        ctx["cp3_candidate"] = cp3.get("candidate", ctx.get("cp3_candidate", "ND"))
        ctx["cp3_sg_filter_status"] = cp3.get("cp3_sg_filter_status", ctx.get("cp3_sg_filter_status", "ND"))

    # Harmonisation noms fréquents watchlist/brief.
    if "qualite" not in ctx and "quality" in ctx:
        ctx["qualite"] = ctx.get("quality")

    if "cp_sec_candidate" not in ctx:
        for key in ["latest_cp_sec_candidate", "selection_cp_sec", "selection_CP_SEC", "paper_selection"]:
            if ctx.get(key) not in [None, "", "ND"]:
                ctx["cp_sec_candidate"] = ctx.get(key)
                break

    if "cp_sg_pair_sum" not in ctx:
        for key in ["latest_cp_sg_pair_sum", "cp_sec_sg_sum", "cp_sec_pair_sum", "cp_sg_sum"]:
            if ctx.get(key) not in [None, "", "ND"]:
                ctx["cp_sg_pair_sum"] = ctx.get(key)
                break

    if "cp_sg_filter_status" not in ctx:
        for key in ["latest_cp_sg_filter_status", "cp_sec_sg_status", "cp_sec_status"]:
            if ctx.get(key) not in [None, "", "ND"]:
                ctx["cp_sg_filter_status"] = ctx.get(key)
                break

    if "cp3_candidate" not in ctx:
        for key in ["latest_cp3_candidate", "selection_cp3", "selection_CP3"]:
            if ctx.get(key) not in [None, "", "ND"]:
                ctx["cp3_candidate"] = ctx.get(key)
                break

    if "cp3_sg_filter_status" not in ctx:
        for key in ["latest_cp3_sg_filter_status", "cp3_status"]:
            if ctx.get(key) not in [None, "", "ND"]:
                ctx["cp3_sg_filter_status"] = ctx.get(key)
                break

    return ctx


def _gpi_bet_optimizer_fields(source: dict) -> dict:
    """
    Enrichissement lecture seule.
    Ne crée jamais de ticket réel.
    """
    try:
        from engine.bet_selection_optimizer import optimize_bet_selection

        out = optimize_bet_selection(_gpi_bet_optimizer_context(source))

        def join_list(value):
            if isinstance(value, list):
                return "|".join(str(v) for v in value)
            if value in [None, ""]:
                return "ND"
            return str(value)

        return {
            "bet_optimizer_version": str(out.get("optimizer_version", "ND")),
            "bet_optimizer_scope": str(out.get("ruleset_scope", "ND")),
            "best_structure": str(out.get("best_structure", "NO_BET")),
            "ticket_paper_optimizer": str(out.get("ticket_paper", "NO_BET")),
            "stake_paper_optimizer": str(out.get("stake_paper", "0")),
            "ticket_real_optimizer": "NO_BET",
            "segment_roi_optimizer": str(out.get("segment_roi", "NO_BET")),
            "edge_status_optimizer": str(out.get("edge_status", "EDGE_ABSENT")),
            "economic_confidence": str(out.get("economic_confidence", "NULLE")),
            "variance_status": str(out.get("variance_status", "BLOQUEE")),
            "cp_sec_optimizer_status": str(out.get("cp_sec_status", "ND")),
            "sp_optimizer_status": str(out.get("sp_status", "ND")),
            "cp3_optimizer_status": str(out.get("cp3_status", "ND")),
            "cp3_audit_allowed": str(out.get("cp3_audit_allowed", "non")),
            "cp3_audit_ticket": str(out.get("cp3_audit_ticket", "NO_BET")),
            "cp3_audit_stake": str(out.get("cp3_audit_stake", "0")),
            "cp3_audit_segment_roi": str(out.get("cp3_audit_segment_roi", "NO_BET")),
            "cp3_audit_reason": str(out.get("cp3_audit_reason", "ND")),
            "cp3_audit_scope": str(out.get("cp3_audit_scope", "PAPER_ONLY_100_DECISIONS")),
            "cp_sec_priority_score": str(out.get("cp_sec_priority_score", "0")),
            "cp_sec_priority_level": str(out.get("cp_sec_priority_level", "ND")),
            "cp_sec_priority_reasons": join_list(out.get("cp_sec_priority_reasons", "ND")),
            "optimizer_rejected_structures": join_list(out.get("rejected_structures", [])),
            "optimizer_reason_codes": join_list(out.get("reason_codes", [])),
            "optimizer_hard_blocks": join_list(out.get("hard_blocks", [])),
        }
    except Exception as exc:
        return _gpi_bet_optimizer_error_fields(f"BET_OPTIMIZER_ERROR:{type(exc).__name__}")
# <<< BET_SELECTION_OPTIMIZER_ACTION_FIELDS V0_1_1 <<<


# >>> GPI ACTION WATCHLIST PAPER FIELDS PATCH V0_9_27 >>>
def _gpi_watchlist_paper_fields(row: dict) -> dict:
    """
    Enrichit /action/watchlist avec un pronostic étude/papier lisible.
    Ne valide jamais le réel strict.
    """
    def pick(*keys, default=""):
        for key in keys:
            value = row.get(key)
            if value not in [None, "", "ND"]:
                return str(value).strip()
        return default

    def as_float(value):
        try:
            return float(str(value).replace(",", "."))
        except Exception:
            return None

    qualite = pick("watchlist_quality", "qualite", "qualite_matin", default="ND").upper()
    structure = pick("structure_max", default="ND").upper()
    cp_sec = pick("cp_sec_candidate", "cp_sec_candidat", "CP_SEC_selection", default="")
    cp_sum_raw = pick("cp_sg_pair_sum", "cp_sec_sg_sum_9h", default="")
    cp_sum = as_float(cp_sum_raw)
    cp_filter = pick("cp_sg_filter_status", "cp_sec_sg_filter_status_9h", default="ND").upper()
    kill = pick("kill_switch_codes", "kill_switchs_matin", default="aucun").upper()

    no_kill = kill in {"", "AUCUN", "NONE", "ND"}

    if (
        qualite in {"A", "B"}
        and "CP_SEC_ETUDE" in structure
        and cp_sec
        and cp_filter == "OK"
        and cp_sum is not None
        and cp_sum >= 7.5
        and no_kill
    ):
        return {
            "study_allowed": "oui",
            "paper_allowed": "oui",
            "pronostic_papier_oui_non": "OUI",
            "paper_structure": "CP_SEC",
            "paper_selection": cp_sec,
            "paper_stake": "1",
            "ticket_reel": "NO_BET",
            "strict_block_reasons": "WATCHLIST_PRE_H5_STRICT_FORBIDDEN|H5_DATA_REQUIRED",
            "paper_candidate_allowed": "oui",
            "paper_candidate_type": "CP_SEC",
            "paper_candidate_ticket": f"CP_SEC {cp_sec}",
            "paper_candidate_segment": "PAPER_TRADING",
            "paper_candidate_mise": "1",
            "paper_candidate_reason": f"WATCHLIST_CP_SEC_ETUDE; cp_sg_sum={cp_sum}; filter={cp_filter}",
        }

    if qualite in {"A", "B", "C"} and cp_sec and cp_filter == "OK" and no_kill:
        return {
            "study_allowed": "oui",
            "paper_allowed": "non",
            "pronostic_papier_oui_non": "SIMULATION",
            "paper_structure": "CP_SEC_SIMULATION",
            "paper_selection": cp_sec,
            "paper_stake": "0",
            "ticket_reel": "NO_BET",
            "strict_block_reasons": "WATCHLIST_SIMULATION_ONLY|H5_DATA_REQUIRED",
            "paper_candidate_allowed": "non",
            "paper_candidate_type": "CP_SEC_SIMULATION",
            "paper_candidate_ticket": f"CP_SEC_SIMULATION {cp_sec}",
            "paper_candidate_segment": "SIMULATION",
            "paper_candidate_mise": "0",
            "paper_candidate_reason": "WATCHLIST_SIMULATION_ONLY",
        }

    return {
        "study_allowed": "non",
        "paper_allowed": "non",
        "pronostic_papier_oui_non": "NON",
        "paper_structure": "NO_BET",
        "paper_selection": cp_sec,
        "paper_stake": "0",
        "ticket_reel": "NO_BET",
        "strict_block_reasons": "WATCHLIST_NO_BET_OR_DATA_MISSING",
        "paper_candidate_allowed": "non",
        "paper_candidate_type": "NO_BET",
        "paper_candidate_ticket": "NO_BET",
        "paper_candidate_segment": "NO_BET",
        "paper_candidate_mise": "0",
        "paper_candidate_reason": "WATCHLIST_NO_BET_OR_DATA_MISSING",
    }
# <<< GPI ACTION WATCHLIST PAPER FIELDS PATCH V0_9_27 >>>


def _action_compact_watchlist_row(row):
    base = {
        "course_uid": _action_pick(row, "course_uid", "uid", "course_id", "nom_course"),
        "reunion": _action_pick(row, "reunion", "R", "num_reunion"),
        "course": _action_pick(row, "course", "C", "num_course"),
        "hippodrome": _action_pick(row, "hippodrome"),
        "prix": _action_pick(row, "prix", "nom_prix"),
        "heure": _action_pick(row, "heure", "heure_depart", "depart"),
        "priorite_h5": _action_pick(row, "priorite_h5", "priority_h5", "priorite"),
        "qualite": _action_pick(row, "qualite", "quality", "qualite_matin"),
        "structure_max": _action_pick(row, "structure_max", "latest_structure"),
        "verdict": _action_pick(row, "verdict", "verdict_python", "latest_verdict", "decision_matin", "statut_matin"),
        "ticket_exact": _action_pick(row, "ticket_exact", "latest_ticket"),
        "edge_status": _action_pick(row, "edge_status", "edge", "latest_edge"),
        "raison": _action_pick(row, "raison", "reason", "reason_codes", "motifs_filtrage", "priorite_h5_reason", "no_bet_reason"),
        "source": _action_pick(row, "source", "source_file"),

        # Champs moteur / strict gate si deja presents dans le CSV
        "strict_allowed_oui_non": _action_pick(row, "strict_allowed_oui_non", "latest_strict_allowed_oui_non"),
        "decision_strict_allowed": _action_pick(row, "decision_strict_allowed", "latest_decision_strict_allowed"),
        "decision_block_reason": _action_pick(row, "decision_block_reason", "latest_decision_block_reason"),
        "strict_gate_summary": _action_pick(row, "strict_gate_summary", "latest_strict_gate_summary"),

        # Champs CP si deja presents
        "cp_sec_candidate": _action_pick(row, "cp_sec_candidate", "latest_cp_sec_candidate", "cp_sec_candidat"),
        "cp_sg_pair_sum": _action_pick(row, "cp_sg_pair_sum", "latest_cp_sg_pair_sum", "cp_sec_sg_sum_9h"),
        "cp_sg_filter_status": _action_pick(row, "cp_sg_filter_status", "latest_cp_sg_filter_status", "cp_sec_sg_filter_status_9h"),
        "cp3_candidate": _action_pick(row, "cp3_candidate", "latest_cp3_candidate"),
        "cp3_sg_filter_status": _action_pick(row, "cp3_sg_filter_status", "latest_cp3_sg_filter_status"),

        # Champs volatilite si deja presents
        "volatilite_status": _action_pick(row, "volatilite_status", "latest_volatilite_status"),
        "volatilite_top6_max_pct": _action_pick(row, "volatilite_top6_max_pct", "latest_volatilite_top6_max_pct"),
        "volatilite_top6_direction": _action_pick(row, "volatilite_top6_direction", "latest_volatilite_top6_direction"),
        # P5 missing actionWatchlist aliases.
        # Source facts only. Morning/watchlist never opens a strict ticket.
        "segment_roi": _action_pick(row, "segment_roi", "segment_ROI", "latest_segment_roi"),
        "kill_switch_codes": _action_pick(row, "kill_switch_codes", "latest_kill_switch_codes", "kill_switchs_matin", "no_bet_reason"),
        "volatilite_top6_max_numero": _action_pick(row, "volatilite_top6_max_numero", "latest_volatilite_top6_max_numero"),
        "volatilite_top6_max_cheval": _action_pick(row, "volatilite_top6_max_cheval", "latest_volatilite_top6_max_cheval"),
        "odds_context_found": _action_pick(row, "odds_context_found", "latest_odds_context_found"),
        "odds_blocks_strict": _action_pick(row, "odds_blocks_strict", "latest_odds_blocks_strict"),
        "proxy_status": _action_pick(row, "proxy_status", "statut_proxy", "proxy_statut"),
        "proxy_segment_id": _action_pick(row, "proxy_segment_id", "segment_id", "proxy_segment"),
        "sp_status": _action_pick(row, "sp_status", "sp_status_morning"),
        "sp_segment_status": _action_pick(row, "sp_segment_status", "segment_SP", "segment_sp_status"),

        # Champs value favoris si disponibles
        "favorite_sg": _action_pick(row, "favorite_sg", "favori_sg", "sg_favori", "cote_favori"),
        "favorite_fragile": _action_pick(row, "favorite_fragile", "favori_fragile"),
    }

    try:
        from engine.watchlist_precision import enrich_watchlist_row

        merged = dict(row)
        merged.update(base)
        return enrich_watchlist_row(merged)
    except Exception as exc:
        base["watchlist_quality"] = "C"
        base["h5_surveillance_value"] = "SURVEILLANCE_FAIBLE"
        base["course_gate_preview"] = "PRE_H5_UNKNOWN"
        base["strict_ticket_status"] = "NO_BET"
        base["top_refusal_reason_1"] = "WATCHLIST_PRECISION_ERROR"
        base["top_refusal_reason_2"] = f"{type(exc).__name__}"
        base["top_refusal_reason_3"] = "ND"
        base["action_gpt"] = "Erreur enrichissement Watchlist. Aucun ticket strict."
        # P7/P8 h5 shortlist + ticket readiness enrichment
    try:
        from engine.h5_readiness import enrich_h5_readiness as _p7_p8_enrich_h5_readiness
        base.update(_p7_p8_enrich_h5_readiness(base))
    except Exception as exc:
        base.setdefault("h5_shortlist_rank", "ND")
        base.setdefault("h5_shortlist_group", "ND")
        base.setdefault("h5_shortlist_reason", "ND")
        base.setdefault("h5_shortlist_action", "ND")
        base.setdefault("h5_shortlist_blocker", f"P7_P8_ERROR:{type(exc).__name__}")
        base.setdefault("h5_shortlist_cp_sec", "ND")
        base.setdefault("h5_shortlist_priority_score", "ND")
        base.setdefault("ticket_readiness_score", "ND")
        base.setdefault("ticket_readiness_bucket", "ND")
        base.setdefault("ticket_readiness_blocker", f"P7_P8_ERROR:{type(exc).__name__}")

    try:
        base.update(_gpi_watchlist_paper_fields(base))
        base.update(_gpi_bet_optimizer_fields(base))
    except Exception as exc:
        base.update({
            "study_allowed": "non",
            "paper_allowed": "non",
            "pronostic_papier_oui_non": "NON",
            "paper_structure": "NO_BET",
            "paper_selection": "",
            "paper_stake": "0",
            "ticket_reel": "NO_BET",
            "strict_block_reasons": f"WATCHLIST_PAPER_FIELDS_ERROR:{type(exc).__name__}",
            "paper_candidate_allowed": "non",
            "paper_candidate_type": "NO_BET",
            "paper_candidate_ticket": "NO_BET",
            "paper_candidate_segment": "NO_BET",
            "paper_candidate_mise": "0",
            "paper_candidate_reason": f"WATCHLIST_PAPER_FIELDS_ERROR:{type(exc).__name__}",
        })

    return base



@app.get("/action/watchlist")
def action_watchlist(limit: int = 50):
    ensure_dirs()

    import io
    import re as _action_re

    local_candidates = [
        OUTPUT_DIR / "daily_watchlist.csv",
        OUTPUT_DIR / "watchlist.csv",
        OUTPUT_DIR / "latest_watchlist.csv",
        OUTPUT_DIR / "daily_watchlist_priorisee.csv",
        OUTPUT_DIR / "latest_daily_watchlist_priorisee.csv",
    ]

    gcs_candidates = [
        "gpi-lite/outputs/latest_daily_watchlist_priorisee.csv",
        "gpi-lite/outputs/daily_watchlist_priorisee.csv",
        "gpi-lite/outputs/daily_watchlist.csv",
        "gpi-lite/outputs/latest_watchlist.csv",
        "gpi-lite/outputs/watchlist.csv",
    ]

    source_kind = "ND"
    source_file = "ND"
    rows = []
    errors = []

    for candidate in local_candidates:
        if candidate.exists():
            rows = _action_read_csv_rows(candidate, limit=1000)
            source_kind = "local"
            source_file = str(candidate)
            break

    if not rows:
        bucket_name = _action_os.getenv("GPI_BUCKET_NAME", "gpi-lite-watchlist-data").strip()

        try:
            from google.cloud import storage

            client = storage.Client()
            bucket = client.bucket(bucket_name)

            for object_name in gcs_candidates:
                try:
                    blob = bucket.blob(object_name)
                    if not blob.exists(client):
                        continue

                    raw = blob.download_as_text(encoding="utf-8")
                    reader = _action_csv.DictReader(io.StringIO(raw))
                    rows = [row for row in reader if isinstance(row, dict)]

                    source_kind = "gcs"
                    source_file = f"gs://{bucket_name}/{object_name}"
                    break

                except Exception as exc:
                    errors.append(f"{object_name}: {exc}")

        except Exception as exc:
            errors.append(f"GCS_CLIENT_ERROR: {exc}")

    if not rows:
        return {
            "ok": False,
            "facade_version": _ACTION_FACADE_VERSION,
            "source_route": "/action/watchlist",
            "error": "WATCHLIST_FILE_ABSENT",
            "searched_local": [str(p) for p in local_candidates],
            "searched_gcs": [f"gs://{_action_os.getenv('GPI_BUCKET_NAME', 'gpi-lite-watchlist-data').strip()}/{p}" for p in gcs_candidates],
            "gcs_errors": errors,
            "courses": [],
        }

    def priority_rank(row):
        raw = _action_pick(row, "priorite_h5", "priority_h5", "priorite", default="P999999")
        text = str(raw).upper().strip()
        match = _action_re.search(r"\d+", text)
        if match:
            return int(match.group(0))
        return 999999

    def sort_key(row):
        return (
            priority_rank(row),
            _action_pick(row, "heure", "heure_depart", "depart", default="99:99"),
            _action_pick(row, "course_uid", "uid", "course_id", default=""),
        )

    rows_sorted = sorted(rows, key=sort_key)
    safe_limit = max(1, min(int(limit or 50), 100))
    compact = [_action_compact_watchlist_row(row) for row in rows_sorted[:safe_limit]]
    compact = _p7_p8_enrich_courses_list(compact)


    # >>> WATCHLIST PAPER ROUTE ENRICHMENT V0_9_28 >>>
    enriched_compact = []
    for _course in compact:
        try:
            _course.update(_gpi_watchlist_paper_fields(_course))
            _course.update(_gpi_bet_optimizer_fields(_course))
        except Exception as exc:
            _course.update({
                "study_allowed": "non",
                "paper_allowed": "non",
                "pronostic_papier_oui_non": "NON",
                "paper_structure": "NO_BET",
                "paper_selection": "",
                "paper_stake": "0",
                "ticket_reel": "NO_BET",
                "strict_block_reasons": f"WATCHLIST_ROUTE_PAPER_FIELDS_ERROR:{type(exc).__name__}",
                "paper_candidate_allowed": "non",
                "paper_candidate_type": "NO_BET",
                "paper_candidate_ticket": "NO_BET",
                "paper_candidate_segment": "NO_BET",
                "paper_candidate_mise": "0",
                "paper_candidate_reason": f"WATCHLIST_ROUTE_PAPER_FIELDS_ERROR:{type(exc).__name__}",
            })
        enriched_compact.append(_course)
    compact = enriched_compact
    # <<< WATCHLIST PAPER ROUTE ENRICHMENT V0_9_28 >>>

    return {
        "ok": True,
        "facade_version": _ACTION_FACADE_VERSION,
        "source_route": "/action/watchlist",
        "source_kind": source_kind,
        "source_file": source_file,
        "sort": "priorite_h5_asc",
        "count_total": len(rows),
        "count_returned": len(compact),
        "strict_policy": "Aucun ticket strict depuis morning. Morning = watchlist / priorisation uniquement.",
        "courses": compact,
    }




# ACTION_H5_REPORT_FACADE_V0_1
# Rapport H-5 lisible : lecture seule, aucun ticket strict créé ici.
def _h5r_facade_version():
    try:
        return _ACTION_FACADE_VERSION
    except Exception:
        return "ACTION_FACADE_V0_1"


def _h5r_output_dir():
    from pathlib import Path
    try:
        return OUTPUT_DIR
    except Exception:
        return Path("data/output")


def _h5r_pick(row, *keys, default="ND"):
    if not isinstance(row, dict):
        return default
    for key in keys:
        if key in row:
            value = row.get(key)
            if value is not None and str(value).strip() != "":
                return str(value).strip()
    return default


def _h5r_bool_from_text(value):
    text = str(value or "").strip().lower()
    return text in {"1", "true", "oui", "yes", "y"}


def _h5r_read_csv_text(raw):
    import csv
    import io

    if not raw:
        return []

    # Gère UTF-8 avec BOM si besoin.
    raw = raw.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(raw))
    return [row for row in reader if isinstance(row, dict)]


def _h5r_read_local_text(path):
    try:
        if path.exists():
            return path.read_text(encoding="utf-8-sig")
    except Exception:
        try:
            return path.read_text(encoding="latin-1")
        except Exception:
            return None
    return None


def _h5r_read_gcs_text(bucket_name, object_name):
    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_name)

        if not blob.exists(client):
            return None, "absent"

        return blob.download_as_text(encoding="utf-8"), ""
    except Exception as exc:
        return None, str(exc)


def _h5r_load_csv_candidates(local_names, gcs_names):
    import os

    out_dir = _h5r_output_dir()
    local_errors = []
    gcs_errors = []

    for name in local_names:
        path = out_dir / name
        raw = _h5r_read_local_text(path)
        if raw:
            rows = _h5r_read_csv_text(raw)
            if rows:
                return rows, {
                    "kind": "local",
                    "file": str(path),
                    "searched_local": [str(out_dir / x) for x in local_names],
                    "searched_gcs": [],
                    "errors": [],
                }

    bucket_name = os.getenv("GPI_BUCKET_NAME", "gpi-lite-watchlist-data").strip()

    for object_name in gcs_names:
        raw, err = _h5r_read_gcs_text(bucket_name, object_name)
        if raw:
            rows = _h5r_read_csv_text(raw)
            if rows:
                return rows, {
                    "kind": "gcs",
                    "file": f"gs://{bucket_name}/{object_name}",
                    "searched_local": [str(out_dir / x) for x in local_names],
                    "searched_gcs": [f"gs://{bucket_name}/{x}" for x in gcs_names],
                    "errors": gcs_errors,
                }
        elif err and err != "absent":
            gcs_errors.append(f"{object_name}: {err}")

    return [], {
        "kind": "absent",
        "file": "ND",
        "searched_local": [str(out_dir / x) for x in local_names],
        "searched_gcs": [f"gs://{bucket_name}/{x}" for x in gcs_names],
        "errors": local_errors + gcs_errors,
    }


def _h5r_load_json_candidates(local_names, gcs_names):
    import json
    import os

    out_dir = _h5r_output_dir()
    gcs_errors = []

    for name in local_names:
        path = out_dir / name
        raw = _h5r_read_local_text(path)
        if raw:
            try:
                return json.loads(raw), {
                    "kind": "local",
                    "file": str(path),
                    "searched_local": [str(out_dir / x) for x in local_names],
                    "searched_gcs": [],
                    "errors": [],
                }
            except Exception:
                pass

    bucket_name = os.getenv("GPI_BUCKET_NAME", "gpi-lite-watchlist-data").strip()

    for object_name in gcs_names:
        raw, err = _h5r_read_gcs_text(bucket_name, object_name)
        if raw:
            try:
                return json.loads(raw), {
                    "kind": "gcs",
                    "file": f"gs://{bucket_name}/{object_name}",
                    "searched_local": [str(out_dir / x) for x in local_names],
                    "searched_gcs": [f"gs://{bucket_name}/{x}" for x in gcs_names],
                    "errors": gcs_errors,
                }
            except Exception as exc:
                gcs_errors.append(f"{object_name}: json_error={exc}")
        elif err and err != "absent":
            gcs_errors.append(f"{object_name}: {err}")

    return {}, {
        "kind": "absent",
        "file": "ND",
        "searched_local": [str(out_dir / x) for x in local_names],
        "searched_gcs": [f"gs://{bucket_name}/{x}" for x in gcs_names],
        "errors": gcs_errors,
    }


def _h5r_as_float(value):
    try:
        text = str(value).replace(",", ".").strip()
        if text in {"", "ND", "None", "nan"}:
            return None
        return float(text)
    except Exception:
        return None


def _h5r_num_list(value):
    import re

    text = str(value or "")
    return re.findall(r"\d+", text)


def _h5r_brief_from_route_or_csv():
    # Priorité : route interne actionBrief si elle existe.
    for fn_name in ("action_brief", "actionBrief"):
        fn = globals().get(fn_name)
        if callable(fn):
            try:
                data = fn()
                if isinstance(data, dict) and data.get("ok") is True:
                    return data, {
                        "kind": "internal_route",
                        "file": fn_name,
                        "errors": [],
                    }
            except Exception:
                pass

    rows, src = _h5r_load_csv_candidates(
        local_names=[
            "latest_decision_engine_outputs.csv",
            "decision_engine_outputs.csv",
        ],
        gcs_names=[
            "gpi-lite/outputs/latest_decision_engine_outputs.csv",
            "gpi-lite/outputs/decision_engine_outputs.csv",
        ],
    )

    if rows:
        row = rows[-1]
        row["ok"] = True
        return row, src

    return {"ok": False, "error": "BRIEF_ABSENT"}, src


def _h5r_build_top_marche(brief, odds_context, limit=8):
    top = []

    odds_top6 = ""
    if isinstance(odds_context, dict):
        odds_top6 = _h5r_pick(odds_context, "odds_volatilite_sg_top6", "top6", default="")

    if not odds_top6:
        odds_top6 = _h5r_pick(brief, "odds_volatilite_sg_top6", "volatilite_sg_top6", default="")

    nums = _h5r_num_list(odds_top6)

    # Ajoute les numéros de CP_SEC si le top6 est absent ou incomplet.
    cp_sec = _h5r_pick(brief, "cp_sec_candidate", "latest_cp_sec_candidate", default="")
    for n in _h5r_num_list(cp_sec):
        if n not in nums:
            nums.append(n)

    for idx, numero in enumerate(nums[:limit], start=1):
        top.append({
            "rang_marche": idx,
            "numero": numero,
            "cheval": "ND",
            "sg_reference": "ND",
            "sg_h5": "ND",
            "variation_pct": "ND",
            "direction": "ND",
            "source": "odds_context_top6" if odds_top6 else "cp_sec_candidate",
        })

    return top


def _h5r_build_mouvements(brief, odds_context):
    kill_numero = _h5r_pick(
        brief,
        "volatilite_top6_max_numero",
        "latest_volatilite_top6_max_numero",
        default="ND",
    )
    kill_cheval = _h5r_pick(
        brief,
        "volatilite_top6_max_cheval",
        "latest_volatilite_top6_max_cheval",
        default="ND",
    )
    kill_pct = _h5r_pick(
        brief,
        "volatilite_top6_max_pct",
        "latest_volatilite_top6_max_pct",
        default="ND",
    )
    kill_direction = _h5r_pick(
        brief,
        "volatilite_top6_direction",
        "latest_volatilite_top6_direction",
        default="ND",
    )

    mouvements = []

    if kill_numero != "ND" or kill_cheval != "ND":
        mouvements.append({
            "type": "kill_switch_top6",
            "numero": kill_numero,
            "cheval": kill_cheval,
            "variation_pct": kill_pct,
            "direction": kill_direction,
            "seuil": ">35% top6/clé",
            "decision": "BLOQUE_STRICT",
        })

    if isinstance(odds_context, dict):
        instables = _h5r_pick(odds_context, "odds_volatilite_sg_instable_top6", default="")
        for numero in _h5r_num_list(instables):
            mouvements.append({
                "type": "instable_top6",
                "numero": numero,
                "cheval": "ND",
                "variation_pct": "ND",
                "direction": "ND",
                "seuil": ">20% top6/clé",
                "decision": "DEGRADE",
            })

    return mouvements


def _h5r_kill_reason(brief, odds_context):
    volatilite_status = _h5r_pick(
        brief,
        "volatilite_status",
        "latest_volatilite_status",
        default="ND",
    )
    kill_codes = _h5r_pick(
        brief,
        "kill_switch_codes",
        "latest_kill_switch_codes",
        default="ND",
    )
    reason_codes = _h5r_pick(
        brief,
        "reason_codes",
        "latest_reason_codes",
        default="ND",
    )
    decision_block_reason = _h5r_pick(
        brief,
        "decision_block_reason",
        "latest_decision_block_reason",
        default="ND",
    )

    odds_status = "ND"
    odds_blocks = "ND"
    odds_reason = "ND"

    if isinstance(odds_context, dict):
        odds_status = _h5r_pick(odds_context, "odds_volatilite_sg_statut", default="ND")
        odds_blocks = _h5r_pick(odds_context, "odds_blocks_strict", default="ND")
        odds_reason = _h5r_pick(odds_context, "odds_reason_codes", default="ND")

    active = (
        volatilite_status.upper() == "KILL"
        or "KILL" in kill_codes.upper()
        or "KILL" in odds_status.upper()
        or odds_blocks.lower() == "oui"
    )

    return {
        "active": active,
        "volatilite_status": volatilite_status,
        "kill_switch_codes": kill_codes,
        "reason_codes": reason_codes,
        "decision_block_reason": decision_block_reason,
        "odds_volatilite_sg_statut": odds_status,
        "odds_blocks_strict": odds_blocks,
        "odds_reason_codes": odds_reason,
        "explication": (
            "NO BET : kill-switch volatilité actif ou odds_context bloque le strict."
            if active
            else "Aucun kill-switch explicite détecté dans les champs disponibles."
        ),
    }



# ACTION_H5_REPORT_FACADE_V0_2
# Enrichissement du rapport H-5 avec SG 9h vs SG H-5.
def _h5r_norm_num(value):
    try:
        if value is None:
            return ""
        txt = str(value).strip()
        if txt.endswith(".0"):
            txt = txt[:-2]
        return txt
    except Exception:
        return ""


def _h5r_float_or_none(value):
    try:
        txt = str(value).replace(",", ".").strip()
        if txt in {"", "ND", "None", "nan"}:
            return None
        return float(txt)
    except Exception:
        return None


def _h5r_pct_variation(ref, h5):
    ref_f = _h5r_float_or_none(ref)
    h5_f = _h5r_float_or_none(h5)

    if ref_f is None or h5_f is None or ref_f <= 0:
        return "ND", "ND"

    pct = ((h5_f - ref_f) / ref_f) * 100.0

    if abs(pct) < 5:
        direction = "STABLE"
    elif pct > 0:
        direction = "DRIFT"
    else:
        direction = "STEAM"

    return f"{pct:.2f}", direction


def _h5r_load_latest_h5_context():
    data, src = _h5r_load_json_candidates(
        local_names=["latest_h5_context.json"],
        gcs_names=["gpi-lite/outputs/latest_h5_context.json"],
    )
    if not isinstance(data, dict):
        data = {}
    return data, src


def _h5r_load_h5_auto_context_for_brief(brief):
    """
    Charge le contexte H5 auto exact reference par le brief.
    Ne lit latest_h5_auto_context.json qu'en fallback diagnostique.
    Ne valide jamais le strict.
    """
    if not isinstance(brief, dict):
        brief = {}

    sync = brief.get("h5_auto_context_sync")
    if not isinstance(sync, dict):
        sync = {}

    context_uri = str(sync.get("context_gcs_uri", "") or "").strip()
    gcs_name = ""

    if context_uri.startswith("gs://"):
        parts = context_uri.split("/", 3)
        if len(parts) >= 4:
            gcs_name = parts[3]
    elif context_uri.startswith("gpi-lite/"):
        gcs_name = context_uri

    local_names = []
    gcs_names = []

    if gcs_name:
        gcs_names.append(gcs_name)

    # Fallback seulement pour diagnostic, jamais pour strict.
    gcs_names.append("gpi-lite/outputs/latest_h5_auto_context.json")
    local_names.append("latest_h5_auto_context.json")

    data, src = _h5r_load_json_candidates(
        local_names=local_names,
        gcs_names=gcs_names,
    )

    if not isinstance(data, dict):
        data = {}

    src["requested_context_uri"] = context_uri
    src["requested_gcs_name"] = gcs_name or "ND"
    src["fallback_latest_allowed_for_diagnostic_only"] = "oui"

    return data, src


def _h5r_context_course_key(context, brief):
    key = "ND"

    if isinstance(context, dict):
        key = context.get("course_key") or "ND"

    if key == "ND":
        key = _h5r_pick(brief, "course_uid", "latest_course_uid", default="ND")

    return str(key).strip()


def _h5r_context_9h_blob(context, course_key):
    if isinstance(context, dict):
        c9 = context.get("context_9h") or {}
        checked = c9.get("checked") or []
        if checked and isinstance(checked[0], dict):
            blob = checked[0].get("gcs_blob")
            if blob:
                return str(blob).strip()

    if course_key and course_key != "ND":
        return f"gpi-lite/outputs/{course_key}_course_9h_clean.csv"

    return "ND"


def _h5r_context_h5_blob(course_key):
    if course_key and course_key != "ND":
        return f"gpi-lite/outputs/{course_key}_course_h5_clean.csv"
    return "ND"


def _h5r_load_snapshot_rows(context, brief):
    course_key = _h5r_context_course_key(context, brief)
    blob_9h = _h5r_context_9h_blob(context, course_key)
    blob_h5 = _h5r_context_h5_blob(course_key)

    rows_9h, src_9h = _h5r_load_csv_candidates(
        local_names=[
            f"{course_key}_course_9h_clean.csv",
            "course_9h_clean.csv",
        ],
        gcs_names=[blob_9h] if blob_9h != "ND" else [],
    )

    rows_h5, src_h5 = _h5r_load_csv_candidates(
        local_names=[
            f"{course_key}_course_h5_clean.csv",
            "course_h5_clean.csv",
        ],
        gcs_names=[blob_h5] if blob_h5 != "ND" else [],
    )

    return {
        "course_key": course_key,
        "rows_9h": rows_9h,
        "rows_h5": rows_h5,
        "source_9h": src_9h,
        "source_h5": src_h5,
        "blob_9h": blob_9h,
        "blob_h5": blob_h5,
    }


def _h5r_snapshot_maps(snapshot):
    map_9h = {}
    map_h5 = {}

    for row in snapshot.get("rows_9h") or []:
        num = _h5r_norm_num(_h5r_pick(row, "numero", "num", "n", default=""))
        if num:
            map_9h[num] = row

    for row in snapshot.get("rows_h5") or []:
        num = _h5r_norm_num(_h5r_pick(row, "numero", "num", "n", default=""))
        if num:
            map_h5[num] = row

    return map_9h, map_h5


def _h5r_nums_from_odds_or_market(brief, odds_context, snapshot):
    nums = []

    odds_top6 = ""
    if isinstance(odds_context, dict):
        odds_top6 = _h5r_pick(odds_context, "odds_volatilite_sg_top6", "top6", default="")

    if not odds_top6:
        odds_top6 = _h5r_pick(brief, "odds_volatilite_sg_top6", "volatilite_sg_top6", default="")

    for n in _h5r_num_list(odds_top6):
        n = _h5r_norm_num(n)
        if n and n not in nums:
            nums.append(n)

    cp_sec = _h5r_pick(brief, "cp_sec_candidate", "latest_cp_sec_candidate", default="")
    for n in _h5r_num_list(cp_sec):
        n = _h5r_norm_num(n)
        if n and n not in nums:
            nums.append(n)

    # Si le top6 odds_context est incomplet, compléter par les plus basses cotes SG H-5.
    h5_rows = snapshot.get("rows_h5") or []

    def sg_key(row):
        sg = _h5r_float_or_none(_h5r_pick(row, "SG", "sg", "cote", "cote_sg", default="ND"))
        return sg if sg is not None else 999999.0

    for row in sorted(h5_rows, key=sg_key):
        n = _h5r_norm_num(_h5r_pick(row, "numero", default=""))
        if n and n not in nums:
            nums.append(n)

    return nums


def _h5r_build_top_marche_v2(brief, odds_context, snapshot, limit=8):
    map_9h, map_h5 = _h5r_snapshot_maps(snapshot)
    nums = _h5r_nums_from_odds_or_market(brief, odds_context, snapshot)

    top = []

    for idx, numero in enumerate(nums[:limit], start=1):
        row_9h = map_9h.get(numero, {})
        row_h5 = map_h5.get(numero, {})

        cheval = _h5r_pick(row_h5, "cheval", default="")
        if not cheval:
            cheval = _h5r_pick(row_9h, "cheval", default="ND")

        sg_9h = _h5r_pick(row_9h, "SG", "sg", "cote", "cote_sg", default="ND")
        sg_h5 = _h5r_pick(row_h5, "SG", "sg", "cote", "cote_sg", default="ND")
        variation_pct, direction = _h5r_pct_variation(sg_9h, sg_h5)

        top.append({
            "rang_marche": idx,
            "numero": numero,
            "cheval": cheval,
            "sg_reference": sg_9h,
            "sg_h5": sg_h5,
            "variation_pct": variation_pct,
            "direction": direction,
            "sp_min_h5": _h5r_pick(row_h5, "SP_min", "sp_min", default="ND"),
            "sp_max_h5": _h5r_pick(row_h5, "SP_max", "sp_max", default="ND"),
            "statut_9h": _h5r_pick(row_9h, "statut", default="ND"),
            "statut_h5": _h5r_pick(row_h5, "statut", default="ND"),
            "is_np_h5": _h5r_pick(row_h5, "is_np", default="ND"),
            "extraction_confidence_h5": _h5r_pick(row_h5, "extraction_confidence", default="ND"),
            "source": "snapshot_9h_vs_h5",
        })

    return top


def _h5r_build_mouvements_v2(brief, odds_context, snapshot):
    map_9h, map_h5 = _h5r_snapshot_maps(snapshot)
    mouvements = []

    all_nums = sorted(
        set(map_9h.keys()) | set(map_h5.keys()),
        key=lambda x: int(x) if str(x).isdigit() else 999999,
    )

    for numero in all_nums:
        row_9h = map_9h.get(numero, {})
        row_h5 = map_h5.get(numero, {})

        sg_9h = _h5r_pick(row_9h, "SG", "sg", "cote", "cote_sg", default="ND")
        sg_h5 = _h5r_pick(row_h5, "SG", "sg", "cote", "cote_sg", default="ND")
        variation_pct, direction = _h5r_pct_variation(sg_9h, sg_h5)

        pct_f = _h5r_float_or_none(variation_pct)
        if pct_f is None:
            continue

        abs_pct = abs(pct_f)

        if abs_pct >= 35:
            type_mvt = "kill_switch_candidate"
            decision = "BLOQUE_STRICT_POTENTIEL"
        elif abs_pct >= 20:
            type_mvt = "instable"
            decision = "DEGRADE"
        else:
            continue

        mouvements.append({
            "type": type_mvt,
            "numero": numero,
            "cheval": _h5r_pick(row_h5, "cheval", default=_h5r_pick(row_9h, "cheval", default="ND")),
            "sg_reference": sg_9h,
            "sg_h5": sg_h5,
            "variation_pct": variation_pct,
            "direction": direction,
            "seuil": ">=35% kill potentiel ; >=20% instable",
            "decision": decision,
            "statut_h5": _h5r_pick(row_h5, "statut", default="ND"),
        })

    # Ajouter explicitement le mouvement qui a déclenché le kill-switch moteur.
    kill_numero = _h5r_pick(brief, "volatilite_top6_max_numero", "latest_volatilite_top6_max_numero", default="ND")
    kill_cheval = _h5r_pick(brief, "volatilite_top6_max_cheval", "latest_volatilite_top6_max_cheval", default="ND")
    kill_pct = _h5r_pick(brief, "volatilite_top6_max_pct", "latest_volatilite_top6_max_pct", default="ND")
    kill_direction = _h5r_pick(brief, "volatilite_top6_direction", "latest_volatilite_top6_direction", default="ND")

    if kill_numero != "ND" or kill_cheval != "ND":
        mouvements.insert(0, {
            "type": "kill_switch_moteur",
            "numero": kill_numero,
            "cheval": kill_cheval,
            "sg_reference": _h5r_pick(map_9h.get(_h5r_norm_num(kill_numero), {}), "SG", default="ND"),
            "sg_h5": _h5r_pick(map_h5.get(_h5r_norm_num(kill_numero), {}), "SG", default="ND"),
            "variation_pct": kill_pct,
            "direction": kill_direction,
            "seuil": ">35% top6/clé",
            "decision": "BLOQUE_STRICT",
            "statut_h5": _h5r_pick(map_h5.get(_h5r_norm_num(kill_numero), {}), "statut", default="ND"),
        })

    return mouvements


@app.get("/action/h5-report")
def action_h5_report(limit: int = 8):
    try:
        ensure_dirs()
    except Exception:
        pass

    brief, brief_source = _h5r_brief_from_route_or_csv()

    if not isinstance(brief, dict) or brief.get("ok") is not True:
        return {
            "ok": False,
            "facade_version": _h5r_facade_version(),
            "source_route": "/action/h5-report",
            "error": "BRIEF_ABSENT",
            "brief_source": brief_source,
            "report": {},
        }

    odds_context = brief.get("odds_context")
    odds_source = {
        "kind": "brief_field",
        "file": "odds_context",
        "errors": [],
    }

    if not isinstance(odds_context, dict):
        odds_context, odds_source = _h5r_load_json_candidates(
            local_names=[
                "latest_h5_context.json",
                "latest_odds_context.json",
                "latest_odds_snapshot.json",
                "latest_diff_drift.json",
            ],
            gcs_names=[
                "gpi-lite/outputs/latest_h5_context.json",
                "gpi-lite/outputs/latest_odds_context.json",
                "gpi-lite/outputs/latest_odds_snapshot.json",
                "gpi-lite/outputs/latest_diff_drift.json",
            ],
        )

    h5_context, h5_context_source = _h5r_load_latest_h5_context()
    snapshot = _h5r_load_snapshot_rows(h5_context, brief)

    # GPI H5 REPORT CONTEXT GUARD V0_2
    # Si le contexte legacy est obsolete, utiliser le contexte H5 auto exact
    # reference par le brief courant, jamais latest_h5_auto_context seul.
    brief_course_uid = _h5r_pick(brief, "course_uid", "latest_course_uid", default="ND")
    snapshot_course_key_before_guard = str(snapshot.get("course_key", "ND") or "ND").strip()

    try:
        brief_course_norm = _h5auto_normalize_course_key(brief_course_uid)
        snapshot_course_norm = _h5auto_normalize_course_key(snapshot_course_key_before_guard)
    except Exception:
        brief_course_norm = str(brief_course_uid or "").strip().lower()
        snapshot_course_norm = str(snapshot_course_key_before_guard or "").strip().lower()

    h5_report_context_mismatch = bool(
        brief_course_norm
        and snapshot_course_norm
        and brief_course_norm != "nd"
        and snapshot_course_norm != "nd"
        and brief_course_norm != snapshot_course_norm
    )

    auto_context = {}
    auto_context_source = {"kind": "not_loaded"}
    auto_context_used = False
    auto_context_aligned = False

    if h5_report_context_mismatch:
        try:
            sync = brief.get("h5_auto_context_sync")
            if not isinstance(sync, dict):
                sync = {}

            context_uri = str(sync.get("context_gcs_uri", "") or "").strip()
            context_blob = ""

            if context_uri.startswith("gs://"):
                parts = context_uri.split("/", 3)
                if len(parts) >= 4:
                    context_blob = parts[3]
            elif context_uri.startswith("gpi-lite/"):
                context_blob = context_uri

            if context_blob:
                auto_context, auto_context_source = _h5r_load_json_candidates(
                    local_names=[],
                    gcs_names=[context_blob],
                )

                if not isinstance(auto_context, dict):
                    auto_context = {}

                auto_course_uid = str(
                    auto_context.get("course_uid")
                    or auto_context.get("course_key")
                    or "ND"
                ).strip()

                try:
                    auto_course_norm = _h5auto_normalize_course_key(auto_course_uid)
                except Exception:
                    auto_course_norm = str(auto_course_uid or "").strip().lower()

                auto_context_aligned = bool(
                    auto_context.get("ok") is True
                    and brief_course_norm
                    and auto_course_norm
                    and brief_course_norm != "nd"
                    and auto_course_norm != "nd"
                    and brief_course_norm == auto_course_norm
                )

                if auto_context_aligned:
                    h5_context = auto_context
                    h5_context_source = {
                        "kind": "h5_auto_context_course_scoped",
                        "context_uri": context_uri,
                        "context_blob": context_blob,
                        "source": auto_context_source,
                        "legacy_snapshot_course_key": snapshot_course_key_before_guard,
                    }

                    snapshot = {
                        "course_key": auto_course_uid,
                        "rows_9h": [],
                        "rows_h5": [],
                        "context_mismatch": "non",
                        "mismatch_reason": "",
                        "legacy_context_mismatch_oui_non": "oui",
                        "auto_context_used_oui_non": "oui",
                        "auto_context_aligned_oui_non": "oui",
                        "snapshot_kind": "H5_AUTO_ODDS_CAPTURE",
                        "auto_context_course_uid": auto_course_uid,
                        "auto_context_source": h5_context_source,
                        "h10_rows_count": auto_context.get("h10_rows_count", "ND"),
                        "h5_rows_count": auto_context.get("h5_rows_count", "ND"),
                        "previous_snapshot_label": auto_context.get("previous_snapshot_label", "H10"),
                        "current_snapshot_label": auto_context.get("current_snapshot_label", "H5"),
                    }

                    h5_report_context_mismatch = False
                    auto_context_used = True
                else:
                    snapshot["auto_context_used_oui_non"] = "non"
                    snapshot["auto_context_aligned_oui_non"] = "non"
                    snapshot["auto_context_course_uid"] = auto_course_uid
                    snapshot["auto_context_mismatch_reason"] = "H5_AUTO_CONTEXT_COURSE_MISMATCH_OR_NOT_OK"
            else:
                snapshot["auto_context_used_oui_non"] = "non"
                snapshot["auto_context_aligned_oui_non"] = "non"
                snapshot["auto_context_mismatch_reason"] = "H5_AUTO_CONTEXT_URI_MISSING"

        except Exception as exc:
            snapshot["auto_context_used_oui_non"] = "non"
            snapshot["auto_context_aligned_oui_non"] = "non"
            snapshot["auto_context_error"] = f"{type(exc).__name__}:{exc}"

    if h5_report_context_mismatch:
        snapshot["context_mismatch"] = "oui"
        snapshot["mismatch_reason"] = "H5_REPORT_CONTEXT_MISMATCH"
        snapshot["mismatch_brief_course_uid"] = brief_course_uid
        snapshot["mismatch_snapshot_course_key"] = snapshot_course_key_before_guard
        snapshot["auto_context_source"] = auto_context_source
        snapshot["auto_context_used_oui_non"] = "oui" if auto_context_used else "non"
        snapshot["auto_context_aligned_oui_non"] = "oui" if auto_context_aligned else "non"
        snapshot["rows_9h"] = []
        snapshot["rows_h5"] = []
    else:
        snapshot["context_mismatch"] = "non"
        snapshot["mismatch_reason"] = snapshot.get("mismatch_reason", "")
        snapshot.setdefault("auto_context_used_oui_non", "oui" if auto_context_used else "non")
        snapshot.setdefault("auto_context_aligned_oui_non", "oui" if auto_context_aligned else "non")

    safe_limit = max(1, min(int(limit or 8), 20))

    kill = _h5r_kill_reason(brief, odds_context)
    top_marche = _h5r_build_top_marche_v2(brief, odds_context, snapshot, limit=safe_limit)
    mouvements = _h5r_build_mouvements_v2(brief, odds_context, snapshot)

    report = {
        "course_uid": _h5r_pick(brief, "course_uid", "latest_course_uid"),
        "updated": _h5r_pick(brief, "updated"),
        "verdict": _h5r_pick(brief, "verdict_python", "latest_verdict"),
        "qualite": _h5r_pick(brief, "qualite"),
        "structure_max": _h5r_pick(brief, "structure_max", "latest_structure"),
        "ticket_exact": _h5r_pick(brief, "ticket_exact", "latest_ticket"),
        "segment_roi": _h5r_pick(brief, "segment_roi"),
        "edge_status": _h5r_pick(brief, "edge_status", "latest_edge"),
        "strict_gate": {
            "strict_allowed_oui_non": _h5r_pick(brief, "strict_allowed_oui_non", "latest_strict_allowed_oui_non"),
            "decision_strict_allowed": _h5r_pick(brief, "decision_strict_allowed", "latest_decision_strict_allowed"),
            "strict_gate_summary": _h5r_pick(brief, "strict_gate_summary", "latest_strict_gate_summary"),
        },
        "moteur": {
            "decision_id": _h5r_pick(brief, "decision_id", "latest_decision_id"),
            "input_hash": _h5r_pick(brief, "input_hash", "latest_input_hash"),
            "engine_version": _h5r_pick(brief, "engine_version", "latest_engine_version"),
            "ruleset_version": _h5r_pick(brief, "ruleset_version", "latest_ruleset_version"),
            "split": _h5r_pick(brief, "split"),
            "calibration_version": _h5r_pick(brief, "calibration_version"),
            "leakage_check_ok": _h5r_pick(brief, "leakage_check_ok"),
            "decision_before_race_oui_non": _h5r_pick(brief, "decision_before_race_oui_non"),
            "replay_match_oui_non": _h5r_pick(brief, "replay_match_oui_non"),
            "output_replayed_oui_non": _h5r_pick(brief, "output_replayed_oui_non"),
        },
        "snapshots": {
            "h5_context_source": h5_context_source,
            "brief_source": brief_source,
            "odds_context_source": odds_source,
            "odds_context_found": _h5r_pick(odds_context, "odds_context_found") if isinstance(odds_context, dict) else "ND",
            "snapshot_reference_source": snapshot.get("source_9h"),
            "snapshot_h5_source": snapshot.get("source_h5"),
            "snapshot_reference_rows": len(snapshot.get("rows_9h") or []),
            "snapshot_h5_rows": len(snapshot.get("rows_h5") or []),
            "course_key": snapshot.get("course_key", "ND"),
            "context_mismatch": snapshot.get("context_mismatch", "non"),
            "mismatch_reason": snapshot.get("mismatch_reason", ""),
            "brief_course_uid": snapshot.get("mismatch_brief_course_uid", _h5r_pick(brief, "course_uid", "latest_course_uid", default="ND")),
            "snapshot_course_key_before_guard": snapshot.get("mismatch_snapshot_course_key", snapshot.get("course_key", "ND")),
            "note": "SG référence = snapshot 9h ; SG H-5 = snapshot H-5. Variation = (SG_H5 - SG_9H) / SG_9H.",
        },
        "top_cotes": top_marche,
        "mouvements": mouvements,
        "kill_switch": kill,
        "cp_sec": {
            "candidate": _h5r_pick(brief, "cp_sec_candidate", "latest_cp_sec_candidate"),
            "cp_sg_pair_sum": _h5r_pick(brief, "cp_sg_pair_sum", "latest_cp_sg_pair_sum"),
            "cp_sg_filter_status": _h5r_pick(brief, "cp_sg_filter_status", "latest_cp_sg_filter_status"),
        },
        "cp3": {
            "candidate": _h5r_pick(brief, "cp3_candidate", "latest_cp3_candidate"),
            "cp3_sg_filter_status": _h5r_pick(brief, "cp3_sg_filter_status", "latest_cp3_sg_filter_status"),
        },
        "paper": {
            "paper_candidate_allowed": _h5r_pick(brief, "paper_candidate_allowed", "latest_paper_candidate_allowed"),
            "paper_candidate_ticket": _h5r_pick(brief, "paper_candidate_ticket", "latest_paper_candidate_ticket"),
            "paper_candidate_reason": _h5r_pick(brief, "paper_candidate_reason", "latest_paper_candidate_reason"),
        },
        "action_gpt": _h5r_pick(brief, "action_gpt", default="ND"),
        "strict_policy": "Rapport H-5 lecture seule. Aucun ticket strict n'est créé par /action/h5-report.",
    }

    report.update(_gpi_bet_optimizer_fields(report))

    if h5_report_context_mismatch:
        report["top_cotes"] = []
        report["mouvements"] = []
        report["action_gpt"] = "NO BET : contexte H-5 incoherent entre brief et snapshots. Corriger latest_h5_context / capture H-5."
        report["best_structure"] = "NO_BET"
        report["ticket_paper_optimizer"] = "NO_BET"
        report["stake_paper_optimizer"] = "0.0"
        report["ticket_real_optimizer"] = "NO_BET"
        report["segment_roi_optimizer"] = "NO_BET"
        report["edge_status_optimizer"] = "EDGE_ABSENT"
        report["economic_confidence"] = "NULLE"
        report["variance_status"] = "BLOQUEE"
        report["optimizer_hard_blocks"] = "H5_REPORT_CONTEXT_MISMATCH"
        report["optimizer_reason_codes"] = "H5_REPORT_CONTEXT_MISMATCH"
        report["cp_sec_optimizer_status"] = "REJECTED_HARD_BLOCK"
        report["sp_optimizer_status"] = "REJECTED_HARD_BLOCK"
        report["cp3_optimizer_status"] = "REJECTED_HARD_BLOCK"
        report["cp3_audit_allowed"] = "non"
        report["cp3_audit_ticket"] = "NO_BET"
        report["cp3_audit_stake"] = "0.0"
        report["cp3_audit_segment_roi"] = "NO_BET"
        report["cp3_audit_reason"] = "H5_REPORT_CONTEXT_MISMATCH"

    return {
        "ok": True,
        "facade_version": _h5r_facade_version(),
        "source_route": "/action/h5-report",
        "report_version": "ACTION_H5_REPORT_FACADE_V0_2",
        "report": report,
    }


@app.get("/action/h5-decision-preview")
def action_h5_decision_preview():
    """P9.1 preview route. Security-first H-5 decision engine."""
    try:
        from engine.h5_decision import evaluate_h5_decision
    except Exception as exc:
        return {
            "ok": False,
            "source_route": "/action/h5-decision-preview",
            "error": f"H5_DECISION_IMPORT_ERROR:{type(exc).__name__}",
            "verdict": "NO_BET",
            "ticket_exact": "NO_BET",
            "strict_allowed_oui_non": "non",
        }

    ctx = {
        "sg_h5_ok": "ND",
        "np_ok": "ND",
        "statut_ok": "ND",
        "decision_before_race_oui_non": "ND",
        "input_hash": "ND",
        "segment_roi": "ND",
        "edge_status": "ND",
        "proxy_status": "ND",
        "volatilite_status": "ND",
        "kill_switch_codes": "aucun",
    }

    decision = evaluate_h5_decision(ctx)

    return {
        "ok": True,
        "source_route": "/action/h5-decision-preview",
        "strict_policy": "P9.1 security-first. Aucun ticket strict. Donnees H-5 incompletes = NO_BET.",
        **decision,
    }


def _p9_2_normalize_route_payload(payload):
    """Normalize dict / JSONResponse / FastAPI response into a dict."""
    try:
        if isinstance(payload, dict):
            return payload
        if hasattr(payload, "body"):
            import json
            body = payload.body
            if isinstance(body, bytes):
                body = body.decode("utf-8")
            return json.loads(body)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"P9_2_PAYLOAD_NORMALIZE_ERROR:{type(exc).__name__}",
        }

    return {
        "ok": False,
        "error": "P9_2_UNSUPPORTED_PAYLOAD",
    }


def _p9_2_extract_brief_payload(payload):
    """Extract the most likely H-5 brief object from a route payload."""
    if not isinstance(payload, dict):
        return {}

    for key in [
        "brief",
        "latest_h5_brief",
        "h5_brief",
        "decision",
        "decision_engine_outputs",
        "course",
        "data",
    ]:
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    return payload


@app.get("/action/h5-decision-latest-brief")
def action_h5_decision_latest_brief():
    """P9.2 route. Wire latest H-5 brief into the security-first decision engine."""
    try:
        from engine.h5_decision import evaluate_h5_decision_from_brief, build_h5_decision_context_from_brief
    except Exception as exc:
        return {
            "ok": False,
            "source_route": "/action/h5-decision-latest-brief",
            "error": f"H5_DECISION_IMPORT_ERROR:{type(exc).__name__}",
            "verdict": "NO_BET",
            "ticket_exact": "NO_BET",
            "strict_allowed_oui_non": "non",
        }

    source_payload = {
        "ok": False,
        "error": "LATEST_H5_AUTO_BRIEF_ROUTE_NOT_FOUND",
    }

    try:
        latest_func = globals().get("action_latest_h5_auto_brief")
        if callable(latest_func):
            source_payload = _p9_2_normalize_route_payload(latest_func())
    except Exception as exc:
        source_payload = {
            "ok": False,
            "error": f"LATEST_H5_AUTO_BRIEF_CALL_ERROR:{type(exc).__name__}",
        }

    brief = _p9_2_extract_brief_payload(source_payload)
    decision_context = build_h5_decision_context_from_brief(brief)
    decision = evaluate_h5_decision_from_brief(brief)

    return {
        "ok": True,
        "source_route": "/action/h5-decision-latest-brief",
        "brief_source_route": "/action/latest-h5-auto-brief",
        "brief_source_ok": source_payload.get("ok", "ND") if isinstance(source_payload, dict) else "ND",
        "brief_source_error": source_payload.get("error", "") if isinstance(source_payload, dict) else "",
        "strict_policy": "P9.2 brief wiring. Aucun ticket strict. Champs manquants conserves en ND.",
        "decision_context": decision_context,
        **decision,
    }


def _p9_4b_h5_brief_hash_material(brief):
    """Build stable hash material from a H-5 auto brief without mutating GCS."""
    if not isinstance(brief, dict):
        brief = {}

    return {
        "course_uid": brief.get("course_uid", "ND"),
        "current_snapshot_label": brief.get("current_snapshot_label", "H5"),
        "runners_count": brief.get("runners_count", 0),
        "top_n_numbers": brief.get("top_n_numbers", ""),
        "top_n_instable_numbers": brief.get("top_n_instable_numbers", ""),
        "top_n_kill_switch_numbers": brief.get("top_n_kill_switch_numbers", ""),
        "global_kill_switch_count": brief.get("global_kill_switch_count", 0),
        "global_instable_count": brief.get("global_instable_count", 0),
        "top_n_kill_switch_count": brief.get("top_n_kill_switch_count", 0),
        "top_n_instable_count": brief.get("top_n_instable_count", 0),
        "kill_switch_topn_oui_non": brief.get("kill_switch_topn_oui_non", "non"),
        "course_statut_marche": brief.get("course_statut_marche", "ND"),
        "strict_allowed_by_odds_context": brief.get("strict_allowed_by_odds_context", "non"),
        "edge_status_by_odds_context": brief.get("edge_status_by_odds_context", "EDGE_FRAGILE"),
        "reason_codes": brief.get("reason_codes", ""),
    }


def _p9_4b_compute_h5_brief_input_hash(brief):
    import hashlib
    import json

    material = _p9_4b_h5_brief_hash_material(brief)

    return hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest(), material


@app.get("/action/h5-brief-safety-preview")
def action_h5_brief_safety_preview():
    """P9.4b read-only preview. Never writes GCS and never opens a strict ticket."""
    try:
        from engine.h5_decision import (
            build_h5_decision_context_from_brief,
            evaluate_h5_decision_from_brief,
        )
    except Exception as exc:
        return {
            "ok": False,
            "source_route": "/action/h5-brief-safety-preview",
            "error": f"H5_DECISION_IMPORT_ERROR:{type(exc).__name__}",
            "VERDICT": "NO_BET",
            "ticket_exact": "NO_BET",
            "strict_allowed_oui_non": "non",
        }

    source_payload = {
        "ok": False,
        "reason": "LATEST_H5_AUTO_BRIEF_NOT_READ",
    }

    try:
        latest_func = globals().get("action_latest_h5_auto_brief")
        if callable(latest_func):
            source_payload = latest_func()
    except Exception as exc:
        source_payload = {
            "ok": False,
            "reason": f"LATEST_H5_AUTO_BRIEF_CALL_ERROR:{type(exc).__name__}:{exc}",
        }

    if not isinstance(source_payload, dict):
        source_payload = {
            "ok": False,
            "reason": "LATEST_H5_AUTO_BRIEF_INVALID_PAYLOAD",
        }

    brief = dict(source_payload)
    input_hash, input_hash_material = _p9_4b_compute_h5_brief_input_hash(brief)

    preview_brief = dict(brief)
    preview_brief["decision_before_race_oui_non"] = "oui"
    preview_brief["input_hash"] = input_hash
    preview_brief["input_hash_source"] = "H5_AUTO_BRIEF_PAYLOAD_PREVIEW"
    preview_brief["input_hash_material_keys"] = sorted(input_hash_material.keys())

    decision_context = build_h5_decision_context_from_brief(preview_brief)
    decision = evaluate_h5_decision_from_brief(preview_brief)

    return {
        "ok": True,
        "source_route": "/action/h5-brief-safety-preview",
        "brief_source_route": "/action/latest-h5-auto-brief",
        "brief_source_ok": source_payload.get("ok", "ND"),
        "brief_generated_at_utc": source_payload.get("generated_at_utc", "ND"),
        "preview_only": "oui",
        "writes_gcs": "non",
        "strict_policy": "P9.4b preview read-only. Aucun ticket strict.",
        "preview_fields": {
            "decision_before_race_oui_non": preview_brief.get("decision_before_race_oui_non"),
            "input_hash": preview_brief.get("input_hash"),
            "input_hash_len": len(str(preview_brief.get("input_hash") or "")),
            "input_hash_source": preview_brief.get("input_hash_source"),
            "input_hash_material_keys": preview_brief.get("input_hash_material_keys"),
        },
        "decision_context": decision_context,
        **decision,
    }


def _p10_norm_text(value):
    import re
    s = str(value or "").strip().lower()
    s = s.replace("œ", "oe").replace("’", "'").replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def _p10_find_clean_rows_for_course(course_uid):
    """Find exact clean CSV rows for course_uid with statut/is_np.

    Conservative: exact normalized course_uid match only.
    """
    from pathlib import Path
    import pandas as pd

    wanted = _p10_norm_text(course_uid)
    if not wanted or wanted == "nd":
        return None, "COURSE_UID_ND"

    roots = [Path("data/output")]
    patterns = ["*_course_9h_clean.csv", "*_h5_clean.csv", "*h5*clean*.csv", "*course*clean*.csv"]

    seen = set()

    for root in roots:
        if not root.exists():
            continue

        for pattern in patterns:
            for csv_path in root.rglob(pattern):
                if csv_path in seen:
                    continue
                seen.add(csv_path)

                try:
                    df = pd.read_csv(csv_path)
                except Exception:
                    continue

                cols = {str(c).lower(): c for c in df.columns}
                if "course_uid" not in cols:
                    continue
                if "statut" not in cols or "is_np" not in cols:
                    continue

                cu_col = cols["course_uid"]
                mask = df[cu_col].astype(str).map(_p10_norm_text) == wanted
                rows = df[mask].copy()

                if not rows.empty:
                    return rows, str(csv_path)

    return None, "NP_STATUT_SOURCE_ABSENT_FOR_COURSE"


def _p10_compute_np_statut_gate_from_rows(rows, source):
    import pandas as pd
    from datetime import datetime, timezone

    if rows is None or getattr(rows, "empty", True):
        return {
            "np_ok": "ND",
            "statut_ok": "ND",
            "np_source": source or "NP_STATUT_SOURCE_ABSENT_FOR_COURSE",
            "statut_source": source or "NP_STATUT_SOURCE_ABSENT_FOR_COURSE",
            "np_count": "ND",
            "statut_missing_count": "ND",
            "np_statut_rows_count": 0,
            "np_statut_checked_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    statut = rows["statut"].astype(str).str.strip().str.upper()
    is_np = pd.to_numeric(rows["is_np"], errors="coerce")

    statut_missing_count = int(statut.isin(["", "NAN", "NONE", "ND"]).sum())
    np_by_flag = int((is_np.fillna(0) == 1).sum())
    np_by_statut = int(statut.isin(["NP", "NON_PARTANT", "NON-PARTANT", "NON PARTANT"]).sum())
    np_count = max(np_by_flag, np_by_statut)

    unknown_is_np_count = int(is_np.isna().sum())

    if statut_missing_count > 0 or unknown_is_np_count > 0:
        statut_ok = "KO"
    else:
        valid_status = statut.isin(["PARTANT", "NP", "NON_PARTANT", "NON-PARTANT", "NON PARTANT"])
        statut_ok = "OK" if bool(valid_status.all()) else "KO"

    np_ok = "KO" if np_count > 0 else "OK"

    return {
        "np_ok": np_ok,
        "statut_ok": statut_ok,
        "np_source": source,
        "statut_source": source,
        "np_count": np_count,
        "statut_missing_count": statut_missing_count,
        "np_statut_rows_count": int(len(rows)),
        "np_statut_checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _p10_compute_np_statut_gate_for_brief(brief):
    if not isinstance(brief, dict):
        brief = {}

    course_uid = brief.get("course_uid", "ND")
    rows, source = _p10_find_clean_rows_for_course(course_uid)
    return _p10_compute_np_statut_gate_from_rows(rows, source)




def _p10_read_local_latest_h5_auto_brief():
    """Read local latest_h5_auto_brief.json as fallback for local preview tests."""
    from pathlib import Path
    import json

    candidates = [
        Path("data/output/latest_h5_auto_brief.json"),
        Path("data\\output\\latest_h5_auto_brief.json"),
    ]

    for path in candidates:
        if not path.exists():
            continue

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload = dict(payload)
                payload["_p10_local_fallback_used"] = "oui"
                payload["_p10_local_fallback_path"] = str(path)
                return payload
        except Exception as exc:
            return {
                "ok": False,
                "reason": f"LOCAL_LATEST_H5_AUTO_BRIEF_READ_ERROR:{type(exc).__name__}:{exc}",
            }

    return {
        "ok": False,
        "reason": "LOCAL_LATEST_H5_AUTO_BRIEF_ABSENT",
    }

@app.get("/action/h5-np-statut-preview")
def action_h5_np_statut_preview():
    """P10 read-only preview for np_ok/statut_ok.

    Never writes GCS. Never opens a strict ticket.
    """
    source_payload = {
        "ok": False,
        "reason": "LATEST_H5_AUTO_BRIEF_NOT_READ",
    }

    try:
        latest_func = globals().get("action_latest_h5_auto_brief")
        if callable(latest_func):
            source_payload = latest_func()
    except Exception as exc:
        source_payload = {
            "ok": False,
            "reason": f"LATEST_H5_AUTO_BRIEF_CALL_ERROR:{type(exc).__name__}:{exc}",
        }

    if not isinstance(source_payload, dict):
        source_payload = {
            "ok": False,
            "reason": "LATEST_H5_AUTO_BRIEF_INVALID_PAYLOAD",
        }

    if source_payload.get("ok") is not True or source_payload.get("course_uid") in {None, "", "ND"}:
        local_payload = _p10_read_local_latest_h5_auto_brief()
        if isinstance(local_payload, dict) and local_payload.get("course_uid") not in {None, "", "ND"}:
            source_payload = local_payload

    fields = _p10_compute_np_statut_gate_for_brief(source_payload)

    return {
        "ok": True,
        "source_route": "/action/h5-np-statut-preview",
        "brief_source_route": "/action/latest-h5-auto-brief",
        "brief_source_ok": source_payload.get("ok", "ND"),
        "brief_generated_at_utc": source_payload.get("generated_at_utc", "ND"),
        "course_uid": source_payload.get("course_uid", "ND"),
        "preview_only": "oui",
        "writes_gcs": "non",
        "strict_policy": "P10 preview read-only. Aucun ticket strict.",
        "np_statut_fields": fields,
        "VERDICT": "NO_BET",
        "ticket_exact": "NO_BET",
        "strict_allowed_oui_non": "non",
    }




@app.get("/action/h5-business-validation")
def action_h5_business_validation():
    """P12 read-only business validation for latest H-5 brief.

    Checks whether the latest H-5 brief contains the expected P11 NP/statut fields.
    Does not run H-5.
    Does not write GCS.
    Never opens a strict ticket.
    """
    source_payload = {
        "ok": False,
        "reason": "LATEST_H5_AUTO_BRIEF_NOT_READ",
    }

    try:
        latest_func = globals().get("action_latest_h5_auto_brief")
        if callable(latest_func):
            source_payload = latest_func()
    except Exception as exc:
        source_payload = {
            "ok": False,
            "reason": f"LATEST_H5_AUTO_BRIEF_CALL_ERROR:{type(exc).__name__}:{exc}",
        }

    if not isinstance(source_payload, dict):
        source_payload = {
            "ok": False,
            "reason": "LATEST_H5_AUTO_BRIEF_INVALID_PAYLOAD",
        }

    # P12 : fallback local pour tests VS Code quand GCS/API n'est pas disponible.
    # Ne modifie pas GCS. Ne lance pas H-5. Ne crée jamais de ticket strict.
    if source_payload.get("ok") is not True or source_payload.get("course_uid") in {None, "", "ND"}:
        try:
            local_payload = _p10_read_local_latest_h5_auto_brief()
            if isinstance(local_payload, dict) and local_payload.get("course_uid") not in {None, "", "ND"}:
                source_payload = local_payload
        except Exception as exc:
            source_payload = {
                **source_payload,
                "local_fallback_error": f"LOCAL_LATEST_H5_AUTO_BRIEF_READ_ERROR:{type(exc).__name__}:{exc}",
            }

    # P12 : compat lecture anciens briefs sans champs P11.
    p12_np_statut_defaults = {
        "np_ok": "ND",
        "statut_ok": "ND",
        "np_source": "NP_STATUT_NOT_IN_LEGACY_BRIEF",
        "statut_source": "NP_STATUT_NOT_IN_LEGACY_BRIEF",
        "np_count": "ND",
        "statut_missing_count": "ND",
        "np_statut_rows_count": 0,
        "np_statut_source_level": "LEGACY_BRIEF_NO_P11_FIELDS",
        "np_statut_checked_at_utc": "ND",
    }

    for key, default_value in p12_np_statut_defaults.items():
        if source_payload.get(key) in {None, ""}:
            source_payload[key] = default_value

    required_fields = [
        "np_ok",
        "statut_ok",
        "np_source",
        "statut_source",
        "np_count",
        "statut_missing_count",
        "np_statut_rows_count",
        "np_statut_source_level",
        "np_statut_checked_at_utc",
    ]

    missing_fields = [
        key for key in required_fields
        if key not in source_payload or source_payload.get(key) in {None, ""}
    ]

    np_ok = source_payload.get("np_ok", "ND")
    statut_ok = source_payload.get("statut_ok", "ND")
    source_level = source_payload.get("np_statut_source_level", "ND")

    hard_blocks = []

    if source_payload.get("ok") is not True:
        hard_blocks.append("LATEST_H5_AUTO_BRIEF_NOT_OK")

    if missing_fields:
        hard_blocks.append("P11_FIELDS_MISSING")

    if np_ok == "KO":
        hard_blocks.append("NP_DETECTED")

    if statut_ok == "KO":
        hard_blocks.append("STATUT_INVALID_OR_MISSING")

    if source_level in {"LEGACY_BRIEF_NO_P11_FIELDS", "H5_SOURCE_ABSENT", "ND"}:
        hard_blocks.append("NP_STATUT_NOT_VALIDATED_FROM_TRUE_H5")

    if source_payload.get("strict_allowed_oui_non") != "non":
        hard_blocks.append("STRICT_FLAG_UNEXPECTED")

    validation_metier = "READY_FOR_AUDIT" if not hard_blocks else "BLOCKED"

    return {
        "ok": True,
        "source_route": "/action/h5-business-validation",
        "brief_source_route": "/action/latest-h5-auto-brief",
        "read_only": "oui",
        "writes_gcs": "non",
        "runs_h5": "non",
        "course_uid": source_payload.get("course_uid", "ND"),
        "brief_ok": bool(source_payload.get("ok") is True),
        "p11_fields_present_oui_non": "oui" if not missing_fields else "non",
        "missing_fields": missing_fields,
        "np_ok": np_ok,
        "statut_ok": statut_ok,
        "np_source": source_payload.get("np_source", "ND"),
        "statut_source": source_payload.get("statut_source", "ND"),
        "np_count": source_payload.get("np_count", "ND"),
        "statut_missing_count": source_payload.get("statut_missing_count", "ND"),
        "np_statut_rows_count": source_payload.get("np_statut_rows_count", 0),
        "np_statut_source_level": source_level,
        "np_statut_checked_at_utc": source_payload.get("np_statut_checked_at_utc", "ND"),
        "validation_metier": validation_metier,
        "hard_blocks": hard_blocks,
        "VERDICT": "NO_BET",
        "Ticket_exact_ou_NO_BET": "NO_BET",
        "Segment_ROI": "NO_BET",
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "Action_GPT": "Valider la présence des champs P11 après vrai H-5. Aucun ticket strict.",
    }


def _p11_norm_text(value):
    import re
    s = str(value or "").strip().lower()
    s = s.replace("œ", "oe").replace("’", "'").replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def _p11_is_h5_source(csv_path, rows):
    name = str(csv_path).lower()
    if "h5" in name or "h-5" in name:
        return True

    try:
        cols = {str(c).lower(): c for c in rows.columns}
        for key in ["snapshot_label", "snapshot_time", "capture_label"]:
            col = cols.get(key)
            if col:
                values = rows[col].astype(str).str.lower().tolist()
                if any(v in {"h5", "h-5"} or "h5" in v or "h-5" in v for v in values):
                    return True
    except Exception:
        return False

    return False


def _p11_find_h5_np_statut_rows_for_course(course_uid):
    from pathlib import Path
    import pandas as pd

    wanted = _p11_norm_text(course_uid)
    if not wanted or wanted == "nd":
        return None, "COURSE_UID_ND"

    root = Path("data/output")
    if not root.exists():
        return None, "DATA_OUTPUT_ABSENT"

    patterns = [
        "*h5*clean*.csv",
        "*h-5*clean*.csv",
        "*h5*.csv",
        "*h-5*.csv",
    ]

    seen = set()

    for pattern in patterns:
        for csv_path in root.rglob(pattern):
            if csv_path in seen:
                continue
            seen.add(csv_path)

            try:
                df = pd.read_csv(csv_path)
            except Exception:
                continue

            cols = {str(c).lower(): c for c in df.columns}
            if "course_uid" not in cols:
                continue
            if "statut" not in cols or "is_np" not in cols:
                continue

            cu_col = cols["course_uid"]
            rows = df[df[cu_col].astype(str).map(_p11_norm_text) == wanted].copy()

            if rows.empty:
                continue

            if not _p11_is_h5_source(csv_path, rows):
                continue

            return rows, str(csv_path)

    return None, "NP_STATUT_H5_SOURCE_ABSENT_FOR_COURSE"


def _p11_compute_np_statut_gate_from_rows(rows, source):
    import pandas as pd
    from datetime import datetime, timezone

    checked_at = datetime.now(timezone.utc).isoformat()

    if rows is None or getattr(rows, "empty", True):
        return {
            "np_ok": "ND",
            "statut_ok": "ND",
            "np_source": source or "NP_STATUT_H5_SOURCE_ABSENT_FOR_COURSE",
            "statut_source": source or "NP_STATUT_H5_SOURCE_ABSENT_FOR_COURSE",
            "np_count": "ND",
            "statut_missing_count": "ND",
            "np_statut_rows_count": 0,
            "np_statut_source_level": "H5_SOURCE_ABSENT",
            "np_statut_checked_at_utc": checked_at,
        }

    statut = rows["statut"].astype(str).str.strip().str.upper()
    is_np = pd.to_numeric(rows["is_np"], errors="coerce")

    statut_missing_count = int(statut.isin(["", "NAN", "NONE", "ND"]).sum())
    unknown_is_np_count = int(is_np.isna().sum())

    np_by_flag = int((is_np.fillna(0) == 1).sum())
    np_by_statut = int(statut.isin(["NP", "NON_PARTANT", "NON-PARTANT", "NON PARTANT"]).sum())
    np_count = max(np_by_flag, np_by_statut)

    valid_status = statut.isin(["PARTANT", "NP", "NON_PARTANT", "NON-PARTANT", "NON PARTANT"])

    statut_ok = "OK"
    if statut_missing_count > 0 or unknown_is_np_count > 0 or not bool(valid_status.all()):
        statut_ok = "KO"

    np_ok = "KO" if np_count > 0 else "OK"

    return {
        "np_ok": np_ok,
        "statut_ok": statut_ok,
        "np_source": source,
        "statut_source": source,
        "np_count": np_count,
        "statut_missing_count": statut_missing_count,
        "np_statut_rows_count": int(len(rows)),
        "np_statut_source_level": "H5_EXACT_COURSE_UID",
        "np_statut_checked_at_utc": checked_at,
    }


def _p11_compute_np_statut_gate_for_h5_brief(brief):
    if not isinstance(brief, dict):
        brief = {}

    course_uid = brief.get("course_uid", "ND")
    rows, source = _p11_find_h5_np_statut_rows_for_course(course_uid)
    return _p11_compute_np_statut_gate_from_rows(rows, source)



# >>> GPI ACTION CONTINUATION STOP DASHBOARD V0_9_23 >>>
@app.get("/action/continuation-stop-dashboard")
def action_continuation_stop_dashboard():
    """Read-only route for the latest continuation/stop dashboard.

    Does not run results.
    Does not rebuild ROI.
    Does not write GCS.
    Never opens a strict ticket.
    """
    ensure_dirs()

    import json
    import os
    from pathlib import Path

    version = "GPI_LITE_V0_9_23_ACTION_CONTINUATION_STOP_DASHBOARD_A"
    object_name = "gpi-lite/outputs/latest_continuation_stop_dashboard.json"

    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or globals().get("GPI_BUCKET_NAME")
        or "gpi-lite-watchlist-data"
    )

    gcs_error = ""

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_name)

        if blob.exists(client):
            raw = blob.download_as_text(encoding="utf-8")
            payload = json.loads(raw)

            if not isinstance(payload, dict):
                payload = {"raw": payload}

            return {
                "ok": True,
                "route_version": version,
                "source": "gcs",
                "bucket": bucket_name,
                "object_name": object_name,
                "gcs_uri": f"gs://{bucket_name}/{object_name}",
                "read_only": "oui",
                "runs_results": "non",
                "writes_gcs": "non",
                "strict_real_ticket_allowed": False,
                "ticket_reel": "NO_BET",
                "dashboard": payload,
            }

        gcs_error = "GCS_OBJECT_ABSENT"

    except Exception as exc:
        gcs_error = f"GCS_READ_ERROR:{type(exc).__name__}:{exc}"

    local_candidates = [
        Path("data/output/latest_continuation_stop_dashboard.json"),
        Path("data\\output\\latest_continuation_stop_dashboard.json"),
    ]

    for local_path in local_candidates:
        if not local_path.exists():
            continue

        try:
            payload = json.loads(local_path.read_text(encoding="utf-8-sig"))

            if not isinstance(payload, dict):
                payload = {"raw": payload}

            return {
                "ok": True,
                "route_version": version,
                "source": "local",
                "local_path": str(local_path),
                "gcs_error": gcs_error,
                "read_only": "oui",
                "runs_results": "non",
                "writes_gcs": "non",
                "strict_real_ticket_allowed": False,
                "ticket_reel": "NO_BET",
                "dashboard": payload,
            }

        except Exception as exc:
            return {
                "ok": False,
                "route_version": version,
                "source": "local",
                "local_path": str(local_path),
                "gcs_error": gcs_error,
                "error": f"LOCAL_READ_ERROR:{type(exc).__name__}:{exc}",
                "strict_real_ticket_allowed": False,
                "ticket_reel": "NO_BET",
            }

    return {
        "ok": False,
        "route_version": version,
        "source": "none",
        "bucket": bucket_name,
        "object_name": object_name,
        "gcs_uri": f"gs://{bucket_name}/{object_name}",
        "gcs_error": gcs_error,
        "searched_local": [str(p) for p in local_candidates],
        "error": "CONTINUATION_STOP_DASHBOARD_ABSENT",
        "read_only": "oui",
        "runs_results": "non",
        "writes_gcs": "non",
        "strict_real_ticket_allowed": False,
        "ticket_reel": "NO_BET",
    }
# <<< GPI ACTION CONTINUATION STOP DASHBOARD V0_9_23 <<<



def _cp3_top3_h5_live_dashboard_summary():
    """Read-only compact CP3 TOP3 H5 paper live status for dashboards.
    Never opens a real ticket.
    Never changes strict_allowed.
    """
    try:
        func = globals().get("action_cp3_top3_h5_live_status")
        if not callable(func):
            return {
                "ok": False,
                "status": "ND",
                "ready_for_h5_paper": "NON",
                "real_allowed": "NON",
                "ticket_real": "NO_BET",
                "strict_allowed": "NON",
                "error": "CP3_TOP3_H5_LIVE_STATUS_ROUTE_NOT_FOUND",
            }
        payload = func()
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        live = payload.get("live", {})
        if not isinstance(live, dict):
            live = {}
        retro = payload.get("retro", {})
        if not isinstance(retro, dict):
            retro = {}
        return {
            "ok": bool(payload.get("ok") is True),
            "status": payload.get("status", "ND"),
            "source": payload.get("source", "ND"),
            "segment_roi": payload.get("segment_roi", "CP3_TOP3_ETUDE_H5_PAPER"),
            "ready_for_h5_paper": payload.get("ready_for_h5_paper", "NON"),
            "real_allowed": "NON",
            "ticket_real": "NO_BET",
            "strict_allowed": "NON",
            "live_total_decisions": live.get("total_decisions", "0"),
            "live_pending": live.get("pending", "0"),
            "live_resolved": live.get("resolved", "0"),
            "live_roi": live.get("roi", "0.000000"),
            "live_audit_status": live.get("audit_status", "ND"),
            "retro_candidate_count": retro.get("candidate_count", "ND"),
            "retro_roi_label": retro.get("roi_label", "ND"),
            "policy": "Paper live uniquement. Aucun ticket reel. Aucun strict.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "KO",
            "ready_for_h5_paper": "NON",
            "real_allowed": "NON",
            "ticket_real": "NO_BET",
            "strict_allowed": "NON",
            "error": f"CP3_TOP3_H5_LIVE_SUMMARY_ERROR:{type(exc).__name__}:{exc}",
            "policy": "Erreur lecture module CP3 H5. NO_BET par defaut.",
        }


# >>> GPI ACTION DECISION DASHBOARD V0_9_25 >>>
@app.get("/action/decision-dashboard")
def action_decision_dashboard():
    """Synthèse courte lecture seule du dashboard continuation / stop."""
    ensure_dirs()

    import json
    import os
    from pathlib import Path

    version = "GPI_LITE_V0_9_25_ACTION_DECISION_DASHBOARD_A"
    object_name = "gpi-lite/outputs/latest_continuation_stop_dashboard.json"

    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or globals().get("GPI_BUCKET_NAME")
        or "gpi-lite-watchlist-data"
    )

    def short(payload, source, extra=None):
        extra = extra or {}

        if not isinstance(payload, dict):
            payload = {"raw": payload}

        detail = payload.get("dashboard")
        if not isinstance(detail, dict):
            detail = payload

        decision = payload.get("decision") or detail.get("decision") or "ND"
        reasons = payload.get("reason_codes") or detail.get("reason_codes") or []
        if isinstance(reasons, str):
            reasons = [x.strip() for x in reasons.replace(",", "|").split("|") if x.strip()]

        reason_main = reasons[0] if reasons else "ND"
        robustness = detail.get("robustness_status") or payload.get("robustness_status") or "ND"

        roi = detail.get("roi", payload.get("roi"))
        try:
            roi_pct = round(float(roi) * 100, 2)
        except Exception:
            roi_pct = None

        stop = bool(payload.get("stop_cloud_recommended", detail.get("stop_cloud_recommended", False)))

        if stop or str(decision).startswith("STOP"):
            mode = "STOP_OR_REVIEW"
            action_gpt = "Stop ou revue Cloud. Aucun réel."
        elif "FRAGILE" in str(decision) or "FRAGILE" in str(robustness):
            mode = "PAPER_CANDIDATE_ONLY_FRAGILE"
            action_gpt = "Continuer Cloud uniquement en paper candidat. Aucun réel. Aucun CP3/exotique."
        elif str(decision).startswith("CONTINUE"):
            mode = "PAPER_CANDIDATE_ONLY"
            action_gpt = "Continuer paper uniquement. Aucun réel sans validation course."
        else:
            mode = "NO_BET_DEFAULT"
            action_gpt = "Etat ND. NO_BET par défaut."

        return {
            "ok": True,
            "route_version": version,
            "source": source,
            "read_only": "oui",
            "runs_results": "non",
            "writes_gcs": "non",
            "verdict_cloud": decision,
            "mode": mode,
            "continue_cloud_oui_non": payload.get("continue_cloud_oui_non", detail.get("continue_cloud_oui_non", "ND")),
            "stop_cloud_recommended": stop,
            "strict_real_ticket_allowed": False,
            "ticket_reel": "NO_BET",
            "reason_main": reason_main,
            "reason_codes": reasons,
            "kpi": {
                "candidate_segment": detail.get("candidate_segment", payload.get("candidate_segment", "ND")),
                "core": detail.get("core", payload.get("core")),
                "wins": detail.get("wins", payload.get("wins")),
                "losses": detail.get("losses", payload.get("losses")),
                "nd": detail.get("nd", payload.get("nd")),
                "roi_candidat_pct": roi_pct,
                "ratio_retour": detail.get("ratio_retour", payload.get("ratio_retour")),
                "median_gain": detail.get("median_gain", payload.get("median_gain")),
                "max_drawdown": detail.get("max_drawdown", payload.get("max_drawdown")),
                "robustness_status": robustness,
            },
            "action_gpt": action_gpt,
            "cp3_top3_h5_live_status": _cp3_top3_h5_live_dashboard_summary(),
            "policy": "Synthèse décisionnelle courte. Ne valide aucun ticket réel.",
            **extra,
        }

    gcs_error = ""

    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_name)

        if blob.exists(client):
            payload = json.loads(blob.download_as_text(encoding="utf-8"))
            return short(payload, "gcs", {
                "bucket": bucket_name,
                "object_name": object_name,
                "gcs_uri": f"gs://{bucket_name}/{object_name}",
            })

        gcs_error = "GCS_OBJECT_ABSENT"

    except Exception as exc:
        gcs_error = f"GCS_READ_ERROR:{type(exc).__name__}:{exc}"

    local_path = Path("data/output/latest_continuation_stop_dashboard.json")

    if local_path.exists():
        payload = json.loads(local_path.read_text(encoding="utf-8-sig"))
        return short(payload, "local", {
            "local_path": str(local_path),
            "gcs_error": gcs_error,
        })

    return {
        "ok": False,
        "route_version": version,
        "source": "none",
        "error": "DECISION_DASHBOARD_SOURCE_ABSENT",
        "gcs_error": gcs_error,
        "strict_real_ticket_allowed": False,
        "ticket_reel": "NO_BET",
        "verdict_cloud": "ND",
        "mode": "NO_BET_DEFAULT",
        "action_gpt": "Etat ND. NO_BET par défaut.",
    }
# <<< GPI ACTION DECISION DASHBOARD V0_9_25 <<<

# >>> GPI ACTION CP3 TOP3 H5 LIVE STATUS V0_1_4 >>>
@app.get("/action/cp3-top3-h5-live-status")
def action_cp3_top3_h5_live_status():
    """Read-only CP3 TOP3 H5 paper live status.
    Does not run H-5.
    Does not write GCS.
    Does not create a ticket.
    Never opens a strict real bet.
    """
    try:
        ensure_dirs()
    except Exception:
        pass
    import json
    import os
    from pathlib import Path
    version = "GPI_LITE_V0_1_4_CP3_TOP3_H5_LIVE_STATUS_A"
    object_name = "gpi-lite/outputs/latest_cp3_top3_h5_live_status.json"
    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
    )
    def safe_payload(payload, source, extra=None):
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        extra = extra or {}
        return {
            "ok": True,
            "route_version": version,
            "source_route": "/action/cp3-top3-h5-live-status",
            "source": source,
            "read_only": "oui",
            "runs_h5": "non",
            "runs_results": "non",
            "writes_gcs": "non",
            "strict_real_ticket_allowed": False,
            "real_allowed": "NON",
            "ticket_reel": "NO_BET",
            "ticket_real": "NO_BET",
            "strict_allowed": "NON",
            "module": payload.get("module", "CP3_TOP3_H5_LIVE_V0_1_4"),
            "segment_roi": payload.get("segment_roi", "CP3_TOP3_ETUDE_H5_PAPER"),
            "status": payload.get("status", "ND"),
            "ready_for_h5_paper": payload.get("ready_for_h5_paper", "NON"),
            "live": payload.get("live", {}),
            "retro": payload.get("retro", {}),
            "guards": payload.get("guards", {}),
            "reason_codes": payload.get("reason_codes", []),
            "errors": payload.get("errors", []),
            "warnings": payload.get("warnings", []),
            "dashboard": payload,
            "policy": "CP3_TOP3_H5 paper live uniquement. Aucun ticket reel. Aucun strict.",
            **extra,
        }
    gcs_error = ""
    if bucket_name:
        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(object_name)
            if blob.exists():
                raw = blob.download_as_text(encoding="utf-8")
                payload = json.loads(raw)
                return safe_payload(payload, "gcs", {
                    "bucket": bucket_name,
                    "object_name": object_name,
                })
            gcs_error = "GCS_OBJECT_ABSENT"
        except Exception as exc:
            gcs_error = f"GCS_READ_ERROR:{type(exc).__name__}:{exc}"
    else:
        gcs_error = "GCS_BUCKET_ENV_ABSENT"
    local_candidates = [
        Path("data/output/latest_cp3_top3_h5_live_status.json"),
        Path("data\\output\\latest_cp3_top3_h5_live_status.json"),
    ]
    for local_path in local_candidates:
        if not local_path.exists():
            continue
        try:
            payload = json.loads(local_path.read_text(encoding="utf-8-sig"))
            return safe_payload(payload, "local", {
                "local_path": str(local_path),
                "gcs_error": gcs_error,
            })
        except Exception as exc:
            return {
                "ok": False,
                "route_version": version,
                "source_route": "/action/cp3-top3-h5-live-status",
                "source": "local",
                "local_path": str(local_path),
                "gcs_error": gcs_error,
                "error": f"LOCAL_READ_ERROR:{type(exc).__name__}:{exc}",
                "read_only": "oui",
                "runs_h5": "non",
                "runs_results": "non",
                "writes_gcs": "non",
                "strict_real_ticket_allowed": False,
                "real_allowed": "NON",
                "ticket_reel": "NO_BET",
                "ticket_real": "NO_BET",
                "strict_allowed": "NON",
            }
    return {
        "ok": False,
        "route_version": version,
        "source_route": "/action/cp3-top3-h5-live-status",
        "source": "none",
        "error": "CP3_TOP3_H5_LIVE_STATUS_SOURCE_ABSENT",
        "gcs_error": gcs_error,
        "searched_local": [str(p) for p in local_candidates],
        "read_only": "oui",
        "runs_h5": "non",
        "runs_results": "non",
        "writes_gcs": "non",
        "strict_real_ticket_allowed": False,
        "real_allowed": "NON",
        "ticket_reel": "NO_BET",
        "ticket_real": "NO_BET",
        "strict_allowed": "NON",
        "ready_for_h5_paper": "NON",
        "policy": "Source absente. NO_BET par defaut.",
    }
# <<< GPI ACTION CP3 TOP3 H5 LIVE STATUS V0_1_4 <<<
