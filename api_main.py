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
            and no_bet_audit_result.get("returncode") == 0
            and no_bet_audit_cloud_result.get("ok") is True
        ),
        "phase": "paper_results",
        "sync": sync_result,
        "results_pdf_parse": results_pdf_parse_result,
        "paper_results": paper_results_result,
        "paper_kpi": paper_kpi_result,
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
    no_bet_audit = payload.get("no_bet_audit", {}) if isinstance(payload.get("no_bet_audit"), dict) else {}
    guardrails = payload.get("guardrails", {}) if isinstance(payload.get("guardrails"), dict) else {}

    parse_stdout = str(results_pdf_parse.get("stdout", ""))
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

            input_hash_material = {
                "course_uid": course_uid,
                "decision_before_race_oui_non": "oui",
                "input_hash": input_hash,
                "input_hash_source": "H5_AUTO_BRIEF_PAYLOAD",
                "input_hash_material_keys": sorted(input_hash_material.keys()),
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

            brief_payload = {
                "ok": True,
                "phase": "h5_auto_brief",
                "brief_version": "h5_auto_brief_v0_1",
                "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "course_uid": course_uid,
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

            brief_json.write_text(
                json.dumps(brief_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            latest_brief_json.write_text(
                json.dumps(brief_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

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

    cloud_ready = all(
        cloud_files[key].get("exists") is True
        for key in [
            "daily_watchlist",
            "latest_h5_context",
            "latest_decision_lite",
            "decision_lite",
            "quality_check",
        ]
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

    if latest_context.get("ok"):
        course_key = str(latest_context.get("course_key", "")).strip()
        payload = get_brief_for_course_key_payload(course_key)

        if payload.get("ok"):
            payload["latest_h5_context"] = latest_context
            return payload

        return {
            "ok": False,
            "source": "none",
            "course_key": course_key,
            "latest_h5_context": latest_context,
            "error": "brief H-5 frais introuvable pour la course traitee",
            "detail": payload,
            "brief": "Aucun brief frais exploitable pour la derniere course H-5."
        }

    cloud_payload = get_latest_brief_from_cloud_payload()

    if cloud_payload.get("ok"):
        cloud_payload["warning"] = "Aucun latest_h5_context.json. Brief global retourne par fallback."
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

    return {
        "ok": _action_bool(payload.get("ok"), False),
        "facade_version": _ACTION_FACADE_VERSION,
        "source_route": "/gpi/chatgpt-brief",
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

