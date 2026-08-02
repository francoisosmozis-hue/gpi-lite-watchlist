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
from v20_p1_cloud_auditor import VERSION as V20_P1_AUDITOR_VERSION
from v20_p1_cloud_auditor import run_cloud_audit
from v20_p1_3_prospective import VERSION as V20_P1_3_VERSION
from v20_p1_3_prospective import get_status as v20_p13_status
from v20_p1_3_prospective import run_capture_latest as v20_p13_capture
from v20_p1_3_prospective import run_post_h5_capture as v20_p13_post_h5_capture
from v20_p1_5_prospective_tracker import VERSION as V20_P1_5_VERSION
from v20_p1_5_prospective_tracker import get_status as v20_p15_status
from v20_p1_5_prospective_tracker import run_capture_latest as v20_p15_capture
from v20_p1_5_prospective_tracker import run_settle_due as v20_p15_settle
from v20_p1_5_prospective_tracker import run_post_h5_capture as v20_p15_post_h5_capture
from v20_p1_5_prospective_tracker import run_post_results_settlement as v20_p15_post_results_settlement
from v20_p1_3_prospective import run_settle_due as v20_p13_settle

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

# GPI_V20_P1_CLOUD_AUDITOR_ROUTES
@app.get("/action/v20/p1-audit-status", dependencies=[Depends(require_api_key)])
def action_v20_p1_audit_status():
    return {
        "ok": True, "version": V20_P1_AUDITOR_VERSION,
        "mode": "AUDIT_ONLY", "cloud_only": True,
        "can_open_strict": False, "ticket_strict": "NO_BET",
        "bankroll_eur": 1000.0, "standard_stake_eur": 1.0,
        "cp3_stake_eur": 3.0,
    }


@app.post("/action/v20/p1-audit-run", dependencies=[Depends(require_api_key)])
def action_v20_p1_audit_run(max_documents: int = 5000):
    report = run_cloud_audit(max_documents=max(1, min(int(max_documents), 20000)))
    report["can_open_strict"] = False
    report["ticket_strict"] = "NO_BET"
    return report


# GPI_V20_P1_3_PROSPECTIVE_API_V1
@app.get("/action/v20/p1-3/status", dependencies=[Depends(require_api_key)])
def action_v20_p13_status():
    return v20_p13_status()


@app.post("/action/v20/p1-3/capture-latest", dependencies=[Depends(require_api_key)])
def action_v20_p13_capture_latest():
    return v20_p13_capture()


@app.post("/action/v20/p1-3/settle-due", dependencies=[Depends(require_api_key)])
def action_v20_p13_settle_due(max_captures: int = 1000):
    return v20_p13_settle(max_captures=max(1, min(int(max_captures), 5000)))


@app.post("/scheduler/v20/p1-3/run", dependencies=[Depends(require_api_key)])
def scheduler_v20_p13_run():
    capture = v20_p13_capture()
    settlement = v20_p13_settle(max_captures=1000)
    return {
        "ok": bool(capture.get("ok") and settlement.get("ok")),
        "version": V20_P1_3_VERSION,
        "mode": "PROSPECTIVE_OBSERVATION_ONLY",
        "capture": capture,
        "settlement": settlement,
        "can_open_strict": False,
        "ticket_strict": "NO_BET",
    }


# GPI_V20_P1_5_PROSPECTIVE_TRACKER_API_V1
@app.get("/action/v20/p1-5/status", dependencies=[Depends(require_api_key)])
def action_v20_p15_status():
    return v20_p15_status()


@app.post("/action/v20/p1-5/capture-latest", dependencies=[Depends(require_api_key)])
def action_v20_p15_capture_latest():
    return v20_p15_capture()


@app.post("/action/v20/p1-5/settle-due", dependencies=[Depends(require_api_key)])
def action_v20_p15_settle_due(max_captures: int = 1000):
    return v20_p15_settle(max_captures=max(1, min(int(max_captures), 5000)))


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

    exact_name = f"{prefix}{suffix}"

    if suffix.startswith("latest_"):
        try:
            exact_blob = bucket.get_blob(exact_name)
        except Exception as exc:
            return None, f"{exact_name}: {type(exc).__name__}: {exc}"

        if exact_blob is None:
            return None, f"aucun fichier exact {exact_name}"

        return exact_blob, ""

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

    def _blob_order_key(blob):
        updated = getattr(blob, "updated", None)
        generation = getattr(blob, "generation", None)

        return (
            updated.timestamp() if updated else 0.0,
            str(getattr(blob, "name", "") or ""),
            int(generation or 0),
        )

    latest = max(candidates, key=_blob_order_key)

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



# >>> GPI DRIVE H5 BLOCKED DATE ROUTE V0_1 >>>
def _gpi_drive_h5_backfill_allowed():
    import os

    return str(os.getenv("GPI_ALLOW_H5_BACKFILL", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "oui",
    }


def _gpi_drive_h5_course_key_from_context(context_9h):
    if not isinstance(context_9h, dict):
        return "ND"

    checked = context_9h.get("checked") or []

    if isinstance(checked, list):
        for item in checked:
            if not isinstance(item, dict):
                continue

            value = (
                item.get("course_key")
                or item.get("course_uid")
                or item.get("h5_pdf")
                or ""
            )

            value = str(value or "").strip()

            if value:
                return value

    return str(context_9h.get("course_key") or context_9h.get("course_uid") or "ND").strip()


def _gpi_drive_h5_date_guard(course_uid):
    import re
    from datetime import datetime

    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
    except Exception:
        today = datetime.now().date().isoformat()

    raw_uid = str(course_uid or "").strip()
    match = re.search(r"(\d{2})_(\d{2})_(\d{4})", raw_uid)

    if not match:
        return {
            "ok": False,
            "blocked": True,
            "reason": "H5_SELECTED_COURSE_DATE_UNREADABLE",
            "course_uid": raw_uid or "ND",
            "course_date": "ND",
            "expected_date": today,
            "backfill_allowed": _gpi_drive_h5_backfill_allowed(),
            "policy": "No live H5 decision may be promoted when course date is unreadable.",
        }

    dd, mm, yyyy = match.groups()
    course_date = f"{yyyy}-{mm}-{dd}"
    backfill_allowed = _gpi_drive_h5_backfill_allowed()

    if course_date != today and not backfill_allowed:
        return {
            "ok": False,
            "blocked": True,
            "reason": "H5_SELECTED_COURSE_NOT_TODAY",
            "course_uid": raw_uid,
            "course_date": course_date,
            "expected_date": today,
            "backfill_allowed": backfill_allowed,
            "policy": "No live H5 decision may be promoted from an old race unless GPI_ALLOW_H5_BACKFILL=true.",
        }

    return {
        "ok": True,
        "blocked": False,
        "reason": "H5_SELECTED_COURSE_DATE_OK",
        "course_uid": raw_uid,
        "course_date": course_date,
        "expected_date": today,
        "backfill_allowed": backfill_allowed,
    }


def _gpi_drive_h5_subprocess_blocked_by_date(h5_result):
    if not isinstance(h5_result, dict):
        return False

    stdout = str(h5_result.get("stdout", "") or "")

    return (
        str(h5_result.get("returncode", "")) == "42"
        or "H5_SELECTED_COURSE_NOT_TODAY" in stdout
        or "H5_SELECTED_COURSE_DATE_UNREADABLE" in stdout
        or "H5 LIVE BLOQUE PAR GARDE-FOU DATE" in stdout
    )


def _gpi_drive_h5_blocked_response(sync_result, context_9h, blocked_guard, h5_result=None, latest_h5_context=None):
    import json

    if not isinstance(blocked_guard, dict):
        blocked_guard = {
            "ok": False,
            "blocked": True,
            "reason": "H5_BLOCKED_BY_DATE",
        }

    blocked_guard_path = OUTPUT_DIR / "latest_h5_blocked_guard.json"

    try:
        blocked_guard_path.write_text(
            json.dumps(blocked_guard, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        blocked_guard = {
            **blocked_guard,
            "blocked_guard_write_error": f"{type(exc).__name__}:{exc}",
        }

    blocked_guard_cloud_sync = {
        "ok": False,
        "skipped": True,
        "reason": "H5_BLOCKED_BY_DATE_GUARD_NOT_UPLOADED",
        "uploads": [],
    }

    try:
        if blocked_guard_path.exists():
            upload_result = upload_file_to_gcs(blocked_guard_path, "outputs")
            blocked_guard_cloud_sync = {
                "ok": bool(isinstance(upload_result, dict) and upload_result.get("ok") is True),
                "files_count": 1,
                "uploads": [upload_result],
                "reason": "ONLY_BLOCKED_GUARD_SYNCED",
            }
    except Exception as exc:
        blocked_guard_cloud_sync = {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
            "reason": "BLOCKED_GUARD_SYNC_FAILED",
            "uploads": [],
        }

    return {
        "ok": False,
        "blocked": True,
        "phase": "h5",
        "reason": blocked_guard.get("reason", "H5_BLOCKED_BY_DATE"),
        "error": blocked_guard.get("reason", "H5_BLOCKED_BY_DATE"),
        "policy": "Old or unreadable H5 race blocked before live promotion.",
        "sync": sync_result,
        "context_9h": context_9h,
        "latest_h5_context": latest_h5_context,
        "h5": h5_result,
        "blocked_guard": blocked_guard,
        "blocked_guard_cloud_sync": blocked_guard_cloud_sync,
        "decision_aliases": {
            "skipped": True,
            "reason": "H5_BLOCKED_BY_DATE",
        },
        "quality": {
            "skipped": True,
            "reason": "H5_BLOCKED_BY_DATE",
        },
        "cloud_sync": {
            "skipped": True,
            "reason": "H5_BLOCKED_BY_DATE",
            "policy": "Full cloud sync skipped after stale H5 block.",
        },
        "brief": {
            "skipped": True,
            "reason": "H5_BLOCKED_BY_DATE",
        },
        "archive": None,
        "ticket_exact": "NO_BET",
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
    }
# <<< GPI DRIVE H5 BLOCKED DATE ROUTE V0_1 <<<

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

    if not context_9h.get("ok"):
        return {
            "ok": False,
            "phase": "h5",
            "error": "fichier 9h correspondant absent pour H-5",
            "sync": sync_result,
            "context_9h": context_9h,
            "latest_h5_context": None,
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

    route_course_key = _gpi_drive_h5_course_key_from_context(context_9h)
    route_h5_date_guard = _gpi_drive_h5_date_guard(route_course_key)

    if route_h5_date_guard.get("blocked") is True:
        return _gpi_drive_h5_blocked_response(
            sync_result=sync_result,
            context_9h=context_9h,
            blocked_guard=route_h5_date_guard,
            h5_result={
                "ok": False,
                "script": "h5_main.py",
                "returncode": 42,
                "stdout": "H5 blocked at /drive/run/h5 before h5_main.py execution.",
                "stderr": "",
            },
            latest_h5_context=None,
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

    # >>> GPI H5 SUBPROCESS BLOCKED RETURN GUARD V0_1 >>>
    if _gpi_drive_h5_subprocess_blocked_by_date(h5_result):
        subprocess_guard = _gpi_drive_h5_date_guard(course_key)

        if subprocess_guard.get("blocked") is not True:
            subprocess_guard = {
                "ok": False,
                "blocked": True,
                "reason": "H5_BLOCKED_BY_SUBPROCESS_RETURN",
                "course_uid": course_key or "ND",
                "course_date": "ND",
                "expected_date": "ND",
                "backfill_allowed": _gpi_drive_h5_backfill_allowed(),
                "policy": "h5_main.py blocked live promotion before write.",
            }

        return _gpi_drive_h5_blocked_response(
            sync_result=sync_result,
            context_9h=context_9h,
            blocked_guard=subprocess_guard,
            h5_result=h5_result,
            latest_h5_context=latest_h5_context,
        )
    # <<< GPI H5 SUBPROCESS BLOCKED RETURN GUARD V0_1 <<<

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

    # GPI_V20_P1_5_RESULTS_SETTLEMENT_HOOK_V1
    p15_results_pipeline_ok = bool(
        results_pdf_parse_result.get("returncode") == 0
        and paper_results_result.get("returncode") == 0
    )
    p15_prospective_settlement = v20_p15_post_results_settlement(
        results_pipeline_ok=p15_results_pipeline_ok
    )

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
        "prospective_settlement_after_results": p15_prospective_settlement,
        "prospective_tracker_version": V20_P1_5_VERSION,
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
            # GPI POST-COURSE GUARDRAILS V0_1
            # Legacy scripts intentionally not used since V0.9.7D
            "legacy_results_main_called": False,
            "legacy_audit_all_called": False,

            # Modern post-course pipeline truth fields
            "results_pdf_parse_called": bool(results_pdf_parse.get("ok") is True),
            "paper_results_called": bool(paper_results.get("ok") is True),
            "paper_kpi_called": bool(paper_kpi.get("ok") is True),
            "candidate_roi_called": bool(candidate_roi.get("ok") is True),
            "candidate_roi_robustness_called": bool(candidate_roi_robustness.get("ok") is True),
            "continuation_stop_dashboard_called": bool(continuation_stop_dashboard.get("ok") is True),
            "no_bet_audit_called": bool(no_bet_audit.get("ok") is True),

            "results_pipeline_effective": bool(
                results_pdf_parse.get("ok") is True
                and paper_results.get("ok") is True
            ),
            "audit_pipeline_effective": bool(
                paper_kpi.get("ok") is True
                and candidate_roi.get("ok") is True
                and candidate_roi_robustness.get("ok") is True
                and continuation_stop_dashboard.get("ok") is True
                and no_bet_audit.get("ok") is True
            ),
            "gcs_sync_effective": bool(
                results_pdf_parse_cloud_sync.get("ok") is True
                and cloud_sync.get("ok") is True
                and kpi_cloud_sync.get("ok") is True
                and candidate_roi_cloud_sync.get("ok") is True
                and candidate_roi_robustness_cloud_sync.get("ok") is True
                and continuation_stop_dashboard_cloud_sync.get("ok") is True
                and no_bet_audit_cloud_sync.get("ok") is True
            ),

            # Safety doctrine
            "h5_main_called": False,
            "decision_engine_outputs_modified": False,
            "paper_trading_50_decisions_uploaded": False,
            "paper_trading_50_decisions_modified": False,
            "post_course_only": True,
            "drive_results_required": False,

            # Backward compatibility only.
            # These fields are legacy and must not be used as modern proof of failure.
            "results_main_called": False,
            "audit_all_called": False,
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




def _h5auto_safe_slug(value, max_len=180):
    """
    GPI V0_9_30B4.2
    Slug technique ASCII-only pour chemins locaux et blobs GCS.

    Doctrine :
    - course_uid officiel conserv? dans les JSON ;
    - chemins techniques toujours ASCII ;
    - correction prudente des mojibakes fr?quents avant slug.
    """
    import hashlib
    import re
    import unicodedata

    raw = str(value or "").strip()
    if not raw:
        return "nd"

    def _repair_mojibake(text):
        text = str(text or "")

        # Cas fr?quent : UTF-8 lu comme latin1/cp1252.
        # Exemple : B\u00c3\u00a9n\u00c3\u00a9voles => B?n?voles
        candidates = [text]
        for enc in ("latin1", "cp1252"):
            try:
                repaired = text.encode(enc).decode("utf-8")
                if repaired and repaired not in candidates:
                    candidates.append(repaired)
            except Exception:
                pass

        # Cas fr?quent console Windows : UTF-8 affich?/recopi? en CP437/CP850.
        # Exemple observ? : B??n??voles.
        replacements = {
            "\u251c\u00ae": "e",  # ??
            "\u251c\u00a8": "e",  # ??
            "\u251c\u00aa": "e",  # ??
            "\u251c\u00a0": "a",  # ? 
            "\u251c\u00a7": "c",  # ??
            "\u251c\u00b4": "o",  # ??
            "\u251c\u00bb": "u",  # ??
            "\u251c\u00b9": "u",  # ??
            "\u00da": "e",         # ?
        }

        for base in list(candidates):
            fixed = base
            for bad, good in replacements.items():
                fixed = fixed.replace(bad, good)
            if fixed and fixed not in candidates:
                candidates.append(fixed)

        bad_markers = [
            "\u00c3",  # ?
            "\u00c2",  # ?
            "\u251c",  # ?
            "\u252c",  # ?
            "\u00da",  # ?
            "\ufffd",  # replacement char
            "?",
        ]

        def score(candidate):
            candidate = str(candidate or "")
            bad = sum(candidate.count(marker) for marker in bad_markers)
            question = candidate.count("?")
            letters = sum(1 for ch in candidate if ch.isalpha())
            return (bad, question, -letters, len(candidate))

        return sorted(candidates, key=score)[0]

    repaired_raw = _repair_mojibake(raw)

    normalized = unicodedata.normalize("NFKD", repaired_raw)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")

    slug = ascii_value.lower()
    slug = slug.replace("&", " et ")
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")

    if not slug:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        slug = f"slug_{digest}"

    if max_len and len(slug) > int(max_len):
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        keep = max(20, int(max_len) - 11)
        slug = f"{slug[:keep].rstrip('_')}_{digest}"

    return slug


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
def drive_run_h5_auto_capture(
    force_technical_capture: str = "non",
    force_course_uid_contains: str = "",
    force_capture_label: str = "",
):
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

    # V0_9_31B2_4_2D_FORCE_TECHNICAL_CAPTURE
    force_technical_capture_oui_non = (
        "oui"
        if str(force_technical_capture or "").strip().lower() in {"oui", "yes", "true", "1"}
        else "non"
    )
    force_course_uid_contains_norm = str(force_course_uid_contains or "").strip().lower()
    force_capture_label_norm = str(force_capture_label or "").strip().upper()

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

        if force_technical_capture_oui_non == "oui":
            if force_course_uid_contains_norm and force_course_uid_contains_norm not in course_uid.lower():
                skipped.append({
                    "course_uid": course_uid,
                    "capture_label": capture_label,
                    "reason": "FORCE_FILTER_COURSE_NOT_MATCHED",
                    "force_course_uid_contains": force_course_uid_contains,
                    "status": status,
                })
                continue

            if force_capture_label_norm and capture_label.upper() != force_capture_label_norm:
                skipped.append({
                    "course_uid": course_uid,
                    "capture_label": capture_label,
                    "reason": "FORCE_FILTER_CAPTURE_LABEL_NOT_MATCHED",
                    "force_capture_label": force_capture_label,
                    "status": status,
                })
                continue

        is_due_capture = _h5auto_is_due_capture(row, now_iso)

        if not is_due_capture and force_technical_capture_oui_non != "oui":
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
            html_blob = f"gpi-lite/raw/zeturf_snapshots/html/{slug}.html"
            odds_csv_blob = f"gpi-lite/outputs/odds/{slug}_odds.csv"
            odds_json_blob = f"gpi-lite/outputs/odds/{slug}_odds.json"

            # V0_9_31B2_4_2C_CLOUD_CAPTURE_HTML_GCS
# V0_9_31B2_4_2D_FORCE_TECHNICAL_CAPTURE
            # Le HTML doit etre uploade avant write_odds_csv afin que le CSV contienne html_gcs_*.
            upload_html = _h5auto_upload_file_to_gcs(html_path, html_blob)

            odds_rows = extract_odds_from_html(
                html_path=html_path,
                course_uid=course_uid,
                snapshot_label=capture_label,
            )

            try:
                from dataclasses import replace as _p342c_replace

                _html_upload_ok = "oui" if upload_html.get("ok") else "non"
                _html_gcs_path = html_blob if upload_html.get("ok") else "ND"
                _html_gcs_uri = upload_html.get("gcs_uri") if upload_html.get("ok") else "ND"
                _html_upload_error = "ND" if upload_html.get("ok") else str(
                    upload_html.get("error")
                    or upload_html.get("reason")
                    or "HTML_GCS_UPLOAD_FAILED"
                )

                odds_rows = [
                    _p342c_replace(
                        row,
                        html_gcs_path=_html_gcs_path,
                        html_gcs_uri=_html_gcs_uri,
                        html_gcs_upload_ok=_html_upload_ok,
                        html_gcs_upload_error=_html_upload_error,
                    )
                    for row in odds_rows
                ]
            except Exception as enrich_exc:
                # Non bloquant : le strict reste ferme, mais on conserve l'erreur dans la capture.
                html_gcs_enrich_error = f"{type(enrich_exc).__name__}:{enrich_exc}"
            else:
                html_gcs_enrich_error = "ND"

            write_odds_csv(odds_rows, odds_csv_path)
            write_odds_json(odds_rows, odds_json_path)

            odds_rows_total += len(odds_rows)

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
                "html_gcs_enrich_error": html_gcs_enrich_error,
                "capture_due_oui_non": "oui" if is_due_capture else "non",
                "force_technical_capture_oui_non": force_technical_capture_oui_non,
                "backfill_technical_oui_non": "oui" if force_technical_capture_oui_non == "oui" else "non",
                "strict_kpi_excluded_oui_non": "oui" if force_technical_capture_oui_non == "oui" else "non",
                "strict_kpi_exclusion_reason": (
                    "FORCE_TECHNICAL_CAPTURE_NOT_PRE_RACE"
                    if force_technical_capture_oui_non == "oui"
                    else "ND"
                ),
                "strict_allowed_oui_non": "non",
                "strict_usable_oui_non": "non",
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
                "capture_due_oui_non": "oui" if is_due_capture else "non",
                "force_technical_capture_oui_non": force_technical_capture_oui_non,
                "backfill_technical_oui_non": "oui" if force_technical_capture_oui_non == "oui" else "non",
                "strict_kpi_excluded_oui_non": "oui" if force_technical_capture_oui_non == "oui" else "non",
                "strict_kpi_exclusion_reason": (
                    "FORCE_TECHNICAL_CAPTURE_NOT_PRE_RACE"
                    if force_technical_capture_oui_non == "oui"
                    else "ND"
                ),
                "strict_allowed_oui_non": "non",
                "strict_usable_oui_non": "non",
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
        "force_technical_capture_oui_non": force_technical_capture_oui_non,
        "force_course_uid_contains": force_course_uid_contains,
        "force_capture_label": force_capture_label,
        "backfill_technical_oui_non": "oui" if force_technical_capture_oui_non == "oui" else "non",
        "strict_kpi_excluded_oui_non": "oui" if force_technical_capture_oui_non == "oui" else "non",
        "strict_kpi_exclusion_reason": (
            "FORCE_TECHNICAL_CAPTURE_NOT_PRE_RACE"
            if force_technical_capture_oui_non == "oui"
            else "ND"
        ),
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

    staking_policy = _gpi_cloud_staking_policy()

    if paper_allowed:
        standard_stake = _gpi_cloud_stake_for_ticket("CP_SEC", allowed=True)
        return {
            "paper_candidate_allowed": "oui",
            "paper_candidate_type": "CP_SEC",
            "paper_candidate_ticket": f"CP_SEC {cp_sec}",
            "paper_candidate_segment": "PAPER_TRADING",
            "paper_candidate_mise": standard_stake,
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
            "paper_stake": standard_stake,
            "strict_block_reasons": (
                "H5_AUTO_BRIEF_READ_ONLY|STRICT_GATES_NOT_VALIDATED|REAL_STRICT_FORBIDDEN"
            ),
            "structure_retenue": "CP_SEC_PAPER",
            "verdict": "PAPER",
            "ticket_exact": "NO_BET",
            "mise_theorique": "0",
            "segment_ROI": "PAPER_TRADING",
            "segment_roi": "PAPER_TRADING",
            "bankroll_eur": staking_policy["bankroll_eur"],
            "standard_stake_eur": staking_policy["standard_stake_eur"],
            "cp3_stake_eur": staking_policy["cp3_stake_eur"],
            "staking_policy_version": staking_policy["policy_version"],
            "staking_policy_source": staking_policy["source"],
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
        "bankroll_eur": staking_policy["bankroll_eur"],
        "standard_stake_eur": staking_policy["standard_stake_eur"],
        "cp3_stake_eur": staking_policy["cp3_stake_eur"],
        "staking_policy_version": staking_policy["policy_version"],
        "staking_policy_source": staking_policy["source"],
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

    # GPI_H5_BRIEF_HISTORY_ARCHIVES_PATCH_V0_9_28B2
    # Liste additive : ne remplace pas le history_archive legacy.
    h5_brief_archive_payloads = []

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
                # GPI_H5_NP_STATUS_EXACT_H5_CSV_PATCH_V0_9_30B3
                # Utilise le CSV H5 exact d?j? t?l?charg? pour ce brief.
                # Fallback conservateur vers la recherche legacy si le CSV exact est illisible.
                np_statut_fields = None

                try:
                    import pandas as pd

                    if h5_local and Path(h5_local).exists():
                        h5_np_rows = pd.read_csv(h5_local)
                        np_statut_fields = _p11_compute_np_statut_gate_from_rows(
                            h5_np_rows,
                            h5_blob or str(h5_local),
                        )
                        if isinstance(np_statut_fields, dict):
                            np_statut_fields["np_statut_exact_h5_csv_oui_non"] = "oui"
                            np_statut_fields["np_statut_exact_h5_csv_local"] = str(h5_local)
                            np_statut_fields["np_statut_exact_h5_csv_gcs"] = h5_blob
                except Exception as exact_exc:
                    np_statut_fields = {
                        "np_ok": "ND",
                        "statut_ok": "ND",
                        "np_source": f"NP_STATUT_EXACT_H5_CSV_ERROR:{type(exact_exc).__name__}",
                        "statut_source": f"NP_STATUT_EXACT_H5_CSV_ERROR:{type(exact_exc).__name__}",
                        "np_count": "ND",
                        "statut_missing_count": "ND",
                        "np_statut_rows_count": 0,
                        "np_statut_source_level": "EXACT_H5_CSV_ERROR",
                        "np_statut_exact_h5_csv_oui_non": "non",
                        "np_statut_exact_h5_csv_local": str(h5_local),
                        "np_statut_exact_h5_csv_gcs": h5_blob,
                        "np_statut_checked_at_utc": datetime.now(timezone.utc).isoformat(),
                    }

                if not isinstance(np_statut_fields, dict):
                    np_statut_fields = _p11_compute_np_statut_gate_for_h5_brief({
                        "course_uid": course_uid,
                        "current_snapshot_label": "H5",
                    })
                    if isinstance(np_statut_fields, dict):
                        np_statut_fields["np_statut_exact_h5_csv_oui_non"] = "non"
                        np_statut_fields["np_statut_fallback_legacy_oui_non"] = "oui"

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
                    "np_statut_exact_h5_csv_oui_non": "non",
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
                "np_statut_exact_h5_csv_oui_non": np_statut_fields.get("np_statut_exact_h5_csv_oui_non", "ND"),
                "np_statut_exact_h5_csv_local": np_statut_fields.get("np_statut_exact_h5_csv_local", "ND"),
                "np_statut_exact_h5_csv_gcs": np_statut_fields.get("np_statut_exact_h5_csv_gcs", "ND"),
                "np_statut_fallback_legacy_oui_non": np_statut_fields.get("np_statut_fallback_legacy_oui_non", "non"),
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
                        "np_statut_rows_count": np_statut_fields.get("np_statut_rows_count", 0),
                        "np_statut_source_level": np_statut_fields.get("np_statut_source_level", "ND"),
                        "np_statut_exact_h5_csv_oui_non": np_statut_fields.get("np_statut_exact_h5_csv_oui_non", "ND"),
                        "np_statut_exact_h5_csv_local": np_statut_fields.get("np_statut_exact_h5_csv_local", "ND"),
                        "np_statut_exact_h5_csv_gcs": np_statut_fields.get("np_statut_exact_h5_csv_gcs", "ND"),
                        "np_statut_fallback_legacy_oui_non": np_statut_fields.get("np_statut_fallback_legacy_oui_non", "non"),
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
                    # V20 P0: le producteur H5 ne peut pas s'auto-attribuer un
                    # split, un audit de fuite ou un replay conformes. Ces
                    # preuves sont produites plus tard par des jobs indépendants.
                    "split": "UNASSIGNED",
                    "calibration_version": "GPI_CALIBRATION_V0_9_2",
                    "leakage_check_ok": "ND",
                    "split_manifest_id": "ND",
                    "leakage_audit_id": "ND",
                    "replay_run_id": "ND",
                    "proof_bundle_hash": "ND",
                    "evidence_level": "P3_RULE_OR_CODE",
                    "proof_status": "UNVERIFIED_SELF_DECLARATION_BLOCKED",
                    "decision_mode": "PAPER_TEST",
                    "decision_before_race_oui_non": decision_before_race_oui_non_effective,
                    "backfill_technical_oui_non": backfill_technical_oui_non,
                    "strict_kpi_excluded_oui_non": strict_kpi_excluded_oui_non,
                    "strict_kpi_exclusion_reason": strict_kpi_exclusion_reason,
                    "created_at": h5_auto_generated_at_utc,
                    "replay_match_oui_non": "ND",
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

            h5_brief_archive_payloads.append({
                "course_uid": course_uid,
                "payload": brief_payload,
            })

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

    # GPI_H5_BRIEF_HISTORY_ARCHIVES_FIX_V0_9_28B2A
    history_archives = []
    history_archive_errors = []

    for _archive_source in h5_brief_archive_payloads:
        try:
            _course_uid = _archive_source.get("course_uid", "H5_AUTO_BRIEF_GLOBAL")
            _payload = _archive_source.get("payload")

            if not isinstance(_payload, dict):
                _payload = {"payload": str(_payload)}

            _exclusion_reason = str(_payload.get("strict_kpi_exclusion_reason", "") or "").strip()

            if _exclusion_reason:
                _archive_status = "POST_START_EXCLUDED"
                _archive_reason = _exclusion_reason
                _decision_before = "non"
                _live_eligible = "non"
                _retro_audit = "oui"
            else:
                _archive_status = "LIVE_BRIEF_CREATED_PRE_RACE"
                _archive_reason = "BRIEF_CREATED"
                _decision_before = "oui"
                _live_eligible = "oui"
                _retro_audit = "non"

            _archive_item = _archive_h5_payload_safe(
                payload=_payload,
                course_uid=_course_uid,
                capture_label="H5",
                source_route="/drive/run/h5-auto-brief",
                artifact_type="h5_auto_brief",
                status=_archive_status,
                reason=_archive_reason,
                decision_before_race_oui_non=_decision_before,
                result_known_at_decision="non",
                live_eligible_oui_non=_live_eligible,
                retro_audit_only_oui_non=_retro_audit,
            )

            if isinstance(_archive_item, dict):
                _archive_item["archive_scope"] = "per_course_brief_v0_9_28B2"
                _archive_item["course_uid"] = _course_uid
                _archive_item["strict_kpi_exclusion_reason"] = _exclusion_reason
                history_archives.append(_archive_item)

        except Exception as exc:
            history_archive_errors.append({
                "course_uid": (
                    _archive_source.get("course_uid", "H5_AUTO_BRIEF_GLOBAL")
                    if isinstance(_archive_source, dict)
                    else "H5_AUTO_BRIEF_GLOBAL"
                ),
                "reason": f"H5_BRIEF_HISTORY_ARCHIVE_ERROR:{type(exc).__name__}:{exc}",
                "ticket_strict": "NO_BET",
                "strict_allowed_oui_non": "non",
            })

    # GPI_H5_BRIEF_LEGACY_SUMMARY_PATCH_V0_9_28C3
    # Le legacy history_archive devient un résumé global distinct.
    # Les archives officielles par course restent dans history_archives.
    legacy_summary_course_uid = "H5_AUTO_BRIEF_GLOBAL_SUMMARY"
    if processed:
        _first_uid = str(processed[0].get("course_uid", "") or "").strip()
        if len(_first_uid) >= 10 and _first_uid[2:3] == "_" and _first_uid[5:6] == "_":
            legacy_summary_course_uid = f"{_first_uid[:10]} - H5_AUTO_BRIEF - GLOBAL_SUMMARY"

    # GPI_H5_BRIEF_LEGACY_SUMMARY_STATUS_PATCH_V0_9_28C4
    # Le résumé global hérite du statut le plus prudent des briefs course.
    legacy_summary_has_post_start_excluded = any(
        isinstance(_archive_item, dict)
        and str(_archive_item.get("status", "") or "").strip() == "POST_START_EXCLUDED"
        for _archive_item in history_archives
    )

    legacy_summary_status = (
        "POST_START_EXCLUDED"
        if legacy_summary_has_post_start_excluded
        else (
            "LIVE_BRIEF_CREATED_PRE_RACE"
            if len(processed) > 0
            else "LIVE_VALID_PRE_RACE"
        )
    )

    legacy_summary_reason = (
        "H5_AUTO_BRIEF_SUMMARY_CONTAINS_POST_START_EXCLUDED"
        if legacy_summary_has_post_start_excluded
        else (
            "BRIEF_CREATED"
            if len(processed) > 0
            else "NO_BRIEF_CREATED"
        )
    )

    legacy_summary_decision_before = (
        "non" if legacy_summary_has_post_start_excluded else "oui"
    )
    legacy_summary_live_eligible = (
        "non" if legacy_summary_has_post_start_excluded else "oui"
    )
    legacy_summary_retro_audit = (
        "oui" if legacy_summary_has_post_start_excluded else "non"
    )

    return {
        "ok": True,
        "phase": "h5_auto_brief",
        "history_archives": history_archives,
        "history_archive_errors": history_archive_errors,
        "history_archive_count": len(history_archives),
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
            course_uid=legacy_summary_course_uid,
            capture_label="SUMMARY",
            source_route="/drive/run/h5-auto-brief",
            artifact_type="h5_auto_brief",
            status=legacy_summary_status,
            reason=legacy_summary_reason,
            decision_before_race_oui_non=legacy_summary_decision_before,
            result_known_at_decision="non",
            live_eligible_oui_non=legacy_summary_live_eligible,
            retro_audit_only_oui_non=legacy_summary_retro_audit,
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

    if cloud_ok:
        h5_context_freshness = _gpi_latest_h5_context_freshness(
            reference_course_uid=str(
                cloud_brief.get("course_uid", "") or ""
            ).strip(),
            reference_source="cloud_brief",
        )
    else:
        h5_context_freshness = {
            "ok": True,
            "latest_h5_context_exists_oui_non": "non",
            "latest_h5_auto_brief_exists_oui_non": "non",
            "latest_h5_context_fresh": False,
            "latest_h5_context_fresh_oui_non": "non",
            "latest_h5_context_mismatch": False,
            "latest_h5_context_mismatch_oui_non": "non",
            "latest_h5_context_course_key": "ND",
            "latest_h5_auto_brief_course_uid": "ND",
            "latest_h5_reference_source": "cloud_brief_unavailable",
            "reason": "CLOUD_BRIEF_NOT_OK",
        }

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

    # GPI STATUS H5 AUTO CONTEXT READY V0_1
    # Lecture seule : ne modifie jamais cloud_ready, strict_allowed_global ni les tickets.
    h5_auto_context_ready_oui_non = "non"
    h5_auto_context_health_source = "/health/h5-report-auto-context"
    h5_auto_context_health_error = ""
    h5_auto_context_course_uid = "ND"
    h5_auto_context_snapshot_kind = "ND"
    h5_auto_context_rows_ok_oui_non = "non"
    h5_auto_context_top_cotes_ok_oui_non = "non"
    h5_auto_context_mouvements_ok_oui_non = "non"
    h5_auto_context_strict_closed_oui_non = "ND"
    h5_auto_context_ticket_real_no_bet_oui_non = "ND"
    legacy_h5_context_stale_oui_non = "ND"

    try:
        # Ne depend pas d'une variable interne potentiellement absente.
        # Le statut legacy est informatif seulement ; il ne modifie pas cloud_ready ni le strict.
        legacy_fresh_value = locals().get("latest_h5_context_fresh", None)
        legacy_fresh_text = str(
            locals().get("latest_h5_context_fresh_oui_non", "")
            or locals().get("latest_h5_context_freshness_oui_non", "")
            or ""
        ).strip().lower()

        if legacy_fresh_value is False or legacy_fresh_text == "non":
            legacy_h5_context_stale_oui_non = "oui"
        elif legacy_fresh_value is True or legacy_fresh_text == "oui":
            legacy_h5_context_stale_oui_non = "non"
        else:
            legacy_h5_context_stale_oui_non = "ND"

        h5_health = health_h5_report_auto_context()
        if not isinstance(h5_health, dict):
            h5_health = {}

        h5_checks = h5_health.get("checks", {})
        if not isinstance(h5_checks, dict):
            h5_checks = {}

        h5_summary = h5_health.get("summary", {})
        if not isinstance(h5_summary, dict):
            h5_summary = {}

        h5_auto_context_course_uid = h5_health.get("course_uid", "ND")
        h5_auto_context_snapshot_kind = h5_summary.get("snapshot_kind", "ND")

        h5_auto_context_rows_ok_oui_non = (
            "oui"
            if h5_checks.get("snapshot_reference_rows_positive") is True
            and h5_checks.get("snapshot_h5_rows_positive") is True
            else "non"
        )

        h5_auto_context_top_cotes_ok_oui_non = (
            "oui" if h5_checks.get("top_cotes_non_empty") is True else "non"
        )

        h5_auto_context_mouvements_ok_oui_non = (
            "oui" if h5_checks.get("mouvements_non_empty") is True else "non"
        )

        h5_auto_context_strict_closed_oui_non = (
            "oui" if h5_checks.get("strict_closed") is True else "non"
        )

        h5_auto_context_ticket_real_no_bet_oui_non = (
            "oui" if h5_checks.get("ticket_real_no_bet") is True else "non"
        )

        h5_auto_context_ready_oui_non = (
            "oui"
            if h5_health.get("ok") is True
            and h5_checks.get("context_mismatch_non") is True
            and h5_checks.get("auto_context_used") is True
            and h5_checks.get("auto_context_aligned") is True
            and h5_checks.get("snapshot_kind_auto") is True
            and h5_checks.get("snapshot_reference_rows_positive") is True
            and h5_checks.get("snapshot_h5_rows_positive") is True
            and h5_checks.get("top_cotes_non_empty") is True
            and h5_checks.get("mouvements_non_empty") is True
            and h5_checks.get("strict_closed") is True
            and h5_checks.get("ticket_real_no_bet") is True
            else "non"
        )

    except Exception as exc:
        h5_auto_context_ready_oui_non = "non"
        h5_auto_context_health_error = f"{type(exc).__name__}:{exc}"

    # GPI STATUS H5 AUTO CONTEXT LATEST UID SAFE V0_1
    # Variable locale securisee pour le shadow gate.
    # Informatif uniquement : ne modifie pas cloud_ready ni le strict.
    latest_course_uid = "ND"
    try:
        _latest_obj = locals().get("latest", {})
        _latest_decision_obj = locals().get("latest_decision", {})
        _paper_status_obj = locals().get("paper_status", {})
        _h5_freshness_obj = locals().get("h5_context_freshness", {})

        # Priorite shadow : la course latest doit suivre le brief H5 auto
        # ou le gate courant, pas le paper_status qui peut pointer vers une autre course.
        _latest_uid_candidates = [
            _h5_freshness_obj.get("latest_h5_auto_brief_course_uid") if isinstance(_h5_freshness_obj, dict) else None,
            locals().get("latest_course_uid_for_gate"),
            locals().get("latest_course_uid"),
            _latest_obj.get("course_uid") if isinstance(_latest_obj, dict) else None,
            _latest_obj.get("course_key") if isinstance(_latest_obj, dict) else None,
            _latest_obj.get("latest_course_uid") if isinstance(_latest_obj, dict) else None,
            _latest_decision_obj.get("course_uid") if isinstance(_latest_decision_obj, dict) else None,
            _latest_decision_obj.get("course_key") if isinstance(_latest_decision_obj, dict) else None,
            _latest_decision_obj.get("latest_course_uid") if isinstance(_latest_decision_obj, dict) else None,
            _paper_status_obj.get("latest_course_uid") if isinstance(_paper_status_obj, dict) else None,
        ]

        _latest_uid_source_names = [
            "h5_context_freshness.latest_h5_auto_brief_course_uid",
            "locals.latest_course_uid_for_gate",
            "locals.latest_course_uid",
            "latest.course_uid",
            "latest.course_key",
            "latest.latest_course_uid",
            "latest_decision.course_uid",
            "latest_decision.course_key",
            "latest_decision.latest_course_uid",
            "paper_status.latest_course_uid",
        ]

        for _idx, _candidate_uid in enumerate(_latest_uid_candidates):
            _candidate_uid = str(_candidate_uid or "").strip()
            if _candidate_uid and _candidate_uid != "ND":
                latest_course_uid = _candidate_uid
                try:
                    cloud_ready_shadow_latest_uid_source = _latest_uid_source_names[_idx]
                except Exception:
                    cloud_ready_shadow_latest_uid_source = "UNKNOWN_SOURCE"
                break

    except Exception:
        latest_course_uid = "ND"

    # GPI STATUS CLOUD READY SHADOW AUDIT COMPUTE V0_1
    # Mini-audit persistant du shadow cloud_ready.
    # N'ouvre jamais cloud_ready, strict_allowed_global ni les tickets.
    cloud_ready_shadow_audit_version = "CLOUD_READY_SHADOW_AUDIT_V0_1"
    cloud_ready_shadow_required_runs = 3
    cloud_ready_shadow_green_runs = 0
    cloud_ready_shadow_audit_ok_oui_non = "non"
    cloud_ready_shadow_current_green_oui_non = "non"
    cloud_ready_shadow_last_mismatch_reason = "INIT"
    cloud_ready_shadow_audit_error = ""
    cloud_ready_shadow_audit_path = "gpi-lite/outputs/cloud_ready_shadow_audit.json"
    cloud_ready_shadow_latest_run_key = "ND"
    # Conserve la source choisie par le bloc latest UID safe.
    cloud_ready_shadow_latest_uid_source = str(
        locals().get("cloud_ready_shadow_latest_uid_source", "ND") or "ND"
    ).strip()

    try:
        import json as _json
        from datetime import datetime as _datetime

        _latest_obj = locals().get("latest", {})
        _latest_decision_obj = locals().get("latest_decision", {})
        _paper_status_obj = locals().get("paper_status", {})
        _h5_freshness_obj = locals().get("h5_context_freshness", {})

        _run_key_candidates = [
            locals().get("latest_input_hash"),
            _latest_obj.get("input_hash") if isinstance(_latest_obj, dict) else None,
            _latest_obj.get("latest_input_hash") if isinstance(_latest_obj, dict) else None,
            _latest_obj.get("decision_id") if isinstance(_latest_obj, dict) else None,
            _latest_obj.get("latest_decision_id") if isinstance(_latest_obj, dict) else None,
            _latest_decision_obj.get("input_hash") if isinstance(_latest_decision_obj, dict) else None,
            _latest_decision_obj.get("decision_id") if isinstance(_latest_decision_obj, dict) else None,
            _paper_status_obj.get("latest_decision_id") if isinstance(_paper_status_obj, dict) else None,
            latest_course_uid,
        ]

        for _run_key in _run_key_candidates:
            _run_key = str(_run_key or "").strip()
            if _run_key and _run_key != "ND":
                cloud_ready_shadow_latest_run_key = _run_key
                break

        _legacy_stale = (
            str(_h5_freshness_obj.get("latest_h5_context_fresh_oui_non", "non")).strip().lower() == "non"
        )

        _current_green = (
            cloud_ready is not True
            and h5_auto_context_ready_oui_non == "oui"
            and str(h5_auto_context_course_uid).strip() != "ND"
            and str(latest_course_uid).strip() != "ND"
            and str(h5_auto_context_course_uid).strip() == str(latest_course_uid).strip()
            and h5_auto_context_rows_ok_oui_non == "oui"
            and h5_auto_context_top_cotes_ok_oui_non == "oui"
            and h5_auto_context_mouvements_ok_oui_non == "oui"
            and h5_auto_context_strict_closed_oui_non == "oui"
            and h5_auto_context_ticket_real_no_bet_oui_non == "oui"
            and _legacy_stale is True
        )

        cloud_ready_shadow_current_green_oui_non = "oui" if _current_green else "non"

        if not _current_green:
            if cloud_ready is True:
                cloud_ready_shadow_last_mismatch_reason = "LEGACY_CLOUD_READY_ALREADY_TRUE"
            elif h5_auto_context_ready_oui_non != "oui":
                cloud_ready_shadow_last_mismatch_reason = "H5_AUTO_CONTEXT_NOT_READY"
            elif str(h5_auto_context_course_uid).strip() != str(latest_course_uid).strip():
                cloud_ready_shadow_last_mismatch_reason = "H5_AUTO_CONTEXT_NOT_ALIGNED_WITH_LATEST"
            elif not _legacy_stale:
                cloud_ready_shadow_last_mismatch_reason = "LEGACY_H5_CONTEXT_NOT_STALE"
            else:
                cloud_ready_shadow_last_mismatch_reason = "H5_AUTO_CONTEXT_SHADOW_GREEN_CONDITION_FAILED"
        else:
            cloud_ready_shadow_last_mismatch_reason = "CURRENT_RUN_GREEN_WAITING_AUDIT_HISTORY"

        _audit_doc = {
            "audit_version": cloud_ready_shadow_audit_version,
            "required_runs": cloud_ready_shadow_required_runs,
            "history": [],
        }

        _bucket, _bucket_error = get_gcs_bucket()
        if _bucket is None:
            cloud_ready_shadow_audit_error = f"GCS_BUCKET_ERROR:{_bucket_error}"
            cloud_ready_shadow_last_mismatch_reason = "AUDIT_STORAGE_UNAVAILABLE"
        else:
            _blob = _bucket.blob(cloud_ready_shadow_audit_path)

            try:
                if _blob.exists():
                    _raw = _blob.download_as_text(encoding="utf-8")
                    _loaded = _json.loads(_raw or "{}")
                    if isinstance(_loaded, dict):
                        _audit_doc = _loaded
            except Exception as _read_exc:
                cloud_ready_shadow_audit_error = f"READ_AUDIT_ERROR:{type(_read_exc).__name__}:{_read_exc}"

            _history = _audit_doc.get("history", [])
            if not isinstance(_history, list):
                _history = []

            # Anti-inflation : un meme run_key ne doit pas compter plusieurs fois.
            _history = [
                _row for _row in _history
                if isinstance(_row, dict)
                and str(_row.get("run_key", "")).strip() != cloud_ready_shadow_latest_run_key
            ]

            _history.append({
                "run_key": cloud_ready_shadow_latest_run_key,
                "course_uid": str(latest_course_uid),
                "h5_auto_context_course_uid": str(h5_auto_context_course_uid),
                "green_oui_non": cloud_ready_shadow_current_green_oui_non,
                "reason": cloud_ready_shadow_last_mismatch_reason,
                "cloud_ready_legacy_oui_non": "oui" if cloud_ready is True else "non",
                "h5_auto_context_ready_oui_non": h5_auto_context_ready_oui_non,
                "h5_auto_context_ready_for_cloud_gate_oui_non": (
                    "oui" if _current_green else "non"
                ),
                "legacy_h5_context_stale_oui_non": "oui" if _legacy_stale else "non",
                "recorded_at_utc": _datetime.utcnow().isoformat() + "Z",
            })

            _history = _history[-20:]

            _consecutive_green = 0
            for _row in reversed(_history):
                if isinstance(_row, dict) and _row.get("green_oui_non") == "oui":
                    _consecutive_green += 1
                else:
                    break

            cloud_ready_shadow_green_runs = _consecutive_green
            cloud_ready_shadow_audit_ok_oui_non = (
                "oui" if _consecutive_green >= cloud_ready_shadow_required_runs else "non"
            )

            if cloud_ready_shadow_audit_ok_oui_non != "oui" and _current_green:
                cloud_ready_shadow_last_mismatch_reason = (
                    f"NOT_ENOUGH_CONSECUTIVE_GREEN_RUNS_{_consecutive_green}_OF_{cloud_ready_shadow_required_runs}"
                )
                if _history:
                    _history[-1]["reason"] = cloud_ready_shadow_last_mismatch_reason

            _audit_doc = {
                "audit_version": cloud_ready_shadow_audit_version,
                "required_runs": cloud_ready_shadow_required_runs,
                "green_runs": cloud_ready_shadow_green_runs,
                "audit_ok_oui_non": cloud_ready_shadow_audit_ok_oui_non,
                "latest_run_key": cloud_ready_shadow_latest_run_key,
                "latest_reason": cloud_ready_shadow_last_mismatch_reason,
                "history": _history,
                "doctrine": "Audit shadow uniquement. Ne modifie jamais cloud_ready ni strict_allowed_global.",
            }

            try:
                _blob.upload_from_string(
                    _json.dumps(_audit_doc, ensure_ascii=False, indent=2),
                    content_type="application/json",
                )
            except Exception as _write_exc:
                cloud_ready_shadow_audit_error = f"WRITE_AUDIT_ERROR:{type(_write_exc).__name__}:{_write_exc}"

    except Exception as _audit_exc:
        cloud_ready_shadow_audit_ok_oui_non = "non"
        cloud_ready_shadow_current_green_oui_non = "non"
        cloud_ready_shadow_audit_error = f"{type(_audit_exc).__name__}:{_audit_exc}"
        cloud_ready_shadow_last_mismatch_reason = "AUDIT_EXCEPTION"

    # GPI STATUS CLOUD READY NEXT SHADOW V0_1
    # Lecture seule : ne remplace jamais cloud_ready.
    cloud_ready_next_oui_non = "non"
    cloud_ready_next_reason = "INIT"
    cloud_ready_next_source = "shadow_audit_only"
    cloud_ready_next_cloud_brief_source = "ND"
    cloud_ready_next_cloud_brief_diag = "ND"
    cloud_ready_next_latest_uid_for_alignment = "ND"
    cloud_ready_next_latest_uid_source = "ND"
    cloud_ready_next_differs_from_legacy_oui_non = "non"

    try:
        _cloud_ready_files_ok = bool(locals().get("cloud_ready_files", False))

        # CLOUD_READY_NEXT CLOUD_BRIEF SAFE SOURCE V0_1
        # Ne depend pas uniquement d'une variable locale cloud_brief_ok.
        # Si elle est absente, on utilise l'equivalent H5 auto_context shadow deja audite.
        _cloud_brief_ok_local = bool(locals().get("cloud_brief_ok", False))
        # CLOUD_READY_NEXT H5 BRIEF DIAG V0_1
        _cloud_brief_h5_ready_ok = h5_auto_context_ready_oui_non == "oui"
        _cloud_brief_h5_course_ok = str(h5_auto_context_course_uid).strip() != "ND"

        # CLOUD_READY_NEXT LATEST UID ALIGNMENT V0_1
        # Utilise le meme principe que le shadow audit : priorite au brief H5 auto.
        _cloud_ready_next_h5_freshness_obj = locals().get("h5_context_freshness", {})
        if not isinstance(_cloud_ready_next_h5_freshness_obj, dict):
            _cloud_ready_next_h5_freshness_obj = {}

        _cloud_ready_next_latest_uid_candidates = [
            _cloud_ready_next_h5_freshness_obj.get("latest_h5_auto_brief_course_uid"),
            locals().get("latest_course_uid_for_gate"),
            locals().get("latest_h5_auto_brief_course_uid"),
            locals().get("latest_course_uid"),
            latest_course_uid,
        ]

        _cloud_ready_next_latest_uid_source_names = [
            "h5_context_freshness.latest_h5_auto_brief_course_uid",
            "locals.latest_course_uid_for_gate",
            "locals.latest_h5_auto_brief_course_uid",
            "locals.latest_course_uid",
            "latest_course_uid_variable",
        ]

        for _idx, _candidate_uid in enumerate(_cloud_ready_next_latest_uid_candidates):
            _candidate_uid = str(_candidate_uid or "").strip()
            if _candidate_uid and _candidate_uid != "ND":
                cloud_ready_next_latest_uid_for_alignment = _candidate_uid
                try:
                    cloud_ready_next_latest_uid_source = _cloud_ready_next_latest_uid_source_names[_idx]
                except Exception:
                    cloud_ready_next_latest_uid_source = "UNKNOWN_SOURCE"
                break

        _cloud_brief_latest_course_ok = str(cloud_ready_next_latest_uid_for_alignment).strip() != "ND"
        _cloud_brief_h5_aligned_ok = (
            str(h5_auto_context_course_uid).strip() == str(cloud_ready_next_latest_uid_for_alignment).strip()
        )
        _cloud_brief_h5_rows_ok = h5_auto_context_rows_ok_oui_non == "oui"
        _cloud_brief_h5_top_cotes_ok = h5_auto_context_top_cotes_ok_oui_non == "oui"
        _cloud_brief_h5_mouvements_ok = h5_auto_context_mouvements_ok_oui_non == "oui"
        _cloud_brief_h5_strict_closed_ok = h5_auto_context_strict_closed_oui_non == "oui"
        _cloud_brief_h5_ticket_no_bet_ok = h5_auto_context_ticket_real_no_bet_oui_non == "oui"

        _cloud_brief_ok_h5_auto_context = (
            _cloud_brief_h5_ready_ok is True
            and _cloud_brief_h5_course_ok is True
            and _cloud_brief_latest_course_ok is True
            and _cloud_brief_h5_aligned_ok is True
            and _cloud_brief_h5_rows_ok is True
            and _cloud_brief_h5_top_cotes_ok is True
            and _cloud_brief_h5_mouvements_ok is True
            and _cloud_brief_h5_strict_closed_ok is True
            and _cloud_brief_h5_ticket_no_bet_ok is True
        )

        # CLOUD_READY_NEXT READONLY AUDIT SOURCE V0_1
        # Source primaire : audit persistant lu en read-only.
        # Ne depend pas du run H5 instantane de /gpi/status.
        _cloud_ready_next_audit_read_ok = False
        _cloud_ready_next_audit_read_error = ""
        _cloud_ready_next_audit_ok_oui_non = "non"
        _cloud_ready_next_audit_migration_allowed_oui_non = "non"
        _cloud_ready_next_audit_green_runs = 0
        _cloud_ready_next_audit_required_runs = 3

        try:
            _audit_reader = globals().get("health_cloud_ready_shadow_audit")
            if callable(_audit_reader):
                _audit_read_payload = _audit_reader()
                if isinstance(_audit_read_payload, dict):
                    _cloud_ready_next_audit_read_ok = _audit_read_payload.get("ok") is True
                    _cloud_ready_next_audit_ok_oui_non = str(_audit_read_payload.get("audit_ok_oui_non", "non") or "non")
                    _cloud_ready_next_audit_migration_allowed_oui_non = str(_audit_read_payload.get("migration_allowed_oui_non", "non") or "non")
                    try:
                        _cloud_ready_next_audit_green_runs = int(_audit_read_payload.get("green_runs", 0))
                    except Exception:
                        _cloud_ready_next_audit_green_runs = 0
                    try:
                        _cloud_ready_next_audit_required_runs = int(_audit_read_payload.get("required_runs", 3))
                    except Exception:
                        _cloud_ready_next_audit_required_runs = 3

                    # CLOUD_READY_NEXT EFFECTIVE AUDIT GATE V0_1
                    # Le gate next utilise le compteur effectif par course_uid quand il existe.
                    # Les champs bruts restent conserves pour audit et diagnostic.
                    _cloud_ready_next_audit_migration_allowed_effective_oui_non = str(
                        _audit_read_payload.get(
                            "migration_allowed_effective_oui_non",
                            _cloud_ready_next_audit_migration_allowed_oui_non,
                        ) or "non"
                    )

                    try:
                        _cloud_ready_next_audit_green_runs_effective = int(
                            _audit_read_payload.get(
                                "green_runs_effective",
                                _cloud_ready_next_audit_green_runs,
                            )
                        )
                    except Exception:
                        _cloud_ready_next_audit_green_runs_effective = _cloud_ready_next_audit_green_runs

                    if (
                        _cloud_ready_next_audit_migration_allowed_effective_oui_non == "oui"
                        and _cloud_ready_next_audit_green_runs_effective >= _cloud_ready_next_audit_required_runs
                    ):
                        _cloud_ready_next_audit_ok_oui_non = "oui"
                        _cloud_ready_next_audit_migration_allowed_oui_non = "oui"
                        _cloud_ready_next_audit_green_runs = _cloud_ready_next_audit_green_runs_effective
                else:
                    _cloud_ready_next_audit_read_error = "AUDIT_READ_PAYLOAD_NOT_DICT"
            else:
                _cloud_ready_next_audit_read_error = "AUDIT_READER_NOT_CALLABLE"
        except Exception as _audit_read_exc:
            _cloud_ready_next_audit_read_error = f"{type(_audit_read_exc).__name__}:{_audit_read_exc}"

        _shadow_green_enough_for_brief = (
            _cloud_ready_next_audit_green_runs >= _cloud_ready_next_audit_required_runs
        )

        _cloud_brief_ok_shadow_audit = (
            _cloud_ready_next_audit_read_ok is True
            and _cloud_ready_next_audit_ok_oui_non == "oui"
            and _cloud_ready_next_audit_migration_allowed_oui_non == "oui"
            and _shadow_green_enough_for_brief is True
            and str(_cloud_ready_next_audit_read_error or "").strip() == ""
        )

        # CLOUD_READY_NEXT MIGRATION PROOF SOURCE V0_1
        # Source stable de migration : preuve fig?e GCS.
        # Ne cr?e aucun ticket, n'ouvre pas le strict, ne modifie pas cloud_ready.
        _cloud_ready_next_proof_ok = False
        _cloud_ready_next_proof_exists = False
        _cloud_ready_next_proof_reason = "PROOF_NOT_READ"
        _cloud_ready_next_proof_course_uid = "ND"
        _cloud_ready_next_proof_green_runs_effective = 0
        _cloud_ready_next_proof_required_runs = 3
        _cloud_ready_next_proof_path = "gpi-lite/outputs/cloud_ready_migration_proof.json"
        _cloud_ready_next_proof_error = ""

        try:
            import json as _proof_json

            _proof_bucket, _proof_bucket_error = get_gcs_bucket()
            if _proof_bucket is None:
                _cloud_ready_next_proof_reason = f"GCS_BUCKET_ERROR:{_proof_bucket_error}"
            else:
                _proof_blob = _proof_bucket.blob(_cloud_ready_next_proof_path)
                if not _proof_blob.exists():
                    _cloud_ready_next_proof_reason = "PROOF_FILE_NOT_FOUND"
                else:
                    _cloud_ready_next_proof_exists = True
                    _proof_raw = _proof_blob.download_as_text(encoding="utf-8")
                    _proof_payload = _proof_json.loads(_proof_raw or "{}")
                    if not isinstance(_proof_payload, dict):
                        _proof_payload = {}

                    _cloud_ready_next_proof_course_uid = str(
                        _proof_payload.get("effective_course_uid", "ND") or "ND"
                    )

                    try:
                        _cloud_ready_next_proof_green_runs_effective = int(
                            _proof_payload.get("green_runs_effective", 0)
                        )
                    except Exception:
                        _cloud_ready_next_proof_green_runs_effective = 0

                    try:
                        _cloud_ready_next_proof_required_runs = int(
                            _proof_payload.get("required_runs", 3)
                        )
                    except Exception:
                        _cloud_ready_next_proof_required_runs = 3

                    _proof_safety = _proof_payload.get("safety_status", {})
                    if not isinstance(_proof_safety, dict):
                        _proof_safety = {}

                    _proof_strict_snapshot = _proof_safety.get("strict_allowed_global", True)
                    _proof_strict_snapshot_ok = (
                        _proof_strict_snapshot is False
                        or str(_proof_strict_snapshot).strip().lower() in {"false", "non", "0"}
                    )

                    _proof_latest_ticket = str(
                        _proof_safety.get("latest_ticket", "ND") or "ND"
                    ).strip()

                    _proof_latest_strict = str(
                        _proof_safety.get("latest_strict_allowed_oui_non", "ND") or "ND"
                    ).strip().lower()

                    _proof_latest_decision_strict = str(
                        _proof_safety.get("latest_decision_strict_allowed", "ND") or "ND"
                    ).strip().lower()

                    _cloud_ready_next_proof_ok = (
                        str(_proof_payload.get("proof_valid_oui_non", "non") or "non") == "oui"
                        and str(_proof_payload.get("migration_allowed_effective_oui_non", "non") or "non") == "oui"
                        and _cloud_ready_next_proof_green_runs_effective >= _cloud_ready_next_proof_required_runs
                        and _proof_strict_snapshot_ok is True
                        and _proof_latest_ticket == "NO_BET"
                        and _proof_latest_strict == "non"
                        and _proof_latest_decision_strict == "non"
                    )

                    _cloud_ready_next_proof_reason = (
                        "PROOF_OK"
                        if _cloud_ready_next_proof_ok is True
                        else "PROOF_GUARDS_NOT_OK"
                    )

        except Exception as _proof_exc:
            _cloud_ready_next_proof_error = f"{type(_proof_exc).__name__}:{_proof_exc}"
            _cloud_ready_next_proof_reason = "PROOF_READ_EXCEPTION"

        if _cloud_ready_next_proof_ok is True:
            _cloud_ready_next_audit_read_ok = True
            _cloud_ready_next_audit_ok_oui_non = "oui"
            _cloud_ready_next_audit_migration_allowed_oui_non = "oui"
            _cloud_ready_next_audit_green_runs = _cloud_ready_next_proof_green_runs_effective
            _cloud_ready_next_audit_required_runs = _cloud_ready_next_proof_required_runs

        _cloud_brief_ok_proof = _cloud_ready_next_proof_ok is True

        _cloud_brief_ok = (
            _cloud_brief_ok_local is True
            or _cloud_brief_ok_h5_auto_context is True
            or _cloud_brief_ok_shadow_audit is True
            or _cloud_brief_ok_proof is True
        )

        # CLOUD_READY_NEXT PROOF SOURCE PRIORITY ONLY V0_1
        # La preuve fig?e GCS doit ?tre la source prioritaire de migration.
        # Le H5 vivant reste visible dans le diagnostic, mais ne masque plus la preuve.
        cloud_ready_next_cloud_brief_source = (
            "locals.cloud_brief_ok"
            if _cloud_brief_ok_local is True
            else (
                "migration_proof_gcs"
                if locals().get("_cloud_brief_ok_proof", False) is True
                else (
                    "h5_auto_context_ready_for_cloud_gate_equivalent"
                    if _cloud_brief_ok_h5_auto_context is True
                    else (
                        "shadow_audit_readonly_migration_allowed"
                        if _cloud_brief_ok_shadow_audit is True
                        else "cloud_brief_not_available"
                    )
                )
            )
        )

        cloud_ready_next_cloud_brief_diag = (
            "local="
            + str(_cloud_brief_ok_local)
            + "|h5_ready="
            + str(_cloud_brief_h5_ready_ok)
            + "|h5_course="
            + str(_cloud_brief_h5_course_ok)
            + "|latest_course="
            + str(_cloud_brief_latest_course_ok)
            + "|latest_uid_source="
            + str(cloud_ready_next_latest_uid_source)
            + "|latest_uid="
            + str(cloud_ready_next_latest_uid_for_alignment)
            + "|aligned="
            + str(_cloud_brief_h5_aligned_ok)
            + "|rows="
            + str(_cloud_brief_h5_rows_ok)
            + "|top_cotes="
            + str(_cloud_brief_h5_top_cotes_ok)
            + "|mouvements="
            + str(_cloud_brief_h5_mouvements_ok)
            + "|strict_closed="
            + str(_cloud_brief_h5_strict_closed_ok)
            + "|ticket_no_bet="
            + str(_cloud_brief_h5_ticket_no_bet_ok)
            + "|shadow_audit="
            + str(_cloud_brief_ok_shadow_audit)
            + "|audit_read_ok="
            + str(_cloud_ready_next_audit_read_ok)
            + "|audit_ok="
            + str(_cloud_ready_next_audit_ok_oui_non)
            + "|audit_migration_allowed="
            + str(_cloud_ready_next_audit_migration_allowed_oui_non)
            + "|audit_green_runs="
            + str(_cloud_ready_next_audit_green_runs)
            + "|audit_required_runs="
            + str(_cloud_ready_next_audit_required_runs)
            + "|audit_effective_migration_allowed="
            + str(locals().get("_cloud_ready_next_audit_migration_allowed_effective_oui_non", "ND"))
            + "|audit_green_runs_effective="
            + str(locals().get("_cloud_ready_next_audit_green_runs_effective", "ND"))
            + "|proof_ok="
            + str(locals().get("_cloud_ready_next_proof_ok", False))
            + "|proof_exists="
            + str(locals().get("_cloud_ready_next_proof_exists", False))
            + "|proof_course_uid="
            + str(locals().get("_cloud_ready_next_proof_course_uid", "ND"))
            + "|proof_green_runs_effective="
            + str(locals().get("_cloud_ready_next_proof_green_runs_effective", "ND"))
            + "|proof_required_runs="
            + str(locals().get("_cloud_ready_next_proof_required_runs", "ND"))
            + "|proof_reason="
            + str(locals().get("_cloud_ready_next_proof_reason", "ND"))
            + "|proof_error="
            + str(locals().get("_cloud_ready_next_proof_error", ""))
            + "|audit_read_error="
            + str(_cloud_ready_next_audit_read_error)
        )

        # CLOUD_READY_NEXT FINAL READONLY AUDIT GATE V0_1
        # Le verdict final utilise l'audit persistant read-only, pas le shadow local du run courant.
        _shadow_green_enough = (
            _cloud_ready_next_audit_green_runs >= _cloud_ready_next_audit_required_runs
        )

        _cloud_ready_next_ok = (
            _cloud_ready_files_ok is True
            and _cloud_brief_ok is True
            and _cloud_ready_next_audit_read_ok is True
            and _cloud_ready_next_audit_ok_oui_non == "oui"
            and _cloud_ready_next_audit_migration_allowed_oui_non == "oui"
            and _shadow_green_enough is True
            and str(_cloud_ready_next_audit_read_error or "").strip() == ""
        )

        cloud_ready_next_oui_non = "oui" if _cloud_ready_next_ok else "non"

        if _cloud_ready_next_ok:
            cloud_ready_next_reason = "CLOUD_READY_NEXT_OK_SHADOW_AUDIT_GREEN"
        elif _cloud_ready_files_ok is not True:
            cloud_ready_next_reason = "CLOUD_READY_FILES_FALSE"
        elif _cloud_brief_ok is not True:
            cloud_ready_next_reason = "CLOUD_BRIEF_NOT_OK"
        elif _cloud_ready_next_audit_read_ok is not True:
            cloud_ready_next_reason = "READONLY_SHADOW_AUDIT_NOT_READABLE"
        elif _cloud_ready_next_audit_ok_oui_non != "oui":
            cloud_ready_next_reason = "READONLY_SHADOW_AUDIT_NOT_OK"
        elif _cloud_ready_next_audit_migration_allowed_oui_non != "oui":
            cloud_ready_next_reason = "READONLY_SHADOW_AUDIT_MIGRATION_NOT_ALLOWED"
        elif not _shadow_green_enough:
            cloud_ready_next_reason = "NOT_ENOUGH_READONLY_SHADOW_GREEN_RUNS"
        elif str(_cloud_ready_next_audit_read_error or "").strip() != "":
            cloud_ready_next_reason = "READONLY_SHADOW_AUDIT_ERROR"
        else:
            cloud_ready_next_reason = "CLOUD_READY_NEXT_BLOCKED_UNKNOWN"

        cloud_ready_next_differs_from_legacy_oui_non = (
            "oui"
            if (cloud_ready is True and cloud_ready_next_oui_non == "non")
            or (cloud_ready is not True and cloud_ready_next_oui_non == "oui")
            else "non"
        )

    except Exception as _next_exc:
        cloud_ready_next_oui_non = "non"
        cloud_ready_next_reason = f"CLOUD_READY_NEXT_EXCEPTION:{type(_next_exc).__name__}:{_next_exc}"
        cloud_ready_next_source = "shadow_audit_exception"
        cloud_ready_next_cloud_brief_source = "exception"
        cloud_ready_next_cloud_brief_diag = "exception"
        cloud_ready_next_latest_uid_for_alignment = "exception"
        cloud_ready_next_latest_uid_source = "exception"

    # GPI STATUS CLOUD READY MIGRATION CONTROL V0_1
    # Migration controlee cloud_ready_next -> cloud_ready.
    # Par defaut, aucune bascule reelle : mode legacy.
    # Rollback explicite : GPI_CLOUD_READY_MIGRATION_MODE=legacy.
    cloud_ready_migration_version = "CLOUD_READY_MIGRATION_CONTROL_V0_1"
    cloud_ready_migration_mode = str(
        os.getenv("GPI_CLOUD_READY_MIGRATION_MODE", "legacy") or "legacy"
    ).strip().lower()

    cloud_ready_migration_allowed_modes = ["legacy", "next"]
    cloud_ready_before_migration = bool(cloud_ready)
    cloud_ready_migration_effective_oui_non = "non"
    cloud_ready_migration_enabled_oui_non = "oui" if cloud_ready_migration_mode == "next" else "non"
    cloud_ready_migration_rollback_env = "GPI_CLOUD_READY_MIGRATION_MODE=legacy"
    cloud_ready_migration_activation_env = "GPI_CLOUD_READY_MIGRATION_MODE=next"
    cloud_ready_migration_reason = "LEGACY_MODE_NO_CHANGE"

    _cloud_ready_migration_strict_allowed = bool(
        locals().get("strict_allowed_global", False)
    )
    # CLOUD_READY MIGRATION LATEST TICKET SAFE SOURCE V0_1
    # Le champ latest_ticket peut etre expose dans le payload final sans exister comme variable locale ici.
    # On le source donc depuis plusieurs objets connus, sans jamais inventer de ticket.
    _cloud_ready_migration_latest_ticket = "ND"
    _cloud_ready_migration_latest_ticket_source = "ND"

    _cloud_ready_migration_latest_obj = locals().get("latest", {})
    if not isinstance(_cloud_ready_migration_latest_obj, dict):
        _cloud_ready_migration_latest_obj = {}

    _cloud_ready_migration_latest_decision_obj = locals().get("latest_decision", {})
    if not isinstance(_cloud_ready_migration_latest_decision_obj, dict):
        _cloud_ready_migration_latest_decision_obj = {}

    _cloud_ready_migration_paper_status_obj = locals().get("paper_status", {})
    if not isinstance(_cloud_ready_migration_paper_status_obj, dict):
        _cloud_ready_migration_paper_status_obj = {}

    _cloud_ready_migration_ticket_candidates = [
        locals().get("latest_ticket"),
        locals().get("ticket"),
        locals().get("ticket_exact"),
        locals().get("ticket_reel"),
        _cloud_ready_migration_latest_obj.get("latest_ticket"),
        _cloud_ready_migration_latest_obj.get("ticket"),
        _cloud_ready_migration_latest_obj.get("ticket_exact"),
        _cloud_ready_migration_latest_obj.get("ticket_reel"),
        _cloud_ready_migration_latest_obj.get("ticket_real"),
        _cloud_ready_migration_latest_obj.get("structure_max"),
        _cloud_ready_migration_latest_decision_obj.get("latest_ticket"),
        _cloud_ready_migration_latest_decision_obj.get("ticket"),
        _cloud_ready_migration_latest_decision_obj.get("ticket_exact"),
        _cloud_ready_migration_latest_decision_obj.get("ticket_reel"),
        _cloud_ready_migration_latest_decision_obj.get("ticket_real"),
        _cloud_ready_migration_latest_decision_obj.get("structure_max"),
        _cloud_ready_migration_paper_status_obj.get("latest_ticket"),
        _cloud_ready_migration_paper_status_obj.get("ticket"),
        _cloud_ready_migration_paper_status_obj.get("ticket_exact"),
        _cloud_ready_migration_paper_status_obj.get("ticket_reel"),
        _cloud_ready_migration_paper_status_obj.get("ticket_real"),
    ]

    _cloud_ready_migration_ticket_source_names = [
        "locals.latest_ticket",
        "locals.ticket",
        "locals.ticket_exact",
        "locals.ticket_reel",
        "latest.latest_ticket",
        "latest.ticket",
        "latest.ticket_exact",
        "latest.ticket_reel",
        "latest.ticket_real",
        "latest.structure_max",
        "latest_decision.latest_ticket",
        "latest_decision.ticket",
        "latest_decision.ticket_exact",
        "latest_decision.ticket_reel",
        "latest_decision.ticket_real",
        "latest_decision.structure_max",
        "paper_status.latest_ticket",
        "paper_status.ticket",
        "paper_status.ticket_exact",
        "paper_status.ticket_reel",
        "paper_status.ticket_real",
    ]

    for _idx, _ticket_candidate in enumerate(_cloud_ready_migration_ticket_candidates):
        _ticket_candidate = str(_ticket_candidate or "").strip()
        if _ticket_candidate and _ticket_candidate != "ND":
            _cloud_ready_migration_latest_ticket = _ticket_candidate
            try:
                _cloud_ready_migration_latest_ticket_source = _cloud_ready_migration_ticket_source_names[_idx]
            except Exception:
                _cloud_ready_migration_latest_ticket_source = "UNKNOWN_SOURCE"
            break

    # CLOUD_READY MIGRATION NO_BET H5 FALLBACK V0_1
    # Fallback conservateur : on accepte NO_BET seulement si le contexte H5 auto confirme
    # explicitement que le ticket reel est NO_BET.
    if (
        _cloud_ready_migration_latest_ticket == "ND"
        and str(locals().get("h5_auto_context_ticket_real_no_bet_oui_non", "non") or "non").strip() == "oui"
    ):
        _cloud_ready_migration_latest_ticket = "NO_BET"
        _cloud_ready_migration_latest_ticket_source = "h5_auto_context_ticket_real_no_bet_oui_non"

    _cloud_ready_migration_safety_ok = (
        _cloud_ready_migration_strict_allowed is not True
        and _cloud_ready_migration_latest_ticket == "NO_BET"
    )

    if cloud_ready_migration_mode not in cloud_ready_migration_allowed_modes:
        cloud_ready_migration_reason = "INVALID_MODE_FALLBACK_LEGACY"
        cloud_ready_migration_mode = "legacy"
        cloud_ready_migration_enabled_oui_non = "non"

    elif cloud_ready_migration_mode == "next":
        if (
            cloud_ready_next_oui_non == "oui"
            and cloud_ready_next_reason == "CLOUD_READY_NEXT_OK_SHADOW_AUDIT_GREEN"
            and _cloud_ready_migration_safety_ok is True
        ):
            cloud_ready = True
            cloud_ready_migration_effective_oui_non = "oui"
            cloud_ready_migration_reason = "MIGRATED_FROM_CLOUD_READY_NEXT"
        elif cloud_ready_next_oui_non != "oui":
            cloud_ready_migration_reason = "NEXT_NOT_READY_NO_MIGRATION"
        elif _cloud_ready_migration_safety_ok is not True:
            cloud_ready_migration_reason = "SAFETY_BLOCK_STRICT_OR_TICKET"
        else:
            cloud_ready_migration_reason = "NEXT_REASON_NOT_VALIDATED"

    else:
        cloud_ready_migration_reason = "LEGACY_MODE_NO_CHANGE"

    cloud_ready_after_migration = bool(cloud_ready)


    # GPI STATUS STRICT GLOBAL AFTER CLOUD_READY MIGRATION V0_2
    # Recalcule la porte globale apres migration cloud_ready_next.
    # N'ouvre le strict que si GPI_STRICT_ACTIVATION_MODE=controlled.
    strict_activation_mode = (
        os.getenv("GPI_STRICT_ACTIVATION_MODE", "closed") or "closed"
    ).strip().lower()

    if strict_activation_mode not in {"closed", "controlled"}:
        strict_activation_mode = "closed"

    strict_global_post_migration_blockers = []

    if mvp_gate_status != "PAPER_READY":
        strict_global_post_migration_blockers.append(
            f"mvp_gate_status {mvp_gate_status}"
        )

    _paper_ready_after_migration = locals().get(
        "paper_kpi_ready_50_for_gate",
        locals().get("paper_kpi_ready_50", None),
    )

    # Si la variable detaillee n'existe pas, PAPER_READY reste la source
    # de verite globale deja calculee par le MVP gate.
    if _paper_ready_after_migration is None:
        _paper_ready_after_migration = mvp_gate_status == "PAPER_READY"

    if not bool(_paper_ready_after_migration):
        strict_global_post_migration_blockers.append("paper_kpi_ready_50 false")

    _result_leak_raw = locals().get(
        "paper_result_leak_count_for_gate",
        locals().get(
            "result_leak_count",
            locals().get("paper_kpi_global_result_leak_count", 0),
        ),
    )

    try:
        _result_leak_for_gate = int(_result_leak_raw or 0)
    except Exception:
        _result_leak_for_gate = 999999

    if _result_leak_for_gate > 0:
        strict_global_post_migration_blockers.append(
            f"{_result_leak_for_gate} result leak"
        )

    if cloud_ready_after_migration is not True:
        strict_global_post_migration_blockers.append("cloud_ready false")

    if not bool(locals().get("cloud_ok", False)):
        strict_global_post_migration_blockers.append("cloud_brief_ok false")

    strict_global_post_migration_candidate = (
        len(strict_global_post_migration_blockers) == 0
    )

    if (
        cloud_ready_before_migration is False
        and cloud_ready_after_migration is True
        and str(strict_allowed_reason or "").strip()
        == "strict global disabled: cloud_ready false"
    ):
        if (
            strict_activation_mode == "controlled"
            and strict_global_post_migration_candidate is True
        ):
            strict_allowed_global = True
            strict_allowed_reason = (
                "strict global enabled after cloud_ready next migration, "
                "decision-level gates still required"
            )
            mvp_gate_reason = strict_allowed_reason
        else:
            strict_allowed_global = False

            if strict_global_post_migration_candidate is True:
                strict_allowed_reason = (
                    "strict global disabled: activation mode closed after "
                    "cloud_ready next migration, set "
                    "GPI_STRICT_ACTIVATION_MODE=controlled to enable global strict gate"
                )
            else:
                strict_allowed_reason = (
                    "strict global disabled after cloud_ready next migration: "
                    + ", ".join(strict_global_post_migration_blockers)
                )

            mvp_gate_reason = strict_allowed_reason

    return {
        "ok": True,
        "source_pack_status": "OK" if source_pack.get("ok") else "KO",

        # GPI STATUS H5 AUTO CONTEXT READY V0_1
        "h5_auto_context_ready_oui_non": h5_auto_context_ready_oui_non,
        "h5_auto_context_health_source": h5_auto_context_health_source,
        "h5_auto_context_health_error": h5_auto_context_health_error,
        "h5_auto_context_course_uid": h5_auto_context_course_uid,
        "h5_auto_context_snapshot_kind": h5_auto_context_snapshot_kind,
        "h5_auto_context_rows_ok_oui_non": h5_auto_context_rows_ok_oui_non,
        "h5_auto_context_top_cotes_ok_oui_non": h5_auto_context_top_cotes_ok_oui_non,
        "h5_auto_context_mouvements_ok_oui_non": h5_auto_context_mouvements_ok_oui_non,
        "h5_auto_context_strict_closed_oui_non": h5_auto_context_strict_closed_oui_non,
        "h5_auto_context_ticket_real_no_bet_oui_non": h5_auto_context_ticket_real_no_bet_oui_non,

        # GPI STATUS H5 AUTO CONTEXT CLOUD GATE SHADOW V0_1
        # Shadow gate informatif uniquement : ne modifie pas cloud_ready.
        "h5_auto_context_aligned_with_latest_oui_non": (
            "oui"
            if str(h5_auto_context_course_uid).strip() != "ND"
            and str(latest_course_uid).strip() != "ND"
            and str(h5_auto_context_course_uid).strip() == str(latest_course_uid).strip()
            else "non"
        ),
        "h5_auto_context_ready_for_cloud_gate_oui_non": (
            "oui"
            if h5_auto_context_ready_oui_non == "oui"
            and str(h5_auto_context_course_uid).strip() != "ND"
            and str(latest_course_uid).strip() != "ND"
            and str(h5_auto_context_course_uid).strip() == str(latest_course_uid).strip()
            and h5_auto_context_rows_ok_oui_non == "oui"
            and h5_auto_context_top_cotes_ok_oui_non == "oui"
            and h5_auto_context_mouvements_ok_oui_non == "oui"
            and h5_auto_context_strict_closed_oui_non == "oui"
            and h5_auto_context_ticket_real_no_bet_oui_non == "oui"
            else "non"
        ),
        "h5_auto_context_cloud_gate_shadow_reason": (
            "READY_SHADOW_ONLY_NO_CLOUD_READY_CHANGE"
            if h5_auto_context_ready_oui_non == "oui"
            and str(h5_auto_context_course_uid).strip() == str(latest_course_uid).strip()
            else "H5_AUTO_CONTEXT_NOT_ALIGNED_OR_NOT_READY"
        ),

        # GPI STATUS CLOUD READY SHADOW V0_1
        # Comparaison informative legacy vs auto_context.
        # Ne modifie jamais cloud_ready, strict_allowed_global ni les tickets.
        "cloud_ready_legacy_oui_non": (
            "oui" if cloud_ready is True else "non"
        ),
        "cloud_ready_auto_context_shadow_oui_non": (
            "oui"
            if h5_auto_context_ready_oui_non == "oui"
            and str(h5_auto_context_course_uid).strip() != "ND"
            and str(latest_course_uid).strip() != "ND"
            and str(h5_auto_context_course_uid).strip() == str(latest_course_uid).strip()
            and h5_auto_context_rows_ok_oui_non == "oui"
            and h5_auto_context_top_cotes_ok_oui_non == "oui"
            and h5_auto_context_mouvements_ok_oui_non == "oui"
            and h5_auto_context_strict_closed_oui_non == "oui"
            and h5_auto_context_ticket_real_no_bet_oui_non == "oui"
            else "non"
        ),
        "cloud_ready_shadow_reason": (
            "LEGACY_AND_AUTO_CONTEXT_READY_SHADOW_ONLY"
            if cloud_ready is True
            and h5_auto_context_ready_oui_non == "oui"
            and str(h5_auto_context_course_uid).strip() == str(latest_course_uid).strip()
            and h5_auto_context_rows_ok_oui_non == "oui"
            and h5_auto_context_top_cotes_ok_oui_non == "oui"
            and h5_auto_context_mouvements_ok_oui_non == "oui"
            and h5_auto_context_strict_closed_oui_non == "oui"
            and h5_auto_context_ticket_real_no_bet_oui_non == "oui"
            else (
                "LEGACY_BLOCKED_AUTO_CONTEXT_READY_SHADOW_ONLY"
                if cloud_ready is not True
                and h5_auto_context_ready_oui_non == "oui"
                and str(h5_auto_context_course_uid).strip() == str(latest_course_uid).strip()
                and h5_auto_context_rows_ok_oui_non == "oui"
                and h5_auto_context_top_cotes_ok_oui_non == "oui"
                and h5_auto_context_mouvements_ok_oui_non == "oui"
                and h5_auto_context_strict_closed_oui_non == "oui"
                and h5_auto_context_ticket_real_no_bet_oui_non == "oui"
                else (
                    "LEGACY_READY_AUTO_CONTEXT_BLOCKED_KEEP_LEGACY"
                    if cloud_ready is True
                    else "LEGACY_AND_AUTO_CONTEXT_BLOCKED"
                )
            )
        ),
        "cloud_ready_migration_candidate_oui_non": (
            "oui"
            if cloud_ready is not True
            and h5_auto_context_ready_oui_non == "oui"
            and str(h5_auto_context_course_uid).strip() != "ND"
            and str(latest_course_uid).strip() != "ND"
            and str(h5_auto_context_course_uid).strip() == str(latest_course_uid).strip()
            and h5_auto_context_rows_ok_oui_non == "oui"
            and h5_auto_context_top_cotes_ok_oui_non == "oui"
            and h5_auto_context_mouvements_ok_oui_non == "oui"
            and h5_auto_context_strict_closed_oui_non == "oui"
            and h5_auto_context_ticket_real_no_bet_oui_non == "oui"
            and str(h5_context_freshness.get("latest_h5_context_fresh_oui_non", "non")).strip().lower() == "non"
            else "non"
        ),

        # GPI STATUS CLOUD READY SHADOW AUDIT V0_1
        "cloud_ready_shadow_audit_version": cloud_ready_shadow_audit_version,
        "cloud_ready_shadow_required_runs": cloud_ready_shadow_required_runs,
        "cloud_ready_shadow_green_runs": cloud_ready_shadow_green_runs,
        "cloud_ready_shadow_current_green_oui_non": cloud_ready_shadow_current_green_oui_non,
        "cloud_ready_shadow_audit_ok_oui_non": cloud_ready_shadow_audit_ok_oui_non,
        "cloud_ready_shadow_last_mismatch_reason": cloud_ready_shadow_last_mismatch_reason,
        "cloud_ready_shadow_audit_error": cloud_ready_shadow_audit_error,
        "cloud_ready_shadow_audit_path": cloud_ready_shadow_audit_path,
        "cloud_ready_shadow_latest_run_key": cloud_ready_shadow_latest_run_key,
        "cloud_ready_shadow_latest_uid_source": cloud_ready_shadow_latest_uid_source,

        # GPI STATUS CLOUD READY NEXT SHADOW V0_1
        "cloud_ready_next_oui_non": cloud_ready_next_oui_non,
        "cloud_ready_next_reason": cloud_ready_next_reason,
        "cloud_ready_next_source": cloud_ready_next_source,
        "cloud_ready_next_cloud_brief_source": cloud_ready_next_cloud_brief_source,
        "cloud_ready_next_cloud_brief_diag": cloud_ready_next_cloud_brief_diag,
        "cloud_ready_next_latest_uid_for_alignment": cloud_ready_next_latest_uid_for_alignment,
        "cloud_ready_next_latest_uid_source": cloud_ready_next_latest_uid_source,
        "cloud_ready_next_differs_from_legacy_oui_non": cloud_ready_next_differs_from_legacy_oui_non,
        "cloud_ready_next_activation_note": "Source shadow. La bascule cloud_ready est controlee par GPI_CLOUD_READY_MIGRATION_MODE.",
        "cloud_ready_migration_version": cloud_ready_migration_version,
        "cloud_ready_migration_mode": cloud_ready_migration_mode,
        "cloud_ready_migration_enabled_oui_non": cloud_ready_migration_enabled_oui_non,
        "cloud_ready_migration_effective_oui_non": cloud_ready_migration_effective_oui_non,
        "cloud_ready_migration_reason": cloud_ready_migration_reason,
        "cloud_ready_before_migration": cloud_ready_before_migration,
        "cloud_ready_after_migration": cloud_ready_after_migration,
        "cloud_ready_migration_activation_env": cloud_ready_migration_activation_env,
        "cloud_ready_migration_rollback_env": cloud_ready_migration_rollback_env,
        "cloud_ready_migration_safety_strict_allowed": _cloud_ready_migration_strict_allowed,
        "cloud_ready_migration_safety_latest_ticket": _cloud_ready_migration_latest_ticket,
        "cloud_ready_migration_safety_latest_ticket_source": _cloud_ready_migration_latest_ticket_source,
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
        # GPI STATUS LEGACY H5 STALE OVERRIDE V0_1
        # Champ informatif uniquement : ne modifie pas cloud_ready ni strict_allowed_global.
        "legacy_h5_context_stale_oui_non": (
            "oui" if str(h5_context_freshness.get("latest_h5_context_fresh_oui_non", "non")).strip().lower() == "non"
            else legacy_h5_context_stale_oui_non
        ),
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

        # GPI STATUS STRICT GLOBAL AFTER CLOUD_READY MIGRATION V0_2B
        "strict_activation_mode": locals().get("strict_activation_mode", "legacy"),
        "strict_global_post_migration_candidate": bool(
            locals().get("strict_global_post_migration_candidate", False)
        ),
        "strict_global_post_migration_blockers": locals().get(
            "strict_global_post_migration_blockers", []
        ),
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
        "latest_verdict": (
            "NO_BET"
            if strict_activation_mode == "closed" or not cloud_ok
            else cloud_brief.get("verdict_python", "ND")
        ),
        "latest_structure": cloud_brief.get("structure_max", "ND") if cloud_ok else "ND",
        "latest_ticket": (
            "NO_BET"
            if strict_activation_mode == "closed" or not cloud_ok
            else cloud_brief.get("ticket_exact", "ND")
        ),
        "latest_edge": cloud_brief.get("edge_status", "ND") if cloud_ok else "ND",
        "latest_decision_source_kind": cloud_brief.get("decision_source_kind", "ND") if cloud_ok else "ND",
        "latest_decision_id": cloud_brief.get("decision_id", "ND") if cloud_ok else "ND",
        "latest_input_hash": cloud_brief.get("input_hash", "ND") if cloud_ok else "ND",
        "latest_engine_version": cloud_brief.get("engine_version", "ND") if cloud_ok else "ND",
        "latest_ruleset_version": cloud_brief.get("ruleset_version", "ND") if cloud_ok else "ND",
        "latest_split_manifest_id": cloud_brief.get("split_manifest_id", "ND") if cloud_ok else "ND",
        "latest_leakage_audit_id": cloud_brief.get("leakage_audit_id", "ND") if cloud_ok else "ND",
        "latest_replay_run_id": cloud_brief.get("replay_run_id", "ND") if cloud_ok else "ND",
        "latest_proof_bundle_hash": cloud_brief.get("proof_bundle_hash", "ND") if cloud_ok else "ND",
        "latest_evidence_level": cloud_brief.get("evidence_level", "ND") if cloud_ok else "ND",
        "latest_proof_status": cloud_brief.get("proof_status", "ND") if cloud_ok else "ND",
        "latest_decision_mode": cloud_brief.get("decision_mode", "ND") if cloud_ok else "ND",
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
        "latest_source_updated": cloud_brief.get("updated", "ND") if cloud_ok else "ND",
        "latest_source_generation": cloud_brief.get("generation", "ND") if cloud_ok else "ND",
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
        "generation": str(getattr(decision_blob, "generation", "") or "ND"),
    }

    # Champs moteur utiles si source DEO.
    for key in [
        "decision_id",
        "input_hash",
        "engine_version",
        "ruleset_version",
        "split_manifest_id",
        "leakage_audit_id",
        "replay_run_id",
        "proof_bundle_hash",
        "evidence_level",
        "proof_status",
        "decision_mode",
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

        # GPI ACTION STATUS STRICT GLOBAL AFTER CLOUD_READY MIGRATION V0_2C
        "strict_activation_mode": payload.get("strict_activation_mode"),
        "strict_global_post_migration_candidate": payload.get(
            "strict_global_post_migration_candidate"
        ),
        "strict_global_post_migration_blockers": payload.get(
            "strict_global_post_migration_blockers", []
        ),
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
        "latest_source_updated": _action_str(payload.get("latest_source_updated")),
        "latest_source_generation": _action_str(payload.get("latest_source_generation")),
        "cloud_brief_error": _action_str(payload.get("cloud_error"), ""),
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
        "split_manifest_id": _action_str(payload.get("split_manifest_id")),
        "leakage_audit_id": _action_str(payload.get("leakage_audit_id")),
        "replay_run_id": _action_str(payload.get("replay_run_id")),
        "proof_bundle_hash": _action_str(payload.get("proof_bundle_hash")),
        "evidence_level": _action_str(payload.get("evidence_level")),
        "proof_status": _action_str(payload.get("proof_status")),
        "decision_mode": _action_str(payload.get("decision_mode")),
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

    compact = _action_compact_status(payload)

    # GPI ACTION STATUS H5 AUTO CONTEXT READY MIRROR V0_1
    # Reprise lecture seule des champs /gpi/status.
    # Ne modifie jamais cloud_ready, strict_allowed_global ni les tickets.
    for _h5_ready_key in [
        "h5_auto_context_ready_oui_non",
        "h5_auto_context_health_source",
        "h5_auto_context_health_error",
        "h5_auto_context_course_uid",
        "h5_auto_context_snapshot_kind",
        "h5_auto_context_rows_ok_oui_non",
        "h5_auto_context_top_cotes_ok_oui_non",
        "h5_auto_context_mouvements_ok_oui_non",
        "h5_auto_context_strict_closed_oui_non",
        "h5_auto_context_ticket_real_no_bet_oui_non",
        "h5_auto_context_aligned_with_latest_oui_non",
        "h5_auto_context_ready_for_cloud_gate_oui_non",
        "h5_auto_context_cloud_gate_shadow_reason",
        "cloud_ready_legacy_oui_non",
        "cloud_ready_auto_context_shadow_oui_non",
        "cloud_ready_shadow_reason",
        "cloud_ready_migration_candidate_oui_non",
        "cloud_ready_shadow_audit_version",
        "cloud_ready_shadow_required_runs",
        "cloud_ready_shadow_green_runs",
        "cloud_ready_shadow_current_green_oui_non",
        "cloud_ready_shadow_audit_ok_oui_non",
        "cloud_ready_shadow_last_mismatch_reason",
        "cloud_ready_shadow_audit_error",
        "cloud_ready_shadow_audit_path",
        "cloud_ready_shadow_latest_run_key",
        "cloud_ready_shadow_latest_uid_source",
        "cloud_ready_next_oui_non",
        "cloud_ready_next_reason",
        "cloud_ready_next_source",
        "cloud_ready_next_cloud_brief_source",
        "cloud_ready_next_cloud_brief_diag",
        "cloud_ready_next_latest_uid_for_alignment",
        "cloud_ready_next_latest_uid_source",
        "cloud_ready_next_differs_from_legacy_oui_non",
        "cloud_ready_next_activation_note",
        "cloud_ready_migration_version",
        "cloud_ready_migration_mode",
        "cloud_ready_migration_enabled_oui_non",
        "cloud_ready_migration_effective_oui_non",
        "cloud_ready_migration_reason",
        "cloud_ready_before_migration",
        "cloud_ready_after_migration",
        "cloud_ready_migration_activation_env",
        "cloud_ready_migration_rollback_env",
        "cloud_ready_migration_safety_strict_allowed",
        "cloud_ready_migration_safety_latest_ticket",
        "cloud_ready_migration_safety_latest_ticket_source",
        "legacy_h5_context_stale_oui_non",
    ]:
        compact[_h5_ready_key] = payload.get(_h5_ready_key, "ND")

    # GPI ACTION STATUS H5 REPORT HEALTH V0_1
    # Expose le health H5 auto_context dans /action/status.
    # Lecture seule : ne modifie jamais strict_allowed_global ni les tickets.
    try:
        h5_health = health_h5_report_auto_context()
        if not isinstance(h5_health, dict):
            h5_health = {}

        h5_summary = h5_health.get("summary", {})
        if not isinstance(h5_summary, dict):
            h5_summary = {}

        h5_checks = h5_health.get("checks", {})
        if not isinstance(h5_checks, dict):
            h5_checks = {}

        h5_ok = h5_health.get("ok") is True

        compact["h5_report_auto_context_health_ok"] = "oui" if h5_ok else "non"
        compact["h5_report_auto_context_health"] = {
            "ok": h5_ok,
            "health_version": h5_health.get("health_version", "ND"),
            "checked_route": h5_health.get("checked_route", "/action/h5-report"),
            "course_uid": h5_health.get("course_uid", "ND"),
            "context_mismatch": h5_summary.get("context_mismatch", "ND"),
            "snapshot_kind": h5_summary.get("snapshot_kind", "ND"),
            "auto_context_used_oui_non": h5_summary.get("auto_context_used_oui_non", "ND"),
            "auto_context_aligned_oui_non": h5_summary.get("auto_context_aligned_oui_non", "ND"),
            "snapshot_reference_rows": h5_summary.get("snapshot_reference_rows", "ND"),
            "snapshot_h5_rows": h5_summary.get("snapshot_h5_rows", "ND"),
            "top_cotes_count": h5_summary.get("top_cotes_count", 0),
            "mouvements_count": h5_summary.get("mouvements_count", 0),
            "strict_allowed_oui_non": h5_summary.get("strict_allowed_oui_non", "ND"),
            "ticket_real_optimizer": h5_summary.get("ticket_real_optimizer", "ND"),
            "optimizer_hard_blocks": h5_summary.get("optimizer_hard_blocks", "ND"),
            "checks": h5_checks,
            "doctrine": "Lecture seule. Ce health ne valide jamais le strict.",
        }

        compact["h5_report_context_mismatch_oui_non"] = (
            "oui" if h5_summary.get("context_mismatch") == "oui" else "non"
        )
        compact["h5_report_snapshot_kind"] = h5_summary.get("snapshot_kind", "ND")
        compact["h5_report_rows_ok_oui_non"] = (
            "oui"
            if h5_checks.get("snapshot_reference_rows_positive") is True
            and h5_checks.get("snapshot_h5_rows_positive") is True
            else "non"
        )
        compact["h5_report_top_cotes_ok_oui_non"] = (
            "oui" if h5_checks.get("top_cotes_non_empty") is True else "non"
        )
        compact["h5_report_mouvements_ok_oui_non"] = (
            "oui" if h5_checks.get("mouvements_non_empty") is True else "non"
        )
        compact["h5_report_strict_closed_oui_non"] = (
            "oui" if h5_checks.get("strict_closed") is True else "non"
        )
        compact["h5_report_ticket_real_no_bet_oui_non"] = (
            "oui" if h5_checks.get("ticket_real_no_bet") is True else "non"
        )

    except Exception as exc:
        compact["h5_report_auto_context_health_ok"] = "non"
        compact["h5_report_auto_context_health"] = {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
            "doctrine": "Erreur health = diagnostic KO. Aucun ticket strict.",
        }

    return compact


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

    if isinstance(run_payload, dict) and run_payload.get("blocked") is True:
        return {
            "ok": False,
            "facade_version": _ACTION_FACADE_VERSION,
            "phase": "h5",
            "run_blocked": True,
            "run_reason": _action_str(run_payload.get("reason"), "H5_BLOCKED_BY_DATE"),
            "run_error": _action_str(run_payload.get("error"), "H5_BLOCKED_BY_DATE"),
            "blocked_guard": run_payload.get("blocked_guard", {}),
            "h5": run_payload.get("h5", {}),
            "blocked_guard_cloud_sync": run_payload.get("blocked_guard_cloud_sync", {}),
            "brief": {
                "skipped": True,
                "reason": "H5_BLOCKED_BY_DATE",
            },
            "status": {
                "skipped": True,
                "reason": "H5_BLOCKED_BY_DATE",
            },
            "ticket_exact": "NO_BET",
            "strict_allowed_oui_non": "non",
        }

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
            # Modern post-course truth fields.
            # Fallback: compute directly from run_payload when legacy guardrails do not expose these fields.
            "results_pipeline_effective": guardrails.get(
                "results_pipeline_effective",
                bool(
                    run_payload.get("results_pdf_parse", {}).get("ok") is True
                    and run_payload.get("paper_results", {}).get("ok") is True
                ),
            ),
            "audit_pipeline_effective": guardrails.get(
                "audit_pipeline_effective",
                bool(
                    run_payload.get("paper_kpi", {}).get("ok") is True
                    and run_payload.get("candidate_roi", {}).get("ok") is True
                    and run_payload.get("candidate_roi_robustness", {}).get("ok") is True
                    and run_payload.get("continuation_stop_dashboard", {}).get("ok") is True
                    and run_payload.get("no_bet_audit", {}).get("ok") is True
                ),
            ),
            "gcs_sync_effective": guardrails.get(
                "gcs_sync_effective",
                bool(
                    run_payload.get("results_pdf_parse_cloud_sync", {}).get("ok") is True
                    and run_payload.get("cloud_sync", {}).get("ok") is True
                    and run_payload.get("kpi_cloud_sync", {}).get("ok") is True
                    and run_payload.get("candidate_roi_cloud_sync", {}).get("ok") is True
                    and run_payload.get("candidate_roi_robustness_cloud_sync", {}).get("ok") is True
                    and run_payload.get("continuation_stop_dashboard_cloud_sync", {}).get("ok") is True
                    and run_payload.get("no_bet_audit_cloud_sync", {}).get("ok") is True
                ),
            ),

            "results_pdf_parse_called": guardrails.get(
                "results_pdf_parse_called",
                bool(run_payload.get("results_pdf_parse", {}).get("ok") is True),
            ),
            "paper_results_called": guardrails.get(
                "paper_results_called",
                bool(run_payload.get("paper_results", {}).get("ok") is True),
            ),
            "paper_kpi_called": guardrails.get(
                "paper_kpi_called",
                bool(run_payload.get("paper_kpi", {}).get("ok") is True),
            ),
            "candidate_roi_called": guardrails.get(
                "candidate_roi_called",
                bool(run_payload.get("candidate_roi", {}).get("ok") is True),
            ),
            "candidate_roi_robustness_called": guardrails.get(
                "candidate_roi_robustness_called",
                bool(run_payload.get("candidate_roi_robustness", {}).get("ok") is True),
            ),
            "continuation_stop_dashboard_called": guardrails.get(
                "continuation_stop_dashboard_called",
                bool(run_payload.get("continuation_stop_dashboard", {}).get("ok") is True),
            ),
            "no_bet_audit_called": guardrails.get(
                "no_bet_audit_called",
                bool(run_payload.get("no_bet_audit", {}).get("ok") is True),
            ),

            # Legacy compatibility
            "legacy_results_main_called": guardrails.get("legacy_results_main_called", False),
            "legacy_audit_all_called": guardrails.get("legacy_audit_all_called", False),
            "results_main_called": guardrails.get("results_main_called", False),
            "audit_all_called": guardrails.get("audit_all_called", False),

            # Safety fields
            "h5_main_called": guardrails.get("h5_main_called", False),
            "decision_engine_outputs_modified": guardrails.get("decision_engine_outputs_modified", False),
            "paper_trading_50_decisions_uploaded": guardrails.get("paper_trading_50_decisions_uploaded", False),
            "paper_trading_50_decisions_modified": guardrails.get("paper_trading_50_decisions_modified", False),
            "post_course_only": guardrails.get("post_course_only", True),
            "drive_results_required": guardrails.get("drive_results_required", False),
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

# GPI_H5_SCHEDULER_STATUS_PATCH_V0_9_28A2
_b212_morning_refresh_state = {
    "last_attempt_monotonic": 0.0,
    "last_attempt_date": "",
}


def _b212_watchlist_today_snapshot(now_paris=None):
    """Return a compact, fail-closed view of today's morning watchlist."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    current = now_paris or datetime.now(ZoneInfo("Europe/Paris"))
    today = current.date().isoformat()
    rows, source = _h5auto_load_latest_watchlist_rows()
    today_rows = [
        row for row in rows
        if _h5auto_parse_course_date_from_uid(row.get("course_uid")) == today
    ]
    return {
        "today_date": today,
        "source": source,
        "rows_total": len(rows),
        "today_rows": len(today_rows),
        "today_course_sample": [
            str(row.get("course_uid", "")) for row in today_rows[:5]
        ],
    }


def _b212_maybe_refresh_late_morning(now_paris=None):
    """Rebuild the morning watchlist after a late Drive upload."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import os
    import threading
    import time

    current = now_paris or datetime.now(ZoneInfo("Europe/Paris"))
    before = _b212_watchlist_today_snapshot(current)
    enabled = str(
        os.getenv("GPI_LATE_MORNING_AUTO_REFRESH_ENABLED", "true")
    ).strip().lower() in {"1", "true", "yes", "oui"}
    result = {
        "enabled": enabled,
        "needed": before["today_rows"] == 0,
        "ran": False,
        "reason": "TODAY_WATCHLIST_PRESENT",
        "before": before,
        "after": before,
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
    }
    if not result["needed"]:
        return result
    if not enabled:
        result["reason"] = "AUTO_REFRESH_DISABLED"
        return result

    try:
        cooldown_seconds = max(
            180,
            int(os.getenv("GPI_LATE_MORNING_REFRESH_COOLDOWN_SECONDS", "600")),
        )
    except Exception:
        cooldown_seconds = 600

    state = _b212_morning_refresh_state
    today = current.date().isoformat()
    elapsed = time.monotonic() - float(state.get("last_attempt_monotonic", 0.0))
    if state.get("last_attempt_date") == today and elapsed < cooldown_seconds:
        result["reason"] = "COOLDOWN_ACTIVE"
        result["retry_after_seconds"] = max(0, int(cooldown_seconds - elapsed))
        return result

    lock = globals().get("_b212_morning_refresh_lock")
    if lock is None:
        lock = threading.Lock()
        globals()["_b212_morning_refresh_lock"] = lock
    if not lock.acquire(blocking=False):
        result["reason"] = "REFRESH_ALREADY_RUNNING"
        return result

    try:
        before_locked = _b212_watchlist_today_snapshot(current)
        result["before"] = before_locked
        if before_locked["today_rows"] > 0:
            result["needed"] = False
            result["reason"] = "TODAY_WATCHLIST_PRESENT_AFTER_LOCK"
            result["after"] = before_locked
            return result

        state["last_attempt_monotonic"] = time.monotonic()
        state["last_attempt_date"] = today
        result["ran"] = True
        morning = drive_run_morning()
        result["morning"] = {
            "ok": morning.get("ok", False) if isinstance(morning, dict) else False,
            "pdf_downloaded_count": morning.get("pdf_downloaded_count", 0)
            if isinstance(morning, dict) else 0,
            "watchlist_rows_count": morning.get("watchlist_rows_count", 0)
            if isinstance(morning, dict) else 0,
            "archive_blocked": morning.get("archive_blocked", "ND")
            if isinstance(morning, dict) else "ND",
        }
        result["after"] = _b212_watchlist_today_snapshot(current)
        result["reason"] = (
            "TODAY_WATCHLIST_REFRESHED"
            if result["after"]["today_rows"] > 0
            else "REFRESH_COMPLETED_TODAY_WATCHLIST_STILL_MISSING"
        )
    except Exception as exc:
        result["reason"] = "REFRESH_FAILED"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        lock.release()
    return result


@app.post("/scheduler/run-due-h5", dependencies=[Depends(require_api_key)])
def scheduler_run_due_h5():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import time as _b210_time
    import uuid as _b210_uuid

    # GPI_H5_SCHEDULER_STABILITY_AUDIT_PATCH_V0_9_31B2_10
    # Diagnostic uniquement. Ne modifie ni le plan, ni la capture, ni le brief.
    # Ne cr?e jamais de ticket strict.
    b210_scheduler_version = "SCHEDULER_RUN_DUE_H5_V0_9_31B2_10"
    b210_started_monotonic = _b210_time.monotonic()
    b210_started_utc = datetime.now(ZoneInfo("UTC")).replace(microsecond=0)
    b210_run_id = (
        f"B210-{b210_started_utc.strftime('%Y%m%dT%H%M%SZ')}-"
        f"{_b210_uuid.uuid4().hex[:8]}"
    )

    now_paris = datetime.now(ZoneInfo("Europe/Paris")).replace(microsecond=0)

    plan_payload = {}
    resolver_payload = {}
    capture_payload = {}
    brief_payload = {}
    status_payload = {}
    morning_self_heal = {}

    plan_ok = False
    resolver_ok = False
    resolver_ran_oui_non = "non"
    capture_ok = False
    brief_ok = False
    brief_skipped_oui_non = "non"
    brief_skip_reason = ""

    # GPI_H5_SCHEDULER_ORCHESTRATION_PATCH_V0_9_29A
    # Orchestration complète : plan -> url_resolver -> capture -> brief.
    def scheduler_int(value, default=0):
        try:
            if value in (None, "", "ND"):
                return default
            return int(value)
        except Exception:
            return default

    # GPI_H5_SCHEDULER_RESOLVER_METRICS_PATCH_V0_9_29A2
    # Les métriques du resolver peuvent être dans resolver_payload["report"].
    def resolver_metric(key, default="ND"):
        if not isinstance(resolver_payload, dict):
            return default

        value = resolver_payload.get(key, None)
        if value not in (None, ""):
            return value

        report = resolver_payload.get("report")
        if isinstance(report, dict):
            value = report.get(key, None)
            if value not in (None, ""):
                return value

        return default

    try:
        morning_self_heal = _b212_maybe_refresh_late_morning(now_paris)
    except Exception as exc:
        morning_self_heal = {
            "enabled": True,
            "needed": True,
            "ran": False,
            "reason": "SELF_HEAL_DIAGNOSTIC_FAILED",
            "error": f"{type(exc).__name__}: {exc}",
            "strict_allowed_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    try:
        plan_payload = drive_run_h5_auto_plan()
        plan_ok = isinstance(plan_payload, dict) and plan_payload.get("ok") is True
    except Exception as exc:
        plan_payload = {
            "ok": False,
            "phase": "h5_auto_plan",
            "error": f"{type(exc).__name__}: {exc}",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    plan_url_required_count = scheduler_int(
        plan_payload.get("url_required_count", 0)
        if isinstance(plan_payload, dict)
        else 0
    )

    if plan_url_required_count > 0:
        resolver_ran_oui_non = "oui"
        try:
            resolver_payload = drive_run_h5_url_resolver()
            resolver_ok = (
                isinstance(resolver_payload, dict)
                and resolver_payload.get("ok") is True
            )
        except Exception as exc:
            resolver_payload = {
                "ok": False,
                "phase": "h5_url_resolver",
                "error": f"{type(exc).__name__}: {exc}",
                "strict_allowed_oui_non": "non",
                "strict_usable_oui_non": "non",
                "ticket_strict": "NO_BET",
            }
    else:
        resolver_payload = {
            "ok": True,
            "phase": "h5_url_resolver",
            "reason": "SKIPPED_NO_URL_REQUIRED",
            "url_required_count": plan_url_required_count,
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }
        resolver_ok = True

    try:
        capture_payload = drive_run_h5_auto_capture()
        capture_ok = isinstance(capture_payload, dict) and capture_payload.get("ok") is True
    except Exception as exc:
        capture_payload = {
            "ok": False,
            "phase": "h5_auto_capture",
            "error": f"{type(exc).__name__}: {exc}",
        }

    # GPI_H5_SCHEDULER_SKIP_IDLE_BRIEF_PATCH_V0_9_29A3
    # Si aucun plan et aucune capture nouvelle, ne pas rejouer les anciens briefs.
    plan_planned_count_for_brief = scheduler_int(
        plan_payload.get("planned_count", 0)
        if isinstance(plan_payload, dict)
        else 0
    )
    plan_due_count_for_brief = scheduler_int(
        plan_payload.get("due_count", 0)
        if isinstance(plan_payload, dict)
        else 0
    )
    capture_count_for_brief = scheduler_int(
        capture_payload.get("capture_count", 0)
        if isinstance(capture_payload, dict)
        else 0
    )
    odds_rows_total_for_brief = scheduler_int(
        capture_payload.get("odds_rows_total", 0)
        if isinstance(capture_payload, dict)
        else 0
    )

    skip_idle_brief = (
        plan_planned_count_for_brief == 0
        and plan_due_count_for_brief == 0
        and capture_count_for_brief == 0
        and odds_rows_total_for_brief == 0
    )

    if skip_idle_brief:
        brief_skipped_oui_non = "oui"
        brief_skip_reason = "SKIPPED_NO_PLAN_NO_CAPTURE"
        brief_payload = {
            "ok": True,
            "phase": "h5_auto_brief",
            "reason": brief_skip_reason,
            "matched_courses": 0,
            "movement_rows_total": 0,
            "briefs_created": 0,
            "error": "",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }
        brief_ok = True
    else:
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

    def as_int(value, default=0):
        try:
            if value in (None, "", "ND"):
                return default
            return int(value)
        except Exception:
            return default

    history_items = []

    for source_name, source_payload in [
        ("plan", plan_payload),
        ("url_resolver", resolver_payload),
        ("capture", capture_payload),
        ("brief", brief_payload),
    ]:
        if isinstance(source_payload, dict):
            archives = source_payload.get("history_archives")
            if isinstance(archives, list):
                for archive in archives:
                    if isinstance(archive, dict):
                        item = dict(archive)
                        item["source"] = source_name
                        history_items.append(item)
            else:
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

    capture_count = as_int(pick(capture_payload, "capture_count", 0))
    odds_rows_total = as_int(pick(capture_payload, "odds_rows_total", 0))
    briefs_created = as_int(pick(brief_payload, "briefs_created", 0))
    movement_rows_total = as_int(pick(brief_payload, "movement_rows_total", 0))

    latest_brief_payload = (
        brief_payload.get("latest_brief")
        if isinstance(brief_payload, dict) and isinstance(brief_payload.get("latest_brief"), dict)
        else {}
    )

    latest_strict_kpi_exclusion_reason = str(
        latest_brief_payload.get("strict_kpi_exclusion_reason", "")
        or ""
    ).strip()

    latest_decision_before_race_oui_non = str(
        latest_brief_payload.get("decision_before_race_oui_non", "")
        or ""
    ).strip()

    latest_brief_course_uid = str(
        latest_brief_payload.get("course_uid", "")
        or pick(status_payload, "latest_course_uid", "")
        or ""
    ).strip()

    if not capture_ok or not brief_ok:
        run_status = "H5_RUN_ERROR"
    elif capture_count == 0 and briefs_created == 0:
        run_status = "H5_RUN_NOT_DUE_OR_NO_NEW_ARTIFACT"
    elif capture_count == 0 and briefs_created > 0 and latest_strict_kpi_exclusion_reason:
        run_status = "H5_RUN_BRIEF_REPLAYED_POST_START_EXCLUDED"
    elif latest_strict_kpi_exclusion_reason:
        run_status = "H5_RUN_POST_START_EXCLUDED"
    elif capture_count > 0 and odds_rows_total > 0 and briefs_created > 0 and movement_rows_total > 0:
        run_status = "H5_RUN_CAPTURED_AND_BRIEFED"
    elif capture_count > 0 and odds_rows_total > 0 and briefs_created == 0:
        run_status = "H5_RUN_CAPTURED_NO_BRIEF"
    elif capture_count == 0 and briefs_created > 0:
        run_status = "H5_RUN_BRIEF_REPLAYED_NO_NEW_CAPTURE"
    else:
        run_status = "H5_RUN_OK_PARTIAL"

    h5_run_ok = run_status != "H5_RUN_ERROR"
    new_artifact_oui_non = "oui" if capture_count > 0 else "non"

    if brief_skipped_oui_non == "oui":
        run_status = "H5_RUN_IDLE_NO_PLAN_NO_CAPTURE"
        new_artifact_oui_non = "non"

    # GPI_V20_P1_4_H5_POST_CAPTURE_HOOK_V1
    # Audit-only and fail-open: never changes H5 status or opens strict.
    p15_prospective_capture = v20_p15_post_h5_capture(
        h5_run_ok=h5_run_ok,
        capture_count=capture_count,
        briefs_created=briefs_created,
    )

    # GPI_H5_SCHEDULER_STABILITY_AUDIT_PATCH_V0_9_31B2_10
    # Contr?le les ?tapes du scheduler et la coh?rence du latest.
    # Aucun appel r?seau suppl?mentaire et aucune ?criture suppl?mentaire.
    b210_status_ok = (
        isinstance(status_payload, dict)
        and status_payload.get("ok") is True
    )

    b210_stage_ok = {
        "plan": bool(plan_ok),
        "url_resolver": bool(resolver_ok),
        "capture": bool(capture_ok),
        "brief": bool(brief_ok),
        "status_after": bool(b210_status_ok),
    }

    b210_stage_payloads = {
        "plan": plan_payload,
        "url_resolver": resolver_payload,
        "capture": capture_payload,
        "brief": brief_payload,
        "status_after": status_payload,
    }

    b210_stage_errors = []

    for b210_stage_name, b210_stage_is_ok in b210_stage_ok.items():
        if b210_stage_is_ok:
            continue

        b210_stage_payload = b210_stage_payloads.get(b210_stage_name, {})

        if isinstance(b210_stage_payload, dict):
            b210_stage_error = str(
                b210_stage_payload.get("error", "")
                or b210_stage_payload.get("reason", "")
                or "STAGE_NOT_OK"
            ).strip()
        else:
            b210_stage_error = "PAYLOAD_NOT_DICT"

        b210_stage_errors.append({
            "stage": b210_stage_name,
            "error": b210_stage_error,
        })

    b210_status_course_uid = str(
        pick(status_payload, "latest_course_uid", "")
        or ""
    ).strip()

    b210_brief_course_uid_normalized = " ".join(
        latest_brief_course_uid.casefold().split()
    )

    b210_status_course_uid_normalized = " ".join(
        b210_status_course_uid.casefold().split()
    )

    if (
        b210_brief_course_uid_normalized
        and b210_status_course_uid_normalized
    ):
        b210_latest_course_uid_consistent_oui_non = (
            "oui"
            if b210_brief_course_uid_normalized
            == b210_status_course_uid_normalized
            else "non"
        )
    else:
        b210_latest_course_uid_consistent_oui_non = "ND"

    b210_completed_utc = datetime.now(
        ZoneInfo("UTC")
    ).replace(microsecond=0)

    b210_duration_ms = int(
        round(
            (_b210_time.monotonic() - b210_started_monotonic)
            * 1000
        )
    )

    b210_stability_ok = (
        all(b210_stage_ok.values())
        and run_status != "H5_RUN_ERROR"
        and b210_latest_course_uid_consistent_oui_non != "non"
    )

    b210_stability_audit = {
        "audit_version": "B2_10_STABILITY_AUDIT_V1",
        "scheduler_run_id": b210_run_id,
        "scheduler_version": b210_scheduler_version,
        "started_at_utc": b210_started_utc.isoformat(),
        "completed_at_utc": b210_completed_utc.isoformat(),
        "duration_ms": b210_duration_ms,
        "route": "/scheduler/run-due-h5",
        "method": "POST",
        "stability_ok": b210_stability_ok,
        "stage_ok": b210_stage_ok,
        "stage_error_count": len(b210_stage_errors),
        "stage_errors": b210_stage_errors,
        "run_status": run_status,
        "latest_brief_course_uid": latest_brief_course_uid,
        "status_latest_course_uid": b210_status_course_uid,
        "latest_course_uid_consistent_oui_non": (
            b210_latest_course_uid_consistent_oui_non
        ),
        "p31_non_recursive_guard_preserved_oui_non": "oui",
        "p31_reader_version_expected": (
            "p31_reuse_b2_official_reader_v0_9_31b2_9_fix1"
        ),
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
    }

    b211_response_payload = {
        "ok": True,
        "h5_run_ok": h5_run_ok,
        "run_status": run_status,
        "route": "/scheduler/run-due-h5",
        "scheduler_version": b210_scheduler_version,
        "scheduler_run_id": b210_run_id,
        "stability_audit": b210_stability_audit,
        "history_archive_ok": history_archive_ok,
        "history_archive_count": len(history_items),
        "history_archive_paths": history_archive_paths,
        "history_archive_items": history_items[:10],
        "now_paris": now_paris.isoformat(),
        "runs_h5": "oui",
        "writes_gcs": "oui",
        "brief_skipped_oui_non": brief_skipped_oui_non,
        "brief_skip_reason": brief_skip_reason,
        "morning_self_heal": morning_self_heal,
        "plan": {
            "ok": plan_payload.get("ok", "ND") if isinstance(plan_payload, dict) else "ND",
            "phase": plan_payload.get("phase", "h5_auto_plan") if isinstance(plan_payload, dict) else "h5_auto_plan",
            "planned_count": plan_payload.get("planned_count", 0) if isinstance(plan_payload, dict) else 0,
            "due_count": plan_payload.get("due_count", 0) if isinstance(plan_payload, dict) else 0,
            "url_required_count": plan_payload.get("url_required_count", 0) if isinstance(plan_payload, dict) else 0,
            "reason": plan_payload.get("reason", "") if isinstance(plan_payload, dict) else "",
            "error": plan_payload.get("error", "") if isinstance(plan_payload, dict) else "",
        },
        "url_resolver": {
            "ran_oui_non": resolver_ran_oui_non,
            "ok": resolver_payload.get("ok", "ND") if isinstance(resolver_payload, dict) else "ND",
            "phase": resolver_payload.get("phase", "h5_url_resolver") if isinstance(resolver_payload, dict) else "h5_url_resolver",
            "rows_input": resolver_metric("rows_input"),
            "rows_output": resolver_metric("rows_output"),
            "url_ok_count": resolver_metric("url_ok_count"),
            "url_not_found_count": resolver_metric("url_not_found_count"),
            "low_confidence_count": resolver_metric("low_confidence_count"),
            "reason": resolver_payload.get("reason", "") if isinstance(resolver_payload, dict) else "",
            "error": resolver_payload.get("error", "") if isinstance(resolver_payload, dict) else "",
        },
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "strict_policy": "Scheduler H5 automation only. Ne crée jamais de ticket strict.",
        "new_artifact_oui_non": new_artifact_oui_non,
        "prospective_capture_after_h5": p15_prospective_capture,
        "prospective_tracker_version": V20_P1_5_VERSION,
        "latest_brief_course_uid": latest_brief_course_uid,
        "latest_strict_kpi_exclusion_reason": latest_strict_kpi_exclusion_reason,
        "latest_decision_before_race_oui_non": latest_decision_before_race_oui_non,
        "capture": {
            "ok": capture_ok,
            "phase": pick(capture_payload, "phase"),
            "reason": pick(capture_payload, "reason", ""),
            "capture_count": capture_count,
            "odds_rows_total": odds_rows_total,
            "error": pick(capture_payload, "error", ""),
        },
        "brief": {
            "ok": brief_ok,
            "phase": pick(brief_payload, "phase"),
            "reason": pick(brief_payload, "reason", ""),
            "matched_courses": pick(brief_payload, "matched_courses", 0),
            "movement_rows_total": movement_rows_total,
            "briefs_created": briefs_created,
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

    try:
        # GPI_H5_SCHEDULER_STRUCTURED_OBSERVABILITY_PATCH_V0_9_31B2_11
        # One bounded JSON event per scheduler cycle.
        # Logging is fail-open and never changes the API response.
        b211_scheduler_event = {
            "severity": "INFO",
            "message": "GPI_H5_SCHEDULER_AUDIT",
            "event": "gpi_h5_scheduler_cycle",
            "observability_version": "GPI_H5_SCHEDULER_STRUCTURED_LOG_V0_9_31B2_11",
            "scheduler_run_id": b210_run_id,
            "scheduler_version": b210_scheduler_version,
            "run_status": run_status,
            "h5_run_ok": h5_run_ok,
            "stability_ok": b210_stability_ok,
            "stage_ok": b210_stage_ok,
            "stage_error_count": len(b210_stage_errors),
            "latest_course_uid_consistent_oui_non": (
                b210_latest_course_uid_consistent_oui_non
            ),
            "p31_non_recursive_guard_preserved_oui_non": "oui",
            "p31_reader_version_expected": (
                "p31_reuse_b2_official_reader_v0_9_31b2_9_fix1"
            ),
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
            "duration_ms": b210_duration_ms,
            "new_artifact_oui_non": new_artifact_oui_non,
            "capture_count": capture_count,
            "briefs_created": briefs_created,
            "movement_rows_total": movement_rows_total,
        }

        print(
            json.dumps(
                b211_scheduler_event,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            flush=True,
        )
    except Exception:
        pass

    return b211_response_payload


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
def _gpi_cloud_staking_policy() -> dict:
    """Single cloud-side source of truth for bankroll and paper stakes."""
    import os

    def positive_amount(name, default):
        try:
            value = float(str(os.getenv(name, default)).replace(",", "."))
            return value if value > 0 else float(default)
        except Exception:
            return float(default)

    return {
        "bankroll_eur": positive_amount("GPI_BANKROLL_EUR", 1000),
        "standard_stake_eur": positive_amount("GPI_STANDARD_STAKE_EUR", 1),
        "cp3_stake_eur": positive_amount("GPI_CP3_STAKE_EUR", 3),
        "policy_version": "GPI_CLOUD_STAKING_V1_1000_1_3",
        "source": "CLOUD_ENGINE",
    }


def _gpi_cloud_stake_for_ticket(ticket, allowed=True) -> str:
    policy = _gpi_cloud_staking_policy()
    normalized = str(ticket or "").strip().upper()
    if not allowed or normalized in {"", "NO_BET", "ND", "NONE", "NULL"}:
        return "0"
    amount = (
        policy["cp3_stake_eur"]
        if "CP3" in normalized
        else policy["standard_stake_eur"]
    )
    return str(int(amount)) if float(amount).is_integer() else str(amount)


def _gpi_bet_optimizer_error_fields(reason: str) -> dict:
    policy = _gpi_cloud_staking_policy()
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
        "bankroll_eur": policy["bankroll_eur"],
        "standard_stake_eur": policy["standard_stake_eur"],
        "cp3_stake_eur": policy["cp3_stake_eur"],
        "staking_policy_version": policy["policy_version"],
        "staking_policy_source": policy["source"],
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
    ctx.update(_gpi_cloud_staking_policy())

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

        ticket_paper = str(out.get("ticket_paper", "NO_BET"))
        cp3_audit_allowed = str(out.get("cp3_audit_allowed", "non"))
        policy = _gpi_cloud_staking_policy()

        return {
            "bet_optimizer_version": str(out.get("optimizer_version", "ND")),
            "bet_optimizer_scope": str(out.get("ruleset_scope", "ND")),
            "best_structure": str(out.get("best_structure", "NO_BET")),
            "ticket_paper_optimizer": ticket_paper,
            "stake_paper_optimizer": _gpi_cloud_stake_for_ticket(
                ticket_paper,
                allowed=ticket_paper.strip().upper() != "NO_BET",
            ),
            "ticket_real_optimizer": "NO_BET",
            "segment_roi_optimizer": str(out.get("segment_roi", "NO_BET")),
            "edge_status_optimizer": str(out.get("edge_status", "EDGE_ABSENT")),
            "economic_confidence": str(out.get("economic_confidence", "NULLE")),
            "variance_status": str(out.get("variance_status", "BLOQUEE")),
            "cp_sec_optimizer_status": str(out.get("cp_sec_status", "ND")),
            "sp_optimizer_status": str(out.get("sp_status", "ND")),
            "cp3_optimizer_status": str(out.get("cp3_status", "ND")),
            "cp3_audit_allowed": cp3_audit_allowed,
            "cp3_audit_ticket": str(out.get("cp3_audit_ticket", "NO_BET")),
            "cp3_audit_stake": _gpi_cloud_stake_for_ticket(
                out.get("cp3_audit_ticket", "NO_BET"),
                allowed=cp3_audit_allowed.strip().lower() == "oui",
            ),
            "cp3_audit_segment_roi": str(out.get("cp3_audit_segment_roi", "NO_BET")),
            "cp3_audit_reason": str(out.get("cp3_audit_reason", "ND")),
            "cp3_audit_scope": str(out.get("cp3_audit_scope", "PAPER_ONLY_100_DECISIONS")),
            "cp_sec_priority_score": str(out.get("cp_sec_priority_score", "0")),
            "cp_sec_priority_level": str(out.get("cp_sec_priority_level", "ND")),
            "cp_sec_priority_reasons": join_list(out.get("cp_sec_priority_reasons", "ND")),
            "optimizer_rejected_structures": join_list(out.get("rejected_structures", [])),
            "optimizer_reason_codes": join_list(out.get("reason_codes", [])),
            "optimizer_hard_blocks": join_list(out.get("hard_blocks", [])),
            "bankroll_eur": policy["bankroll_eur"],
            "standard_stake_eur": policy["standard_stake_eur"],
            "cp3_stake_eur": policy["cp3_stake_eur"],
            "staking_policy_version": policy["policy_version"],
            "staking_policy_source": policy["source"],
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

    # GPI H5 REPORT AUTO BRIEF SYNC V0_1
    # /gpi/brief peut provenir de latest_decision_engine_outputs.csv et ne pas
    # transporter h5_auto_context_sync. Dans ce cas, tenter d'enrichir le brief
    # depuis latest_h5_auto_brief.json uniquement si le course_uid est identique.
    try:
        brief_sync = brief.get("h5_auto_context_sync") if isinstance(brief, dict) else None

        if not isinstance(brief_sync, dict) or not brief_sync.get("context_gcs_uri"):
            auto_brief, auto_brief_source = _h5r_load_json_candidates(
                local_names=["latest_h5_auto_brief.json"],
                gcs_names=["gpi-lite/outputs/latest_h5_auto_brief.json"],
            )

            if not isinstance(auto_brief, dict):
                auto_brief = {}

            brief_course_uid_for_sync = _h5r_pick(
                brief,
                "course_uid",
                "latest_course_uid",
                default="ND",
            )
            auto_brief_course_uid = str(auto_brief.get("course_uid", "ND") or "ND").strip()

            try:
                brief_sync_norm = _h5auto_normalize_course_key(brief_course_uid_for_sync)
                auto_brief_sync_norm = _h5auto_normalize_course_key(auto_brief_course_uid)
            except Exception:
                brief_sync_norm = str(brief_course_uid_for_sync or "").strip().lower()
                auto_brief_sync_norm = str(auto_brief_course_uid or "").strip().lower()

            auto_brief_aligned = bool(
                brief_sync_norm
                and auto_brief_sync_norm
                and brief_sync_norm != "nd"
                and auto_brief_sync_norm != "nd"
                and brief_sync_norm == auto_brief_sync_norm
            )

            if auto_brief_aligned and isinstance(auto_brief.get("h5_auto_context_sync"), dict):
                brief["h5_auto_context_sync"] = auto_brief.get("h5_auto_context_sync")
                brief["h5_auto_context_sync_injected_oui_non"] = "oui"
                brief["h5_auto_context_sync_source"] = auto_brief_source
                brief["h5_auto_context_sync_auto_brief_course_uid"] = auto_brief_course_uid
            else:
                # Fallback direct : latest_h5_auto_brief peut pointer une autre course.
                # On tente alors le contexte course-scope derive du brief courant.
                try:
                    import re
                    import unicodedata

                    def _h5r_slug_from_course_uid(value):
                        raw = str(value or "").strip().lower()
                        raw = unicodedata.normalize("NFKD", raw)
                        raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
                        raw = raw.replace("&", " ")
                        raw = re.sub(r"[^a-z0-9]+", "_", raw)
                        raw = re.sub(r"_+", "_", raw).strip("_")
                        return raw

                    direct_slug = _h5r_slug_from_course_uid(brief_course_uid_for_sync)
                    direct_blob = (
                        "gpi-lite/outputs/h5_contexts/"
                        + direct_slug
                        + "_h5_auto_context.json"
                    )

                    direct_context, direct_context_source = _h5r_load_json_candidates(
                        local_names=[],
                        gcs_names=[direct_blob],
                    )

                    if not isinstance(direct_context, dict):
                        direct_context = {}

                    direct_context_course_uid = str(
                        direct_context.get("course_uid")
                        or direct_context.get("course_key")
                        or "ND"
                    ).strip()

                    # Fallback GCS fuzzy : le slug stocke peut contenir des artefacts
                    # d'encodage. On liste h5_contexts sur la date puis on valide par
                    # comparaison du course_uid interne, pas par nom de fichier.
                    if not direct_context.get("ok"):
                        try:
                            bucket, bucket_error = get_gcs_bucket()
                            fuzzy_errors = []
                            fuzzy_candidates = []
                            fuzzy_match_blob = ""

                            date_prefix = str(brief_course_uid_for_sync or "")[:10]
                            date_slug = date_prefix.replace("_", "_").strip()

                            if bucket is None:
                                fuzzy_errors.append(f"GCS_BUCKET_ERROR:{bucket_error}")
                            else:
                                fuzzy_prefix = "gpi-lite/outputs/h5_contexts/" + date_slug
                                for blob_obj in bucket.list_blobs(prefix=fuzzy_prefix):
                                    blob_name = getattr(blob_obj, "name", "")
                                    if not blob_name.endswith("_h5_auto_context.json"):
                                        continue

                                    fuzzy_payload, fuzzy_source = _h5r_load_json_candidates(
                                        local_names=[],
                                        gcs_names=[blob_name],
                                    )

                                    if not isinstance(fuzzy_payload, dict):
                                        continue

                                    fuzzy_course_uid = str(
                                        fuzzy_payload.get("course_uid")
                                        or fuzzy_payload.get("course_key")
                                        or "ND"
                                    ).strip()

                                    try:
                                        fuzzy_norm = _h5auto_normalize_course_key(fuzzy_course_uid)
                                    except Exception:
                                        fuzzy_norm = str(fuzzy_course_uid or "").strip().lower()

                                    fuzzy_candidates.append({
                                        "blob": blob_name,
                                        "course_uid": fuzzy_course_uid,
                                        "norm": fuzzy_norm,
                                    })

                                    if (
                                        fuzzy_payload.get("ok") is True
                                        and fuzzy_norm
                                        and brief_sync_norm
                                        and fuzzy_norm != "nd"
                                        and brief_sync_norm != "nd"
                                        and fuzzy_norm == brief_sync_norm
                                    ):
                                        direct_context = fuzzy_payload
                                        direct_context_source = {
                                            "kind": "gcs_fuzzy_course_uid_match",
                                            "file": "gs://" + GCS_BUCKET + "/" + blob_name,
                                            "searched_prefix": fuzzy_prefix,
                                            "matched_blob": blob_name,
                                            "direct_blob_attempted": direct_blob,
                                            "errors": fuzzy_errors,
                                        }
                                        direct_context_course_uid = fuzzy_course_uid
                                        fuzzy_match_blob = blob_name
                                        direct_blob = blob_name
                                        break

                            brief["h5_auto_context_sync_fuzzy_candidates_count"] = len(fuzzy_candidates)
                            brief["h5_auto_context_sync_fuzzy_match_blob"] = fuzzy_match_blob
                            brief["h5_auto_context_sync_fuzzy_errors"] = fuzzy_errors

                        except Exception as fuzzy_exc:
                            brief["h5_auto_context_sync_fuzzy_error"] = f"{type(fuzzy_exc).__name__}:{fuzzy_exc}"

                    try:
                        direct_context_norm = _h5auto_normalize_course_key(direct_context_course_uid)
                    except Exception:
                        direct_context_norm = str(direct_context_course_uid or "").strip().lower()

                    direct_aligned = bool(
                        direct_context.get("ok") is True
                        and brief_sync_norm
                        and direct_context_norm
                        and brief_sync_norm != "nd"
                        and direct_context_norm != "nd"
                        and brief_sync_norm == direct_context_norm
                    )

                    if direct_aligned:
                        brief["h5_auto_context_sync"] = {
                            "ok": True,
                            "reason": "H5_AUTO_CONTEXT_DIRECT_COURSE_SCOPE",
                            "course_uid": direct_context_course_uid,
                            "context_gcs_uri": "gs://gpi-lite-watchlist-data/" + direct_blob,
                            "strict_allowed_oui_non": "non",
                            "ticket_strict": "NO_BET",
                        }
                        brief["h5_auto_context_sync_injected_oui_non"] = "oui"
                        brief["h5_auto_context_sync_injection_reason"] = "DIRECT_COURSE_SCOPE_CONTEXT_FOUND"
                        brief["h5_auto_context_sync_auto_brief_course_uid"] = auto_brief_course_uid
                        brief["h5_auto_context_sync_direct_course_uid"] = direct_context_course_uid
                        brief["h5_auto_context_sync_direct_blob"] = direct_blob
                        brief["h5_auto_context_sync_direct_source"] = direct_context_source
                    else:
                        brief["h5_auto_context_sync_injected_oui_non"] = "non"
                        brief["h5_auto_context_sync_injection_reason"] = "LATEST_H5_AUTO_BRIEF_MISMATCH_AND_DIRECT_CONTEXT_NOT_FOUND_OR_MISMATCH"
                        brief["h5_auto_context_sync_auto_brief_course_uid"] = auto_brief_course_uid
                        brief["h5_auto_context_sync_auto_brief_source"] = auto_brief_source
                        brief["h5_auto_context_sync_direct_blob"] = direct_blob
                        brief["h5_auto_context_sync_direct_course_uid"] = direct_context_course_uid
                        brief["h5_auto_context_sync_direct_source"] = direct_context_source

                except Exception as direct_exc:
                    brief["h5_auto_context_sync_injected_oui_non"] = "non"
                    brief["h5_auto_context_sync_injection_reason"] = "DIRECT_COURSE_SCOPE_CONTEXT_ERROR"
                    brief["h5_auto_context_sync_injection_error"] = f"{type(direct_exc).__name__}:{direct_exc}"
                    brief["h5_auto_context_sync_auto_brief_course_uid"] = auto_brief_course_uid
                    brief["h5_auto_context_sync_auto_brief_source"] = auto_brief_source

    except Exception as exc:
        try:
            brief["h5_auto_context_sync_injected_oui_non"] = "non"
            brief["h5_auto_context_sync_injection_error"] = f"{type(exc).__name__}:{exc}"
        except Exception:
            pass

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

                    h10_blob = str(auto_context.get("h10_odds_csv_gcs", "") or "").strip()
                    h5_blob = str(auto_context.get("h5_odds_csv_gcs", "") or "").strip()

                    h10_local = h10_blob.split("/")[-1] if h10_blob else "latest_h10_odds.csv"
                    h5_local = h5_blob.split("/")[-1] if h5_blob else "latest_h5_odds.csv"

                    h10_rows, h10_source = _h5r_load_csv_candidates(
                        local_names=[h10_local],
                        gcs_names=[h10_blob] if h10_blob else [],
                    )
                    h5_rows, h5_source = _h5r_load_csv_candidates(
                        local_names=[h5_local],
                        gcs_names=[h5_blob] if h5_blob else [],
                    )

                    if not isinstance(h10_rows, list):
                        h10_rows = []
                    if not isinstance(h5_rows, list):
                        h5_rows = []

                    snapshot = {
                        "course_key": auto_course_uid,
                        "rows_9h": h10_rows,
                        "rows_h5": h5_rows,
                        "source_9h": h10_source,
                        "source_h5": h5_source,
                        "snapshot_reference_source": h10_source,
                        "snapshot_h5_source": h5_source,
                        "snapshot_reference_rows": len(h10_rows),
                        "snapshot_h5_rows": len(h5_rows),
                        "context_mismatch": "non",
                        "mismatch_reason": "",
                        "legacy_context_mismatch_oui_non": "oui",
                        "auto_context_used_oui_non": "oui",
                        "auto_context_aligned_oui_non": "oui",
                        "snapshot_kind": "H5_AUTO_ODDS_CAPTURE",
                        "auto_context_course_uid": auto_course_uid,
                        "auto_context_source": h5_context_source,
                        "h10_odds_csv_gcs": h10_blob,
                        "h5_odds_csv_gcs": h5_blob,
                        "h10_rows_count": len(h10_rows),
                        "h5_rows_count": len(h5_rows),
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
            "h5_auto_context_sync_injected_oui_non": brief.get("h5_auto_context_sync_injected_oui_non", "ND"),
            "h5_auto_context_sync_injection_reason": brief.get("h5_auto_context_sync_injection_reason", ""),
            "h5_auto_context_sync_injection_error": brief.get("h5_auto_context_sync_injection_error", ""),
            "h5_auto_context_sync_auto_brief_course_uid": brief.get("h5_auto_context_sync_auto_brief_course_uid", ""),
            "h5_auto_context_sync_direct_blob": brief.get("h5_auto_context_sync_direct_blob", ""),
            "h5_auto_context_sync_direct_course_uid": brief.get("h5_auto_context_sync_direct_course_uid", ""),
            "h5_auto_context_sync_direct_source": brief.get("h5_auto_context_sync_direct_source", {}),
            "h5_auto_context_sync_fuzzy_match_blob": brief.get("h5_auto_context_sync_fuzzy_match_blob", ""),
            "h5_auto_context_sync_fuzzy_candidates_count": brief.get("h5_auto_context_sync_fuzzy_candidates_count", "ND"),
            "h5_auto_context_sync_fuzzy_error": brief.get("h5_auto_context_sync_fuzzy_error", ""),
            "h5_auto_context_sync_fuzzy_errors": brief.get("h5_auto_context_sync_fuzzy_errors", []),
            "auto_context_used_oui_non": snapshot.get("auto_context_used_oui_non", "ND"),
            "auto_context_aligned_oui_non": snapshot.get("auto_context_aligned_oui_non", "ND"),
            "legacy_context_mismatch_oui_non": snapshot.get("legacy_context_mismatch_oui_non", "ND"),
            "snapshot_kind": snapshot.get("snapshot_kind", "LEGACY_H5_CONTEXT"),
            "auto_context_course_uid": snapshot.get("auto_context_course_uid", "ND"),
            "auto_context_mismatch_reason": snapshot.get("auto_context_mismatch_reason", ""),
            "auto_context_error": snapshot.get("auto_context_error", ""),
            "auto_context_source": snapshot.get("auto_context_source", {}),
            "h10_odds_csv_gcs": snapshot.get("h10_odds_csv_gcs", ""),
            "h5_odds_csv_gcs": snapshot.get("h5_odds_csv_gcs", ""),
            "h10_rows_count": snapshot.get("h10_rows_count", "ND"),
            "h5_rows_count": snapshot.get("h5_rows_count", "ND"),
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


@app.get("/health/h5-report-auto-context")
def health_h5_report_auto_context():
    """
    Health leger de non-regression pour /action/h5-report.
    Controle le fallback h5_auto_context course-scope + CSV H10/H5.
    Ne valide jamais un ticket strict.
    """
    try:
        payload = action_h5_report()
        report = payload.get("report", {}) if isinstance(payload, dict) else {}
        snapshots = report.get("snapshots", {}) if isinstance(report, dict) else {}

        top_cotes = report.get("top_cotes", []) if isinstance(report, dict) else []
        mouvements = report.get("mouvements", []) if isinstance(report, dict) else []
        strict_gate = report.get("strict_gate", {}) if isinstance(report, dict) else {}

        def _as_int(value):
            try:
                return int(value)
            except Exception:
                return 0

        checks = {
            "route_ok": payload.get("ok") is True if isinstance(payload, dict) else False,
            "context_mismatch_non": snapshots.get("context_mismatch") == "non",
            "auto_context_used": snapshots.get("auto_context_used_oui_non") == "oui",
            "auto_context_aligned": snapshots.get("auto_context_aligned_oui_non") == "oui",
            "snapshot_kind_auto": snapshots.get("snapshot_kind") == "H5_AUTO_ODDS_CAPTURE",
            "snapshot_reference_rows_positive": _as_int(snapshots.get("snapshot_reference_rows")) > 0,
            "snapshot_h5_rows_positive": _as_int(snapshots.get("snapshot_h5_rows")) > 0,
            "top_cotes_non_empty": isinstance(top_cotes, list) and len(top_cotes) > 0,
            "mouvements_non_empty": isinstance(mouvements, list) and len(mouvements) > 0,
            "strict_closed": strict_gate.get("strict_allowed_oui_non") == "non",
            "ticket_real_no_bet": report.get("ticket_real_optimizer") == "NO_BET",
        }

        ok = all(checks.values())

        return {
            "ok": ok,
            "health_version": "H5_REPORT_AUTO_CONTEXT_HEALTH_V0_1",
            "source_route": "/health/h5-report-auto-context",
            "checked_route": "/action/h5-report",
            "course_uid": report.get("course_uid", "ND"),
            "checks": checks,
            "summary": {
                "context_mismatch": snapshots.get("context_mismatch", "ND"),
                "snapshot_kind": snapshots.get("snapshot_kind", "ND"),
                "auto_context_used_oui_non": snapshots.get("auto_context_used_oui_non", "ND"),
                "auto_context_aligned_oui_non": snapshots.get("auto_context_aligned_oui_non", "ND"),
                "snapshot_reference_rows": snapshots.get("snapshot_reference_rows", "ND"),
                "snapshot_h5_rows": snapshots.get("snapshot_h5_rows", "ND"),
                "top_cotes_count": len(top_cotes) if isinstance(top_cotes, list) else 0,
                "mouvements_count": len(mouvements) if isinstance(mouvements, list) else 0,
                "strict_allowed_oui_non": strict_gate.get("strict_allowed_oui_non", "ND"),
                "ticket_real_optimizer": report.get("ticket_real_optimizer", "ND"),
                "optimizer_hard_blocks": report.get("optimizer_hard_blocks", "ND"),
            },
            "doctrine": "Health de non-regression lecture seule. Ne cree aucun ticket et ne valide jamais le strict.",
        }

    except Exception as exc:
        return {
            "ok": False,
            "health_version": "H5_REPORT_AUTO_CONTEXT_HEALTH_V0_1",
            "source_route": "/health/h5-report-auto-context",
            "error": f"{type(exc).__name__}:{exc}",
            "doctrine": "Erreur health = NO_BET implicite. Aucun ticket strict.",
        }





# CLOUD_READY SHADOW AUDIT EFFECTIVE GREEN RUNS V0_1
def _gpi_compute_effective_green_runs_by_course(history, required_runs=3, current_course_uid=None):
    """
    Calcule une serie verte effective par course_uid.
    Les runs d'autres courses sont ignores.
    Un run non vert du meme course_uid casse la serie.
    """
    if not isinstance(history, list):
        history = []

    required_runs = int(required_runs or 3)

    def _s(value):
        return str(value or "").strip()

    def _is_green(entry):
        return (
            _s(entry.get("green_oui_non")) == "oui"
            and _s(entry.get("course_uid")) != ""
            and _s(entry.get("course_uid")) == _s(entry.get("h5_auto_context_course_uid"))
            and _s(entry.get("h5_auto_context_ready_oui_non")) == "oui"
            and _s(entry.get("h5_auto_context_ready_for_cloud_gate_oui_non")) == "oui"
            and _s(entry.get("cloud_ready_legacy_oui_non")) == "non"
        )

    # Course effective = course courante si fournie, sinon derniere entree historisee.
    effective_course_uid = _s(current_course_uid)
    if not effective_course_uid:
        for entry in reversed(history):
            if isinstance(entry, dict) and _s(entry.get("course_uid")):
                effective_course_uid = _s(entry.get("course_uid"))
                break

    green_runs_effective = 0
    ignored_other_course_runs = 0
    last_effective_break_reason = "ND"
    last_effective_break_run_key = "ND"
    last_effective_break_recorded_at_utc = "ND"

    if not effective_course_uid:
        return {
            "green_runs_effective": 0,
            "migration_allowed_effective_oui_non": "non",
            "effective_course_uid": "ND",
            "last_effective_break_reason": "NO_EFFECTIVE_COURSE_UID",
            "last_effective_break_run_key": "ND",
            "last_effective_break_recorded_at_utc": "ND",
            "ignored_other_course_runs": 0,
        }

    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue

        entry_course_uid = _s(entry.get("course_uid"))

        if entry_course_uid != effective_course_uid:
            ignored_other_course_runs += 1
            continue

        if _is_green(entry):
            green_runs_effective += 1
            continue

        last_effective_break_reason = _s(entry.get("reason")) or "SAME_COURSE_NON_GREEN"
        last_effective_break_run_key = _s(entry.get("run_key")) or "ND"
        last_effective_break_recorded_at_utc = _s(entry.get("recorded_at_utc")) or "ND"
        break

    return {
        "green_runs_effective": green_runs_effective,
        "migration_allowed_effective_oui_non": "oui" if green_runs_effective >= required_runs else "non",
        "effective_course_uid": effective_course_uid,
        "last_effective_break_reason": last_effective_break_reason,
        "last_effective_break_run_key": last_effective_break_run_key,
        "last_effective_break_recorded_at_utc": last_effective_break_recorded_at_utc,
        "ignored_other_course_runs": ignored_other_course_runs,
    }


# CLOUD READY SHADOW AUDIT READ V0_1
@app.get("/health/cloud-ready-shadow-audit")
def health_cloud_ready_shadow_audit():
    """
    Lecture seule de l'audit cloud_ready shadow.
    Ne modifie jamais cloud_ready, strict_allowed_global ni les tickets.
    """
    audit_path = "gpi-lite/outputs/cloud_ready_shadow_audit.json"

    try:
        import json as _json

        bucket, bucket_error = get_gcs_bucket()
        if bucket is None:
            return {
                "ok": False,
                "health_version": "CLOUD_READY_SHADOW_AUDIT_READ_V0_1",
                "audit_exists": False,
                "audit_path": audit_path,
                "error": f"GCS_BUCKET_ERROR:{bucket_error}",
                "doctrine": "Lecture seule. Aucun gate reel modifie.",
            }

        blob = bucket.blob(audit_path)
        if not blob.exists():
            return {
                "ok": False,
                "health_version": "CLOUD_READY_SHADOW_AUDIT_READ_V0_1",
                "audit_exists": False,
                "audit_path": audit_path,
                "audit_ok_oui_non": "non",
                "required_runs": 3,
                "green_runs": 0,
                "latest_reason": "AUDIT_FILE_NOT_FOUND",
                "history_count": 0,
                "history_tail": [],
                "doctrine": "Lecture seule. Aucun gate reel modifie.",
            }

        raw = blob.download_as_text(encoding="utf-8")
        audit = _json.loads(raw or "{}")
        if not isinstance(audit, dict):
            audit = {}

        history = audit.get("history", [])
        if not isinstance(history, list):
            history = []

        required_runs = audit.get("required_runs", 3)
        green_runs = audit.get("green_runs", 0)
        audit_ok_oui_non = str(audit.get("audit_ok_oui_non", "non") or "non")

        try:
            required_runs = int(required_runs)
        except Exception:
            required_runs = 3

        try:
            green_runs = int(green_runs)
        except Exception:
            green_runs = 0

        latest = history[-1] if history and isinstance(history[-1], dict) else {}

        return {
            "ok": True,
            "health_version": "CLOUD_READY_SHADOW_AUDIT_READ_V0_1",
            "audit_exists": True,
            "audit_path": audit_path,
            "audit_version": audit.get("audit_version", "ND"),
            "audit_ok_oui_non": audit_ok_oui_non,
            "required_runs": required_runs,
            "green_runs": green_runs,
            "remaining_green_runs": max(required_runs - green_runs, 0),
            "migration_allowed_oui_non": (
                "oui" if audit_ok_oui_non == "oui" and green_runs >= required_runs else "non"
            ),
            "latest_run_key": audit.get("latest_run_key", "ND"),
            "latest_reason": audit.get("latest_reason", "ND"),
            "latest_course_uid": latest.get("course_uid", "ND") if isinstance(latest, dict) else "ND",
            "latest_h5_auto_context_course_uid": latest.get("h5_auto_context_course_uid", "ND") if isinstance(latest, dict) else "ND",
            "latest_green_oui_non": latest.get("green_oui_non", "ND") if isinstance(latest, dict) else "ND",
            "history_count": len(history),
            # CLOUD_READY SHADOW AUDIT EFFECTIVE RETURN V0_1
        # CLOUD_READY EFFECTIVE GREEN RUNS SAFE COURSE UID V0_1
        **_gpi_compute_effective_green_runs_by_course(
            history=history,
            required_runs=required_runs,
            current_course_uid=(
                locals().get("latest_course_uid")
                or locals().get("_latest_course_uid")
                or (
                    history[-1].get("course_uid")
                    if isinstance(history, list)
                    and len(history) > 0
                    and isinstance(history[-1], dict)
                    else ""
                )
            ),
        ),
        "green_runs_raw": green_runs,
        "history_tail": history[-10:],
            "doctrine": "Lecture seule. Ne modifie jamais cloud_ready, strict_allowed_global ni les tickets.",
        }

    except Exception as exc:
        return {
            "ok": False,
            "health_version": "CLOUD_READY_SHADOW_AUDIT_READ_V0_1",
            "audit_exists": False,
            "audit_path": audit_path,
            "error": f"{type(exc).__name__}:{exc}",
            "doctrine": "Erreur lecture audit = aucune migration. Aucun gate reel modifie.",
        }


# CLOUD_READY ACTION AUDIT EFFECTIVE FIELDS V0_3
@app.get("/action/cloud-ready-shadow-audit")
def action_cloud_ready_shadow_audit():
    """
    Facade compacte lecture seule pour l'audit cloud_ready shadow.
    """
    payload = _action_call(health_cloud_ready_shadow_audit, "health_cloud_ready_shadow_audit")

    if payload.get("ok") is not True:
        return {
            "ok": False,
            "facade_version": _ACTION_FACADE_VERSION,
            "source_route": "/health/cloud-ready-shadow-audit",
            "audit_exists": payload.get("audit_exists", False),
            "audit_path": payload.get("audit_path", "gpi-lite/outputs/cloud_ready_shadow_audit.json"),
            "audit_ok_oui_non": payload.get("audit_ok_oui_non", "non"),
            "green_runs_raw": payload.get("green_runs_raw", 0),
            "green_runs_effective": payload.get("green_runs_effective", 0),
            "migration_allowed_oui_non": "non",
            "migration_allowed_effective_oui_non": payload.get("migration_allowed_effective_oui_non", "non"),
            "effective_course_uid": payload.get("effective_course_uid", "ND"),
            "last_effective_break_reason": payload.get("last_effective_break_reason", "ND"),
            "last_effective_break_run_key": payload.get("last_effective_break_run_key", "ND"),
            "last_effective_break_recorded_at_utc": payload.get("last_effective_break_recorded_at_utc", "ND"),
            "ignored_other_course_runs": payload.get("ignored_other_course_runs", 0),
            "error": _action_str(payload.get("error")),
            "doctrine": "Lecture seule. Aucun gate reel modifie.",
        }

    return {
        "ok": True,
        "facade_version": _ACTION_FACADE_VERSION,
        "source_route": "/health/cloud-ready-shadow-audit",
        "audit_exists": payload.get("audit_exists", False),
        "audit_path": payload.get("audit_path", "ND"),
        "audit_version": payload.get("audit_version", "ND"),
        "audit_ok_oui_non": payload.get("audit_ok_oui_non", "non"),
        "required_runs": payload.get("required_runs", 3),
        "green_runs": payload.get("green_runs", 0),
        "green_runs_raw": payload.get("green_runs_raw", payload.get("green_runs", 0)),
        "green_runs_effective": payload.get("green_runs_effective", 0),
        "remaining_green_runs": payload.get("remaining_green_runs", 3),
        "migration_allowed_oui_non": payload.get("migration_allowed_oui_non", "non"),
        "migration_allowed_effective_oui_non": payload.get("migration_allowed_effective_oui_non", "non"),
        "effective_course_uid": payload.get("effective_course_uid", "ND"),
        "last_effective_break_reason": payload.get("last_effective_break_reason", "ND"),
        "last_effective_break_run_key": payload.get("last_effective_break_run_key", "ND"),
        "last_effective_break_recorded_at_utc": payload.get("last_effective_break_recorded_at_utc", "ND"),
        "ignored_other_course_runs": payload.get("ignored_other_course_runs", 0),
        "latest_run_key": payload.get("latest_run_key", "ND"),
        "latest_reason": payload.get("latest_reason", "ND"),
        "latest_course_uid": payload.get("latest_course_uid", "ND"),
        "latest_h5_auto_context_course_uid": payload.get("latest_h5_auto_context_course_uid", "ND"),
        "latest_green_oui_non": payload.get("latest_green_oui_non", "ND"),
        "history_count": payload.get("history_count", 0),
        "history_tail": payload.get("history_tail", []),
        "strict_note": "Migration autorisee seulement si audit_ok_oui_non=oui. Ce endpoint ne modifie pas cloud_ready.",
        "doctrine": "Lecture seule. Aucun ticket strict cree.",
    }




@app.get("/action/latest-h5-auto-context")
def action_latest_h5_auto_context():
    """
    GPI V0_9_31A
    Facade lecture seule du dernier contexte H5 auto.

    Ne modifie pas GCS.
    Ne cr?e jamais de ticket strict.
    Ne remplace pas les statuts officiels LeTrot / France Galop.
    """
    from pathlib import Path
    from datetime import datetime, timezone
    import json

    blob = "gpi-lite/outputs/latest_h5_auto_context.json"
    local_path = Path("data/output/latest_h5_auto_context.json")

    download_latest = {
        "ok": False,
        "reason": "DOWNLOAD_NOT_ATTEMPTED",
        "blob": blob,
        "local_path": str(local_path),
        "gcs_uri": f"gs://gpi-lite-watchlist-data/{blob}",
    }

    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)

        downloader = globals().get("_h5auto_download_file_from_gcs")
        if callable(downloader):
            download_latest = downloader(blob, local_path)
        else:
            download_latest = {
                "ok": False,
                "reason": "H5AUTO_DOWNLOADER_NOT_AVAILABLE",
                "blob": blob,
                "local_path": str(local_path),
                "gcs_uri": f"gs://gpi-lite-watchlist-data/{blob}",
            }
    except Exception as exc:
        download_latest = {
            "ok": False,
            "reason": f"DOWNLOAD_ERROR:{type(exc).__name__}:{exc}",
            "blob": blob,
            "local_path": str(local_path),
            "gcs_uri": f"gs://gpi-lite-watchlist-data/{blob}",
        }

    payload = None

    try:
        if local_path.exists():
            payload = json.loads(local_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {
            "ok": False,
            "phase": "latest_h5_auto_context",
            "reason": f"LOCAL_JSON_READ_ERROR:{type(exc).__name__}:{exc}",
            "download_latest": download_latest,
            "source": "local_after_download",
            "gcs_uri": f"gs://gpi-lite-watchlist-data/{blob}",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
            "facade_version": "action_latest_h5_auto_context_v0_9_31a",
            "action_route": "/action/latest-h5-auto-context",
        }

    if not isinstance(payload, dict):
        return {
            "ok": False,
            "phase": "latest_h5_auto_context",
            "reason": "LATEST_H5_AUTO_CONTEXT_NOT_FOUND_OR_INVALID",
            "download_latest": download_latest,
            "source": "cloud_storage",
            "gcs_uri": f"gs://gpi-lite-watchlist-data/{blob}",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
            "facade_version": "action_latest_h5_auto_context_v0_9_31a",
            "action_route": "/action/latest-h5-auto-context",
        }

    # Doctrine de s?curit? : cette facade ne peut jamais ouvrir le strict.
    payload["strict_allowed_oui_non"] = "non"
    payload["strict_usable_oui_non"] = "non"
    payload["ticket_strict"] = "NO_BET"

    payload.setdefault("ok", True)
    payload["phase"] = "latest_h5_auto_context"
    payload["source"] = "cloud_storage"
    payload["gcs_uri"] = f"gs://gpi-lite-watchlist-data/{blob}"
    payload["download_latest"] = download_latest
    payload["facade_version"] = "action_latest_h5_auto_context_v0_9_31a"
    payload["action_route"] = "/action/latest-h5-auto-context"
    payload["generated_at_action_utc"] = datetime.now(timezone.utc).isoformat()
    payload["doctrine"] = (
        "Lecture seule du contexte H5 auto. "
        "Ne vaut pas validation stricte sans NP/statut officiels LeTrot ou France Galop."
    )


    try:
        official_fields = _p31_compute_official_np_statut_gate_for_context(payload)
    except Exception as exc:
        official_fields = {
            "np_ok": "ND",
            "statut_ok": "ND",
            "np_source": f"OFFICIAL_STATUS_GATE_ERROR:{type(exc).__name__}:{exc}",
            "statut_source": f"OFFICIAL_STATUS_GATE_ERROR:{type(exc).__name__}:{exc}",
            "np_statut_source_level": "OFFICIAL_STATUS_GATE_ERROR",
            "official_runner_status_available_oui_non": "non",
            "official_join_ok_oui_non": "non",
        }

    payload["official_runner_status_fields"] = official_fields
    payload["official_runner_status_contract_version"] = "official_runner_status_contract_v0_9_31b1_3"
    payload["official_overlay_version"] = "official_source_kind_join_ok_v0_9_31b1_3"
    payload["official_runner_status_available_oui_non"] = official_fields.get(
        "official_runner_status_available_oui_non", "non"
    )
    payload["official_join_ok_oui_non"] = official_fields.get(
        "official_join_ok_oui_non", "non"
    )

    if official_fields.get("np_statut_source_level") in {
        "OFFICIAL_H5",
        "OFFICIAL_H5_SIMULATED_CONTRACT",
        "OFFICIAL_H5_LETROT",
        "OFFICIAL_H5_FRANCE_GALOP",
        "OFFICIAL_H5_MIXED_OFFICIAL_SOURCE",
        "OFFICIAL_H5_SOURCE_UNCLASSIFIED",
    }:
        for key in [
            "np_ok",
            "statut_ok",
            "np_source",
            "statut_source",
            "official_source",
            "official_contract_type",
            "official_source_kind",
            "np_count",
            "statut_missing_count",
            "unknown_is_np_count",
            "np_statut_rows_count",
            "np_statut_source_level",
            "np_statut_checked_at_utc",
        ]:
            if key in official_fields:
                payload[key] = official_fields.get(key)
        payload["official_runner_status_override_oui_non"] = "oui"
    else:
        payload["official_runner_status_override_oui_non"] = "non"

    # Verrou doctrine : m?me avec NP/statut OK, cette fa?ade ne valide jamais un ticket strict.
    payload["strict_allowed_oui_non"] = "non"
    payload["strict_usable_oui_non"] = "non"
    payload["ticket_strict"] = "NO_BET"


    # V0_9_31B2_6_1_COURSE_SCOPED_CONTEXT_FALLBACK
    fallback_overlay = _p361_read_course_scoped_h5_context_for_overlay(payload)
    if isinstance(fallback_overlay, dict) and isinstance(fallback_overlay.get("merged_payload"), dict):
        payload = fallback_overlay["merged_payload"]
    else:
        payload["course_level_context_fallback_overlay_applied_oui_non"] = "non"
        payload["course_level_context_fallback_overlay_error"] = "FALLBACK_OVERLAY_NOT_APPLIED"

    payload = _p361_apply_course_level_context_overlay(payload)


    return payload

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

    if np_ok == "ND":
        hard_blocks.append("NP_STATUS_NOT_VALIDATED")

    if statut_ok == "KO":
        hard_blocks.append("STATUT_INVALID_OR_MISSING")

    if statut_ok == "ND":
        hard_blocks.append("STATUT_NOT_VALIDATED")

    if source_level in {
        "LEGACY_BRIEF_NO_P11_FIELDS",
        "H5_SOURCE_ABSENT",
        "H5_STATUS_FIELDS_MISSING",
        "ZETURF_H5_ODDS_STATUS_FIELDS_PRESENT_UNVERIFIED",
        "ND",
    }:
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

    columns = {str(col).strip().lower(): col for col in rows.columns}
    required_columns = ["statut", "is_np"]
    missing_columns = [col for col in required_columns if col not in columns]

    if missing_columns:
        return {
            "np_ok": "ND",
            "statut_ok": "ND",
            "np_source": "ZETURF_H5_ODDS_STATUS_FIELDS_MISSING",
            "statut_source": "ZETURF_H5_ODDS_STATUS_FIELDS_MISSING",
            "np_source_detail": source,
            "statut_source_detail": source,
            "missing_np_statut_columns": missing_columns,
            "np_count": "ND",
            "statut_missing_count": "ND",
            "np_statut_rows_count": int(len(rows)),
            "np_statut_source_level": "H5_STATUS_FIELDS_MISSING",
            "np_statut_checked_at_utc": checked_at,
        }

    statut_col = columns["statut"]
    is_np_col = columns["is_np"]

    statut_raw = rows[statut_col].astype(str).str.strip()
    statut = statut_raw.str.upper()
    is_np_raw = rows[is_np_col].astype(str).str.strip()
    is_np_upper = is_np_raw.str.upper()
    is_np = pd.to_numeric(rows[is_np_col], errors="coerce")

    statut_missing_count = int(statut.isin(["", "NAN", "NONE", "ND"]).sum())
    unknown_is_np_count = int(is_np.isna().sum())

    all_statut_nd = bool(statut.isin(["", "NAN", "NONE", "ND"]).all())
    all_is_np_nd = bool(is_np_upper.isin(["", "NAN", "NONE", "ND"]).all())

    if all_statut_nd and all_is_np_nd:
        return {
            "np_ok": "ND",
            "statut_ok": "ND",
            "np_source": "ZETURF_H5_ODDS_STATUS_FIELDS_PRESENT_BUT_ND",
            "statut_source": "ZETURF_H5_ODDS_STATUS_FIELDS_PRESENT_BUT_ND",
            "np_source_detail": source,
            "statut_source_detail": source,
            "np_count": "ND",
            "statut_missing_count": statut_missing_count,
            "unknown_is_np_count": unknown_is_np_count,
            "np_statut_rows_count": int(len(rows)),
            "np_statut_source_level": "ZETURF_H5_ODDS_STATUS_FIELDS_PRESENT_UNVERIFIED",
            "np_statut_checked_at_utc": checked_at,
        }

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
        "unknown_is_np_count": unknown_is_np_count,
        "np_statut_rows_count": int(len(rows)),
        "np_statut_source_level": "ZETURF_H5_ODDS_STATUS_FIELDS_PRESENT",
        "np_statut_checked_at_utc": checked_at,
    }


def _p11_compute_np_statut_gate_for_h5_brief(brief):
    if not isinstance(brief, dict):
        brief = {}

    course_uid = brief.get("course_uid", "ND")
    rows, source = _p11_find_h5_np_statut_rows_for_course(course_uid)
    return _p11_compute_np_statut_gate_from_rows(rows, source)






# >>> GPI V0_9_31B1 OFFICIAL RUNNER STATUS CONTRACT >>>
# V0_9_31B1_1_OFFICIAL_SOURCE_KIND
# V0_9_31B1_2_JOIN_OK_LEVELS
# V0_9_31B1_3_VERSION_FIELDS
def _p31_official_status_download_latest():
    """
    Lecture seule.
    Tente de r?cup?rer le contrat officiel simul? depuis GCS si disponible.
    Ne cr?e jamais de ticket strict.
    """
    from pathlib import Path

    blob = "gpi-lite/outputs/official_runner_status_latest.csv"
    local_path = Path("data/output/official_runner_status_latest.csv")

    result = {
        "ok": False,
        "reason": "DOWNLOAD_NOT_ATTEMPTED",
        "blob": blob,
        "local_path": str(local_path),
        "gcs_uri": f"gs://gpi-lite-watchlist-data/{blob}",
    }

    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        downloader = globals().get("_h5auto_download_file_from_gcs")
        if callable(downloader):
            result = downloader(blob, local_path)
        else:
            result = {
                "ok": False,
                "reason": "H5AUTO_DOWNLOADER_NOT_AVAILABLE",
                "blob": blob,
                "local_path": str(local_path),
                "gcs_uri": f"gs://gpi-lite-watchlist-data/{blob}",
            }
    except Exception as exc:
        result = {
            "ok": False,
            "reason": f"DOWNLOAD_ERROR:{type(exc).__name__}:{exc}",
            "blob": blob,
            "local_path": str(local_path),
            "gcs_uri": f"gs://gpi-lite-watchlist-data/{blob}",
        }

    return result


def _p31_read_official_status_for_course(course_uid):
    """
    V0_9_31B2_12_COURSE_SCOPED_OFFICIAL_READER

    Lit d'abord le contrat officiel propre a la course. Le fichier ``latest``
    n'est qu'un repli de compatibilite. Cette fonction reste en lecture seule
    et ne peut jamais ouvrir le strict.

    Important: ne pas appeler ``_p372_force_enriched_course_context`` ici afin
    de conserver la garantie anti-recursion introduite en B2.9-fix1.
    """
    try:
        slug = _p36_safe_slug(course_uid)
    except Exception:
        slug = "nd"

    if slug and slug != "nd":
        scoped_result = _p37_read_text_local_or_gcs(
            local_candidates=[
                f"data/output/official_runner_status/{slug}_official_runner_status.csv",
                f"data/input/official_runner_status/{slug}_official_runner_status.csv",
            ],
            gcs_candidates=[
                f"gpi-lite/outputs/official_runner_status/{slug}_official_runner_status.csv",
            ],
        )

        if isinstance(scoped_result, dict) and scoped_result.get("ok"):
            return scoped_result, {
                "ok": True,
                "reason": "COURSE_SCOPED_SOURCE_SELECTED_LATEST_NOT_DOWNLOADED",
                "course_slug": slug,
            }, "COURSE_SCOPED"
    else:
        scoped_result = {
            "ok": False,
            "reason": "COURSE_SLUG_ND",
            "errors": ["COURSE_SLUG_ND"],
        }

    download_status = _p31_official_status_download_latest()
    latest_result = _p37_read_text_local_or_gcs(
        local_candidates=[
            "data/output/official_runner_status_latest.csv",
            "data/output/official_runner_status.csv",
            "data/input/official_runner_status_latest.csv",
            "data/input/official_runner_status.csv",
        ],
        gcs_candidates=[
            "gpi-lite/outputs/official_runner_status_latest.csv",
            "gpi-lite/outputs/official_runner_status.csv",
        ],
    )

    if isinstance(latest_result, dict):
        latest_result = dict(latest_result)
        latest_result["course_scoped_attempt"] = {
            "ok": scoped_result.get("ok", False) if isinstance(scoped_result, dict) else False,
            "reason": scoped_result.get("reason", "ND") if isinstance(scoped_result, dict) else "RESULT_NOT_DICT",
            "errors": scoped_result.get("errors", []) if isinstance(scoped_result, dict) else [],
            "course_slug": slug,
        }

    return latest_result, download_status, "LATEST_FALLBACK"


def _p31_find_official_np_statut_rows_for_course(course_uid):
    """
    V0_9_31B2_9_REUSE_B2_OFFICIAL_READER_IN_P31

    Remplace la lecture pandas fragile du CSV officiel par la lecture robuste B2 :
    - _p37_read_text_local_or_gcs
    - _p37_csv_rows_from_text
    - _p37_row_matches_course
    - _p37_normalize_runner_status_row

    Lecture seule.
    Ne scrape rien.
    N'ouvre jamais le strict.
    """
    import pandas as pd

    wanted = _p37_norm_text(course_uid)
    if not wanted or wanted == "nd":
        return None, "COURSE_UID_ND", {
            "download_official_status": {"ok": False, "reason": "COURSE_UID_ND"},
            "reader_version": "p31_reuse_b2_official_reader_v0_9_31b2_9_fix1",
            "strict_allowed_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    read_result, download_status, read_strategy = (
        _p31_read_official_status_for_course(course_uid)
    )

    diagnostics = {
        "reader_version": "p31_course_scoped_first_v0_9_31b2_12",
        "official_status_read_strategy": read_strategy,
        "download_official_status": download_status,
        "official_status_read_result": {
            "ok": read_result.get("ok", False) if isinstance(read_result, dict) else False,
            "source": read_result.get("source", "ND") if isinstance(read_result, dict) else "READ_RESULT_NOT_DICT",
            "path": read_result.get("path", "ND") if isinstance(read_result, dict) else "ND",
            "gcs_uri": read_result.get("gcs_uri", "ND") if isinstance(read_result, dict) else "ND",
            "errors": read_result.get("errors", []) if isinstance(read_result, dict) else [],
        },
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
    }

    if not isinstance(read_result, dict) or not read_result.get("ok"):
        diagnostics["official_status_join_status"] = "OFFICIAL_RUNNER_STATUS_SOURCE_ABSENT"
        diagnostics["official_status_rows_total"] = 0
        diagnostics["official_status_rows_matched_course"] = 0
        return None, "OFFICIAL_RUNNER_STATUS_SOURCE_ABSENT", diagnostics

    try:
        csv_payload = _p37_csv_rows_from_text(read_result.get("text", ""))
    except Exception as exc:
        diagnostics["official_status_join_status"] = f"OFFICIAL_STATUS_CSV_PARSE_ERROR:{type(exc).__name__}:{exc}"
        diagnostics["official_status_rows_total"] = 0
        diagnostics["official_status_rows_matched_course"] = 0
        return None, "OFFICIAL_STATUS_CSV_PARSE_ERROR", diagnostics

    rows = csv_payload.get("rows", []) if isinstance(csv_payload, dict) else []
    fieldnames = csv_payload.get("fieldnames", []) if isinstance(csv_payload, dict) else []

    # V0_9_31B2_9_FIX1_NON_RECURSIVE_P31_READER
    # Ne jamais appeler _p372_force_enriched_course_context ici :
    # action_latest_h5_auto_context -> _p31 -> _p372 peut creer une recursion indirecte.
    # Le match officiel se fait par course_uid exact, suffisant depuis B2.8.10-fix2b.
    context_payload = {
        "course_uid": course_uid,
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
    }

    diagnostics["p31_context_force_skipped_oui_non"] = "oui"
    diagnostics["p31_context_force_skip_reason"] = "NON_RECURSIVE_P31_READER_B2_9_FIX1"

    try:
        column_audit = _p37_classify_official_runner_status_columns(fieldnames)
    except Exception as exc:
        column_audit = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}

    try:
        source_diagnostics = _p371_build_official_status_source_diagnostics(rows, context_payload)
    except Exception as exc:
        source_diagnostics = {
            "source_alignment_status": "SOURCE_DIAGNOSTICS_ERROR",
            "reason_codes": [f"{type(exc).__name__}:{exc}"],
        }

    matched = []
    rejected_sample = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        try:
            match_info = _p37_row_matches_course(row, context_payload)
        except Exception as exc:
            match_info = {
                "match": False,
                "score": 0,
                "reason_codes": [f"MATCH_ERROR:{type(exc).__name__}:{exc}"],
            }

        if match_info.get("match"):
            normalized = _p37_normalize_runner_status_row(row)

            normalized["course_uid"] = _p37_pick(row, ["course_uid", "course_key"], course_uid)
            normalized["checked_at_utc"] = _p37_pick(row, ["checked_at_utc", "checked_at", "created_at"], "ND")
            normalized["official_source"] = _p37_pick(row, ["official_source"], normalized.get("official_source", "ND"))
            normalized["statut_source"] = _p37_pick(row, ["statut_source"], normalized.get("statut_source", "ND"))
            normalized["np_statut_confidence"] = _p37_pick(row, ["np_statut_confidence", "confidence"], normalized.get("np_statut_confidence", "ND"))

            normalized["match_score"] = match_info.get("score", 0)
            normalized["match_reason_codes"] = match_info.get("reason_codes", [])

            matched.append(normalized)

        elif len(rejected_sample) < 8:
            rejected_sample.append({
                "match_score": match_info.get("score", 0),
                "match_reason_codes": match_info.get("reason_codes", []),
                "course_uid": _p37_pick(row, ["course_uid", "course_key"], "ND"),
                "reunion_no": _p37_pick(row, ["reunion_no", "reunion", "num_reunion", "reunion_num", "r"], "ND"),
                "course_no": _p37_pick(row, ["course_no", "num_course", "course_num", "c", "course_no_int"], "ND"),
                "scheduled_time": _p37_pick(row, ["scheduled_time", "course_time", "start_time", "heure_depart", "heure"], "ND"),
                "race_name": _p37_pick(row, ["race_name", "course_name", "prix", "libelle"], "ND"),
            })

    required = [
        "course_uid",
        "numero",
        "cheval",
        "statut",
        "is_np",
        "official_source",
        "statut_source",
        "np_statut_confidence",
        "checked_at_utc",
    ]

    df = pd.DataFrame(matched)

    for col in required:
        if col not in df.columns:
            df[col] = "ND"

    source = (
        read_result.get("gcs_uri")
        or read_result.get("path")
        or read_result.get("source")
        or "OFFICIAL_RUNNER_STATUS_B2_ROBUST_READER"
    )

    diagnostics.update({
        "official_status_csv_fieldnames": fieldnames,
        "official_status_column_audit": column_audit,
        "official_status_source_diagnostics": source_diagnostics,
        "official_status_source_alignment_status": source_diagnostics.get("source_alignment_status", "ND") if isinstance(source_diagnostics, dict) else "ND",
        "official_status_source_diagnostics_reason_codes": source_diagnostics.get("reason_codes", []) if isinstance(source_diagnostics, dict) else [],
        "official_status_rows_total": len(rows),
        "official_status_rows_matched_course": len(matched),
        "official_status_join_status": "OFFICIAL_STATUS_ROWS_MATCHED_COMPLETE" if len(matched) > 0 else "NO_OFFICIAL_STATUS_ROWS_MATCH_COURSE",
        "rejected_rows_sample": rejected_sample,
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
    })

    return df, source, diagnostics



def _p31_compute_official_np_statut_gate_from_rows(rows, source, diagnostics=None):
    import pandas as pd
    from datetime import datetime, timezone

    checked_at = datetime.now(timezone.utc).isoformat()
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}

    if rows is None or getattr(rows, "empty", True):
        return {
            "np_ok": "ND",
            "statut_ok": "ND",
            "np_source": source or "OFFICIAL_SOURCE_ABSENT_FOR_COURSE",
            "statut_source": source or "OFFICIAL_SOURCE_ABSENT_FOR_COURSE",
            "official_source": "ND",
            "np_count": "ND",
            "statut_missing_count": "ND",
            "unknown_is_np_count": "ND",
            "np_statut_rows_count": 0,
            "np_statut_source_level": "OFFICIAL_SOURCE_ABSENT",
            "np_statut_checked_at_utc": checked_at,
            "official_join_ok_oui_non": "non",
            "official_runner_status_available_oui_non": "non",
            **diagnostics,
        }

    columns = {str(col).strip().lower(): col for col in rows.columns}
    required_columns = [
        "course_uid",
        "numero",
        "cheval",
        "statut",
        "is_np",
        "official_source",
        "statut_source",
        "np_statut_confidence",
        "checked_at_utc",
    ]
    missing_columns = [col for col in required_columns if col not in columns]

    if missing_columns:
        return {
            "np_ok": "ND",
            "statut_ok": "ND",
            "np_source": "OFFICIAL_STATUS_FIELDS_MISSING",
            "statut_source": "OFFICIAL_STATUS_FIELDS_MISSING",
            "official_source": "ND",
            "missing_np_statut_columns": missing_columns,
            "np_count": "ND",
            "statut_missing_count": "ND",
            "unknown_is_np_count": "ND",
            "np_statut_rows_count": int(len(rows)),
            "np_statut_source_level": "OFFICIAL_STATUS_FIELDS_MISSING",
            "np_statut_checked_at_utc": checked_at,
            "official_join_ok_oui_non": "non",
            "official_runner_status_available_oui_non": "non",
            **diagnostics,
        }

    statut_col = columns["statut"]
    is_np_col = columns["is_np"]
    official_source_col = columns["official_source"]
    confidence_col = columns["np_statut_confidence"]

    statut_raw = rows[statut_col].astype(str).str.strip()
    statut = statut_raw.str.upper()

    is_np_raw = rows[is_np_col].astype(str).str.strip()
    is_np_upper = is_np_raw.str.upper()
    is_np_num = pd.to_numeric(rows[is_np_col], errors="coerce")

    statut_missing_count = int(statut.isin(["", "NAN", "NONE", "ND"]).sum())
    unknown_is_np_count = int(is_np_upper.isin(["", "NAN", "NONE", "ND"]).sum())

    np_by_flag_num = int((is_np_num.fillna(0) == 1).sum())
    np_by_flag_txt = int(is_np_upper.isin(["1", "OUI", "YES", "TRUE", "VRAI"]).sum())
    np_by_statut = int(statut.isin(["NP", "NON_PARTANT", "NON-PARTANT", "NON PARTANT"]).sum())
    np_count = max(np_by_flag_num, np_by_flag_txt, np_by_statut)

    valid_status = statut.isin([
        "PARTANT",
        "PARTANTS",
        "OK",
        "NP",
        "NON_PARTANT",
        "NON-PARTANT",
        "NON PARTANT",
    ])

    official_sources = sorted(
        set(
            str(v).strip()
            for v in rows[official_source_col].dropna().tolist()
            if str(v).strip()
        )
    )

    confidence_values = sorted(
        set(
            str(v).strip()
            for v in rows[confidence_col].dropna().tolist()
            if str(v).strip()
        )
    )

    official_sources_upper = [
        str(v).strip().upper()
        for v in official_sources
        if str(v).strip()
    ]

    is_simulated_contract = any("SIMULATED_CONTRACT" in v for v in official_sources_upper)
    is_letrot = any(("LETROT" in v or "LE_TROT" in v or "LE TROT" in v) for v in official_sources_upper)
    is_france_galop = any(("FRANCE_GALOP" in v or "FRANCE GALOP" in v or "FRANCEGALOP" in v) for v in official_sources_upper)

    if is_simulated_contract:
        official_contract_type = "SIMULATED"
        official_source_kind = "SIMULATED_CONTRACT"
        ok_source_level = "OFFICIAL_H5_SIMULATED_CONTRACT"
    elif is_letrot and not is_france_galop:
        official_contract_type = "REAL_OFFICIAL"
        official_source_kind = "LETROT"
        ok_source_level = "OFFICIAL_H5_LETROT"
    elif is_france_galop and not is_letrot:
        official_contract_type = "REAL_OFFICIAL"
        official_source_kind = "FRANCE_GALOP"
        ok_source_level = "OFFICIAL_H5_FRANCE_GALOP"
    elif is_letrot and is_france_galop:
        official_contract_type = "REAL_OFFICIAL_MIXED"
        official_source_kind = "LETROT|FRANCE_GALOP"
        ok_source_level = "OFFICIAL_H5_MIXED_OFFICIAL_SOURCE"
    elif official_sources_upper:
        official_contract_type = "UNKNOWN"
        official_source_kind = "UNKNOWN_OFFICIAL_CONTRACT"
        ok_source_level = "OFFICIAL_H5_SOURCE_UNCLASSIFIED"
    else:
        official_contract_type = "ND"
        official_source_kind = "ND"
        ok_source_level = "OFFICIAL_SOURCE_ABSENT"

    if statut_missing_count > 0 or unknown_is_np_count > 0 or not bool(valid_status.all()):
        statut_ok = "KO"
        source_level = "OFFICIAL_STATUS_INCOMPLETE_OR_INVALID"
    else:
        statut_ok = "OK"
        source_level = ok_source_level

    np_ok = "KO" if np_count > 0 else ("OK" if statut_ok == "OK" else "ND")

    official_join_ok_levels = {
        "OFFICIAL_H5",
        "OFFICIAL_H5_SIMULATED_CONTRACT",
        "OFFICIAL_H5_LETROT",
        "OFFICIAL_H5_FRANCE_GALOP",
        "OFFICIAL_H5_MIXED_OFFICIAL_SOURCE",
        "OFFICIAL_H5_SOURCE_UNCLASSIFIED",
    }

    return {
        "np_ok": np_ok,
        "statut_ok": statut_ok,
        "np_source": source,
        "statut_source": source,
        "official_source": "|".join(official_sources) if official_sources else "ND",
        "official_contract_type": official_contract_type,
        "official_source_kind": official_source_kind,
        "official_confidence_values": "|".join(confidence_values) if confidence_values else "ND",
        "np_count": np_count,
        "statut_missing_count": statut_missing_count,
        "unknown_is_np_count": unknown_is_np_count,
        "np_statut_rows_count": int(len(rows)),
        "np_statut_source_level": source_level,
        "np_statut_checked_at_utc": checked_at,
        "official_runner_status_contract_version": "official_runner_status_contract_v0_9_31b1_3",
        "official_overlay_version": "official_source_kind_join_ok_v0_9_31b1_3",
        "official_join_ok_oui_non": "oui" if source_level in official_join_ok_levels else "non",
        "official_runner_status_available_oui_non": "oui",
        **diagnostics,
    }


def _p31_compute_official_np_statut_gate_for_context(context):
    if not isinstance(context, dict):
        context = {}

    course_uid = context.get("course_uid", "ND")
    rows, source, diagnostics = _p31_find_official_np_statut_rows_for_course(course_uid)
    return _p31_compute_official_np_statut_gate_from_rows(rows, source, diagnostics)


@app.get("/action/official-runner-status-preview")
def action_official_runner_status_preview():
    """
    V0_9_31B1 preview lecture seule.
    Ne scrape pas.
    Ne modifie pas GCS.
    Ne cr?e jamais de ticket strict.
    """
    source_payload = action_latest_h5_auto_context()
    if not isinstance(source_payload, dict):
        source_payload = {}

    fields = _p31_compute_official_np_statut_gate_for_context(source_payload)

    return {
        "ok": True,
        "phase": "official_runner_status_preview",
        "preview_version": "official_runner_status_contract_v0_9_31b1_3",
        "official_runner_status_contract_version": "official_runner_status_contract_v0_9_31b1_3",
        "official_overlay_version": "official_source_kind_join_ok_v0_9_31b1_3",
        "course_uid": source_payload.get("course_uid", "ND"),
        "course_slug": source_payload.get("course_slug", "ND"),
        "fields": fields,
        "VERDICT": "NO_BET",
        "Ticket_exact_ou_NO_BET": "NO_BET",
        "Segment_ROI": "NO_BET",
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "doctrine": "Preview NP/statut officiel simul?. Ne vaut pas ticket strict.",
    }
# <<< GPI V0_9_31B1 OFFICIAL RUNNER STATUS CONTRACT <<<





# >>> GPI V0_9_31B2_1 LETROT TODAY INDEX PREVIEW >>>
# V0_9_31B2_1_1_REGEX_FIX
# V0_9_31B2_1_2_PARSE_QUALITY_GUARD
# V0_9_31B2_1_3_STATUS_EXTRACTION
# V0_9_31B2_1_4_DEDUPE_OVERPARSE_GUARD
# V0_9_31B2_1_5_CANONICAL_STATUS_CODES
def _p32_letrot_now_utc_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _p32_norm_space(value):
    import re
    import html as html_lib

    s = "" if value is None else str(value)
    s = html_lib.unescape(s)
    s = s.replace("\xa0", " ")
    s = s.replace("&nbsp;", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _p32_fetch_letrot_today_html(timeout_seconds=12):
    """
    Lecture seule LeTrot.
    Ne modifie rien.
    Ne contourne aucune protection.
    """
    import urllib.request

    source_url = "https://www.letrot.com/courses/aujourd-hui"

    req = urllib.request.Request(
        source_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 compatible; GPI-Lite-Watchlist/0.9 "
                "(official index preview; read-only)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
        },
        method="GET",
    )

    checked_at = _p32_letrot_now_utc_iso()

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            html_text = raw.decode(charset, errors="replace")
            return {
                "ok": True,
                "source_url": source_url,
                "checked_at_utc": checked_at,
                "http_status": getattr(resp, "status", "ND"),
                "content_type": resp.headers.get("content-type", "ND"),
                "html_bytes": len(raw),
                "html": html_text,
                "error": "ND",
            }
    except Exception as exc:
        return {
            "ok": False,
            "source_url": source_url,
            "checked_at_utc": checked_at,
            "http_status": "ND",
            "content_type": "ND",
            "html_bytes": 0,
            "html": "",
            "error": f"{type(exc).__name__}:{exc}",
        }


def _p32_html_to_text(html_text):
    import re
    import html as html_lib

    s = "" if html_text is None else str(html_text)

    s = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", s)
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", s)
    s = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", s)

    # S?parer les balises utiles avant suppression.
    s = re.sub(r"(?i)<(br|/p|/div|/section|/article|/li|/a|/h[1-6])[^>]*>", "\n", s)
    s = re.sub(r"(?is)<[^>]+>", " ", s)

    s = html_lib.unescape(s)
    s = s.replace("\xa0", " ")
    s = s.replace("&nbsp;", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s+", "\n", s)
    s = re.sub(r"\n{2,}", "\n", s)

    return s.strip()



def _p32_strip_accents(value):
    import unicodedata

    s = "" if value is None else str(value)
    return "".join(
        c
        for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _p32_status_regex_terms():
    # Variantes accentu?es, non accentu?es et affichages d?grad?s Windows.
    return [
        "en cours",
        "\u00e0 venir",
        "a venir",
        "? venir",
        "termin\u00e9e",
        "terminee",
        "termin?e",
        "provisoire",
        "annul\u00e9e",
        "annulee",
        "annul?e",
        "report\u00e9e",
        "reportee",
        "report?e",
    ]


def _p32_extract_course_status_from_window(value):
    """
    Extraction prudente d'un statut course dans une fen?tre texte.
    Retourne ND si ambigu ou absent.
    """
    import re

    raw = _p32_norm_space(value)
    if not raw:
        return "ND"

    folded = _p32_strip_accents(raw).lower()
    folded = folded.replace("?", "a")

    checks = [
        (r"\ben cours\b", "en cours"),
        (r"\ba venir\b", "\u00e0 venir"),
        (r"\bterminee\b", "termin\u00e9e"),
        (r"\bprovisoire\b", "provisoire"),
        (r"\bannulee\b", "annul\u00e9e"),
        (r"\breportee\b", "report\u00e9e"),
    ]

    found = []
    for pattern, canonical in checks:
        if re.search(pattern, folded, flags=re.IGNORECASE):
            found.append(canonical)

    # Si plusieurs statuts apparaissent dans la m?me fen?tre, on garde le premier
    # dans l'ordre m?tier ci-dessus. C'est volontairement prudent.
    return found[0] if found else "ND"




def _p32_status_text_candidates(value):
    """
    G?n?re plusieurs variantes d'un statut :
    - brut ;
    - r?paration mojibake fr?quente ;
    - variante sans accents.
    """
    s = _p32_norm_space(value)
    candidates = []

    if s:
        candidates.append(s)

    # R?paration mojibake classique UTF-8 lu comme latin-1.
    try:
        repaired = s.encode("latin-1", errors="ignore").decode("utf-8", errors="ignore")
        repaired = _p32_norm_space(repaired)
        if repaired and repaired not in candidates:
            candidates.append(repaired)
    except Exception:
        pass

    replacements = {
        "??": "?",
        "??": "?",
        "??": "?",
        "??": "?",
        "? ": "? ",
        "? ": "?",
        "??": "?",
        "??": "?",
        "??": "?",
        "??": "?",
        "??": "?",
        "??": "?",
        "?": "",
        "?": "",
    }

    repaired2 = s
    for old, new in replacements.items():
        repaired2 = repaired2.replace(old, new)

    repaired2 = _p32_norm_space(repaired2)
    if repaired2 and repaired2 not in candidates:
        candidates.append(repaired2)

    folded = _p32_strip_accents(repaired2 or s).lower()
    folded = folded.replace("?", "a")
    folded = _p32_norm_space(folded)
    if folded and folded not in candidates:
        candidates.append(folded)

    return candidates


def _p32_canonical_status_code(value):
    """
    Code stable pour matching et audit.
    Ne d?pend pas des accents ni de l'affichage PowerShell.
    """
    import re

    for candidate in _p32_status_text_candidates(value):
        folded = _p32_strip_accents(candidate).lower()
        folded = folded.replace("?", "a")
        folded = _p32_norm_space(folded)

        if re.search(r"\ben cours\b", folded):
            return "LIVE"

        if re.search(r"\ba venir\b", folded):
            return "UPCOMING"

        if re.search(r"\bterminee\b", folded):
            return "FINISHED"

        if re.search(r"\bprovisoire\b", folded):
            return "PROVISIONAL"

        if re.search(r"\bannulee\b", folded):
            return "CANCELLED"

        if re.search(r"\breportee\b", folded):
            return "POSTPONED"

    return "ND"




# V0_9_31B2_8_3_FIX1_PRESERVE_LETROT_HREFS
def _p383_fix1_extract_observed_hrefs_from_segment(segment):
    """
    Extrait uniquement les hrefs observ?s dans un segment HTML LeTrot.
    Ne construit pas d?URL artificielle hors transformation chemin relatif -> URL absolue letrot.com.
    """
    import re
    from html import unescape

    raw = str(segment or "")
    out = []
    seen = set()

    href_re = re.compile(
        r'<a\b[^>]*?href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<label>.*?)</a>',
        flags=re.I | re.S,
    )

    for m in href_re.finditer(raw):
        href = unescape(m.group("href") or "").strip()
        label_html = m.group("label") or ""
        label = re.sub(r"<[^>]+>", " ", label_html)
        label = re.sub(r"\s+", " ", unescape(label)).strip()

        if not href:
            continue

        href_l = href.lower()
        label_l = label.lower()

        plausible = (
            "course" in href_l
            or "courses" in href_l
            or "reunion" in href_l
            or "partant" in href_l
            or "programme" in href_l
            or "prix" in label_l
            or "course" in label_l
        )

        if not plausible:
            continue

        if href.startswith("http://") or href.startswith("https://"):
            url = href
            path = href
        elif href.startswith("/"):
            path = href
            url = "https://www.letrot.com" + href
        else:
            continue

        if url in seen:
            continue

        seen.add(url)
        out.append({
            "href": href,
            "path": path,
            "url": url,
            "label": label,
        })

    return out


def _p383_fix1_match_observed_href_to_course(segment, course_no):
    """
    Associe prudemment une URL observ?e ? Cx.
    Si aucun indice Cx n?est trouv?, renvoie ND.
    """
    hrefs = _p383_fix1_extract_observed_hrefs_from_segment(segment)

    try:
        cno = int(course_no)
    except Exception:
        return {
            "detail_url": "ND",
            "detail_path": "ND",
            "detail_label": "ND",
            "detail_url_match_status": "COURSE_NO_INVALID",
            "detail_url_match_score": 0,
            "detail_url_match_reason_codes": [],
            "observed_href_count": len(hrefs),
        }

    scored = []

    for item in hrefs:
        joined = " ".join([
            str(item.get("href", "")),
            str(item.get("path", "")),
            str(item.get("url", "")),
            str(item.get("label", "")),
        ])

        text = _p37_norm_text(joined)

        score = 0
        reasons = []

        tokens = [
            f"c{cno}",
            f"c {cno}",
            f"course {cno}",
            f"course n {cno}",
            f"course numero {cno}",
        ]

        if any(tok in text for tok in tokens):
            score += 6
            reasons.append("COURSE_NO_TOKEN_MATCH")

        # Indices moins forts, seulement pour d?partager.
        if "partant" in text:
            score += 1
            reasons.append("PARTANT_TOKEN")
        if "prix" in text:
            score += 1
            reasons.append("PRIX_TOKEN")
        if "course" in text:
            score += 1
            reasons.append("COURSE_TOKEN")

        if score >= 6:
            scored.append({
                "score": score,
                "reason_codes": reasons,
                "item": item,
            })

    if not scored:
        return {
            "detail_url": "ND",
            "detail_path": "ND",
            "detail_label": "ND",
            "detail_url_match_status": "NO_OBSERVED_HREF_MATCH_COURSE",
            "detail_url_match_score": 0,
            "detail_url_match_reason_codes": [],
            "observed_href_count": len(hrefs),
        }

    scored = sorted(scored, key=lambda x: x.get("score", 0), reverse=True)
    best = scored[0]
    item = best.get("item", {}) or {}

    return {
        "detail_url": item.get("url", "ND"),
        "detail_path": item.get("path", "ND"),
        "detail_label": item.get("label", "ND"),
        "detail_url_match_status": "OBSERVED_HREF_MATCHED_COURSE",
        "detail_url_match_score": best.get("score", 0),
        "detail_url_match_reason_codes": best.get("reason_codes", []),
        "observed_href_count": len(hrefs),
    }


def _p32_extract_letrot_today_index_from_html(html_text):
    """
    Extraction prudente depuis le HTML public LeTrot.
    Niveau B2.1 : index r?unions/courses visible uniquement.
    Pas de matching ZEturf.
    Pas de partants d?taill?s.
    """
    import re

    source_url = "https://www.letrot.com/courses/aujourd-hui"
    checked_at = _p32_letrot_now_utc_iso()

    text = _p32_html_to_text(html_text)
    one_line = _p32_norm_space(text)

    status_terms = _p32_status_regex_terms()
    status_pattern = "|".join(re.escape(term) for term in status_terms)

    meeting_re = re.compile(
        rf"(?P<meeting_time>\d{{1,2}}:\d{{2}})\s+"
        rf"R(?P<reunion_no>\d+)\s+"
        rf"(?P<hippodrome>[A-Z?-??-?0-9'?\-\s]+?)\s+"
        rf"(?P<meeting_status>{status_pattern})\s+"
        rf"(?P<declared_courses>\d+)\s*courses?",
        flags=re.IGNORECASE,
    )

    course_re = re.compile(
        rf"(?P<course_time>\d{{1,2}}:\d{{2}})\s+"
        rf"(?:(?P<status_before>{status_pattern})\s+)?"
        rf"C(?P<course_no>\d+)\s+C(?P=course_no)"
        rf"(?:\s+(?P<tail>.*?))?"
        rf"(?=\s+\d{{1,2}}:\d{{2}}\s+(?:(?:{status_pattern})\s+)?C\d+\s+C\d+|\s+\d{{1,2}}:\d{{2}}\s+R\d+|$)",
        flags=re.IGNORECASE,
    )

    meetings = []
    meeting_matches = list(meeting_re.finditer(one_line))

    for idx, m in enumerate(meeting_matches):
        start = m.end()
        end = meeting_matches[idx + 1].start() if idx + 1 < len(meeting_matches) else len(one_line)
        segment = one_line[start:end]

        meeting_status = _p32_norm_space(m.group("meeting_status")).lower()
        hippodrome = _p32_norm_space(m.group("hippodrome")).upper()

        courses = []
        seen_course_keys = set()

        for c in course_re.finditer(segment):
            course_no = str(c.group("course_no")).strip()
            course_time = _p32_norm_space(c.group("course_time"))

            status_before = _p32_norm_space(c.group("status_before")).lower()
            tail = _p32_norm_space(c.group("tail")).lower()

            window_start = max(0, c.start() - 120)
            window_end = min(len(segment), c.end() + 260)
            status_window = segment[window_start:window_end]

            course_status = _p32_extract_course_status_from_window(status_before)
            if course_status == "ND":
                course_status = _p32_extract_course_status_from_window(tail)
            if course_status == "ND":
                course_status = _p32_extract_course_status_from_window(status_window)

            key = (course_no, course_time)
            if key in seen_course_keys:
                continue
            seen_course_keys.add(key)

            detail_href = _p383_fix1_match_observed_href_to_course(segment, course_no)

            courses.append({
                "course_no": f"C{course_no}",
                "course_no_int": int(course_no),
                "scheduled_time": course_time,
                "course_status": course_status,
                "course_status_code": _p32_canonical_status_code(course_status),
                "official_source": "LETROT",
                "official_source_kind": "LETROT_TODAY_INDEX",
                "detail_url": detail_href.get("detail_url", "ND"),
                "detail_path": detail_href.get("detail_path", "ND"),
                "detail_label": detail_href.get("detail_label", "ND"),
                "detail_url_match_status": detail_href.get("detail_url_match_status", "ND"),
                "detail_url_match_score": detail_href.get("detail_url_match_score", 0),
                "detail_url_match_reason_codes": detail_href.get("detail_url_match_reason_codes", []),
                "observed_href_count": detail_href.get("observed_href_count", 0),
            })

        # Fallback B2.1.2 : si la regex horaire/statut ne r?cup?re pas toutes les courses,
        # on r?cup?re au moins les num?ros C1/C2/C3 visibles dans le segment.
        declared_count = int(m.group("declared_courses"))
        if len(courses) < declared_count:
            existing_course_ints = {int(c.get("course_no_int")) for c in courses if str(c.get("course_no_int", "")).isdigit()}
            fallback_re = re.compile(r"\bC(?P<course_no>\d{1,2})\b(?:\s+C(?P=course_no)\b)?", flags=re.IGNORECASE)

            for fc in fallback_re.finditer(segment):
                try:
                    fallback_no = int(fc.group("course_no"))
                except Exception:
                    continue

                if fallback_no < 1 or fallback_no > max(declared_count, 20):
                    continue

                if fallback_no in existing_course_ints:
                    continue

                before = segment[max(0, fc.start() - 80):fc.start()]
                time_matches = list(re.finditer(r"\b\d{1,2}:\d{2}\b", before))
                fallback_time = time_matches[-1].group(0) if time_matches else "ND"

                status_window = segment[max(0, fc.start() - 160):min(len(segment), fc.end() + 320)]
                fallback_status = _p32_extract_course_status_from_window(status_window)

                fallback_detail_href = _p383_fix1_match_observed_href_to_course(segment, fallback_no)

                courses.append({
                    "course_no": f"C{fallback_no}",
                    "course_no_int": fallback_no,
                    "scheduled_time": fallback_time,
                    "course_status": fallback_status,
                    "course_status_code": _p32_canonical_status_code(fallback_status),
                    "official_source": "LETROT",
                    "official_source_kind": "LETROT_TODAY_INDEX_FALLBACK",
                    "detail_url": fallback_detail_href.get("detail_url", "ND"),
                    "detail_path": fallback_detail_href.get("detail_path", "ND"),
                    "detail_label": fallback_detail_href.get("detail_label", "ND"),
                    "detail_url_match_status": fallback_detail_href.get("detail_url_match_status", "ND"),
                    "detail_url_match_score": fallback_detail_href.get("detail_url_match_score", 0),
                    "detail_url_match_reason_codes": fallback_detail_href.get("detail_url_match_reason_codes", []),
                    "observed_href_count": fallback_detail_href.get("observed_href_count", 0),
                })
                existing_course_ints.add(fallback_no)

            courses = sorted(courses, key=lambda x: int(x.get("course_no_int", 999)))

        # Guard B2.1.4 : une r?union ne peut pas produire plus de courses que d?clar?.
        # On conserve la premi?re occurrence de chaque Cx et on ignore les num?ros hors borne.
        normalized_courses = []
        seen_course_numbers = set()

        for crs in sorted(courses, key=lambda x: int(x.get("course_no_int", 999))):
            try:
                no = int(crs.get("course_no_int", 999))
            except Exception:
                continue

            if no < 1 or no > declared_count:
                continue

            if no in seen_course_numbers:
                continue

            seen_course_numbers.add(no)
            normalized_courses.append(crs)

        courses = normalized_courses

        meetings.append({
            "reunion": f"R{m.group('reunion_no')}",
            "reunion_no": int(m.group("reunion_no")),
            "hippodrome": hippodrome,
            "meeting_time": _p32_norm_space(m.group("meeting_time")),
            "meeting_status": meeting_status,
            "meeting_status_code": _p32_canonical_status_code(meeting_status),
            "declared_courses_count": int(m.group("declared_courses")),
            "parsed_courses_count": len(courses),
            "courses": courses,
            "official_source": "LETROT",
            "official_source_kind": "LETROT_TODAY_INDEX",
        })

    courses_count = sum(len(m.get("courses", [])) for m in meetings)
    declared_courses_total = sum(int(m.get("declared_courses_count", 0) or 0) for m in meetings)
    course_status_nd_count = sum(
        1
        for m in meetings
        for c in m.get("courses", [])
        if str(c.get("course_status_code", "ND")).strip().upper() == "ND"
    )

    overparsed_meetings_count = sum(
        1
        for m in meetings
        if int(m.get("parsed_courses_count", 0) or 0) > int(m.get("declared_courses_count", 0) or 0)
    )

    underparsed_meetings_count = sum(
        1
        for m in meetings
        if int(m.get("parsed_courses_count", 0) or 0) < int(m.get("declared_courses_count", 0) or 0)
    )

    course_status_code_counts = {}
    meeting_status_code_counts = {}

    for m in meetings:
        m_code = str(m.get("meeting_status_code", "ND") or "ND")
        meeting_status_code_counts[m_code] = meeting_status_code_counts.get(m_code, 0) + 1

        for c in m.get("courses", []):
            c_code = str(c.get("course_status_code", "ND") or "ND")
            course_status_code_counts[c_code] = course_status_code_counts.get(c_code, 0) + 1

    if not meetings:
        parse_status = "NO_MEETING_PARSED"
        parse_quality_oui_non = "non"
        parse_warning = "Aucune r?union LeTrot pars?e."
    elif courses_count == 0:
        parse_status = "MEETINGS_ONLY_NO_COURSES"
        parse_quality_oui_non = "non"
        parse_warning = "R?unions d?tect?es, mais aucune course pars?e."
    elif declared_courses_total and courses_count > declared_courses_total:
        parse_status = "OVERPARSE_COURSE_COUNT"
        parse_quality_oui_non = "non"
        parse_warning = f"Parsing excessif : {courses_count}/{declared_courses_total} courses d?tect?es."
    elif overparsed_meetings_count > 0:
        parse_status = "OVERPARSE_MEETING_COUNT"
        parse_quality_oui_non = "non"
        parse_warning = f"R?union(s) en overparse : {overparsed_meetings_count}."
    elif declared_courses_total and courses_count < declared_courses_total:
        parse_status = "PARTIAL_COURSE_PARSE"
        parse_quality_oui_non = "non"
        parse_warning = f"Parsing partiel : {courses_count}/{declared_courses_total} courses d?tect?es."
    elif underparsed_meetings_count > 0:
        parse_status = "UNDERPARSE_MEETING_COUNT"
        parse_quality_oui_non = "non"
        parse_warning = f"R?union(s) incompl?te(s) : {underparsed_meetings_count}."
    elif course_status_nd_count > 0:
        parse_status = "PARTIAL_STATUS_PARSE"
        parse_quality_oui_non = "non"
        parse_warning = f"Statuts course incomplets : {course_status_nd_count} statut(s) ND."
    else:
        parse_status = "OK"
        parse_quality_oui_non = "oui"
        parse_warning = "ND"

    return {
        "ok": bool(meetings),
        "phase": "letrot_today_index_preview",
        "preview_version": "letrot_today_index_preview_v0_9_31b2_1_5",
        "source_url": source_url,
        "official_source": "LETROT",
        "official_source_kind": "LETROT_TODAY_INDEX",
        "checked_at_utc": checked_at,
        "parse_status": parse_status,
        "parse_quality_oui_non": parse_quality_oui_non,
        "parse_warning": parse_warning,
        "meetings_count": len(meetings),
        "courses_count": courses_count,
        "declared_courses_total": declared_courses_total,
        "course_status_nd_count": course_status_nd_count,
        "overparsed_meetings_count": overparsed_meetings_count,
        "underparsed_meetings_count": underparsed_meetings_count,
        "status_codes_version": "letrot_status_codes_v0_9_31b2_1_5",
        "course_status_code_counts": course_status_code_counts,
        "meeting_status_code_counts": meeting_status_code_counts,
        "meetings": meetings,
        "raw_text_chars": len(text),
        "raw_text_sample": text[:1200],
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "Segment_ROI": "NO_BET",
        "doctrine": (
            "Index LeTrot lecture seule. "
            "Ne vaut pas matching ZEturf, ne vaut pas statut partant officiel d?taill?, "
            "ne vaut pas validation stricte."
        ),
    }


@app.get("/action/letrot-today-index-preview")
def action_letrot_today_index_preview():
    """
    GPI V0_9_31B2.1
    Preview lecture seule de l'index LeTrot du jour.

    Ne modifie pas GCS.
    Ne matche pas ZEturf.
    Ne r?cup?re pas encore les partants d?taill?s.
    Ne cr?e jamais de ticket strict.
    """
    fetch = _p32_fetch_letrot_today_html()

    if not fetch.get("ok"):
        return {
            "ok": False,
            "phase": "letrot_today_index_preview",
            "preview_version": "letrot_today_index_preview_v0_9_31b2_1_5",
            "source_url": fetch.get("source_url", "https://www.letrot.com/courses/aujourd-hui"),
            "official_source": "LETROT",
            "official_source_kind": "LETROT_TODAY_INDEX",
            "checked_at_utc": fetch.get("checked_at_utc", _p32_letrot_now_utc_iso()),
            "http_status": fetch.get("http_status", "ND"),
            "content_type": fetch.get("content_type", "ND"),
            "html_bytes": fetch.get("html_bytes", 0),
            "parse_status": "FETCH_FAILED",
            "parse_quality_oui_non": "non",
            "parse_warning": "Fetch LeTrot ?chou? ou indisponible.",
            "error": fetch.get("error", "ND"),
            "meetings_count": 0,
            "courses_count": 0,
            "declared_courses_total": 0,
            "course_status_nd_count": 0,
            "overparsed_meetings_count": 0,
            "underparsed_meetings_count": 0,
            "status_codes_version": "letrot_status_codes_v0_9_31b2_1_5",
            "course_status_code_counts": {},
            "meeting_status_code_counts": {},
            "meetings": [],
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
            "Segment_ROI": "NO_BET",
            "doctrine": "Fetch LeTrot ?chou? ou indisponible. Donn?e officielle absente => NO_BET strict.",
        }

    parsed = _p32_extract_letrot_today_index_from_html(fetch.get("html", ""))
    parsed["http_status"] = fetch.get("http_status", "ND")
    parsed["content_type"] = fetch.get("content_type", "ND")
    parsed["html_bytes"] = fetch.get("html_bytes", 0)

    return parsed
# <<< GPI V0_9_31B2_1 LETROT TODAY INDEX PREVIEW <<<





# >>> GPI V0_9_31B2_2 LETROT COURSE MATCH PREVIEW >>>
# V0_9_31B2_2_1_CONTEXT_CAPTURE_FIX
# V0_9_31B2_2_2_H5_CONTEXT_BOM_FIX
# V0_9_31B2_3_COURSE_LEVEL_AUDIT_PREVIEW
def _p33_norm_for_match(value):
    import re
    import unicodedata

    s = "" if value is None else str(value)
    s = _p32_norm_space(s)

    # R?paration mojibake fr?quente.
    try:
        repaired = s.encode("latin-1", errors="ignore").decode("utf-8", errors="ignore")
        if repaired:
            s = repaired
    except Exception:
        pass

    replacements = {
        "??": "?",
        "??": "?",
        "??": "?",
        "??": "?",
        "? ": "? ",
        "??": "?",
        "??": "?",
        "??": "?",
        "??": "?",
        "??": "?",
        "??": "?",
        "?": "",
        "?": "",
        "_": " ",
        "-": " ",
        "'": " ",
        "?": " ",
    }

    for old, new in replacements.items():
        s = s.replace(old, new)

    s = "".join(
        c
        for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )

    s = s.upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _p33_extract_hippodrome_from_course_uid(course_uid):
    """
    Extrait prudemment l'hippodrome depuis un course_uid de type :
    13_07_2026 - BEAUMONT DE LOMAGNE - Prix du Lions Club_ Pronostics & Cotes
    """
    import re

    uid = _p32_norm_space(course_uid)

    if not uid or uid == "ND":
        return {
            "hippodrome_raw": "ND",
            "hippodrome_norm": "ND",
            "date_raw": "ND",
            "extract_status": "NO_COURSE_UID",
        }

    date_raw = "ND"
    m_date = re.search(r"\b(\d{2})[_/-](\d{2})[_/-](\d{4})\b", uid)
    if m_date:
        date_raw = f"{m_date.group(3)}-{m_date.group(2)}-{m_date.group(1)}"

    parts = [p.strip() for p in uid.split(" - ") if p.strip()]

    if len(parts) >= 2:
        hippodrome_raw = parts[1]
    else:
        # Fallback faible : retirer date en t?te et texte apr?s second tiret si possible.
        tmp = re.sub(r"^\s*\d{2}[_/-]\d{2}[_/-]\d{4}\s*[-?]\s*", "", uid)
        hippodrome_raw = tmp.split(" - ")[0].strip() if tmp else "ND"

    hippodrome_norm = _p33_norm_for_match(hippodrome_raw)

    return {
        "hippodrome_raw": hippodrome_raw or "ND",
        "hippodrome_norm": hippodrome_norm or "ND",
        "date_raw": date_raw,
        "extract_status": "OK" if hippodrome_norm and hippodrome_norm != "ND" else "HIPPO_ND",
    }



def _p33_pick_first_non_empty(payload, keys):
    if not isinstance(payload, dict):
        return None, "ND"

    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        value_s = str(value).strip()
        if value_s and value_s.upper() != "ND":
            return value_s, key

    return None, "ND"


def _p33_extract_course_level_inputs(context_payload):
    """
    Audit des entr?es disponibles pour une jointure course-level.
    On ne d?duit pas artificiellement le num?ro de course depuis le nom du prix.
    """
    fields = {
        "course_no": [
            "course_no",
            "course_num",
            "course_number",
            "race_no",
            "race_number",
            "course_index",
            "num_course",
            "numero_course",
        ],
        "scheduled_time": [
            "scheduled_time",
            "start_time",
            "heure_course",
            "heure_depart",
            "race_time",
            "depart_time",
            "start_at",
        ],
        "race_name": [
            "race_name",
            "course_name",
            "prix",
            "prix_course",
            "nom_prix",
        ],
        "reunion_no": [
            "reunion_no",
            "meeting_no",
            "reunion",
            "meeting",
        ],
    }

    values = {}
    sources = {}
    available = []
    missing = []

    for logical_name, keys in fields.items():
        value, source = _p33_pick_first_non_empty(context_payload, keys)
        values[logical_name] = value
        sources[logical_name] = source

        if value is None:
            missing.append(logical_name)
        else:
            available.append(logical_name)

    return {
        "values": values,
        "sources": sources,
        "available_fields": available,
        "missing_fields": missing,
    }


def _p33_summarize_candidate_courses(selected_meeting):
    if not isinstance(selected_meeting, dict):
        return []

    out = []
    for course in selected_meeting.get("courses", []) or []:
        if not isinstance(course, dict):
            continue

        out.append({
            "reunion": selected_meeting.get("reunion", "ND"),
            "hippodrome": selected_meeting.get("hippodrome", "ND"),
            "course_no": course.get("course_no", "ND"),
            "course_no_int": course.get("course_no_int", "ND"),
            "scheduled_time": course.get("scheduled_time", "ND"),
            "course_status": course.get("course_status", "ND"),
            "course_status_code": course.get("course_status_code", "ND"),
            "official_source_kind": course.get("official_source_kind", "ND"),
            "detail_url": course.get("detail_url", "ND"),
            "detail_path": course.get("detail_path", "ND"),
            "detail_label": course.get("detail_label", "ND"),
            "detail_url_match_status": course.get("detail_url_match_status", "ND"),
            "detail_url_match_score": course.get("detail_url_match_score", 0),
            "detail_url_match_reason_codes": course.get("detail_url_match_reason_codes", []),
            "observed_href_count": course.get("observed_href_count", 0),
        })

    return out


def _p33_course_level_audit(context_payload, selected_meeting):
    inputs = _p33_extract_course_level_inputs(context_payload)
    candidate_courses = _p33_summarize_candidate_courses(selected_meeting)

    selected_course = None
    course_match_status = "COURSE_NOT_MATCHED"
    course_level_input_status = "COURSE_LEVEL_INPUTS_ABSENT"
    course_match_blocker = "COURSE_NUMBER_AND_TIME_ABSENT_IN_CONTEXT"
    reason_codes = []

    course_no = inputs["values"].get("course_no")
    scheduled_time = inputs["values"].get("scheduled_time")

    if not isinstance(selected_meeting, dict) or not selected_meeting:
        course_level_input_status = "NO_SELECTED_MEETING"
        course_match_blocker = "NO_SELECTED_MEETING"
        reason_codes.append("NO_SELECTED_MEETING")
    elif not candidate_courses:
        course_level_input_status = "NO_CANDIDATE_COURSES"
        course_match_blocker = "NO_CANDIDATE_COURSES_IN_SELECTED_MEETING"
        reason_codes.append("NO_CANDIDATE_COURSES_IN_SELECTED_MEETING")
    elif course_no:
        normalized_no = str(course_no).strip().upper()
        if not normalized_no.startswith("C"):
            normalized_no = f"C{normalized_no}"

        matches = [
            c for c in candidate_courses
            if str(c.get("course_no", "")).strip().upper() == normalized_no
        ]

        if len(matches) == 1:
            selected_course = matches[0]
            course_match_status = "MATCH_OK_COURSE_LEVEL_PREVIEW"
            course_level_input_status = "COURSE_LEVEL_INPUTS_PRESENT"
            course_match_blocker = "ND"
            reason_codes.append("COURSE_NO_MATCH_PREVIEW")
        elif len(matches) > 1:
            course_level_input_status = "COURSE_LEVEL_INPUTS_PRESENT"
            course_match_blocker = "COURSE_NO_AMBIGUOUS"
            reason_codes.append("COURSE_NO_AMBIGUOUS")
        else:
            course_level_input_status = "COURSE_LEVEL_INPUTS_PRESENT"
            course_match_blocker = "COURSE_NO_NOT_FOUND_IN_LETROT_MEETING"
            reason_codes.append("COURSE_NO_NOT_FOUND_IN_LETROT_MEETING")
    elif scheduled_time:
        matches = [
            c for c in candidate_courses
            if str(c.get("scheduled_time", "")).strip() == str(scheduled_time).strip()
        ]

        if len(matches) == 1:
            selected_course = matches[0]
            course_match_status = "MATCH_OK_COURSE_LEVEL_PREVIEW"
            course_level_input_status = "COURSE_LEVEL_INPUTS_PRESENT"
            course_match_blocker = "ND"
            reason_codes.append("SCHEDULED_TIME_MATCH_PREVIEW")
        elif len(matches) > 1:
            course_level_input_status = "COURSE_LEVEL_INPUTS_PRESENT"
            course_match_blocker = "SCHEDULED_TIME_AMBIGUOUS"
            reason_codes.append("SCHEDULED_TIME_AMBIGUOUS")
        else:
            course_level_input_status = "COURSE_LEVEL_INPUTS_PRESENT"
            course_match_blocker = "SCHEDULED_TIME_NOT_FOUND_IN_LETROT_MEETING"
            reason_codes.append("SCHEDULED_TIME_NOT_FOUND_IN_LETROT_MEETING")
    else:
        reason_codes.append("COURSE_NUMBER_AND_TIME_ABSENT_IN_CONTEXT")

    return {
        "course_level_audit_version": "course_level_audit_preview_v0_9_31b2_3",
        "course_level_input_status": course_level_input_status,
        "course_level_available_fields": inputs.get("available_fields", []),
        "course_level_missing_fields": inputs.get("missing_fields", []),
        "course_level_input_values": inputs.get("values", {}),
        "course_level_input_sources": inputs.get("sources", {}),
        "course_match_status": course_match_status,
        "course_match_blocker": course_match_blocker,
        "course_match_reason_codes": reason_codes,
        "candidate_courses_count": len(candidate_courses),
        "candidate_courses": candidate_courses,
        "selected_course": selected_course,
    }



def _p33_match_zeturf_context_to_letrot_index(context_payload, letrot_payload):
    """
    Preview de jointure ZEturf ? LeTrot.
    Lecture seule.
    Ne cr?e aucun statut officiel exploitable strict.
    """
    checked_at = _p32_letrot_now_utc_iso()

    course_uid = (
        context_payload.get("course_uid")
        or context_payload.get("latest_course_uid")
        or context_payload.get("course")
        or "ND"
    )

    z = _p33_extract_hippodrome_from_course_uid(course_uid)

    meetings = letrot_payload.get("meetings", []) if isinstance(letrot_payload, dict) else []

    candidates = []
    for m in meetings:
        h_raw = m.get("hippodrome", "ND")
        h_norm = _p33_norm_for_match(h_raw)

        score = 0.0
        reasons = []

        if z["hippodrome_norm"] != "ND" and h_norm == z["hippodrome_norm"]:
            score = 1.0
            reasons.append("HIPPO_EXACT")
        elif z["hippodrome_norm"] != "ND" and (
            z["hippodrome_norm"] in h_norm or h_norm in z["hippodrome_norm"]
        ):
            score = 0.82
            reasons.append("HIPPO_CONTAINS")
        else:
            score = 0.0

        if score > 0:
            candidates.append({
                "score": score,
                "reason_codes": reasons,
                "reunion": m.get("reunion", "ND"),
                "reunion_no": m.get("reunion_no", "ND"),
                "hippodrome": h_raw,
                "hippodrome_norm": h_norm,
                "meeting_time": m.get("meeting_time", "ND"),
                "meeting_status": m.get("meeting_status", "ND"),
                "meeting_status_code": m.get("meeting_status_code", "ND"),
                "declared_courses_count": m.get("declared_courses_count", 0),
                "parsed_courses_count": m.get("parsed_courses_count", 0),
                "courses": m.get("courses", []),
            })

    candidates = sorted(candidates, key=lambda x: float(x.get("score", 0)), reverse=True)

    if not isinstance(context_payload, dict) or not context_payload:
        match_status = "SOURCE_UNAVAILABLE"
        meeting_match_status = "NO_CONTEXT"
        course_match_status = "NO_CONTEXT"
        match_score = 0.0
        selected = None
        reason_codes = ["LATEST_H5_CONTEXT_UNAVAILABLE"]
    elif not isinstance(letrot_payload, dict) or not letrot_payload.get("ok"):
        match_status = "SOURCE_UNAVAILABLE"
        meeting_match_status = "NO_LETROT_INDEX"
        course_match_status = "NO_LETROT_INDEX"
        match_score = 0.0
        selected = None
        reason_codes = ["LETROT_INDEX_UNAVAILABLE"]
    elif z.get("extract_status") != "OK":
        match_status = "NO_MATCH"
        meeting_match_status = "HIPPO_EXTRACTION_FAILED"
        course_match_status = "COURSE_NOT_MATCHED"
        match_score = 0.0
        selected = None
        reason_codes = ["ZETURF_HIPPODROME_EXTRACTION_FAILED"]
    elif len(candidates) == 1 and candidates[0].get("score", 0) >= 0.80:
        match_status = "MATCH_OK_MEETING_ONLY"
        meeting_match_status = "MATCH_OK"
        course_match_status = "COURSE_NOT_MATCHED"
        match_score = candidates[0]["score"]
        selected = candidates[0]
        reason_codes = candidates[0].get("reason_codes", []) + ["COURSE_NUMBER_ABSENT_IN_CONTEXT"]
    elif len(candidates) > 1:
        match_status = "MATCH_AMBIGUOUS"
        meeting_match_status = "MATCH_AMBIGUOUS"
        course_match_status = "COURSE_NOT_MATCHED"
        match_score = candidates[0].get("score", 0) if candidates else 0.0
        selected = candidates[0]
        reason_codes = ["MULTIPLE_LETROT_MEETING_CANDIDATES"]
    else:
        match_status = "NO_MATCH"
        meeting_match_status = "NO_MATCH"
        course_match_status = "COURSE_NOT_MATCHED"
        match_score = 0.0
        selected = None
        reason_codes = ["NO_HIPPODROME_MATCH_IN_LETROT_INDEX"]

    course_level_audit = _p33_course_level_audit(
        context_payload if isinstance(context_payload, dict) else {},
        selected,
    )

    # Si un futur contexte fournit course_no ou scheduled_time, la route pourra signaler
    # un match course-level en preview. Mais cela n'ouvre toujours pas le strict.
    if (
        meeting_match_status == "MATCH_OK"
        and course_level_audit.get("course_match_status") == "MATCH_OK_COURSE_LEVEL_PREVIEW"
    ):
        course_match_status = "MATCH_OK_COURSE_LEVEL_PREVIEW"
        reason_codes = list(reason_codes) + course_level_audit.get("course_match_reason_codes", [])

    # Important : preview seulement. M?me un match course-level preview ne vaut pas
    # contr?le NP officiel d?taill?. Join strict non.
    official_join_ok_oui_non = "non"

    return {
        "ok": True,
        "phase": "letrot_course_match_preview",
        "preview_version": "letrot_course_match_preview_v0_9_31b2_3",
        "checked_at_utc": checked_at,
        "source_context_phase": context_payload.get("phase", "ND") if isinstance(context_payload, dict) else "ND",
        "source_letrot_phase": letrot_payload.get("phase", "ND") if isinstance(letrot_payload, dict) else "ND",
        "source_letrot_preview_version": letrot_payload.get("preview_version", "ND") if isinstance(letrot_payload, dict) else "ND",
        "course_uid": course_uid,
        "zeturf_hippodrome_raw": z.get("hippodrome_raw", "ND"),
        "zeturf_hippodrome_norm": z.get("hippodrome_norm", "ND"),
        "zeturf_date": z.get("date_raw", "ND"),
        "hippodrome_extract_status": z.get("extract_status", "ND"),
        "match_status": match_status,
        "meeting_match_status": meeting_match_status,
        "course_match_status": course_match_status,
        "match_score": match_score,
        "reason_codes": reason_codes,
        "candidate_count": len(candidates),
        "selected_meeting": selected,
        "course_level_audit_version": course_level_audit.get("course_level_audit_version"),
        "course_level_input_status": course_level_audit.get("course_level_input_status"),
        "course_level_available_fields": course_level_audit.get("course_level_available_fields"),
        "course_level_missing_fields": course_level_audit.get("course_level_missing_fields"),
        "course_level_input_values": course_level_audit.get("course_level_input_values"),
        "course_level_input_sources": course_level_audit.get("course_level_input_sources"),
        "course_match_blocker": course_level_audit.get("course_match_blocker"),
        "course_match_reason_codes": course_level_audit.get("course_match_reason_codes"),
        "candidate_courses_count": course_level_audit.get("candidate_courses_count"),
        "candidate_courses": course_level_audit.get("candidate_courses"),
        "selected_course": course_level_audit.get("selected_course"),
        "candidates": candidates[:10],
        "official_source": "LETROT",
        "official_source_kind": "LETROT_TODAY_INDEX_MATCH_PREVIEW",
        "official_join_ok_oui_non": official_join_ok_oui_non,
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "Segment_ROI": "NO_BET",
        "doctrine": (
            "Preview de matching ZEturf ? LeTrot. "
            "Jointure r?union possible, mais course pr?cise non valid?e ? ce stade. "
            "Ne vaut pas contr?le NP officiel d?taill?. Strict ferm?."
        ),
    }


@app.get("/action/letrot-course-match-preview")
def action_letrot_course_match_preview():
    """
    GPI V0_9_31B2.2.1
    Preview de matching latest_h5_auto_context ? LeTrot today index.

    Lecture seule.
    Pas d'?criture GCS.
    Pas de NP officiel d?taill?.
    Pas de ticket strict.
    """
    try:
        context_payload = action_latest_h5_auto_context()
    except Exception as exc:
        context_payload = {
            "ok": False,
            "phase": "latest_h5_auto_context",
            "error": f"{type(exc).__name__}:{exc}",
        }

    try:
        letrot_payload = action_letrot_today_index_preview()
    except Exception as exc:
        letrot_payload = {
            "ok": False,
            "phase": "letrot_today_index_preview",
            "error": f"{type(exc).__name__}:{exc}",
        }

    return _p33_match_zeturf_context_to_letrot_index(context_payload, letrot_payload)
# <<< GPI V0_9_31B2_2 LETROT COURSE MATCH PREVIEW <<<





# >>> GPI V0_9_31B2_4_1 ZETURF COURSE LEVEL SOURCE AUDIT >>>
# V0_9_31B2_4_2B_HTML_GCS_READ_PRIORITY
# V0_9_31B2_4_2C_CLOUD_CAPTURE_HTML_GCS

# V0_9_31B2_5_ZETURF_HTML_COURSE_LEVEL_EXTRACTOR
def _p35_clean_text(value):
    import re
    import html as _html

    raw = _html.unescape(str(value or ""))
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _p35_strip_accents(value):
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", str(value or ""))
        if unicodedata.category(c) != "Mn"
    )


def _p35_norm_for_match(value):
    import re

    raw = _p35_strip_accents(str(value or "").lower())
    raw = raw.replace("&", " et ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _p35_extract_title_from_html(html_text):
    import re

    m = re.search(r"<title[^>]*>(.*?)</title>", str(html_text or ""), flags=re.I | re.S)
    return _p35_clean_text(m.group(1)) if m else ""


def _p35_extract_course_uid_parts(course_uid):
    import re

    raw = str(course_uid or "").strip()
    out = {
        "date": "ND",
        "hippodrome": "ND",
        "race_name": "ND",
        "source": "ND",
    }

    m = re.match(
        r"^\s*(?P<date>\d{2}_\d{2}_\d{4})\s*-\s*(?P<hippo>.*?)\s*-\s*(?P<race>.*?)(?:_|\s+Pronostics|\s*$)",
        raw,
        flags=re.I,
    )

    if not m:
        return out

    out["date"] = m.group("date").strip()
    out["hippodrome"] = _p35_clean_text(m.group("hippo"))
    out["race_name"] = _p35_clean_text(m.group("race"))
    out["source"] = "COURSE_UID_FALLBACK"
    return out



# V0_9_31B2_5_3_ZETURF_SCOPED_SCHEDULED_TIME_EXTRACTOR
def _p353_extract_scoped_zeturf_scheduled_time(html_text, source_html_path="", reunion_no="ND", course_no="ND", race_name="ND"):
    import re

    html_text = str(html_text or "")
    source_html_path = str(source_html_path or "")
    search_space = html_text + "\n" + source_html_path

    out = {
        "extractor_version": "zeturf_scoped_scheduled_time_extractor_v0_9_31b2_5_3",
        "status": "ND",
        "scheduled_time": "ND",
        "reunion_no": str(reunion_no or "ND"),
        "course_no": str(course_no or "ND"),
        "source": "ND",
        "reason_codes": [],
        "candidate_times": [],
        "rejected_global_time": "ND",
    }

    r = _p35_to_int_or_none(reunion_no)
    c = _p35_to_int_or_none(course_no)

    if not html_text:
        out["reason_codes"].append("HTML_ABSENT")
        return out

    if r is None or c is None:
        out["reason_codes"].append("RC_ABSENT_FOR_SCOPED_TIME")
        return out

    time_regexes = [
        r'"heureDepart"\s*:\s*"(?P<h>\d{1,2})[:h](?P<m>\d{2})"',
        r'"heure"\s*:\s*"(?P<h>\d{1,2})[:h](?P<m>\d{2})"',
        r'"dateDepart"[^"]*"\s*:\s*"[^"]*T(?P<h>\d{1,2}):(?P<m>\d{2})',
        r"\b(?P<h>\d{1,2})h(?P<m>\d{2})\b",
        r"\b(?P<h>\d{1,2}):(?P<m>\d{2})\b",
    ]

    def norm_time(match):
        return f"{int(match.group('h')):02d}:{int(match.group('m')):02d}"

    def add_candidate(time_value, source, reason, distance="ND", window_sample=""):
        if not time_value:
            return
        out["candidate_times"].append({
            "time": time_value,
            "source": source,
            "reason": reason,
            "distance": distance,
            "sample": _p35_clean_text(window_sample)[:220],
        })

    # 1) Patterns forts : numReunion + numCourse dans le meme bloc que l'heure.
    strong_patterns = [
        rf'"numReunion"\s*:\s*"?{r}"?.{{0,500}}?"numCourse"\s*:\s*"?{c}"?.{{0,700}}?(?P<time>"heureDepart"\s*:\s*"\d{{1,2}}[:h]\d{{2}}"|"heure"\s*:\s*"\d{{1,2}}[:h]\d{{2}}"|"dateDepart"[^"]*"\s*:\s*"[^"]*T\d{{1,2}}:\d{{2}})',
        rf'"numCourse"\s*:\s*"?{c}"?.{{0,500}}?"numReunion"\s*:\s*"?{r}"?.{{0,700}}?(?P<time>"heureDepart"\s*:\s*"\d{{1,2}}[:h]\d{{2}}"|"heure"\s*:\s*"\d{{1,2}}[:h]\d{{2}}"|"dateDepart"[^"]*"\s*:\s*"[^"]*T\d{{1,2}}:\d{{2}})',
    ]

    for pat in strong_patterns:
        for m in re.finditer(pat, search_space, flags=re.I | re.S):
            chunk = m.group(0)
            for treg in time_regexes[:3]:
                tm = re.search(treg, chunk, flags=re.I | re.S)
                if tm:
                    add_candidate(norm_time(tm), "HTML_JSON_RC_STRONG_SCOPE", "RC_AND_TIME_IN_SAME_JSON_BLOCK", 0, chunk)
                    break

    # 2) Fenetres autour de R2C5 / R 2 C 5 / /R2C5.
    rc_patterns = [
        rf"R\s*{r}\s*C\s*{c}\b",
        rf"/R{r}C{c}(?:[-_/]|[A-Za-z0-9])",
        rf'"reunion"\s*:\s*"?R?{r}"?.{{0,120}}?"course"\s*:\s*"?C?{c}"?',
    ]

    for rc_pat in rc_patterns:
        for rc_match in re.finditer(rc_pat, search_space, flags=re.I | re.S):
            center = rc_match.start()
            start = max(0, center - 450)
            end = min(len(search_space), center + 450)
            window = search_space[start:end]

            for treg in time_regexes:
                for tm in re.finditer(treg, window, flags=re.I | re.S):
                    distance = abs((start + tm.start()) - center)
                    # Distance prudente : si trop loin, on ne valide pas l'heure.
                    if distance <= 280:
                        add_candidate(norm_time(tm), "HTML_RC_NEARBY_WINDOW", "TIME_NEAR_CURRENT_RC", distance, window)

    # 3) Ne plus accepter une heure globale non scopee.
    global_first_time = None
    for treg in time_regexes:
        tm = re.search(treg, search_space, flags=re.I | re.S)
        if tm:
            global_first_time = norm_time(tm)
            break

    if global_first_time:
        out["rejected_global_time"] = global_first_time

    # 4) Decision : seulement les candidats scopes sont acceptables.
    if out["candidate_times"]:
        # Choix prudent : candidat le plus proche de la zone RC.
        sorted_candidates = sorted(
            out["candidate_times"],
            key=lambda x: 999999 if x.get("distance") == "ND" else int(x.get("distance") or 999999)
        )
        chosen = sorted_candidates[0]
        out["scheduled_time"] = chosen.get("time", "ND")
        out["status"] = "OK_SCOPED"
        out["source"] = chosen.get("source", "ND")
        out["reason_codes"].append(chosen.get("reason", "SCOPED_TIME_FOUND"))
        return out

    if global_first_time:
        out["status"] = "REJECTED_UNSCOPED_GLOBAL_TIME"
        out["reason_codes"].append("ZETURF_TIME_GLOBAL_UNSCOPED_REJECTED")
        return out

    out["status"] = "ND"
    out["reason_codes"].append("ZETURF_SCOPED_TIME_NOT_FOUND")
    return out


def _p35_extract_zeturf_course_level_from_html(html_text, source_html_path="", course_uid=""):
    import re

    html_text = str(html_text or "")
    source_html_path = str(source_html_path or "")
    course_uid = str(course_uid or "")

    uid_parts = _p35_extract_course_uid_parts(course_uid)
    title = _p35_extract_title_from_html(html_text)

    result = {
        "extractor_version": "zeturf_html_course_level_extractor_v0_9_31b2_5",
        "source_html_path": source_html_path or "ND",
        "html_available_oui_non": "oui" if html_text else "non",
        "html_chars": len(html_text),
        "course_uid": course_uid or "ND",
        "html_title": title or "ND",
        "reunion_no": "ND",
        "course_no": "ND",
        "scheduled_time": "ND",
        "race_name": "ND",
        "hippodrome": "ND",
        "date": "ND",
        "confidence": "ND",
        "sources": [],
        "reason_codes": [],
    }

    if not html_text:
        result["reason_codes"].append("HTML_ABSENT")
        return result

    search_space = html_text + "\n" + source_html_path

    rc_patterns = [
        r"/R(?P<r>\d{1,2})C(?P<c>\d{1,2})(?:[-_/]|[A-Za-z0-9])",
        r"\bR\s*(?P<r>\d{1,2})\s*C\s*(?P<c>\d{1,2})\b",
        r'"numReunion"\s*:\s*(?P<r>\d{1,2}).{0,160}?"numCourse"\s*:\s*(?P<c>\d{1,2})',
    ]

    for pat in rc_patterns:
        m = re.search(pat, search_space, flags=re.I | re.S)
        if m:
            result["reunion_no"] = str(m.group("r"))
            result["course_no"] = str(m.group("c"))
            result["sources"].append("HTML_RC_PATTERN")
            break

    scoped_time = _p353_extract_scoped_zeturf_scheduled_time(
        html_text=html_text,
        source_html_path=source_html_path,
        reunion_no=result.get("reunion_no", "ND"),
        course_no=result.get("course_no", "ND"),
        race_name=result.get("race_name", "ND"),
    )

    result["scheduled_time_extract"] = scoped_time

    if scoped_time.get("status") == "OK_SCOPED" and scoped_time.get("scheduled_time") != "ND":
        result["scheduled_time"] = scoped_time.get("scheduled_time", "ND")
        result["sources"].append(scoped_time.get("source", "HTML_TIME_SCOPED"))
    else:
        result["scheduled_time"] = "ND"
        result["reason_codes"].extend(scoped_time.get("reason_codes", []))

    title_norm = _p35_norm_for_match(title)

    if title and "prix" in title_norm:
        cleaned = re.sub(r"\s*-\s*ZEturf.*$", "", title, flags=re.I)
        cleaned = re.sub(r"\s*\|\s*ZEturf.*$", "", cleaned, flags=re.I)
        result["race_name"] = _p35_clean_text(cleaned)
        result["sources"].append("HTML_TITLE_RACE_NAME")

    if uid_parts["race_name"] != "ND" and (result["race_name"] == "ND" or len(str(result["race_name"])) > 120):
        result["race_name"] = uid_parts["race_name"]
        result["sources"].append("COURSE_UID_RACE_NAME_FALLBACK")

    if uid_parts["hippodrome"] != "ND":
        result["hippodrome"] = uid_parts["hippodrome"]
        result["sources"].append("COURSE_UID_HIPPODROME_FALLBACK")

    if uid_parts["date"] != "ND":
        result["date"] = uid_parts["date"]
        result["sources"].append("COURSE_UID_DATE_FALLBACK")

    has_rc = result["reunion_no"] != "ND" and result["course_no"] != "ND"
    has_time = result["scheduled_time"] != "ND"
    has_name = result["race_name"] != "ND"

    if has_rc and has_time and has_name:
        result["confidence"] = "HIGH"
    elif (has_rc and has_name) or (has_time and has_name):
        result["confidence"] = "MEDIUM"
    elif has_name:
        result["confidence"] = "LOW_NAME_ONLY"
    else:
        result["confidence"] = "ND"
        result["reason_codes"].append("COURSE_LEVEL_METADATA_NOT_FOUND")

    return result


def _p35_time_to_minutes(value):
    import re

    m = re.search(r"(?P<h>\d{1,2})[:h](?P<m>\d{2})", str(value or ""))
    if not m:
        return None

    return int(m.group("h")) * 60 + int(m.group("m"))


def _p35_to_int_or_none(value):
    import re

    try:
        raw = str(value or "").strip().upper()
        if raw in ("", "ND", "NONE", "NULL"):
            return None

        # Cas directs : 5, 5.0
        try:
            return int(float(raw))
        except Exception:
            pass

        # Cas LeTrot / ZEturf : C5, R2, course C5, etc.
        m = re.search(r"(?<!\d)(?:C|R)?\s*(\d{1,2})(?!\d)", raw)
        if m:
            return int(m.group(1))

        return None
    except Exception:
        return None


def _p35_candidate_field(candidate, keys):
    if not isinstance(candidate, dict):
        return ""

    for key in keys:
        value = candidate.get(key)
        if value not in (None, "", "ND"):
            return value

    return ""


def _p35_name_similarity(a, b):
    a_norm = _p35_norm_for_match(a)
    b_norm = _p35_norm_for_match(b)

    if not a_norm or not b_norm:
        return 0.0

    if a_norm in b_norm or b_norm in a_norm:
        return 1.0

    a_tokens = set(a_norm.split())
    b_tokens = set(b_norm.split())

    if not a_tokens or not b_tokens:
        return 0.0

    return len(a_tokens & b_tokens) / max(1, len(a_tokens | b_tokens))


def _p35_compare_zeturf_with_letrot_candidates(zeturf_meta, candidate_courses):
    if not isinstance(candidate_courses, list):
        candidate_courses = []

    z_course_no = _p35_to_int_or_none(zeturf_meta.get("course_no"))
    z_time = _p35_time_to_minutes(zeturf_meta.get("scheduled_time"))
    z_name = zeturf_meta.get("race_name", "ND")

    scored = []

    for candidate in candidate_courses:
        if not isinstance(candidate, dict):
            continue

        c_course_no = _p35_to_int_or_none(
            _p35_candidate_field(candidate, ["course_no_int", "course_no", "numero", "num", "num_course", "course_number", "course_index"])
        )
        c_time_raw = _p35_candidate_field(candidate, ["scheduled_time", "heure", "start_time", "depart", "heure_depart", "course_time"])
        c_time = _p35_time_to_minutes(c_time_raw)
        c_name = _p35_candidate_field(candidate, ["race_name", "nom", "prix", "title", "course_name", "libelle"])

        score = 0
        reasons = []

        if z_course_no is not None and c_course_no is not None and z_course_no == c_course_no:
            score += 4
            reasons.append("COURSE_NO_MATCH")

        time_delta_minutes = "ND"

        if z_time is not None and c_time is not None:
            delta = abs(z_time - c_time)
            time_delta_minutes = delta
            if delta <= 3:
                score += 3
                reasons.append("TIME_MATCH_3MIN")
            elif delta <= 5:
                score += 2
                reasons.append("TIME_MATCH_5MIN")
            elif delta <= 10:
                score += 1
                reasons.append("TIME_NEAR_10MIN")
            else:
                reasons.append("TIME_MISMATCH_GT_10MIN")

        sim = _p35_name_similarity(z_name, c_name)

        if sim >= 0.80:
            score += 3
            reasons.append("RACE_NAME_MATCH_STRONG")
        elif sim >= 0.55:
            score += 2
            reasons.append("RACE_NAME_MATCH_MEDIUM")
        elif sim >= 0.35:
            score += 1
            reasons.append("RACE_NAME_MATCH_WEAK")

        scored.append({
            "score": score,
            "reason_codes": reasons,
            "candidate": candidate,
            "candidate_course_no": c_course_no if c_course_no is not None else "ND",
            "candidate_scheduled_time": c_time_raw or "ND",
            "time_delta_minutes": time_delta_minutes,
            "candidate_race_name": c_name or "ND",
            "name_similarity": round(sim, 3),
        })

    scored = sorted(scored, key=lambda item: item.get("score", 0), reverse=True)

    best = scored[0] if scored else None
    second = scored[1] if len(scored) > 1 else None

    ambiguous = bool(
        best
        and second
        and best.get("score", 0) > 0
        and (best.get("score", 0) - second.get("score", 0)) <= 1
    )

    robust = bool(best and best.get("score", 0) >= 6 and not ambiguous)
    partial = bool(best and best.get("score", 0) >= 3 and not robust)

    return {
        "compare_version": "zeturf_letrot_course_compare_v0_9_31b2_5",
        "candidate_courses_count": len(candidate_courses),
        "best_score": best.get("score", 0) if best else 0,
        "best_reason_codes": best.get("reason_codes", []) if best else [],
        "best_candidate": best.get("candidate", {}) if best else {},
        "second_score": second.get("score", 0) if second else "ND",
        "ambiguous_oui_non": "oui" if ambiguous else "non",
        "match_status": "ROBUST_MATCH" if robust else "PARTIAL_MATCH" if partial else "NO_ROBUST_MATCH",
        "official_join_ok_oui_non": "oui" if robust else "non",
        "scored_candidates_sample": scored[:5],
    }



# V0_9_31B2_5_2_ZETURF_SCHEDULED_TIME_GUARD
def _p352_build_zeturf_scheduled_time_guard(zeturf_meta, compare_payload):
    if not isinstance(zeturf_meta, dict):
        zeturf_meta = {}

    if not isinstance(compare_payload, dict):
        compare_payload = {}

    scored = compare_payload.get("scored_candidates_sample", [])
    if not isinstance(scored, list):
        scored = []

    best_score = compare_payload.get("best_score", 0)

    best_scored = {}
    for item in scored:
        if not isinstance(item, dict):
            continue
        if item.get("score") == best_score:
            best_scored = item
            break

    if not best_scored and scored:
        best_scored = scored[0] if isinstance(scored[0], dict) else {}

    best_candidate = compare_payload.get("best_candidate", {})
    if not isinstance(best_candidate, dict):
        best_candidate = {}

    z_time = zeturf_meta.get("scheduled_time", "ND")
    c_time = (
        best_scored.get("candidate_scheduled_time")
        or best_candidate.get("scheduled_time")
        or "ND"
    )
    delta = best_scored.get("time_delta_minutes", "ND")

    reason_codes = []
    status = "ND"

    try:
        delta_numeric = int(delta)
    except Exception:
        delta_numeric = None

    if z_time in ("", None, "ND"):
        status = "ND"
        reason_codes.append("ZETURF_TIME_ND")
    elif c_time in ("", None, "ND"):
        status = "ND"
        reason_codes.append("LETROT_TIME_ND")
    elif delta_numeric is None:
        status = "ND"
        reason_codes.append("TIME_DELTA_ND")
    elif delta_numeric <= 10:
        status = "OK"
        reason_codes.append("ZETURF_TIME_MATCH_LETROT_LE_10MIN")
    else:
        status = "SUSPECT"
        reason_codes.append("ZETURF_TIME_MISMATCH_LETROT_GT_10MIN")

    compare_reasons = compare_payload.get("best_reason_codes", [])
    if isinstance(compare_reasons, list) and "TIME_MISMATCH_GT_10MIN" in compare_reasons:
        if "ZETURF_TIME_MISMATCH_LETROT_GT_10MIN" not in reason_codes:
            reason_codes.append("ZETURF_TIME_MISMATCH_LETROT_GT_10MIN")
        status = "SUSPECT"

    official_join_allowed_by_time_guard = "oui" if status == "OK" else "non"

    return {
        "guard_version": "zeturf_scheduled_time_guard_v0_9_31b2_5_2",
        "status": status,
        "zeturf_scheduled_time": z_time or "ND",
        "letrot_best_scheduled_time": c_time or "ND",
        "time_delta_vs_letrot_minutes": delta if delta not in ("", None) else "ND",
        "official_join_allowed_by_time_guard_oui_non": official_join_allowed_by_time_guard,
        "reason_codes": reason_codes,
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
    }


def _p34_bucket_name():
    import os

    return (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or globals().get("GPI_BUCKET_NAME")
        or "gpi-lite-watchlist-data"
    )


def _p34_norm_slashes(value):
    return str(value or "").replace("\\", "/").strip()


def _p34_read_text_local_or_gcs(path_or_object, bucket_name=None):
    """
    Lecture d?fensive :
    1. chemin local ;
    2. gs://bucket/object ;
    3. objet GCS direct.
    """
    from pathlib import Path

    value = _p34_norm_slashes(path_or_object)
    bucket_name = bucket_name or _p34_bucket_name()

    if not value:
        return {
            "ok": False,
            "source": "none",
            "path_or_object": value,
            "error": "EMPTY_PATH_OR_OBJECT",
            "text": "",
        }

    # 1. Local exact.
    try:
        p = Path(value)
        if p.exists():
            return {
                "ok": True,
                "source": "local",
                "path_or_object": value,
                "local_path": str(p),
                "error": "ND",
                "text": p.read_text(encoding="utf-8-sig"),
            }
    except Exception as exc:
        local_error = f"{type(exc).__name__}:{exc}"
    else:
        local_error = "LOCAL_NOT_FOUND"

    # 2. GCS.
    try:
        from google.cloud import storage

        if value.startswith("gs://"):
            raw = value[5:]
            parts = raw.split("/", 1)
            if len(parts) != 2:
                raise ValueError("INVALID_GS_URI")
            bucket_name = parts[0]
            object_name = parts[1]
        else:
            object_name = value.lstrip("/")

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_name)

        if not blob.exists():
            # Fallback si un chemin local data/... est stock? en GCS sous gpi-lite/...
            fallback_objects = []
            if object_name.startswith("data/"):
                fallback_objects.append("gpi-lite/" + object_name)
                fallback_objects.append("gpi-lite/outputs/" + object_name)

            for fallback in fallback_objects:
                fb = bucket.blob(fallback)
                if fb.exists():
                    blob = fb
                    object_name = fallback
                    break
            else:
                return {
                    "ok": False,
                    "source": "gcs",
                    "bucket": bucket_name,
                    "object": object_name,
                    "path_or_object": value,
                    "error": f"GCS_OBJECT_NOT_FOUND;local_error={local_error}",
                    "text": "",
                }

        return {
            "ok": True,
            "source": "gcs",
            "bucket": bucket_name,
            "object": object_name,
            "path_or_object": value,
            "error": "ND",
            "text": blob.download_as_text(encoding="utf-8-sig"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "source": "gcs",
            "bucket": bucket_name,
            "path_or_object": value,
            "error": f"{type(exc).__name__}:{exc};local_error={local_error}",
            "text": "",
        }


def _p34_read_csv_rows_from_text(text):
    import csv
    import io

    if not text:
        return [], []

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return reader.fieldnames or [], rows


def _p34_extract_course_level_columns(fieldnames):
    fieldnames = fieldnames or []
    fieldset = {str(x).strip() for x in fieldnames if str(x).strip()}

    expected_groups = {
        "course_no": [
            "course_no",
            "course_num",
            "course_number",
            "race_no",
            "race_number",
            "course_index",
            "num_course",
            "numero_course",
        ],
        "scheduled_time": [
            "scheduled_time",
            "start_time",
            "heure_course",
            "heure_depart",
            "race_time",
            "depart_time",
            "start_at",
        ],
        "race_name": [
            "race_name",
            "course_name",
            "prix",
            "prix_course",
            "nom_prix",
        ],
        "reunion_no": [
            "reunion_no",
            "meeting_no",
            "reunion",
            "meeting",
        ],
    }

    present = {}
    missing = []

    for logical, candidates in expected_groups.items():
        found = [c for c in candidates if c in fieldset]
        present[logical] = found
        if not found:
            missing.append(logical)

    return {
        "present": present,
        "missing": missing,
        "available_logical_fields": [k for k, v in present.items() if v],
    }


def _p34_extract_course_level_source_from_context(context_payload):
    """
    Audit source course-level c?t? ZEturf/H5.
    Ne valide aucune course.
    """
    bucket_name = _p34_bucket_name()

    if not isinstance(context_payload, dict):
        context_payload = {}

    h5_csv_gcs = context_payload.get("h5_odds_csv_gcs") or "ND"
    h5_csv_local = context_payload.get("np_statut_exact_h5_csv_local") or "ND"

    csv_read = None

    # Priorit? GCS car production Cloud Run ne poss?de pas toujours les fichiers locaux.
    if h5_csv_gcs and h5_csv_gcs != "ND":
        csv_read = _p34_read_text_local_or_gcs(h5_csv_gcs, bucket_name=bucket_name)

    if not csv_read or not csv_read.get("ok"):
        if h5_csv_local and h5_csv_local != "ND":
            csv_read = _p34_read_text_local_or_gcs(h5_csv_local, bucket_name=bucket_name)

    if not csv_read:
        csv_read = {
            "ok": False,
            "source": "none",
            "error": "NO_H5_CSV_REFERENCE",
            "text": "",
        }

    fieldnames, rows = _p34_read_csv_rows_from_text(csv_read.get("text", ""))
    column_audit = _p34_extract_course_level_columns(fieldnames)

    first_row = rows[0] if rows else {}
    source_html_path = (
        first_row.get("html_gcs_path")
        or first_row.get("html_gcs_uri")
        or first_row.get("source_html_path")
        or context_payload.get("html_gcs_path")
        or context_payload.get("source_html_path")
        or "ND"
    )

    html_read = {
        "ok": False,
        "source": "none",
        "error": "NO_SOURCE_HTML_PATH",
        "text": "",
    }

    if source_html_path and source_html_path != "ND":
        html_read = _p34_read_text_local_or_gcs(source_html_path, bucket_name=bucket_name)

    source_status = "SOURCE_AUDIT_OK"

    if not csv_read.get("ok"):
        source_status = "H5_CSV_UNAVAILABLE"
    elif not rows:
        source_status = "H5_CSV_EMPTY"
    elif column_audit.get("available_logical_fields"):
        source_status = "COURSE_LEVEL_COLUMNS_PRESENT_IN_H5_CSV"
    elif html_read.get("ok"):
        source_status = "H5_CSV_NO_COURSE_LEVEL_COLUMNS_BUT_HTML_AVAILABLE"
    else:
        source_status = "H5_CSV_NO_COURSE_LEVEL_COLUMNS_AND_HTML_UNAVAILABLE"

    html_text = html_read.get("text", "") or ""

    # V0_9_31B2_5_ZETURF_HTML_COURSE_LEVEL_EXTRACTOR
    zeturf_html_course_level = _p35_extract_zeturf_course_level_from_html(
        html_text=html_text,
        source_html_path=source_html_path,
        course_uid=str(context_payload.get("course_uid", "ND")) if isinstance(context_payload, dict) else "ND",
    )

    candidate_courses = []
    letrot_course_level_probe = {
        "ok": False,
        "source": "ND",
        "error": "ND",
        "candidate_courses_count": 0,
        "candidate_courses": [],
    }

    try:
        if "action_letrot_course_match_preview" in globals():
            letrot_payload = action_letrot_course_match_preview()

            candidate_courses = (
                letrot_payload.get("candidate_courses")
                or letrot_payload.get("course_level_candidates")
                or letrot_payload.get("candidate_courses_sample")
                or letrot_payload.get("candidates")
                or []
            )

            if not candidate_courses and isinstance(letrot_payload.get("course_level_audit"), dict):
                candidate_courses = letrot_payload["course_level_audit"].get("candidate_courses", []) or []

            letrot_course_level_probe = {
                "ok": True,
                "source": "action_letrot_course_match_preview",
                "match_status": letrot_payload.get("course_match_status", "ND"),
                "selected_meeting": letrot_payload.get("selected_meeting", "ND"),
                "candidate_courses_count": len(candidate_courses) if isinstance(candidate_courses, list) else 0,
                "candidate_courses": candidate_courses,
            }
        else:
            letrot_course_level_probe["error"] = "LETROT_MATCH_PREVIEW_ROUTE_NOT_AVAILABLE"
    except Exception as exc:
        candidate_courses = []
        letrot_course_level_probe = {
            "ok": False,
            "source": "action_letrot_course_match_preview",
            "error": f"{type(exc).__name__}:{exc}",
            "candidate_courses_count": 0,
            "candidate_courses": [],
        }

    zeturf_letrot_course_compare = _p35_compare_zeturf_with_letrot_candidates(
        zeturf_html_course_level,
        candidate_courses,
    )

    zeturf_scheduled_time_guard = _p352_build_zeturf_scheduled_time_guard(
        zeturf_html_course_level,
        zeturf_letrot_course_compare,
    )

    course_level_official_join_raw_oui_non = zeturf_letrot_course_compare.get(
        "official_join_ok_oui_non",
        "non",
    )

    course_level_official_join_ok_oui_non = (
        "oui"
        if course_level_official_join_raw_oui_non == "oui"
        and zeturf_scheduled_time_guard.get("official_join_allowed_by_time_guard_oui_non") == "oui"
        else "non"
    )

    if (
        course_level_official_join_raw_oui_non == "oui"
        and course_level_official_join_ok_oui_non != "oui"
    ):
        zeturf_scheduled_time_guard.setdefault("reason_codes", []).append(
            "OFFICIAL_JOIN_BLOCKED_BY_ZETURF_TIME_GUARD"
        )


    return {
        "course_level_source_audit_version": "zeturf_course_level_source_audit_v0_9_31b2_4_1",
        "bucket": bucket_name,
        "course_uid": context_payload.get("course_uid", "ND"),
        "course_key": context_payload.get("course_key", "ND"),
        "course_slug": context_payload.get("course_slug", "ND"),
        "h5_csv_gcs": h5_csv_gcs,
        "h5_csv_local": h5_csv_local,
        "h5_csv_read_ok_oui_non": "oui" if csv_read.get("ok") else "non",
        "h5_csv_read_source": csv_read.get("source", "ND"),
        "h5_csv_read_error": csv_read.get("error", "ND"),
        "h5_csv_fieldnames": fieldnames,
        "h5_csv_rows_count": len(rows),
        "course_level_columns_present": column_audit.get("present", {}),
        "course_level_columns_missing": column_audit.get("missing", []),
        "course_level_available_logical_fields": column_audit.get("available_logical_fields", []),
        "source_html_path": source_html_path,
        "source_html_available_oui_non": "oui" if html_read.get("ok") else "non",
        "source_html_read_source": html_read.get("source", "ND"),
        "source_html_read_error": html_read.get("error", "ND"),
        "source_html_chars": len(html_text),
        "source_html_sample": html_text[:1200],
        "zeturf_html_course_level": zeturf_html_course_level,
        "letrot_course_level_probe": {k: v for k, v in letrot_course_level_probe.items() if k != "candidate_courses"},
        "zeturf_letrot_course_compare": zeturf_letrot_course_compare,
        "zeturf_scheduled_time_guard": zeturf_scheduled_time_guard,
        "course_level_official_join_raw_oui_non": course_level_official_join_raw_oui_non,
        "course_level_official_join_ok_oui_non": course_level_official_join_ok_oui_non,
        "course_level_source_status": source_status,
    }



# V0_9_31B2_6_COURSE_LEVEL_CONTEXT_SYNC

# V0_9_31B2_6_1_COURSE_SCOPED_CONTEXT_FALLBACK
def _p361_read_course_scoped_h5_context_for_overlay(payload, bucket_name=None):
    """
    Lit le contexte H5 course-specific si latest_h5_auto_context ne contient pas
    encore les champs course-level propages.

    Ne peut jamais ouvrir le strict.
    """
    import json
    import os
    from pathlib import Path

    if not isinstance(payload, dict):
        return {
            "ok": False,
            "reason": "PAYLOAD_NOT_DICT",
            "merged_payload": payload,
        }

    course_uid = str(payload.get("course_uid") or payload.get("course_key") or "ND")
    if not course_uid or course_uid == "ND":
        return {
            "ok": False,
            "reason": "COURSE_UID_ABSENT",
            "merged_payload": payload,
        }

    try:
        slug = _p36_safe_slug(course_uid)
    except Exception:
        slug = str(payload.get("course_slug") or "").strip()

    if not slug:
        return {
            "ok": False,
            "reason": "COURSE_SLUG_ABSENT",
            "merged_payload": payload,
        }

    blob_name = f"gpi-lite/outputs/h5_contexts/{slug}_h5_auto_context.json"

    bucket_name = (
        bucket_name
        or os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or "gpi-lite-watchlist-data"
    )

    candidate_payload = None
    read_source = "ND"
    read_error = "ND"

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if blob.exists():
            raw = blob.download_as_text(encoding="utf-8")
            candidate_payload = json.loads(raw)
            read_source = "gcs_course_scoped_context"
        else:
            read_error = "GCS_COURSE_SCOPED_CONTEXT_ABSENT"
    except Exception as exc:
        read_error = f"GCS_READ_ERROR:{type(exc).__name__}:{exc}"

    if not isinstance(candidate_payload, dict):
        local_path = Path("data/output/h5_contexts") / f"{slug}_h5_auto_context.json"
        if local_path.exists():
            try:
                candidate_payload = json.loads(local_path.read_text(encoding="utf-8-sig"))
                read_source = "local_course_scoped_context"
                read_error = "ND"
            except Exception as exc:
                read_error = f"LOCAL_READ_ERROR:{type(exc).__name__}:{exc}"

    if not isinstance(candidate_payload, dict):
        payload["course_level_context_fallback_overlay_version"] = "course_scoped_context_fallback_overlay_v0_9_31b2_6_1"
        payload["course_level_context_fallback_overlay_applied_oui_non"] = "non"
        payload["course_level_context_fallback_overlay_source"] = "none"
        payload["course_level_context_fallback_overlay_error"] = read_error
        payload["course_level_context_fallback_overlay_blob"] = blob_name
        payload["strict_allowed_oui_non"] = "non"
        payload["strict_usable_oui_non"] = "non"
        payload["ticket_strict"] = "NO_BET"
        return {
            "ok": False,
            "reason": read_error,
            "bucket": bucket_name,
            "blob": blob_name,
            "merged_payload": payload,
        }

    merged = dict(payload)

    safe_keys = [
        "reunion_no",
        "course_no",
        "scheduled_time",
        "race_name",
        "course_level_context_sync_version",
        "course_level_context_sync_status",
        "course_level_context_sync_checked_at_utc",
        "course_level_context_sync_missing_fields",
        "course_level_context_sync_source",
        "course_level_validated_fields",
        "course_level_official_join_ok_oui_non",
        "course_level_match_status",
        "course_level_match_best_score",
        "course_level_match_reason_codes",
        "course_level_time_guard_status",
        "course_level_time_guard_reason_codes",
    ]

    merged_keys = []

    for key in safe_keys:
        value = candidate_payload.get(key)
        if value not in (None, "", "ND"):
            if merged.get(key) in (None, "", "ND") or key.startswith("course_level_"):
                merged[key] = value
                merged_keys.append(key)

    fields = merged.get("course_level_validated_fields", {})
    if isinstance(fields, dict):
        for key in ["reunion_no", "course_no", "scheduled_time", "race_name"]:
            value = fields.get(key)
            if value not in (None, "", "ND") and merged.get(key) in (None, "", "ND"):
                merged[key] = value
                if key not in merged_keys:
                    merged_keys.append(key)

    merged["course_level_context_fallback_overlay_version"] = "course_scoped_context_fallback_overlay_v0_9_31b2_6_1"
    merged["course_level_context_fallback_overlay_applied_oui_non"] = "oui" if merged_keys else "non"
    merged["course_level_context_fallback_overlay_source"] = read_source
    merged["course_level_context_fallback_overlay_blob"] = blob_name
    merged["course_level_context_fallback_overlay_merged_keys"] = merged_keys
    merged["course_level_context_fallback_overlay_error"] = "ND"

    merged["strict_allowed_oui_non"] = "non"
    merged["strict_usable_oui_non"] = "non"
    merged["ticket_strict"] = "NO_BET"

    return {
        "ok": True,
        "reason": "COURSE_SCOPED_CONTEXT_MERGED",
        "bucket": bucket_name,
        "blob": blob_name,
        "source": read_source,
        "merged_keys": merged_keys,
        "merged_payload": merged,
    }


def _p361_apply_course_level_context_overlay(payload):
    """
    Recopie les metadonnees course-level validees dans les champs top-level
    attendus par les previews LeTrot.

    Ne peut jamais ouvrir le strict.
    """
    if not isinstance(payload, dict):
        return payload

    fields = payload.get("course_level_validated_fields", {})
    if not isinstance(fields, dict):
        fields = {}

    aliases = {
        "reunion_no": [
            "reunion_no",
            "reunion_num",
            "num_reunion",
            "course_level_reunion_no",
            "latest_reunion_no",
        ],
        "course_no": [
            "course_no",
            "course_num",
            "num_course",
            "course_level_course_no",
            "latest_course_no",
        ],
        "scheduled_time": [
            "scheduled_time",
            "course_time",
            "start_time",
            "heure_depart",
            "course_level_scheduled_time",
            "latest_scheduled_time",
        ],
        "race_name": [
            "race_name",
            "course_name",
            "prix",
            "course_level_race_name",
            "latest_race_name",
        ],
    }

    overlay_applied = []
    overlay_missing = []

    for canonical, keys in aliases.items():
        value = fields.get(canonical, "ND")

        if value in (None, "", "ND"):
            overlay_missing.append(canonical)
            continue

        if payload.get(canonical) in (None, "", "ND"):
            payload[canonical] = value
            overlay_applied.append(canonical)

        for key in keys:
            if payload.get(key) in (None, "", "ND"):
                payload[key] = value

    payload["course_level_context_overlay_version"] = "course_level_context_overlay_v0_9_31b2_6_1"
    payload["course_level_context_overlay_applied_fields"] = overlay_applied
    payload["course_level_context_overlay_missing_fields"] = overlay_missing
    payload["course_level_context_overlay_applied_oui_non"] = "oui" if overlay_applied else "non"

    payload["strict_allowed_oui_non"] = "non"
    payload["strict_usable_oui_non"] = "non"
    payload["ticket_strict"] = "NO_BET"

    return payload


def _p36_safe_slug(value):
    import re
    import unicodedata

    raw = str(value or "ND")
    raw = "".join(
        c for c in unicodedata.normalize("NFD", raw)
        if unicodedata.category(c) != "Mn"
    )
    raw = raw.lower()
    raw = raw.replace("&", " et ")
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw[:180] or "nd"


def _p36_write_json_local(path_obj, payload):
    import json
    from pathlib import Path

    path_obj = Path(path_obj)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    path_obj.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path_obj)


def _p36_upload_json_to_gcs(payload, blob_name, bucket_name=None):
    import json
    import os

    bucket_name = (
        bucket_name
        or os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or "gpi-lite-watchlist-data"
    )

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(
            json.dumps(payload, ensure_ascii=False, indent=2),
            content_type="application/json",
        )

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


@app.post("/action/zeturf-course-level-context-sync", dependencies=[Depends(require_api_key)])
def action_zeturf_course_level_context_sync():
    """
    GPI V0_9_31B2.6
    Propagation technique des metadonnees course-level validees dans latest_h5_auto_context.

    Ne cree aucun ticket.
    Ne rend pas le strict jouable.
    official_join_ok reste une validation course-level uniquement.
    """
    from datetime import datetime, timezone
    import copy
    from pathlib import Path

    checked_at_utc = datetime.now(timezone.utc).isoformat()

    try:
        context_payload = action_latest_h5_auto_context()
    except Exception as exc:
        context_payload = {
            "ok": False,
            "phase": "latest_h5_auto_context",
            "error": f"{type(exc).__name__}:{exc}",
        }

    if not isinstance(context_payload, dict) or not context_payload.get("ok"):
        return {
            "ok": False,
            "phase": "zeturf_course_level_context_sync",
            "sync_version": "zeturf_course_level_context_sync_v0_9_31b2_6",
            "reason": "LATEST_H5_AUTO_CONTEXT_UNAVAILABLE",
            "context_error": context_payload.get("error", "ND") if isinstance(context_payload, dict) else "CONTEXT_NOT_DICT",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    try:
        source_preview = action_zeturf_course_level_source_preview()
    except Exception as exc:
        return {
            "ok": False,
            "phase": "zeturf_course_level_context_sync",
            "sync_version": "zeturf_course_level_context_sync_v0_9_31b2_6",
            "reason": "SOURCE_PREVIEW_FAILED",
            "error": f"{type(exc).__name__}:{exc}",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    if not isinstance(source_preview, dict) or not source_preview.get("ok"):
        return {
            "ok": False,
            "phase": "zeturf_course_level_context_sync",
            "sync_version": "zeturf_course_level_context_sync_v0_9_31b2_6",
            "reason": "SOURCE_PREVIEW_UNAVAILABLE",
            "source_preview_error": source_preview.get("error", "ND") if isinstance(source_preview, dict) else "SOURCE_PREVIEW_NOT_DICT",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    zeturf_meta = source_preview.get("zeturf_html_course_level", {})
    compare = source_preview.get("zeturf_letrot_course_compare", {})
    time_guard = source_preview.get("zeturf_scheduled_time_guard", {})

    if not isinstance(zeturf_meta, dict):
        zeturf_meta = {}
    if not isinstance(compare, dict):
        compare = {}
    if not isinstance(time_guard, dict):
        time_guard = {}

    official_join_course_level_oui_non = (
        "oui"
        if source_preview.get("official_join_ok_oui_non") == "oui"
        and compare.get("match_status") == "ROBUST_MATCH"
        and time_guard.get("status") == "OK"
        else "non"
    )

    validated_fields = {
        "reunion_no": zeturf_meta.get("reunion_no", "ND"),
        "course_no": zeturf_meta.get("course_no", "ND"),
        "scheduled_time": zeturf_meta.get("scheduled_time", "ND"),
        "race_name": zeturf_meta.get("race_name", "ND"),
        "hippodrome": zeturf_meta.get("hippodrome", "ND"),
        "date": zeturf_meta.get("date", "ND"),
    }

    missing = [
        key for key, value in validated_fields.items()
        if key in {"reunion_no", "course_no", "scheduled_time", "race_name"}
        and value in (None, "", "ND")
    ]

    if missing:
        course_level_context_status = "COURSE_LEVEL_CONTEXT_NOT_SYNCED_MISSING_FIELDS"
        sync_allowed = False
    elif official_join_course_level_oui_non != "oui":
        course_level_context_status = "COURSE_LEVEL_CONTEXT_NOT_SYNCED_JOIN_NOT_ROBUST"
        sync_allowed = False
    else:
        course_level_context_status = "COURSE_LEVEL_CONTEXT_SYNCED"
        sync_allowed = True

    enriched_context = copy.deepcopy(context_payload)

    # Champs top-level attendus par les previews LeTrot.
    if sync_allowed:
        enriched_context["reunion_no"] = validated_fields["reunion_no"]
        enriched_context["course_no"] = validated_fields["course_no"]
        enriched_context["scheduled_time"] = validated_fields["scheduled_time"]
        enriched_context["race_name"] = validated_fields["race_name"]

    # Audit course-level, toujours visible, m?me si non sync.
    enriched_context["course_level_context_sync_version"] = "zeturf_course_level_context_sync_v0_9_31b2_6"
    enriched_context["course_level_context_sync_status"] = course_level_context_status
    enriched_context["course_level_context_sync_checked_at_utc"] = checked_at_utc
    enriched_context["course_level_context_sync_missing_fields"] = missing
    enriched_context["course_level_context_sync_source"] = "/action/zeturf-course-level-source-preview"
    enriched_context["course_level_validated_fields"] = validated_fields
    enriched_context["course_level_official_join_ok_oui_non"] = official_join_course_level_oui_non
    enriched_context["course_level_match_status"] = compare.get("match_status", "ND")
    enriched_context["course_level_match_best_score"] = compare.get("best_score", "ND")
    enriched_context["course_level_match_reason_codes"] = compare.get("best_reason_codes", [])
    enriched_context["course_level_time_guard_status"] = time_guard.get("status", "ND")
    enriched_context["course_level_time_guard_reason_codes"] = time_guard.get("reason_codes", [])

    # Garde doctrine absolue.
    enriched_context["strict_allowed_oui_non"] = "non"
    enriched_context["strict_usable_oui_non"] = "non"
    enriched_context["ticket_strict"] = "NO_BET"
    enriched_context["Segment_ROI"] = "NO_BET"

    enriched_context = _p361_apply_course_level_context_overlay(enriched_context)

    course_uid = str(enriched_context.get("course_uid", "ND"))
    slug = _p36_safe_slug(course_uid)

    local_latest = Path("data/output/latest_h5_auto_context.json")
    local_course = Path("data/output/h5_contexts") / f"{slug}_h5_auto_context.json"

    local_latest_path = _p36_write_json_local(local_latest, enriched_context)
    local_course_path = _p36_write_json_local(local_course, enriched_context)

    latest_blob = "gpi-lite/outputs/latest_h5_auto_context.json"
    course_blob = f"gpi-lite/outputs/h5_contexts/{slug}_h5_auto_context.json"

    upload_latest = _p36_upload_json_to_gcs(enriched_context, latest_blob)
    upload_course = _p36_upload_json_to_gcs(enriched_context, course_blob)

    return {
        "ok": True,
        "phase": "zeturf_course_level_context_sync",
        "sync_version": "zeturf_course_level_context_sync_v0_9_31b2_6",
        "course_uid": course_uid,
        "course_level_context_sync_status": course_level_context_status,
        "sync_applied_oui_non": "oui" if sync_allowed else "non",
        "validated_fields": validated_fields,
        "missing_fields": missing,
        "source_preview_course_uid": source_preview.get("course_uid", "ND"),
        "source_preview_official_join_ok_oui_non": source_preview.get("official_join_ok_oui_non", "ND"),
        "course_level_official_join_ok_oui_non": official_join_course_level_oui_non,
        "match_status": compare.get("match_status", "ND"),
        "match_best_score": compare.get("best_score", "ND"),
        "time_guard_status": time_guard.get("status", "ND"),
        "local_latest_path": local_latest_path,
        "local_course_path": local_course_path,
        "upload_latest_h5_auto_context": upload_latest,
        "upload_course_h5_auto_context": upload_course,
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "Segment_ROI": "NO_BET",
        "doctrine": (
            "Propagation course-level uniquement. "
            "Ne valide pas NP/statut partants/proxy/split/calibration. "
            "Strict ferme."
        ),
    }



# V0_9_31B2_7_OFFICIAL_RUNNER_STATUS_COURSE_JOIN
def _p37_norm_text(value):
    import re
    import unicodedata

    raw = str(value or "").strip()
    raw = "".join(
        c for c in unicodedata.normalize("NFD", raw)
        if unicodedata.category(c) != "Mn"
    )
    raw = raw.lower()
    raw = raw.replace("&", " et ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _p37_pick(row, keys, default="ND"):
    if not isinstance(row, dict):
        return default

    lower = {str(k).strip().lower(): k for k in row.keys()}

    for key in keys:
        lk = str(key).strip().lower()
        if lk in lower:
            value = row.get(lower[lk])
            if value not in (None, ""):
                return value

    return default


def _p37_to_int_or_none(value):
    import re

    raw = str(value or "").strip().upper()
    if raw in ("", "ND", "NONE", "NULL"):
        return None

    try:
        return int(float(raw))
    except Exception:
        pass

    m = re.search(r"(?<!\d)(?:C|R|N|NO|NUMERO)?\s*(\d{1,2})(?!\d)", raw)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None

    return None


def _p37_bool_np(value):
    raw = _p37_norm_text(value)

    if raw in {"oui", "yes", "true", "1", "np", "non partant", "nonpartant"}:
        return "oui"

    if raw in {"non", "no", "false", "0", "partant", "p"}:
        return "non"

    return "ND"


# V0_9_31B2_8_9_FIX2_DELIMITER_SAFE_OFFICIAL_STATUS_CSV_READER
def _p37_norm_csv_key(key):
    """
    Normalise les noms de colonnes CSV officiels.
    Corrige BOM UTF-8 normal et BOM mojibake sans dependre de l'affichage console.
    """
    txt = "" if key is None else str(key)
    txt = txt.lstrip("\ufeff")
    txt = txt.replace("\ufeff", "")
    txt = txt.replace("\u00ef\u00bb\u00bf", "")
    return txt.strip()


def _p37_detect_csv_dialect(raw):
    """
    Detecte un separateur CSV simple sans modifier csv.excel globalement.
    Retourne uniquement delimiter + source.
    """
    import csv

    raw_s = str(raw or "")
    lines = raw_s.splitlines()
    first_line = lines[0] if lines else ""

    if ";" in first_line and first_line.count(";") >= first_line.count(","):
        return ";", "semicolon"

    if "\t" in first_line:
        return "\t", "tab"

    try:
        sample = raw_s[:4096]
        sniffed = csv.Sniffer().sniff(sample, delimiters=";,\t")
        delimiter = getattr(sniffed, "delimiter", ",") or ","
        return delimiter, f"sniffer:{repr(delimiter)}"
    except Exception:
        return ",", "fallback_comma"


def _p37_csv_rows_from_text(text):
    """
    Lecture CSV officielle BOM-safe + delimiter-safe.
    Retourne fieldnames et rows avec cles normalisees.
    Ne modifie pas les valeurs metier.
    """
    import csv
    import io

    if not text:
        return {
            "fieldnames": [],
            "rows": [],
            "raw_fieldnames": [],
            "reader_version": "delimiter_safe_official_status_csv_reader_v0_9_31b2_8_9_fix3",
            "dialect_source": "empty_text",
            "delimiter": "ND",
        }

    raw = str(text or "")
    raw = raw.lstrip("\ufeff")
    raw = raw.replace("\ufeff", "")
    raw = raw.replace("\u00ef\u00bb\u00bf", "")

    delimiter, dialect_source = _p37_detect_csv_dialect(raw)

    reader = csv.DictReader(io.StringIO(raw), dialect="excel", delimiter=delimiter)
    raw_fieldnames = list(reader.fieldnames or [])
    fieldnames = [_p37_norm_csv_key(x) for x in raw_fieldnames]

    rows = []
    for row in reader:
        if not isinstance(row, dict):
            continue

        clean_row = {}
        for k, v in row.items():
            clean_key = _p37_norm_csv_key(k)
            if not clean_key:
                continue
            clean_row[clean_key] = v

        rows.append(clean_row)

    return {
        "fieldnames": fieldnames,
        "rows": rows,
        "raw_fieldnames": raw_fieldnames,
        "reader_version": "delimiter_safe_official_status_csv_reader_v0_9_31b2_8_9_fix3",
        "dialect_source": dialect_source,
        "delimiter": delimiter,
    }


def _p37_read_text_local_or_gcs(local_candidates, gcs_candidates, bucket_name=None):
    import os
    from pathlib import Path

    bucket_name = (
        bucket_name
        or os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or "gpi-lite-watchlist-data"
    )

    errors = []

    for blob_name in gcs_candidates:
        try:
            from google.cloud import storage

            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name)

            if blob.exists():
                return {
                    "ok": True,
                    "source": "gcs",
                    "bucket": bucket_name,
                    "path": blob_name,
                    "gcs_uri": f"gs://{bucket_name}/{blob_name}",
                    "text": blob.download_as_text(encoding="utf-8"),
                    "errors": errors,
                }

            errors.append(f"GCS_ABSENT:{blob_name}")
        except Exception as exc:
            errors.append(f"GCS_ERROR:{blob_name}:{type(exc).__name__}:{exc}")

    for local_path in local_candidates:
        lp = Path(local_path)
        if not lp.exists():
            errors.append(f"LOCAL_ABSENT:{local_path}")
            continue

        try:
            return {
                "ok": True,
                "source": "local",
                "bucket": bucket_name,
                "path": str(lp),
                "gcs_uri": "ND",
                "text": lp.read_text(encoding="utf-8-sig"),
                "errors": errors,
            }
        except Exception as exc:
            errors.append(f"LOCAL_ERROR:{local_path}:{type(exc).__name__}:{exc}")

    return {
        "ok": False,
        "source": "none",
        "bucket": bucket_name,
        "path": "ND",
        "gcs_uri": "ND",
        "text": "",
        "errors": errors,
    }


def _p37_classify_official_runner_status_columns(fieldnames):
    cols = {str(c).strip().lower(): c for c in fieldnames or []}

    def has_any(keys):
        return any(str(k).strip().lower() in cols for k in keys)

    return {
        "has_course_uid": has_any(["course_uid", "course_key"]),
        "has_reunion_no": has_any(["reunion_no", "reunion", "num_reunion", "reunion_num", "r"]),
        "has_course_no": has_any(["course_no", "num_course", "course_num", "c", "course_no_int"]),
        "has_scheduled_time": has_any(["scheduled_time", "course_time", "start_time", "heure_depart", "heure"]),
        "has_race_name": has_any(["race_name", "course_name", "prix", "libelle"]),
        "has_numero": has_any(["numero", "num", "no", "n", "runner_no"]),
        "has_cheval": has_any(["cheval", "runner", "horse", "nom_cheval", "name"]),
        "has_statut": has_any(["statut", "status", "runner_status", "statut_partant"]),
        "has_is_np": has_any(["is_np", "np", "non_partant", "nonpartant"]),
        "has_official_source": has_any(["official_source", "source_officielle", "source", "official_source_kind"]),
        "has_confidence": has_any(["np_statut_confidence", "confidence", "official_confidence"]),
    }


def _p37_row_matches_course(row, context):
    if not isinstance(row, dict):
        return {
            "match": False,
            "score": 0,
            "reason_codes": ["ROW_NOT_DICT"],
        }

    if not isinstance(context, dict):
        context = {}

    reasons = []
    score = 0

    ctx_course_uid = _p37_norm_text(context.get("course_uid") or context.get("course_key"))
    row_course_uid = _p37_norm_text(_p37_pick(row, ["course_uid", "course_key"], ""))

    if ctx_course_uid and row_course_uid and ctx_course_uid == row_course_uid:
        score += 8
        reasons.append("COURSE_UID_MATCH")

    ctx_reunion = _p37_to_int_or_none(context.get("reunion_no"))
    row_reunion = _p37_to_int_or_none(_p37_pick(row, ["reunion_no", "reunion", "num_reunion", "reunion_num", "r"], ""))

    if ctx_reunion is not None and row_reunion is not None and ctx_reunion == row_reunion:
        score += 3
        reasons.append("REUNION_NO_MATCH")

    ctx_course_no = _p37_to_int_or_none(context.get("course_no"))
    row_course_no = _p37_to_int_or_none(_p37_pick(row, ["course_no", "num_course", "course_num", "c", "course_no_int"], ""))

    if ctx_course_no is not None and row_course_no is not None and ctx_course_no == row_course_no:
        score += 4
        reasons.append("COURSE_NO_MATCH")

    ctx_time = _p37_norm_text(context.get("scheduled_time"))
    row_time = _p37_norm_text(_p37_pick(row, ["scheduled_time", "course_time", "start_time", "heure_depart", "heure"], ""))

    if ctx_time and row_time and ctx_time == row_time:
        score += 3
        reasons.append("SCHEDULED_TIME_MATCH")

    ctx_race = _p37_norm_text(context.get("race_name"))
    row_race = _p37_norm_text(_p37_pick(row, ["race_name", "course_name", "prix", "libelle"], ""))

    if ctx_race and row_race:
        ctx_tokens = set(ctx_race.split())
        row_tokens = set(row_race.split())
        inter = len(ctx_tokens & row_tokens)
        union = max(1, len(ctx_tokens | row_tokens))
        similarity = inter / union

        if similarity >= 0.60:
            score += 2
            reasons.append("RACE_NAME_MATCH")

    # Match robuste minimal :
    # - course_uid exact ; OU
    # - reunion + course_no ; OU
    # - course_no + scheduled_time.
    robust = (
        "COURSE_UID_MATCH" in reasons
        or ("REUNION_NO_MATCH" in reasons and "COURSE_NO_MATCH" in reasons)
        or ("COURSE_NO_MATCH" in reasons and "SCHEDULED_TIME_MATCH" in reasons)
    )

    return {
        "match": bool(robust),
        "score": score,
        "reason_codes": reasons or ["NO_COURSE_MATCH"],
    }


def _p37_normalize_runner_status_row(row):
    numero = _p37_pick(row, ["numero", "num", "no", "n", "runner_no"], "ND")
    cheval = _p37_pick(row, ["cheval", "runner", "horse", "nom_cheval", "name"], "ND")
    statut = _p37_pick(row, ["statut", "status", "runner_status", "statut_partant"], "ND")
    is_np_raw = _p37_pick(row, ["is_np", "np", "non_partant", "nonpartant"], "ND")
    source = _p37_pick(row, ["official_source", "source_officielle", "source", "official_source_kind"], "ND")
    confidence = _p37_pick(row, ["np_statut_confidence", "confidence", "official_confidence"], "ND")

    is_np = _p37_bool_np(is_np_raw)

    if is_np == "ND":
        statut_norm = _p37_norm_text(statut)
        if statut_norm in {"np", "non partant", "nonpartant", "non partant officiel"}:
            is_np = "oui"
        elif statut_norm in {"partant", "partant officiel", "ok", "running"}:
            is_np = "non"

    return {
        "numero": numero,
        "cheval": cheval,
        "statut": statut,
        "is_np": is_np,
        "official_source": source,
        "np_statut_confidence": confidence,
    }



# V0_9_31B2_7_1_OFFICIAL_STATUS_SOURCE_DIAGNOSTICS
def _p371_build_official_status_source_diagnostics(rows, context_payload):
    if not isinstance(rows, list):
        rows = []

    if not isinstance(context_payload, dict):
        context_payload = {}

    ctx_course_uid_raw = str(context_payload.get("course_uid") or context_payload.get("course_key") or "ND")
    ctx_course_uid_norm = _p37_norm_text(ctx_course_uid_raw)
    ctx_reunion = _p37_to_int_or_none(context_payload.get("reunion_no"))
    ctx_course_no = _p37_to_int_or_none(context_payload.get("course_no"))
    ctx_time = _p37_norm_text(context_payload.get("scheduled_time"))
    ctx_race = _p37_norm_text(context_payload.get("race_name"))

    course_uid_counts = {}
    reunion_course_counts = {}
    scheduled_time_counts = {}
    race_name_counts = {}

    rows_with_course_uid = 0
    rows_with_reunion_course = 0
    rows_with_scheduled_time = 0
    rows_with_race_name = 0

    uid_exact_match_count = 0
    reunion_course_match_count = 0
    scheduled_time_match_count = 0
    race_name_similarity_count = 0

    sample_values = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        row_course_uid = str(_p37_pick(row, ["course_uid", "course_key"], "ND") or "ND")
        row_course_uid_norm = _p37_norm_text(row_course_uid)

        if row_course_uid not in ("", "ND"):
            rows_with_course_uid += 1
            course_uid_counts[row_course_uid] = course_uid_counts.get(row_course_uid, 0) + 1

            if row_course_uid_norm and row_course_uid_norm == ctx_course_uid_norm:
                uid_exact_match_count += 1

        row_reunion = _p37_to_int_or_none(_p37_pick(row, ["reunion_no", "reunion", "num_reunion", "reunion_num", "r"], ""))
        row_course_no = _p37_to_int_or_none(_p37_pick(row, ["course_no", "num_course", "course_num", "c", "course_no_int"], ""))

        if row_reunion is not None and row_course_no is not None:
            rows_with_reunion_course += 1
            key = f"R{row_reunion}C{row_course_no}"
            reunion_course_counts[key] = reunion_course_counts.get(key, 0) + 1

            if ctx_reunion == row_reunion and ctx_course_no == row_course_no:
                reunion_course_match_count += 1

        row_time = str(_p37_pick(row, ["scheduled_time", "course_time", "start_time", "heure_depart", "heure"], "ND") or "ND")
        row_time_norm = _p37_norm_text(row_time)

        if row_time not in ("", "ND"):
            rows_with_scheduled_time += 1
            scheduled_time_counts[row_time] = scheduled_time_counts.get(row_time, 0) + 1

            if ctx_time and row_time_norm == ctx_time:
                scheduled_time_match_count += 1

        row_race = str(_p37_pick(row, ["race_name", "course_name", "prix", "libelle"], "ND") or "ND")
        row_race_norm = _p37_norm_text(row_race)

        if row_race not in ("", "ND"):
            rows_with_race_name += 1
            race_name_counts[row_race] = race_name_counts.get(row_race, 0) + 1

            if ctx_race and row_race_norm:
                ctx_tokens = set(ctx_race.split())
                row_tokens = set(row_race_norm.split())
                sim = len(ctx_tokens & row_tokens) / max(1, len(ctx_tokens | row_tokens))
                if sim >= 0.60:
                    race_name_similarity_count += 1

        if len(sample_values) < 10:
            sample_values.append({
                "course_uid": row_course_uid,
                "reunion_no": row_reunion if row_reunion is not None else "ND",
                "course_no": row_course_no if row_course_no is not None else "ND",
                "scheduled_time": row_time,
                "race_name": row_race,
                "numero": _p37_pick(row, ["numero", "num", "no", "n", "runner_no"], "ND"),
                "cheval": _p37_pick(row, ["cheval", "runner", "horse", "nom_cheval", "name"], "ND"),
                "statut": _p37_pick(row, ["statut", "status", "runner_status", "statut_partant"], "ND"),
                "is_np": _p37_pick(row, ["is_np", "np", "non_partant", "nonpartant"], "ND"),
            })

    unique_course_uids = [
        {"course_uid": key, "rows": count}
        for key, count in sorted(course_uid_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    unique_reunion_courses = [
        {"reunion_course": key, "rows": count}
        for key, count in sorted(reunion_course_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    unique_scheduled_times = [
        {"scheduled_time": key, "rows": count}
        for key, count in sorted(scheduled_time_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    unique_race_names = [
        {"race_name": key, "rows": count}
        for key, count in sorted(race_name_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    reason_codes = []

    if not rows:
        reason_codes.append("OFFICIAL_STATUS_SOURCE_EMPTY")
    elif uid_exact_match_count == 0 and rows_with_course_uid > 0:
        reason_codes.append("OFFICIAL_STATUS_SOURCE_STALE_OR_WRONG_COURSE")
    elif rows_with_course_uid == 0:
        reason_codes.append("OFFICIAL_STATUS_COURSE_UID_COLUMN_EMPTY")

    if rows_with_reunion_course == 0:
        reason_codes.append("OFFICIAL_STATUS_NO_REUNION_COURSE_COLUMNS")

    if rows_with_scheduled_time == 0:
        reason_codes.append("OFFICIAL_STATUS_NO_SCHEDULED_TIME_COLUMN")

    if rows_with_race_name == 0:
        reason_codes.append("OFFICIAL_STATUS_NO_RACE_NAME_COLUMN")

    if uid_exact_match_count > 0 or reunion_course_match_count > 0:
        source_alignment_status = "SOURCE_CAN_MATCH_CURRENT_COURSE"
    elif rows and rows_with_course_uid > 0:
        source_alignment_status = "SOURCE_STALE_OR_WRONG_COURSE"
    elif rows:
        source_alignment_status = "SOURCE_SCHEMA_INSUFFICIENT_FOR_COURSE_MATCH"
    else:
        source_alignment_status = "SOURCE_EMPTY"

    return {
        "diagnostics_version": "official_status_source_diagnostics_v0_9_31b2_7_1",
        "context_course_uid": ctx_course_uid_raw,
        "context_reunion_no": context_payload.get("reunion_no", "ND"),
        "context_course_no": context_payload.get("course_no", "ND"),
        "context_scheduled_time": context_payload.get("scheduled_time", "ND"),
        "context_race_name": context_payload.get("race_name", "ND"),
        "rows_total": len(rows),
        "rows_with_course_uid": rows_with_course_uid,
        "rows_with_reunion_course": rows_with_reunion_course,
        "rows_with_scheduled_time": rows_with_scheduled_time,
        "rows_with_race_name": rows_with_race_name,
        "uid_exact_match_count": uid_exact_match_count,
        "reunion_course_match_count": reunion_course_match_count,
        "scheduled_time_match_count": scheduled_time_match_count,
        "race_name_similarity_count": race_name_similarity_count,
        "source_alignment_status": source_alignment_status,
        "reason_codes": reason_codes,
        "unique_course_uids_sample": unique_course_uids[:10],
        "unique_reunion_courses_sample": unique_reunion_courses[:10],
        "unique_scheduled_times_sample": unique_scheduled_times[:10],
        "unique_race_names_sample": unique_race_names[:10],
        "rows_sample": sample_values,
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
    }



# V0_9_31B2_7_2_FORCE_ENRICHED_H5_CONTEXT
def _p372_force_enriched_course_context(payload):
    """
    Garantit que les champs course-level sont presents dans le contexte utilise
    par official-runner-status-course-level-preview.

    Ne peut jamais ouvrir le strict.
    """
    if not isinstance(payload, dict):
        payload = {}

    # 1) Appliquer les overlays existants si disponibles.
    try:
        fallback_overlay = _p361_read_course_scoped_h5_context_for_overlay(payload)
        if isinstance(fallback_overlay, dict) and isinstance(fallback_overlay.get("merged_payload"), dict):
            payload = fallback_overlay["merged_payload"]
    except Exception as exc:
        payload["course_level_context_force_overlay_error"] = f"FALLBACK_OVERLAY_ERROR:{type(exc).__name__}:{exc}"

    try:
        payload = _p361_apply_course_level_context_overlay(payload)
    except Exception as exc:
        payload["course_level_context_force_overlay_error"] = f"TOPLEVEL_OVERLAY_ERROR:{type(exc).__name__}:{exc}"

    required = ["reunion_no", "course_no", "scheduled_time", "race_name"]
    missing = [k for k in required if payload.get(k) in (None, "", "ND")]

    # 2) Si encore incomplet, relire la source-preview valid?e.
    if missing:
        try:
            source_preview = action_zeturf_course_level_source_preview()
        except Exception as exc:
            source_preview = {
                "ok": False,
                "error": f"{type(exc).__name__}:{exc}",
            }

        if isinstance(source_preview, dict) and source_preview.get("ok"):
            zeturf_meta = source_preview.get("zeturf_html_course_level", {})
            if isinstance(zeturf_meta, dict):
                mapping = {
                    "reunion_no": zeturf_meta.get("reunion_no", "ND"),
                    "course_no": zeturf_meta.get("course_no", "ND"),
                    "scheduled_time": zeturf_meta.get("scheduled_time", "ND"),
                    "race_name": zeturf_meta.get("race_name", "ND"),
                }

                for key, value in mapping.items():
                    if value not in (None, "", "ND") and payload.get(key) in (None, "", "ND"):
                        payload[key] = value

                payload["course_level_context_force_source_preview_used_oui_non"] = "oui"
                payload["course_level_context_force_source_preview_official_join_ok_oui_non"] = source_preview.get("official_join_ok_oui_non", "ND")
                payload["course_level_context_force_source_preview_match_status"] = (
                    source_preview.get("zeturf_letrot_course_compare", {}) or {}
                ).get("match_status", "ND")
                payload["course_level_context_force_source_preview_time_guard_status"] = (
                    source_preview.get("zeturf_scheduled_time_guard", {}) or {}
                ).get("status", "ND")
        else:
            payload["course_level_context_force_source_preview_used_oui_non"] = "non"
            payload["course_level_context_force_source_preview_error"] = source_preview.get("error", "SOURCE_PREVIEW_UNAVAILABLE") if isinstance(source_preview, dict) else "SOURCE_PREVIEW_NOT_DICT"

    final_missing = [k for k in required if payload.get(k) in (None, "", "ND")]

    payload["course_level_context_force_version"] = "force_enriched_h5_context_v0_9_31b2_7_2"
    payload["course_level_context_force_missing_fields"] = final_missing
    payload["course_level_context_force_ok_oui_non"] = "oui" if not final_missing else "non"

    payload["strict_allowed_oui_non"] = "non"
    payload["strict_usable_oui_non"] = "non"
    payload["ticket_strict"] = "NO_BET"

    return payload



# V0_9_31B2_8_LETROT_RUNNER_STATUS_CAPTURE
def _p38_pick_any(obj, keys, default="ND"):
    if not isinstance(obj, dict):
        return default

    lower = {str(k).strip().lower(): k for k in obj.keys()}

    for key in keys:
        lk = str(key).strip().lower()
        if lk in lower:
            value = obj.get(lower[lk])
            if value not in (None, ""):
                return value

    return default


def _p38_to_np_oui_non(value, statut="ND"):
    raw = _p37_norm_text(value)
    statut_norm = _p37_norm_text(statut)

    if raw in {"oui", "yes", "true", "1", "np", "non partant", "nonpartant"}:
        return "oui"

    if raw in {"non", "no", "false", "0", "partant", "p"}:
        return "non"

    if statut_norm in {"np", "non partant", "nonpartant", "forfait", "non partant officiel"}:
        return "oui"

    if statut_norm in {"partant", "partant officiel", "ok", "running", "en course"}:
        return "non"

    return "ND"


def _p38_extract_runner_list_from_payload(payload):
    """
    Cherche une liste de partants dans un objet LeTrot d?j? disponible.

    Cette fonction ne scrape pas et n'invente rien.
    Elle accepte uniquement des listes d?j? structur?es.
    """
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "reason": "PAYLOAD_NOT_DICT",
            "source_key": "ND",
            "raw_runners": [],
        }

    candidate_keys = [
        "runners",
        "runner_status",
        "partants",
        "participants",
        "chevaux",
        "starters",
        "horses",
        "engages",
        "entries",
        "declares",
    ]

    for key in candidate_keys:
        value = _p38_pick_any(payload, [key], None)
        if isinstance(value, list) and value:
            return {
                "ok": True,
                "reason": "STRUCTURED_RUNNER_LIST_FOUND",
                "source_key": key,
                "raw_runners": value,
            }

    # Cherche un niveau imbriqu? simple.
    for parent_key in ["course", "selected_course", "details", "payload", "data"]:
        parent = _p38_pick_any(payload, [parent_key], None)
        if isinstance(parent, dict):
            nested = _p38_extract_runner_list_from_payload(parent)
            if nested.get("ok"):
                nested["source_key"] = f"{parent_key}.{nested.get('source_key', 'ND')}"
                return nested

    return {
        "ok": False,
        "reason": "STRUCTURED_RUNNER_LIST_ABSENT",
        "source_key": "ND",
        "raw_runners": [],
    }


def _p38_normalize_official_runner(row, context_payload, selected_course):
    if not isinstance(row, dict):
        row = {}

    numero = _p38_pick_any(row, [
        "numero", "num", "no", "n", "runner_no", "numPmu", "num_pmu", "numeroPmu"
    ], "ND")

    cheval = _p38_pick_any(row, [
        "cheval", "nom", "name", "horse", "runner", "nomCheval", "nom_cheval", "libelle"
    ], "ND")

    statut = _p38_pick_any(row, [
        "statut", "status", "runner_status", "statut_partant", "etat", "etat_partant"
    ], "ND")

    is_np_raw = _p38_pick_any(row, [
        "is_np", "np", "non_partant", "nonpartant", "isNonPartant", "nonPartant"
    ], "ND")

    is_np = _p38_to_np_oui_non(is_np_raw, statut)

    if statut in (None, "", "ND"):
        statut = "NP" if is_np == "oui" else "PARTANT" if is_np == "non" else "ND"

    return {
        "course_uid": context_payload.get("course_uid", "ND"),
        "reunion_no": context_payload.get("reunion_no", "ND"),
        "course_no": context_payload.get("course_no", "ND"),
        "scheduled_time": context_payload.get("scheduled_time", "ND"),
        "race_name": context_payload.get("race_name", "ND"),
        "numero": numero,
        "cheval": cheval,
        "statut": statut,
        "is_np": is_np,
        "official_source": "LETROT_MATCHED_COURSE_SELECTED_PAYLOAD",
        "statut_source": "LETROT_MATCHED_COURSE_SELECTED_PAYLOAD",
        "np_statut_confidence": "HIGH" if numero not in ("", "ND") and cheval not in ("", "ND") and is_np != "ND" else "LOW",
    }


def _p38_validate_official_runner_rows(rows):
    if not isinstance(rows, list):
        rows = []

    reason_codes = []

    if not rows:
        return {
            "ok": False,
            "status": "NO_RUNNER_ROWS",
            "reason_codes": ["NO_RUNNER_ROWS"],
            "rows_count": 0,
            "missing_numero_count": 0,
            "missing_cheval_count": 0,
            "missing_is_np_count": 0,
            "np_count": 0,
        }

    missing_numero = sum(1 for r in rows if r.get("numero") in (None, "", "ND"))
    missing_cheval = sum(1 for r in rows if r.get("cheval") in (None, "", "ND"))
    missing_is_np = sum(1 for r in rows if r.get("is_np") in (None, "", "ND"))
    np_count = sum(1 for r in rows if r.get("is_np") == "oui")

    if missing_numero:
        reason_codes.append("RUNNER_NUMERO_MISSING")
    if missing_cheval:
        reason_codes.append("RUNNER_NAME_MISSING")
    if missing_is_np:
        reason_codes.append("RUNNER_IS_NP_MISSING")

    if reason_codes:
        return {
            "ok": False,
            "status": "RUNNER_ROWS_INCOMPLETE",
            "reason_codes": reason_codes,
            "rows_count": len(rows),
            "missing_numero_count": missing_numero,
            "missing_cheval_count": missing_cheval,
            "missing_is_np_count": missing_is_np,
            "np_count": np_count,
        }

    return {
        "ok": True,
        "status": "RUNNER_ROWS_COMPLETE",
        "reason_codes": ["RUNNER_ROWS_COMPLETE"],
        "rows_count": len(rows),
        "missing_numero_count": 0,
        "missing_cheval_count": 0,
        "missing_is_np_count": 0,
        "np_count": np_count,
    }


def _p38_write_csv_rows_utf8sig(path_obj, rows):
    import csv
    from pathlib import Path

    path_obj = Path(path_obj)
    path_obj.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "course_uid",
        "reunion_no",
        "course_no",
        "scheduled_time",
        "race_name",
        "numero",
        "cheval",
        "statut",
        "is_np",
        "official_source",
        "statut_source",
        "np_statut_confidence",
        "checked_at_utc",
    ]

    with path_obj.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return str(path_obj)


# V0_9_31B2_8_10_FIX2_ENRICH_CAPTURE_ROWS_COURSE_IDENTITY
def _p3810_fix2_enrich_capture_rows_course_identity(rows, context_payload, selected_course):
    """
    Ajoute l'identit? course manquante aux lignes captur?es avant ?criture CSV.

    Priorit? :
    1. selected_course si disponible ;
    2. context_payload ;
    3. ND.

    S?curit? :
    - ne remplace jamais une valeur d?j? pr?sente et non ND ;
    - ne force pas un course_uid contextuel si selected_course porte un course_uid conflictuel ;
    - n'ouvre jamais le strict.
    """

    def _missing(value):
        return value is None or str(value).strip() in {"", "ND", "None", "null"}

    def _clean(value):
        if _missing(value):
            return "ND"
        return str(value).strip()

    def _pick(*values):
        for value in values:
            if not _missing(value):
                return _clean(value)
        return "ND"

    if not isinstance(context_payload, dict):
        context_payload = {}

    if not isinstance(selected_course, dict):
        selected_course = {}

    context_course_uid = _pick(context_payload.get("course_uid"))
    selected_course_uid = _pick(
        selected_course.get("course_uid"),
        selected_course.get("course_key"),
        selected_course.get("uid"),
    )

    context_uid_norm = _p37_norm_text(context_course_uid)
    selected_uid_norm = _p37_norm_text(selected_course_uid)

    selected_has_conflicting_uid = (
        selected_uid_norm
        and selected_uid_norm != "nd"
        and context_uid_norm
        and context_uid_norm != "nd"
        and selected_uid_norm != context_uid_norm
    )

    safe_course_uid = "ND" if selected_has_conflicting_uid else _pick(
        selected_course_uid,
        context_course_uid,
    )

    safe_reunion_no = _pick(
        selected_course.get("reunion_no"),
        selected_course.get("reunion"),
        selected_course.get("num_reunion"),
        selected_course.get("r"),
        context_payload.get("reunion_no"),
    )

    safe_course_no = _pick(
        selected_course.get("course_no"),
        selected_course.get("course_no_int"),
        selected_course.get("num_course"),
        selected_course.get("c"),
        context_payload.get("course_no"),
    )

    safe_scheduled_time = _pick(
        selected_course.get("scheduled_time"),
        selected_course.get("course_time"),
        selected_course.get("start_time"),
        selected_course.get("heure_depart"),
        selected_course.get("heure"),
        context_payload.get("scheduled_time"),
    )

    safe_race_name = _pick(
        selected_course.get("race_name"),
        selected_course.get("course_name"),
        selected_course.get("prix"),
        selected_course.get("detail_label"),
        context_payload.get("race_name"),
    )

    enriched = []

    for row in rows or []:
        if not isinstance(row, dict):
            continue

        r = dict(row)

        if _missing(r.get("course_uid")):
            r["course_uid"] = safe_course_uid

        if _missing(r.get("reunion_no")):
            r["reunion_no"] = safe_reunion_no

        if _missing(r.get("course_no")):
            r["course_no"] = safe_course_no

        if _missing(r.get("scheduled_time")):
            r["scheduled_time"] = safe_scheduled_time

        if _missing(r.get("race_name")):
            r["race_name"] = safe_race_name

        enriched.append(r)

    filled_count = sum(
        1 for r in enriched
        if not _missing(r.get("course_uid"))
    )

    return {
        "ok": True,
        "version": "capture_rows_course_identity_enrichment_v0_9_31b2_8_10_fix2b",
        "rows": enriched,
        "rows_total": len(enriched),
        "rows_with_course_uid": filled_count,
        "selected_has_conflicting_uid": "oui" if selected_has_conflicting_uid else "non",
        "safe_course_uid": safe_course_uid,
        "safe_reunion_no": safe_reunion_no,
        "safe_course_no": safe_course_no,
        "safe_scheduled_time": safe_scheduled_time,
        "safe_race_name": safe_race_name,
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
    }


# V0_9_31B2_8_10_GUARD_OFFICIAL_LATEST_OVERWRITE
def _p3810_guard_official_latest_upload(local_path, blob_name):
    """
    Emp?che official_runner_status_latest.csv d'?tre ?cras? par une course
    diff?rente de la course H5 active.

    Lecture seule.
    Ne scrape rien.
    N'ouvre jamais le strict.
    """
    from pathlib import Path

    target = str(blob_name or "").strip()

    if target != "gpi-lite/outputs/official_runner_status_latest.csv":
        return {
            "ok": True,
            "reason": "NOT_OFFICIAL_RUNNER_STATUS_LATEST",
            "target_blob": target,
            "guard_version": "official_latest_overwrite_guard_v0_9_31b2_8_10_fix2b",
        }

    path = Path(local_path)
    if not path.exists():
        return {
            "ok": False,
            "reason": "CAPTURE_REJECTED_LOCAL_CSV_ABSENT",
            "target_blob": target,
            "local_path": str(path),
            "guard_version": "official_latest_overwrite_guard_v0_9_31b2_8_10_fix2b",
        }

    try:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"CAPTURE_REJECTED_LOCAL_CSV_READ_ERROR:{type(exc).__name__}:{exc}",
            "target_blob": target,
            "local_path": str(path),
            "guard_version": "official_latest_overwrite_guard_v0_9_31b2_8_10_fix2b",
        }

    try:
        csv_payload = _p37_csv_rows_from_text(text)
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"CAPTURE_REJECTED_LOCAL_CSV_PARSE_ERROR:{type(exc).__name__}:{exc}",
            "target_blob": target,
            "local_path": str(path),
            "guard_version": "official_latest_overwrite_guard_v0_9_31b2_8_10_fix2b",
        }

    rows = csv_payload.get("rows", []) if isinstance(csv_payload, dict) else []
    fieldnames = csv_payload.get("fieldnames", []) if isinstance(csv_payload, dict) else []

    if not rows:
        return {
            "ok": False,
            "reason": "CAPTURE_REJECTED_NO_ROWS_FOR_LATEST",
            "target_blob": target,
            "local_path": str(path),
            "fieldnames": fieldnames,
            "guard_version": "official_latest_overwrite_guard_v0_9_31b2_8_10_fix2b",
        }

    try:
        active_context = action_latest_h5_auto_context()
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"CAPTURE_REJECTED_ACTIVE_CONTEXT_ERROR:{type(exc).__name__}:{exc}",
            "target_blob": target,
            "local_path": str(path),
            "rows_total": len(rows),
            "guard_version": "official_latest_overwrite_guard_v0_9_31b2_8_10_fix2b",
        }

    if not isinstance(active_context, dict) or not active_context.get("ok"):
        return {
            "ok": False,
            "reason": "CAPTURE_REJECTED_ACTIVE_CONTEXT_UNAVAILABLE",
            "target_blob": target,
            "local_path": str(path),
            "rows_total": len(rows),
            "active_context_ok": active_context.get("ok", "ND") if isinstance(active_context, dict) else "CONTEXT_NOT_DICT",
            "guard_version": "official_latest_overwrite_guard_v0_9_31b2_8_10_fix2b",
        }

    matched = []
    rejected_sample = []

    active_course_uid_norm = _p37_norm_text(active_context.get("course_uid", "ND"))

    for row in rows:
        row_course_uid_norm = _p37_norm_text(row.get("course_uid", "ND"))

        exact_course_uid_match = (
            active_course_uid_norm
            and active_course_uid_norm != "nd"
            and row_course_uid_norm
            and row_course_uid_norm == active_course_uid_norm
        )

        if exact_course_uid_match:
            match_info = {
                "match": True,
                "score": 100,
                "reason_codes": ["EXACT_COURSE_UID_MATCH_GUARD_FIX1"],
            }
        else:
            try:
                match_info = _p37_row_matches_course(row, active_context)
            except Exception as exc:
                match_info = {
                    "match": False,
                    "score": 0,
                    "reason_codes": [f"MATCH_ERROR:{type(exc).__name__}:{exc}"],
                }

        if match_info.get("match"):
            matched.append({
                "course_uid": row.get("course_uid", "ND"),
                "reunion_no": row.get("reunion_no", "ND"),
                "course_no": row.get("course_no", "ND"),
                "scheduled_time": row.get("scheduled_time", "ND"),
                "race_name": row.get("race_name", "ND"),
                "match_score": match_info.get("score", 0),
                "match_reason_codes": match_info.get("reason_codes", []),
            })
        elif len(rejected_sample) < 5:
            rejected_sample.append({
                "course_uid": row.get("course_uid", "ND"),
                "reunion_no": row.get("reunion_no", "ND"),
                "course_no": row.get("course_no", "ND"),
                "scheduled_time": row.get("scheduled_time", "ND"),
                "race_name": row.get("race_name", "ND"),
                "match_score": match_info.get("score", 0),
                "match_reason_codes": match_info.get("reason_codes", []),
            })

    active_fields = {
        "course_uid": active_context.get("course_uid", "ND"),
        "course_slug": active_context.get("course_slug", "ND"),
        "reunion_no": active_context.get("reunion_no", "ND"),
        "course_no": active_context.get("course_no", "ND"),
        "scheduled_time": active_context.get("scheduled_time", "ND"),
        "race_name": active_context.get("race_name", "ND"),
        "runners_count": active_context.get("runners_count", "ND"),
    }

    if not matched:
        return {
            "ok": False,
            "reason": "CAPTURE_REJECTED_WRONG_ACTIVE_COURSE_FOR_LATEST",
            "target_blob": target,
            "local_path": str(path),
            "rows_total": len(rows),
            "rows_matched_active_context": 0,
            "active_context_fields": active_fields,
            "csv_fieldnames": fieldnames,
            "rejected_sample": rejected_sample,
            "guard_version": "official_latest_overwrite_guard_v0_9_31b2_8_10_fix2b",
            "strict_allowed_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    return {
        "ok": True,
        "reason": "OFFICIAL_LATEST_UPLOAD_ALLOWED_ACTIVE_COURSE_MATCHED",
        "target_blob": target,
        "local_path": str(path),
        "rows_total": len(rows),
        "rows_matched_active_context": len(matched),
        "active_context_fields": active_fields,
        "matched_sample": matched[:5],
        "guard_version": "official_latest_overwrite_guard_v0_9_31b2_8_10_fix2b",
    }


def _p38_upload_file_to_gcs(local_path, blob_name, content_type="text/csv"):
    import os
    from pathlib import Path

    guard = _p3810_guard_official_latest_upload(local_path, blob_name)

    if not isinstance(guard, dict):
        guard = {
            "ok": False,
            "reason": "OFFICIAL_LATEST_GUARD_NOT_DICT",
            "guard_version": "official_latest_overwrite_guard_v0_9_31b2_8_10_fix2b",
        }

    if not guard.get("ok"):
        return {
            "ok": False,
            "reason": guard.get("reason", "OFFICIAL_LATEST_UPLOAD_REJECTED"),
            "bucket": (
                os.getenv("GPI_BUCKET_NAME")
                or os.getenv("BUCKET_NAME")
                or os.getenv("GPI_BUCKET")
                or globals().get("GPI_BUCKET_NAME")
                or "gpi-lite-watchlist-data"
            ),
            "blob": blob_name,
            "gcs_uri": f"gs://{(os.getenv('GPI_BUCKET_NAME') or os.getenv('BUCKET_NAME') or os.getenv('GPI_BUCKET') or globals().get('GPI_BUCKET_NAME') or 'gpi-lite-watchlist-data')}/{blob_name}",
            "guard": guard,
            "strict_allowed_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or globals().get("GPI_BUCKET_NAME")
        or "gpi-lite-watchlist-data"
    )

    path = Path(local_path)
    if not path.exists():
        return {
            "ok": False,
            "reason": "LOCAL_FILE_ABSENT",
            "bucket": bucket_name,
            "blob": blob_name,
            "local_path": str(path),
            "gcs_uri": f"gs://{bucket_name}/{blob_name}",
            "guard": guard,
        }

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(path), content_type=content_type)

        return {
            "ok": True,
            "reason": "UPLOADED",
            "bucket": bucket_name,
            "blob": blob_name,
            "local_path": str(path),
            "gcs_uri": f"gs://{bucket_name}/{blob_name}",
            "guard": guard,
        }

    except Exception as exc:
        return {
            "ok": False,
            "reason": f"GCS_UPLOAD_ERROR:{type(exc).__name__}:{exc}",
            "bucket": bucket_name,
            "blob": blob_name,
            "local_path": str(path),
            "gcs_uri": f"gs://{bucket_name}/{blob_name}",
            "guard": guard,
        }


def _p381_get_selected_course_for_runner_capture(letrot_match):
    """
    Selection robuste de la course LeTrot match?e pour B2.8.
    Priorit? :
    1. selected_course de letrot-course-match-preview ;
    2. best candidate de zeturf-course-level-source-preview.
    """
    if isinstance(letrot_match, dict):
        selected = letrot_match.get("selected_course", {}) or {}
        if isinstance(selected, dict) and selected:
            return {
                "ok": True,
                "selected_course": selected,
                "selected_course_source": "letrot_course_match_preview.selected_course",
                "selected_course_reason": "SELECTED_COURSE_FROM_LETROT_PREVIEW",
                "fallback_source_preview_used_oui_non": "non",
            }

    try:
        source_preview = action_zeturf_course_level_source_preview()
    except Exception as exc:
        source_preview = {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }

    fallback = _p381_extract_best_candidate_from_source_preview(source_preview)

    if fallback.get("ok"):
        return {
            "ok": True,
            "selected_course": fallback.get("selected_course", {}),
            "selected_course_source": fallback.get("source", "source_preview"),
            "selected_course_reason": fallback.get("reason", "SOURCE_PREVIEW_FALLBACK"),
            "fallback_source_preview_used_oui_non": "oui",
            "source_preview_course_uid": source_preview.get("course_uid", "ND") if isinstance(source_preview, dict) else "ND",
            "source_preview_official_join_ok_oui_non": source_preview.get("official_join_ok_oui_non", "ND") if isinstance(source_preview, dict) else "ND",
        }

    return {
        "ok": False,
        "selected_course": {},
        "selected_course_source": "ND",
        "selected_course_reason": fallback.get("reason", "SELECTED_COURSE_NOT_FOUND"),
        "fallback_source_preview_used_oui_non": "oui",
        "source_preview_error": source_preview.get("error", "ND") if isinstance(source_preview, dict) else "SOURCE_PREVIEW_NOT_DICT",
    }



# V0_9_31B2_8_2_LETROT_DETAIL_RUNNER_EXTRACTION
def _p382_collect_possible_detail_urls(obj, max_depth=4):
    """
    Collecte prudente des URLs ou chemins detail pr?sents dans un payload.
    Ne construit pas d?URL artificielle.
    """
    import re

    urls = []
    seen = set()

    def walk(value, path="", depth=0):
        if depth > max_depth:
            return

        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k}" if path else str(k), depth + 1)
            return

        if isinstance(value, list):
            for i, v in enumerate(value[:50]):
                walk(v, f"{path}[{i}]", depth + 1)
            return

        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return

            candidates = []

            # URL compl?te.
            if raw.startswith("http://") or raw.startswith("https://"):
                candidates.append(raw)

            # Chemin relatif LeTrot possible.
            if raw.startswith("/") and ("course" in raw.lower() or "reunion" in raw.lower() or "partant" in raw.lower()):
                candidates.append("https://www.letrot.com" + raw)

            # URL contenue dans une cha?ne.
            for m in re.finditer(r"https?://[^\s\"']+", raw):
                candidates.append(m.group(0))

            for url in candidates:
                if url not in seen:
                    seen.add(url)
                    urls.append({
                        "path": path,
                        "url": url,
                    })

    walk(obj)

    return urls


def _p382_fetch_url_text(url):
    """
    Fetch HTTP simple, lecture seule.
    """
    try:
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 GPI-Lite Watchlist official runner status preview",
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        }

        r = requests.get(url, headers=headers, timeout=20)

        return {
            "ok": 200 <= r.status_code < 400,
            "http_status": r.status_code,
            "content_type": r.headers.get("content-type", "ND"),
            "text": r.text or "",
            "error": "ND",
        }
    except Exception as exc:
        return {
            "ok": False,
            "http_status": "ND",
            "content_type": "ND",
            "text": "",
            "error": f"{type(exc).__name__}:{exc}",
        }


def _p382_extract_runners_from_json_like(value):
    """
    Recherche r?cursive de listes structur?es de partants dans JSON.
    """
    found = []

    def looks_like_runner(row):
        if not isinstance(row, dict):
            return False

        keys = {str(k).lower() for k in row.keys()}

        has_num = bool(keys & {
            "numero", "num", "no", "n", "runner_no", "numpmu", "num_pmu", "numeropmu"
        })
        has_name = bool(keys & {
            "cheval", "nom", "name", "horse", "runner", "nomcheval", "nom_cheval", "libelle"
        })

        return has_num and has_name

    def walk(obj, path="", depth=0):
        if depth > 7:
            return

        if isinstance(obj, list):
            runner_like = [x for x in obj if looks_like_runner(x)]
            if len(runner_like) >= 3:
                found.append({
                    "path": path,
                    "rows": runner_like,
                    "rows_count": len(runner_like),
                })

            for i, x in enumerate(obj[:100]):
                walk(x, f"{path}[{i}]", depth + 1)

        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}" if path else str(k), depth + 1)

    walk(value)

    if found:
        found_sorted = sorted(found, key=lambda x: x.get("rows_count", 0), reverse=True)
        return {
            "ok": True,
            "reason": "JSON_STRUCTURED_RUNNER_LIST_FOUND",
            "source_path": found_sorted[0].get("path", "ND"),
            "raw_runners": found_sorted[0].get("rows", []),
            "all_matches_sample": [
                {"path": x.get("path"), "rows_count": x.get("rows_count")}
                for x in found_sorted[:10]
            ],
        }

    return {
        "ok": False,
        "reason": "JSON_STRUCTURED_RUNNER_LIST_ABSENT",
        "source_path": "ND",
        "raw_runners": [],
        "all_matches_sample": [],
    }


def _p382_extract_json_objects_from_html(text):
    """
    Extraction prudente de JSON embarqu?.
    Ne tente pas de parser tout le HTML comme donn?es de course si structure absente.
    """
    import json
    import re

    raw = str(text or "")
    candidates = []

    # Scripts JSON purs.
    for m in re.finditer(
        r'<script[^>]+type=["\']application/json["\'][^>]*>(?P<body>.*?)</script>',
        raw,
        flags=re.I | re.S,
    ):
        body = m.group("body").strip()
        if body:
            candidates.append(body)

    # __NEXT_DATA__ / state fr?quents.
    for m in re.finditer(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(?P<body>.*?)</script>',
        raw,
        flags=re.I | re.S,
    ):
        body = m.group("body").strip()
        if body:
            candidates.append(body)

    parsed = []

    for body in candidates[:20]:
        try:
            parsed.append(json.loads(body))
        except Exception:
            continue

    return parsed


def _p382_extract_runners_from_html_table(text):
    """
    Fallback tr?s prudent sur HTML : ne valide que si des lignes semblent avoir numero + nom.
    """
    import re

    raw = str(text or "")
    rows = []

    tr_blocks = re.findall(r"<tr\b[^>]*>(.*?)</tr>", raw, flags=re.I | re.S)

    for tr in tr_blocks:
        clean = re.sub(r"<script\b.*?</script>", " ", tr, flags=re.I | re.S)
        clean = re.sub(r"<style\b.*?</style>", " ", clean, flags=re.I | re.S)
        cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", clean, flags=re.I | re.S)

        values = []
        for cell in cells:
            txt = re.sub(r"<[^>]+>", " ", cell)
            txt = re.sub(r"\s+", " ", txt).strip()
            if txt:
                values.append(txt)

        if len(values) < 2:
            continue

        numero = values[0]
        cheval = values[1]

        # Prudence : numero court + nom non vide.
        if _p37_to_int_or_none(numero) is not None and len(str(cheval).strip()) >= 2:
            rows.append({
                "numero": numero,
                "cheval": cheval,
                "statut": "PARTANT",
                "is_np": "non",
            })

    if len(rows) >= 3:
        return {
            "ok": True,
            "reason": "HTML_TABLE_RUNNER_LIST_FOUND",
            "source_path": "html.tr.td",
            "raw_runners": rows,
        }

    return {
        "ok": False,
        "reason": "HTML_TABLE_RUNNER_LIST_ABSENT",
        "source_path": "ND",
        "raw_runners": [],
    }



# V0_9_31B2_8_3_FIX2_ADD_DETAIL_URL_DISCOVERY_ROUTE

# V0_9_31B2_8_4_LETROT_RAW_HTML_AUDIT_CONTRACT
@app.get("/action/letrot-raw-html-audit-preview", dependencies=[Depends(require_api_key)])
def action_letrot_raw_html_audit_preview():
    """
    GPI V0_9_31B2.8.4
    Audit lecture seule du HTML brut LeTrot disponible c?t? backend.

    Ne scrape pas les partants.
    Ne construit pas d?URL artificielle.
    N??crit aucun fichier officiel.
    Strict toujours ferm?.
    """
    from datetime import datetime, timezone
    import re

    checked_at_utc = datetime.now(timezone.utc).isoformat()

    fetch_payload = {}
    fetch_error = "ND"

    # On tente les fonctions de fetch existantes sans supposer leur nom exact.
    fetch_fn_candidates = [
        "_p32_fetch_letrot_today_html",
        "_p32_letrot_fetch_today_html",
        "_p32_fetch_letrot_today_index",
        "_p32_fetch_letrot_html",
    ]

    for fn_name in fetch_fn_candidates:
        fn = globals().get(fn_name)
        if callable(fn):
            try:
                candidate = fn()
                if isinstance(candidate, dict):
                    fetch_payload = candidate
                    fetch_error = "ND"
                    break
            except Exception as exc:
                fetch_error = f"{fn_name}:{type(exc).__name__}:{exc}"

    # Fallback : lire le contrat public d?j? expos? par l?index preview.
    letrot_index = {}
    try:
        letrot_index = action_letrot_today_index_preview()
    except Exception as exc:
        letrot_index = {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }

    raw_html = ""

    if isinstance(fetch_payload, dict):
        for key in [
            "html",
            "raw_html",
            "html_text",
            "text",
            "content",
            "body",
            "response_text",
        ]:
            value = fetch_payload.get(key)
            if isinstance(value, str) and value.strip():
                raw_html = value
                break

    if not raw_html and isinstance(letrot_index, dict):
        for key in [
            "raw_html",
            "html",
            "html_text",
            "raw_text",
            "text",
            "raw_text_sample",
        ]:
            value = letrot_index.get(key)
            if isinstance(value, str) and value.strip():
                raw_html = value
                break

    hrefs = []
    href_count_total = 0

    if raw_html:
        href_re = re.compile(
            r'<a\b[^>]*?href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<label>.*?)</a>',
            flags=re.I | re.S,
        )

        for m in href_re.finditer(raw_html):
            href_count_total += 1

            if len(hrefs) >= 50:
                continue

            label_html = m.group("label") or ""
            label = re.sub(r"<[^>]+>", " ", label_html)
            label = re.sub(r"\s+", " ", label).strip()

            hrefs.append({
                "href": m.group("href"),
                "label": label,
            })

    plausible_course_hrefs = []

    for item in hrefs:
        href_l = str(item.get("href", "")).lower()
        label_l = str(item.get("label", "")).lower()

        if (
            "course" in href_l
            or "courses" in href_l
            or "reunion" in href_l
            or "partant" in href_l
            or "programme" in href_l
            or "prix" in label_l
            or "course" in label_l
        ):
            plausible_course_hrefs.append(item)

    fetch_payload_keys = sorted(list(fetch_payload.keys())) if isinstance(fetch_payload, dict) else []
    letrot_index_keys = sorted(list(letrot_index.keys())) if isinstance(letrot_index, dict) else []

    return {
        "ok": True,
        "phase": "letrot_raw_html_audit_preview",
        "preview_version": "letrot_raw_html_audit_preview_v0_9_31b2_8_4",
        "checked_at_utc": checked_at_utc,
        "fetch_function_candidates_checked": fetch_fn_candidates,
        "fetch_payload_available_oui_non": "oui" if bool(fetch_payload) else "non",
        "fetch_payload_keys": fetch_payload_keys,
        "fetch_error": fetch_error,
        "letrot_index_ok": letrot_index.get("ok", False) if isinstance(letrot_index, dict) else False,
        "letrot_index_keys": letrot_index_keys,
        "raw_html_available_oui_non": "oui" if bool(raw_html) else "non",
        "raw_html_chars": len(raw_html),
        "href_count_total": href_count_total,
        "href_sample": hrefs[:20],
        "plausible_course_href_count": len(plausible_course_hrefs),
        "plausible_course_href_sample": plausible_course_hrefs[:20],
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "Segment_ROI": "NO_BET",
        "doctrine": (
            "Audit HTML LeTrot lecture seule. Aucun partant invent?. "
            "Aucun fichier officiel ?crit. Strict ferm?."
        ),
    }



# V0_9_31B2_8_5_FEED_RAW_HTML_HREFS_TO_DISCOVERY
def _p385_fetch_letrot_raw_html_for_discovery():
    """
    R?cup?re le HTML brut LeTrot d?j? accessible backend.
    Lecture seule.
    """
    fetch_fn_candidates = [
        "_p32_fetch_letrot_today_html",
        "_p32_letrot_fetch_today_html",
        "_p32_fetch_letrot_today_index",
        "_p32_fetch_letrot_html",
    ]

    errors = []

    for fn_name in fetch_fn_candidates:
        fn = globals().get(fn_name)

        if not callable(fn):
            errors.append(f"{fn_name}:NOT_CALLABLE")
            continue

        try:
            payload = fn()
        except Exception as exc:
            errors.append(f"{fn_name}:{type(exc).__name__}:{exc}")
            continue

        if not isinstance(payload, dict):
            errors.append(f"{fn_name}:PAYLOAD_NOT_DICT")
            continue

        for key in ["html", "raw_html", "html_text", "text", "content", "body", "response_text"]:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return {
                    "ok": True,
                    "source_function": fn_name,
                    "source_key": key,
                    "raw_html": value,
                    "raw_html_chars": len(value),
                    "errors": errors,
                }

        errors.append(f"{fn_name}:NO_HTML_KEY")

    return {
        "ok": False,
        "source_function": "ND",
        "source_key": "ND",
        "raw_html": "",
        "raw_html_chars": 0,
        "errors": errors,
    }


def _p385_extract_letrot_course_hrefs_from_raw_html(raw_html):
    """
    Extrait les hrefs /courses/YYYY-MM-DD/<meeting_id>/<course_no> observ?s.
    Ne construit pas d?URL artificielle hors conversion chemin relatif letrot.com.
    """
    import re
    from html import unescape

    raw = str(raw_html or "")
    out = []
    seen = set()

    a_re = re.compile(
        r'<a\b[^>]*?href=["\'](?P<href>/courses/\d{4}-\d{2}-\d{2}/\d+/\d+)["\'][^>]*>(?P<label>.*?)</a>',
        flags=re.I | re.S,
    )

    for m in a_re.finditer(raw):
        href = unescape(m.group("href") or "").strip()
        label_html = m.group("label") or ""
        label = re.sub(r"<[^>]+>", " ", label_html)
        label = re.sub(r"\s+", " ", unescape(label)).strip()

        if not href or href in seen:
            continue

        seen.add(href)

        parts = href.strip("/").split("/")
        # courses / YYYY-MM-DD / meeting_id / course_no
        date = parts[1] if len(parts) >= 4 else "ND"
        meeting_id = parts[2] if len(parts) >= 4 else "ND"
        course_no = parts[3] if len(parts) >= 4 else "ND"

        time_match = re.search(r"\b(?P<hour>\d{1,2})[:h](?P<minute>\d{2})\b", label)
        scheduled_time = "ND"
        if time_match:
            scheduled_time = f"{int(time_match.group('hour')):02d}:{time_match.group('minute')}"

        out.append({
            "href": href,
            "path": href,
            "url": "https://www.letrot.com" + href,
            "date": date,
            "meeting_id": meeting_id,
            "course_no": course_no,
            "course_no_int": _p37_to_int_or_none(course_no),
            "scheduled_time": scheduled_time,
            "label": label,
        })

    return out


def _p385_match_course_href_by_course_no_and_time(candidate, hrefs):
    """
    Match prudent :
    - course_no exact obligatoire ;
    - scheduled_time exact fortement prioritaire ;
    - si plusieurs Cx existent sans heure exacte, refus.
    """
    if not isinstance(candidate, dict):
        candidate = {}

    if not isinstance(hrefs, list):
        hrefs = []

    c_course_no = _p37_to_int_or_none(
        candidate.get("course_no_int")
        or candidate.get("course_no")
        or candidate.get("num_course")
    )

    c_time = str(candidate.get("scheduled_time") or "ND").strip()

    scored = []

    for item in hrefs:
        if not isinstance(item, dict):
            continue

        i_course_no = _p37_to_int_or_none(item.get("course_no_int") or item.get("course_no"))
        i_time = str(item.get("scheduled_time") or "ND").strip()

        score = 0
        reasons = []

        if c_course_no is not None and i_course_no == c_course_no:
            score += 5
            reasons.append("COURSE_NO_MATCH")
        else:
            continue

        if c_time not in ("", "ND") and i_time == c_time:
            score += 8
            reasons.append("SCHEDULED_TIME_MATCH")

        if score > 0:
            scored.append({
                "score": score,
                "reason_codes": reasons,
                "item": item,
            })

    if not scored:
        return {
            "ok": False,
            "reason": "NO_RAW_HTML_HREF_MATCH_COURSE",
            "detail_url": "ND",
            "detail_path": "ND",
            "detail_label": "ND",
            "detail_match_score": 0,
            "detail_match_reason_codes": [],
            "scored_hrefs_sample": [],
        }

    scored = sorted(scored, key=lambda x: x.get("score", 0), reverse=True)
    best = scored[0]
    best_score = best.get("score", 0)

    # S?curit? : sans match heure, plusieurs C7 possibles => refus.
    best_reasons = best.get("reason_codes", [])
    if "SCHEDULED_TIME_MATCH" not in best_reasons:
        same_score_count = sum(1 for x in scored if x.get("score", 0) == best_score)
        if same_score_count > 1:
            return {
                "ok": False,
                "reason": "AMBIGUOUS_COURSE_NO_ONLY_HREF_MATCH",
                "detail_url": "ND",
                "detail_path": "ND",
                "detail_label": "ND",
                "detail_match_score": best_score,
                "detail_match_reason_codes": best_reasons,
                "scored_hrefs_sample": [
                    {
                        "score": x.get("score", 0),
                        "reason_codes": x.get("reason_codes", []),
                        "url": (x.get("item", {}) or {}).get("url", "ND"),
                        "label": (x.get("item", {}) or {}).get("label", "ND"),
                    }
                    for x in scored[:10]
                ],
            }

    item = best.get("item", {}) or {}

    return {
        "ok": True,
        "reason": "RAW_HTML_HREF_MATCHED_BY_COURSE_NO_AND_TIME",
        "detail_url": item.get("url", "ND"),
        "detail_path": item.get("path", "ND"),
        "detail_label": item.get("label", "ND"),
        "detail_match_score": best_score,
        "detail_match_reason_codes": best_reasons,
        "scored_hrefs_sample": [
            {
                "score": x.get("score", 0),
                "reason_codes": x.get("reason_codes", []),
                "url": (x.get("item", {}) or {}).get("url", "ND"),
                "label": (x.get("item", {}) or {}).get("label", "ND"),
                "course_no": (x.get("item", {}) or {}).get("course_no", "ND"),
                "scheduled_time": (x.get("item", {}) or {}).get("scheduled_time", "ND"),
            }
            for x in scored[:10]
        ],
    }


@app.get("/action/letrot-detail-url-discovery-preview", dependencies=[Depends(require_api_key)])
def action_letrot_detail_url_discovery_preview():
    """
    GPI V0_9_31B2.8.3-FIX2
    Diagnostic d?URLs d?tail LeTrot observ?es.

    Lecture seule.
    Ne construit pas d?URL artificielle.
    Ne scrape pas de partants.
    Strict toujours ferm?.
    """
    from datetime import datetime, timezone

    checked_at_utc = datetime.now(timezone.utc).isoformat()

    try:
        source_preview = action_zeturf_course_level_source_preview()
    except Exception as exc:
        source_preview = {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }

    candidate = {}

    if isinstance(source_preview, dict):
        compare = source_preview.get("zeturf_letrot_course_compare", {})
        if isinstance(compare, dict):
            for key in ["selected_course", "best_candidate", "candidate", "matched_candidate"]:
                value = compare.get(key)
                if isinstance(value, dict) and value:
                    candidate = value
                    break

            if not candidate:
                scored = (
                    compare.get("scored_candidates")
                    or compare.get("candidates")
                    or compare.get("candidate_courses")
                    or []
                )
                if isinstance(scored, list) and scored:
                    first = scored[0]
                    if isinstance(first, dict):
                        maybe = first.get("candidate", first)
                        if isinstance(maybe, dict):
                            candidate = maybe

    try:
        letrot_index = action_letrot_today_index_preview()
    except Exception as exc:
        letrot_index = {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }

    observed_urls = []
    observed_url_count_from_candidates = 0

    # 1) Priorit? aux champs detail_url d?j? inject?s par B2.8.3-FIX1 dans les candidates.
    if isinstance(letrot_index, dict):
        for meeting in letrot_index.get("meetings", []) or []:
            if not isinstance(meeting, dict):
                continue

            for course in meeting.get("courses", []) or []:
                if not isinstance(course, dict):
                    continue

                detail_url = course.get("detail_url", "ND")
                if detail_url not in (None, "", "ND"):
                    observed_url_count_from_candidates += 1
                    observed_urls.append({
                        "source": "letrot_today_index.meetings.courses.detail_url",
                        "course_no": course.get("course_no", "ND"),
                        "course_no_int": course.get("course_no_int", "ND"),
                        "scheduled_time": course.get("scheduled_time", "ND"),
                        "detail_url": detail_url,
                        "detail_path": course.get("detail_path", "ND"),
                        "detail_label": course.get("detail_label", "ND"),
                        "detail_url_match_status": course.get("detail_url_match_status", "ND"),
                        "detail_url_match_score": course.get("detail_url_match_score", 0),
                        "detail_url_match_reason_codes": course.get("detail_url_match_reason_codes", []),
                        "observed_href_count": course.get("observed_href_count", 0),
                    })

    # 2) Fallback : si la route expose un HTML brut, utiliser le helper B2.8.3 existant si pr?sent.
    raw_html = ""
    if isinstance(letrot_index, dict):
        raw_html = (
            letrot_index.get("raw_html")
            or letrot_index.get("html")
            or letrot_index.get("raw_text")
            or ""
        )

    if raw_html:
        try:
            href_items = _p383_extract_observed_detail_urls_from_letrot_html(raw_html)
        except Exception as exc:
            href_items = []
            raw_html_extract_error = f"{type(exc).__name__}:{exc}"
        else:
            raw_html_extract_error = "ND"

        for item in href_items:
            if isinstance(item, dict):
                observed_urls.append({
                    "source": "raw_html.href",
                    "detail_url": item.get("url", "ND"),
                    "detail_path": item.get("path", "ND"),
                    "detail_label": item.get("label", "ND"),
                    "href": item.get("href", "ND"),
                })
    else:
        raw_html_extract_error = "RAW_HTML_ABSENT"

    matched_detail_url = {
        "ok": False,
        "reason": "NO_OBSERVED_DETAIL_URL_MATCH_COURSE",
        "detail_url": "ND",
        "detail_path": "ND",
        "detail_label": "ND",
        "detail_match_score": 0,
        "detail_match_reason_codes": [],
    }

    # V0_9_31B2_8_5_FEED_RAW_HTML_HREFS_TO_DISCOVERY
    raw_html_fetch_for_discovery = _p385_fetch_letrot_raw_html_for_discovery()
    raw_html_course_hrefs = []

    if raw_html_fetch_for_discovery.get("ok"):
        raw_html_course_hrefs = _p385_extract_letrot_course_hrefs_from_raw_html(
            raw_html_fetch_for_discovery.get("raw_html", "")
        )

        raw_match = _p385_match_course_href_by_course_no_and_time(candidate, raw_html_course_hrefs)

        if raw_match.get("ok"):
            matched_detail_url = raw_match

            if isinstance(candidate, dict):
                candidate["detail_url"] = raw_match.get("detail_url", "ND")
                candidate["detail_path"] = raw_match.get("detail_path", "ND")
                candidate["detail_label"] = raw_match.get("detail_label", "ND")
                candidate["detail_url_match_status"] = raw_match.get("reason", "ND")
                candidate["detail_url_match_score"] = raw_match.get("detail_match_score", 0)
                candidate["detail_url_match_reason_codes"] = raw_match.get("detail_match_reason_codes", [])

            observed_urls.append({
                "source": "raw_html_href_course_no_time_match",
                "detail_url": raw_match.get("detail_url", "ND"),
                "detail_path": raw_match.get("detail_path", "ND"),
                "detail_label": raw_match.get("detail_label", "ND"),
                "detail_url_match_status": raw_match.get("reason", "ND"),
                "detail_url_match_score": raw_match.get("detail_match_score", 0),
                "detail_url_match_reason_codes": raw_match.get("detail_match_reason_codes", []),
            })

    # 3) Si candidate a d?j? un detail_url, c?est le meilleur signal.
    if isinstance(candidate, dict) and candidate.get("detail_url") not in (None, "", "ND"):
        matched_detail_url = {
            "ok": True,
            "reason": "CANDIDATE_DETAIL_URL_PRESENT",
            "detail_url": candidate.get("detail_url", "ND"),
            "detail_path": candidate.get("detail_path", "ND"),
            "detail_label": candidate.get("detail_label", "ND"),
            "detail_match_score": candidate.get("detail_url_match_score", 0),
            "detail_match_reason_codes": candidate.get("detail_url_match_reason_codes", []),
        }
    else:
        # 4) Sinon, chercher dans observed_urls selon course_no candidate.
        try:
            candidate_course_no = _p37_to_int_or_none(
                candidate.get("course_no_int")
                or candidate.get("course_no")
                or candidate.get("num_course")
            )
        except Exception:
            candidate_course_no = None

        if candidate_course_no is not None:
            for item in observed_urls:
                item_no = _p37_to_int_or_none(
                    item.get("course_no_int")
                    or item.get("course_no")
                )
                if item_no == candidate_course_no and item.get("detail_url") not in (None, "", "ND"):
                    matched_detail_url = {
                        "ok": True,
                        "reason": "OBSERVED_DETAIL_URL_MATCHED_BY_COURSE_NO",
                        "detail_url": item.get("detail_url", "ND"),
                        "detail_path": item.get("detail_path", "ND"),
                        "detail_label": item.get("detail_label", "ND"),
                        "detail_match_score": item.get("detail_url_match_score", 0),
                        "detail_match_reason_codes": item.get("detail_url_match_reason_codes", []),
                    }
                    break

    return {
        "ok": True,
        "phase": "letrot_detail_url_discovery_preview",
        "preview_version": "letrot_detail_url_discovery_preview_v0_9_31b2_8_3_fix2",
        "checked_at_utc": checked_at_utc,
        "source_preview_ok": source_preview.get("ok", False) if isinstance(source_preview, dict) else False,
        "candidate": candidate,
        "letrot_index_ok": letrot_index.get("ok", False) if isinstance(letrot_index, dict) else False,
        "letrot_index_has_raw_html_oui_non": "oui" if raw_html else "non",
        "letrot_index_raw_html_chars": len(raw_html),
        "raw_html_extract_error": raw_html_extract_error,
        "raw_html_fetch_for_discovery": {
            "ok": raw_html_fetch_for_discovery.get("ok", False),
            "source_function": raw_html_fetch_for_discovery.get("source_function", "ND"),
            "source_key": raw_html_fetch_for_discovery.get("source_key", "ND"),
            "raw_html_chars": raw_html_fetch_for_discovery.get("raw_html_chars", 0),
            "errors": raw_html_fetch_for_discovery.get("errors", []),
        },
        "raw_html_course_href_count": len(raw_html_course_hrefs),
        "raw_html_course_hrefs_sample": raw_html_course_hrefs[:20],
        "observed_url_count_from_candidates": observed_url_count_from_candidates,
        "observed_detail_url_count": len(observed_urls),
        "observed_detail_urls_sample": observed_urls[:20],
        "matched_detail_url": matched_detail_url,
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "Segment_ROI": "NO_BET",
        "doctrine": (
            "Discovery URL d?tail LeTrot observ?e uniquement. "
            "Aucune URL artificielle. Aucun partant invent?. Strict ferm?."
        ),
    }



# V0_9_31B2_8_6_FEED_DISCOVERED_DETAIL_URL_INTO_CAPTURE
def _p386_apply_discovered_detail_url_to_selected_course(selected_course):
    """
    Injecte dans selected_course une detail_url observ?e par la discovery.
    Ne construit aucune URL.
    """
    if not isinstance(selected_course, dict):
        selected_course = {}

    detail_url_discovery = {}

    try:
        detail_url_discovery = action_letrot_detail_url_discovery_preview()
    except Exception as exc:
        detail_url_discovery = {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }

    matched = {}
    if isinstance(detail_url_discovery, dict):
        matched = detail_url_discovery.get("matched_detail_url", {}) or {}

    applied = False
    reason = "NO_DISCOVERED_DETAIL_URL_APPLIED"

    if isinstance(matched, dict) and matched.get("ok") and matched.get("detail_url") not in (None, "", "ND"):
        selected_course["detail_url"] = matched.get("detail_url", "ND")
        selected_course["detail_path"] = matched.get("detail_path", "ND")
        selected_course["detail_label"] = matched.get("detail_label", "ND")
        selected_course["detail_url_match_status"] = matched.get("reason", "ND")
        selected_course["detail_url_match_score"] = matched.get("detail_match_score", 0)
        selected_course["detail_url_match_reason_codes"] = matched.get("detail_match_reason_codes", [])
        selected_course["detail_url_source"] = "letrot_detail_url_discovery_preview"
        applied = True
        reason = "DISCOVERED_DETAIL_URL_APPLIED"

    return {
        "selected_course": selected_course,
        "detail_url_discovery": detail_url_discovery,
        "matched_detail_url": matched,
        "applied_oui_non": "oui" if applied else "non",
        "reason": reason,
    }



# V0_9_31B2_8_7_HARDEN_DETAIL_RUNNER_TABLE_EXTRACTION
def _p387_clean_runner_name(value):
    """
    Nettoie un nom cheval issu du HTML LeTrot.
    V0_9_31B2.8.8 : durcit la suppression des IDs num?riques finaux
    m?me lorsqu'ils sont suivis de glyphes parasites.
    Ne fabrique pas de nom : si la valeur est inutilisable, renvoie ND.
    """
    import re
    from html import unescape

    txt = str(value or "").strip()
    txt = unescape(txt)
    txt = re.sub(r"<[^>]+>", " ", txt)

    # Normalisation des apostrophes / entit?s fr?quentes.
    txt = txt.replace("&#039;", "'")
    txt = txt.replace("&apos;", "'")

    # Supprime glyphes parasites connus avant et apr?s nettoyage.
    txt = txt.replace("?", " ")
    txt = txt.replace("?", " ")
    txt = txt.replace("\ufffd", " ")

    txt = re.sub(r"\s+", " ", txt).strip()

    # Supprime une s?quence finale : nombre d'identification + ?ventuels glyphes non lettres.
    # Exemples : "KHEOPS DU VAAL 9187 ?" -> "KHEOPS DU VAAL"
    #            "KELLE BELLE JASMA 974 ?" -> "KELLE BELLE JASMA"
    txt = re.sub(r"\s+\d{2,6}(?:\s*[^A-Za-z?-??-??-?0-9'\-]+)*\s*$", "", txt).strip()

    # Deuxi?me passage au cas o? un glyphe ?tait coll? ? l'ID.
    txt = re.sub(r"\s+\d{2,6}\s*$", "", txt).strip()

    # Supprime r?sidus finaux non utiles.
    txt = re.sub(r"[^A-Za-z?-??-??-?0-9'\- ]+$", "", txt).strip()
    txt = re.sub(r"\s+", " ", txt).strip()

    if not txt:
        return "ND"

    # Rejet : valeur num?rique seule ou quasi num?rique.
    compact = re.sub(r"[\s\W_]+", "", txt, flags=re.UNICODE)
    if compact.isdigit():
        return "ND"

    # Rejet : pas de lettre.
    if not re.search(r"[A-Za-z?-??-??-?]", txt):
        return "ND"

    return txt


def _p387_normalize_runner_numero(value):
    """
    N?accepte que les num?ros de partants strictement num?riques >= 1.
    Rejette 0, 2e, 3e, rangs d?arriv?e, ND, etc.
    """
    raw = str(value or "").strip()

    if not raw.isdigit():
        return None

    try:
        no = int(raw)
    except Exception:
        return None

    if no < 1 or no > 99:
        return None

    return no


def _p387_filter_hardened_runner_rows(rows):
    """
    Filtre les lignes parasites extraites de tableaux HTML.
    Conserve uniquement les lignes qui ressemblent ? de vrais partants :
    - numero entier >= 1 ;
    - cheval textuel avec au moins une lettre ;
    - numero non dupliqu?.
    """
    if not isinstance(rows, list):
        rows = []

    valid = []
    rejected = []
    seen_numero = set()

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            rejected.append({
                "index": idx,
                "reason": "ROW_NOT_DICT",
                "row": row,
            })
            continue

        numero_raw = row.get("numero", "ND")
        cheval_raw = row.get("cheval", "ND")

        numero_int = _p387_normalize_runner_numero(numero_raw)
        cheval_clean = _p387_clean_runner_name(cheval_raw)

        reasons = []

        if numero_int is None:
            reasons.append("INVALID_NUMERO")

        if cheval_clean == "ND":
            reasons.append("INVALID_CHEVAL")

        if numero_int is not None and numero_int in seen_numero:
            reasons.append("DUPLICATE_NUMERO")

        if reasons:
            rejected.append({
                "index": idx,
                "reason": ",".join(reasons),
                "numero": numero_raw,
                "cheval": cheval_raw,
            })
            continue

        seen_numero.add(numero_int)

        clean_row = dict(row)
        clean_row["numero"] = str(numero_int)
        clean_row["cheval"] = cheval_clean
        clean_row["runner_row_filter_version"] = "hardened_detail_runner_table_filter_v0_9_31b2_8_7"
        clean_row["runner_row_filter_status"] = "ACCEPTED_NUMERIC_NUMERO_TEXTUAL_CHEVAL"

        # Source plus pr?cise que l?ancien libell? g?n?rique.
        clean_row["official_source"] = "LETROT_DETAIL_HTML_TABLE_FILTERED"
        clean_row["statut_source"] = "LETROT_DETAIL_HTML_TABLE_FILTERED"

        # On ?vite HIGH tant que le parser HTML table n?est pas audit? plus finement.
        clean_row["np_statut_confidence"] = "MEDIUM"

        valid.append(clean_row)

    valid = sorted(valid, key=lambda r: int(r.get("numero", "999")))

    audit = {
        "filter_version": "hardened_detail_runner_table_filter_v0_9_31b2_8_7",
        "raw_rows_count": len(rows),
        "accepted_rows_count": len(valid),
        "rejected_rows_count": len(rejected),
        "accepted_numero_min": int(valid[0]["numero"]) if valid else None,
        "accepted_numero_max": int(valid[-1]["numero"]) if valid else None,
        "accepted_numeros": [r.get("numero", "ND") for r in valid],
        "rejected_rows_sample": rejected[:20],
        "status": "FILTERED_VALID_RUNNERS" if valid else "NO_VALID_RUNNER_AFTER_FILTER",
    }

    return {
        "rows": valid,
        "audit": audit,
    }


def _p387_validate_hardened_runner_rows(rows, filter_audit):
    """
    Validation renforc?e post-filtrage.
    """
    base_validation = _p38_validate_official_runner_rows(rows)

    reason_codes = []
    ok = True

    if not base_validation.get("ok"):
        ok = False
        reason_codes.extend(base_validation.get("reason_codes", []))

    rows_count = len(rows) if isinstance(rows, list) else 0

    if rows_count < 3:
        ok = False
        reason_codes.append("TOO_FEW_VALID_RUNNER_ROWS")

    numeros = []
    for row in rows:
        no = _p387_normalize_runner_numero(row.get("numero", "ND")) if isinstance(row, dict) else None
        if no is None:
            ok = False
            reason_codes.append("INVALID_NUMERO_AFTER_FILTER")
        else:
            numeros.append(no)

        cheval = _p387_clean_runner_name(row.get("cheval", "ND")) if isinstance(row, dict) else "ND"
        if cheval == "ND":
            ok = False
            reason_codes.append("INVALID_CHEVAL_AFTER_FILTER")

    if len(numeros) != len(set(numeros)):
        ok = False
        reason_codes.append("DUPLICATE_NUMERO_AFTER_FILTER")

    if not reason_codes:
        reason_codes.append("HARDENED_RUNNER_ROWS_COMPLETE")

    return {
        "ok": ok,
        "status": "HARDENED_RUNNER_ROWS_COMPLETE" if ok else "HARDENED_RUNNER_ROWS_INVALID",
        "reason_codes": sorted(set(reason_codes)),
        "rows_count": rows_count,
        "base_validation": base_validation,
        "filter_audit": filter_audit,
    }


@app.post("/action/official-runner-status-letrot-detail-capture", dependencies=[Depends(require_api_key)])
def action_official_runner_status_letrot_detail_capture():
    """
    GPI V0_9_31B2.8.2
    Tente une capture officielle des partants depuis une page/detail LeTrot si une URL exploitable existe.

    Ne construit pas d'URL artificielle.
    Ne cr?e aucun partant depuis ZEturf.
    N'?crase pas official_runner_status_latest.csv si capture KO.
    Strict toujours ferm?.
    """
    from datetime import datetime, timezone
    from pathlib import Path
    import json

    checked_at_utc = datetime.now(timezone.utc).isoformat()

    try:
        base_capture = action_official_runner_status_letrot_capture()
    except Exception as exc:
        base_capture = {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }

    if not isinstance(base_capture, dict):
        base_capture = {}

    context_payload = {
        "course_uid": base_capture.get("course_uid", "ND"),
        "reunion_no": (base_capture.get("context_course_fields", {}) or {}).get("reunion_no", "ND"),
        "course_no": (base_capture.get("context_course_fields", {}) or {}).get("course_no", "ND"),
        "scheduled_time": (base_capture.get("context_course_fields", {}) or {}).get("scheduled_time", "ND"),
        "race_name": (base_capture.get("context_course_fields", {}) or {}).get("race_name", "ND"),
    }

    # V0_9_31B2_8_10_FIX2B_HYDRATE_DETAIL_CAPTURE_CONTEXT
    detail_context_hydration = {
        "ok": False,
        "version": "detail_capture_context_hydration_v0_9_31b2_8_10_fix2b",
        "reason": "NOT_APPLIED",
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
    }

    try:
        if context_payload.get("course_uid") in (None, "", "ND"):
            latest_ctx = action_latest_h5_auto_context()
            if isinstance(latest_ctx, dict) and latest_ctx.get("ok"):
                for key in ["course_uid", "course_slug", "runners_count", "reunion_no", "course_no", "scheduled_time", "race_name"]:
                    value = latest_ctx.get(key)
                    if value not in (None, "", "ND") and context_payload.get(key) in (None, "", "ND"):
                        context_payload[key] = value

                detail_context_hydration = {
                    "ok": True,
                    "version": "detail_capture_context_hydration_v0_9_31b2_8_10_fix2b",
                    "reason": "HYDRATED_FROM_LATEST_H5_AUTO_CONTEXT",
                    "course_uid": context_payload.get("course_uid", "ND"),
                    "course_slug": context_payload.get("course_slug", "ND"),
                    "runners_count": context_payload.get("runners_count", "ND"),
                    "strict_allowed_oui_non": "non",
                    "ticket_strict": "NO_BET",
                }
            else:
                detail_context_hydration = {
                    "ok": False,
                    "version": "detail_capture_context_hydration_v0_9_31b2_8_10_fix2b",
                    "reason": "LATEST_H5_AUTO_CONTEXT_UNAVAILABLE",
                    "strict_allowed_oui_non": "non",
                    "ticket_strict": "NO_BET",
                }

        context_payload = _p372_force_enriched_course_context(context_payload)

    except Exception as exc:
        detail_context_hydration = {
            "ok": False,
            "version": "detail_capture_context_hydration_v0_9_31b2_8_10_fix2b",
            "reason": f"HYDRATION_ERROR:{type(exc).__name__}:{exc}",
            "strict_allowed_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    selected_course = base_capture.get("selected_course", {}) or {}
    if not isinstance(selected_course, dict):
        selected_course = {}

    # V0_9_31B2_8_6_FEED_DISCOVERED_DETAIL_URL_INTO_CAPTURE
    discovered_detail_url_application = _p386_apply_discovered_detail_url_to_selected_course(selected_course)
    selected_course = discovered_detail_url_application.get("selected_course", selected_course)

    if not isinstance(selected_course, dict):
        selected_course = {}

    possible_urls = _p382_collect_possible_detail_urls(selected_course)

    detail_fetch = {
        "ok": False,
        "reason": "DETAIL_URL_ABSENT",
        "url": "ND",
        "http_status": "ND",
        "content_type": "ND",
        "text_chars": 0,
        "error": "ND",
    }

    extraction = {
        "ok": False,
        "reason": "DETAIL_URL_ABSENT",
        "source": "ND",
        "raw_runners": [],
    }

    if possible_urls:
        chosen_url = possible_urls[0].get("url", "ND")
        fetched = _p382_fetch_url_text(chosen_url)

        detail_fetch = {
            "ok": fetched.get("ok", False),
            "reason": "DETAIL_FETCH_OK" if fetched.get("ok") else "DETAIL_FETCH_FAILED",
            "url": chosen_url,
            "http_status": fetched.get("http_status", "ND"),
            "content_type": fetched.get("content_type", "ND"),
            "text_chars": len(fetched.get("text", "") or ""),
            "error": fetched.get("error", "ND"),
        }

        if fetched.get("ok"):
            json_objects = _p382_extract_json_objects_from_html(fetched.get("text", ""))

            for obj in json_objects:
                js_ext = _p382_extract_runners_from_json_like(obj)
                if js_ext.get("ok"):
                    extraction = {
                        "ok": True,
                        "reason": js_ext.get("reason", "JSON_STRUCTURED_RUNNER_LIST_FOUND"),
                        "source": js_ext.get("source_path", "json"),
                        "raw_runners": js_ext.get("raw_runners", []),
                        "json_matches_sample": js_ext.get("all_matches_sample", []),
                    }
                    break

            if not extraction.get("ok"):
                html_ext = _p382_extract_runners_from_html_table(fetched.get("text", ""))
                if html_ext.get("ok"):
                    extraction = {
                        "ok": True,
                        "reason": html_ext.get("reason", "HTML_TABLE_RUNNER_LIST_FOUND"),
                        "source": html_ext.get("source_path", "html"),
                        "raw_runners": html_ext.get("raw_runners", []),
                    }
                else:
                    extraction = {
                        "ok": False,
                        "reason": "DETAIL_RUNNER_LIST_ABSENT",
                        "source": "ND",
                        "raw_runners": [],
                    }
        else:
            extraction = {
                "ok": False,
                "reason": "DETAIL_FETCH_FAILED",
                "source": "ND",
                "raw_runners": [],
            }

    normalized_rows_raw = []

    if extraction.get("ok"):
        for raw_row in extraction.get("raw_runners", []):
            if isinstance(raw_row, dict):
                row = _p38_normalize_official_runner(raw_row, context_payload, selected_course)
                row["checked_at_utc"] = checked_at_utc
                normalized_rows_raw.append(row)

    # V0_9_31B2_8_7_HARDEN_DETAIL_RUNNER_TABLE_EXTRACTION
    hardened_filter = _p387_filter_hardened_runner_rows(normalized_rows_raw)
    normalized_rows = hardened_filter.get("rows", [])
    runner_row_filter_audit = hardened_filter.get("audit", {})

    validation = _p387_validate_hardened_runner_rows(normalized_rows, runner_row_filter_audit)

    uploads = {
        "latest": {
            "ok": False,
            "reason": "NOT_UPLOADED_NO_VALID_DETAIL_CAPTURE",
        },
        "course_scoped": {
            "ok": False,
            "reason": "NOT_UPLOADED_NO_VALID_DETAIL_CAPTURE",
        },
    }

    local_paths = {
        "latest": "ND",
        "course_scoped": "ND",
    }

    capture_status = "OFFICIAL_RUNNER_STATUS_DETAIL_NOT_CAPTURED"

    capture_rows_course_identity_enrichment = {
        "ok": False,
        "version": "capture_rows_course_identity_enrichment_v0_9_31b2_8_10_fix2b",
        "reason": "NOT_APPLIED_VALIDATION_KO",
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
    }

    if validation.get("ok"):
        capture_status = "OFFICIAL_RUNNER_STATUS_DETAIL_CAPTURED"

        capture_rows_course_identity_enrichment = _p3810_fix2_enrich_capture_rows_course_identity(
            normalized_rows,
            context_payload,
            selected_course,
        )
        normalized_rows = capture_rows_course_identity_enrichment.get("rows", normalized_rows)

        slug = "nd"
        try:
            slug = _p36_safe_slug(context_payload.get("course_uid", "ND"))
        except Exception:
            pass

        latest_path = Path("data/output/official_runner_status_latest.csv")
        course_path = Path("data/output/official_runner_status") / f"{slug}_official_runner_status.csv"

        local_paths["latest"] = _p38_write_csv_rows_utf8sig(latest_path, normalized_rows)
        local_paths["course_scoped"] = _p38_write_csv_rows_utf8sig(course_path, normalized_rows)

        uploads["latest"] = _p38_upload_file_to_gcs(
            latest_path,
            "gpi-lite/outputs/official_runner_status_latest.csv",
            content_type="text/csv",
        )
        uploads["course_scoped"] = _p38_upload_file_to_gcs(
            course_path,
            f"gpi-lite/outputs/official_runner_status/{slug}_official_runner_status.csv",
            content_type="text/csv",
        )

    diagnostic = {
        "ok": True,
        "phase": "official_runner_status_letrot_detail_capture",
        "capture_version": "official_runner_status_letrot_detail_capture_v0_9_31b2_8_2",
        "checked_at_utc": checked_at_utc,
        "course_uid": context_payload.get("course_uid", "ND"),
        "context_course_fields": context_payload,
        "detail_context_hydration": detail_context_hydration,
        "base_capture_status": base_capture.get("capture_status", "ND"),
        "base_selected_course_found_oui_non": base_capture.get("selected_course_found_oui_non", "ND"),
        "base_selected_course_source": base_capture.get("selected_course_source", "ND"),
        "selected_course": selected_course,
        "discovered_detail_url_application": discovered_detail_url_application,
        "possible_detail_urls": possible_urls,
        "possible_detail_url_count": len(possible_urls),
        "detail_fetch": detail_fetch,
        "runner_extraction_ok": extraction.get("ok", False),
        "runner_extraction_reason": extraction.get("reason", "ND"),
        "runner_extraction_source": extraction.get("source", "ND"),
        "raw_runner_count": len(extraction.get("raw_runners", [])),
        "normalized_runner_raw_count": len(normalized_rows_raw),
        "normalized_runner_count": len(normalized_rows),
        "runner_row_filter_audit": runner_row_filter_audit,
        "validation": validation,
        "capture_rows_course_identity_enrichment": capture_rows_course_identity_enrichment,
        "capture_status": capture_status,
        "local_paths": local_paths,
        "uploads": uploads,
        "official_runner_status_available_oui_non": "oui" if validation.get("ok") and (uploads.get("latest", {}) or {}).get("ok") else "non",
        "official_join_ok_oui_non": "oui" if validation.get("ok") and (uploads.get("latest", {}) or {}).get("ok") else "non",
        "official_latest_upload_required_for_join_oui_non": "oui",
        "matched_runner_status_sample": normalized_rows[:20],
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "Segment_ROI": "NO_BET",
        "doctrine": (
            "Capture detail LeTrot prudente. Aucun partant invente. "
            "Aucun ecrasement du latest officiel si extraction KO. Strict ferme."
        ),
    }

    try:
        Path("data/output").mkdir(parents=True, exist_ok=True)
        diag_path = Path("data/output/official_runner_status_letrot_detail_capture_latest.json")
        diag_path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
        diagnostic["diagnostic_local_path"] = str(diag_path)
    except Exception as exc:
        diagnostic["diagnostic_write_error"] = f"{type(exc).__name__}:{exc}"

    return diagnostic


@app.post("/action/official-runner-status-letrot-capture", dependencies=[Depends(require_api_key)])
def action_official_runner_status_letrot_capture():
    """
    GPI V0_9_31B2.8
    Capture prudente des partants/NP officiels depuis la course LeTrot match?e.

    N'?crit official_runner_status_latest.csv que si une liste structur?e
    de partants existe r?ellement dans selected_course ou payload associ?.

    Ne peut jamais ouvrir le strict.
    """
    from datetime import datetime, timezone
    from pathlib import Path
    import json

    checked_at_utc = datetime.now(timezone.utc).isoformat()

    try:
        context_payload = action_latest_h5_auto_context()
    except Exception as exc:
        context_payload = {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }

    if not isinstance(context_payload, dict):
        context_payload = {}

    try:
        context_payload = _p372_force_enriched_course_context(context_payload)
    except Exception:
        try:
            context_payload = _p361_apply_course_level_context_overlay(context_payload)
        except Exception:
            pass

    try:
        letrot_match = action_letrot_course_match_preview()
    except Exception as exc:
        letrot_match = {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }

    selected_course_pick = _p381_get_selected_course_for_runner_capture(letrot_match)
    selected_course = selected_course_pick.get("selected_course", {}) if isinstance(selected_course_pick, dict) else {}

    if not isinstance(selected_course, dict):
        selected_course = {}

    extraction = _p38_extract_runner_list_from_payload(selected_course)

    capture_status = "OFFICIAL_RUNNER_STATUS_NOT_CAPTURED"
    reason_codes = []

    if not isinstance(letrot_match, dict) or not letrot_match.get("ok"):
        reason_codes.append("LETROT_COURSE_MATCH_PREVIEW_UNAVAILABLE")

    if not selected_course:
        reason_codes.append("SELECTED_COURSE_ABSENT")
    else:
        reason_codes.append("SELECTED_COURSE_FOUND")

    if not extraction.get("ok"):
        reason_codes.append(extraction.get("reason", "STRUCTURED_RUNNER_LIST_ABSENT"))

    normalized_rows_raw = []

    if extraction.get("ok"):
        for raw_row in extraction.get("raw_runners", []):
            if isinstance(raw_row, dict):
                row = _p38_normalize_official_runner(raw_row, context_payload, selected_course)
                row["checked_at_utc"] = checked_at_utc
                normalized_rows_raw.append(row)

    # V0_9_31B2_8_7_HARDEN_DETAIL_RUNNER_TABLE_EXTRACTION
    hardened_filter = _p387_filter_hardened_runner_rows(normalized_rows_raw)
    normalized_rows = hardened_filter.get("rows", [])
    runner_row_filter_audit = hardened_filter.get("audit", {})

    validation = _p387_validate_hardened_runner_rows(normalized_rows, runner_row_filter_audit)

    uploads = {
        "latest": {
            "ok": False,
            "reason": "NOT_UPLOADED_NO_VALID_CAPTURE",
        },
        "course_scoped": {
            "ok": False,
            "reason": "NOT_UPLOADED_NO_VALID_CAPTURE",
        },
    }

    local_paths = {
        "latest": "ND",
        "course_scoped": "ND",
    }

    capture_rows_course_identity_enrichment = {
        "ok": False,
        "version": "capture_rows_course_identity_enrichment_v0_9_31b2_8_10_fix2b",
        "reason": "NOT_APPLIED_VALIDATION_KO",
        "strict_allowed_oui_non": "non",
        "ticket_strict": "NO_BET",
    }

    if validation.get("ok"):
        capture_status = "OFFICIAL_RUNNER_STATUS_CAPTURED"
        reason_codes.extend(validation.get("reason_codes", []))

        capture_rows_course_identity_enrichment = _p3810_fix2_enrich_capture_rows_course_identity(
            normalized_rows,
            context_payload,
            selected_course,
        )
        normalized_rows = capture_rows_course_identity_enrichment.get("rows", normalized_rows)

        slug = context_payload.get("course_slug")
        if not slug:
            try:
                slug = _p36_safe_slug(context_payload.get("course_uid", "ND"))
            except Exception:
                slug = "nd"

        latest_path = Path("data/output/official_runner_status_latest.csv")
        course_path = Path("data/output/official_runner_status") / f"{slug}_official_runner_status.csv"

        local_paths["latest"] = _p38_write_csv_rows_utf8sig(latest_path, normalized_rows)
        local_paths["course_scoped"] = _p38_write_csv_rows_utf8sig(course_path, normalized_rows)

        uploads["latest"] = _p38_upload_file_to_gcs(
            latest_path,
            "gpi-lite/outputs/official_runner_status_latest.csv",
            content_type="text/csv",
        )
        uploads["course_scoped"] = _p38_upload_file_to_gcs(
            course_path,
            f"gpi-lite/outputs/official_runner_status/{slug}_official_runner_status.csv",
            content_type="text/csv",
        )
    else:
        reason_codes.extend(validation.get("reason_codes", []))

    # Diagnostic JSON toujours ?crit localement, mais le CSV latest officiel n'est pas ?cras? si capture KO.
    diagnostic = {
        "ok": True,
        "phase": "official_runner_status_letrot_capture",
        "capture_version": "official_runner_status_letrot_capture_v0_9_31b2_8",
        "checked_at_utc": checked_at_utc,
        "course_uid": context_payload.get("course_uid", "ND"),
        "context_course_fields": {
            "reunion_no": context_payload.get("reunion_no", "ND"),
            "course_no": context_payload.get("course_no", "ND"),
            "scheduled_time": context_payload.get("scheduled_time", "ND"),
            "race_name": context_payload.get("race_name", "ND"),
        },
        "letrot_match_ok": letrot_match.get("ok", False) if isinstance(letrot_match, dict) else False,
        "letrot_course_match_status": letrot_match.get("course_match_status", "ND") if isinstance(letrot_match, dict) else "ND",
        "letrot_course_level_input_status": letrot_match.get("course_level_input_status", "ND") if isinstance(letrot_match, dict) else "ND",
        "selected_course": selected_course,
        "selected_course_found_oui_non": "oui" if selected_course else "non",
        "selected_course_source": selected_course_pick.get("selected_course_source", "ND") if isinstance(selected_course_pick, dict) else "ND",
        "selected_course_reason": selected_course_pick.get("selected_course_reason", "ND") if isinstance(selected_course_pick, dict) else "ND",
        "selected_course_fallback_source_preview_used_oui_non": selected_course_pick.get("fallback_source_preview_used_oui_non", "ND") if isinstance(selected_course_pick, dict) else "ND",
        "runner_extraction_ok": extraction.get("ok", False),
        "runner_extraction_reason": extraction.get("reason", "ND"),
        "runner_extraction_source_key": extraction.get("source_key", "ND"),
        "raw_runner_count": len(extraction.get("raw_runners", [])),
        "normalized_runner_raw_count": len(normalized_rows_raw),
        "normalized_runner_count": len(normalized_rows),
        "runner_row_filter_audit": runner_row_filter_audit,
        "validation": validation,
        "capture_rows_course_identity_enrichment": capture_rows_course_identity_enrichment,
        "capture_status": capture_status,
        "reason_codes": reason_codes,
        "local_paths": local_paths,
        "uploads": uploads,
        "official_runner_status_available_oui_non": "oui" if validation.get("ok") and (uploads.get("latest", {}) or {}).get("ok") else "non",
        "official_join_ok_oui_non": "oui" if validation.get("ok") and (uploads.get("latest", {}) or {}).get("ok") else "non",
        "official_latest_upload_required_for_join_oui_non": "oui",
        "matched_runner_status_sample": normalized_rows[:20],
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "Segment_ROI": "NO_BET",
        "doctrine": (
            "Capture officielle prudente depuis selected_course LeTrot. "
            "Aucun partant invente. Aucun strict ouvert."
        ),
    }

    try:
        Path("data/output").mkdir(parents=True, exist_ok=True)
        diag_path = Path("data/output/official_runner_status_letrot_capture_latest.json")
        diag_path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
        diagnostic["diagnostic_local_path"] = str(diag_path)
    except Exception as exc:
        diagnostic["diagnostic_write_error"] = f"{type(exc).__name__}:{exc}"

    return diagnostic


@app.get("/action/official-runner-status-course-level-preview", dependencies=[Depends(require_api_key)])
def action_official_runner_status_course_level_preview():
    """
    GPI V0_9_31B2.7
    Jointure preview des statuts officiels partants/NP au niveau course precise.

    Lecture seule.
    Ne scrape pas de nouveaux partants.
    Ne valide jamais le strict.
    """
    from datetime import datetime, timezone

    checked_at_utc = datetime.now(timezone.utc).isoformat()

    try:
        context_payload = action_latest_h5_auto_context()
    except Exception as exc:
        context_payload = {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }

    if not isinstance(context_payload, dict) or not context_payload.get("ok"):
        return {
            "ok": False,
            "phase": "official_runner_status_course_level_preview",
            "preview_version": "official_runner_status_course_level_preview_v0_9_31b2_7",
            "checked_at_utc": checked_at_utc,
            "reason": "LATEST_H5_AUTO_CONTEXT_UNAVAILABLE",
            "context_error": context_payload.get("error", "ND") if isinstance(context_payload, dict) else "CONTEXT_NOT_DICT",
            "official_runner_status_available_oui_non": "non",
            "official_join_ok_oui_non": "non",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
        }

    # V0_9_31B2_7_2_FORCE_ENRICHED_H5_CONTEXT
    context_payload = _p372_force_enriched_course_context(context_payload)

    context_course_fields = {
        "course_uid": context_payload.get("course_uid", "ND"),
        "reunion_no": context_payload.get("reunion_no", "ND"),
        "course_no": context_payload.get("course_no", "ND"),
        "scheduled_time": context_payload.get("scheduled_time", "ND"),
        "race_name": context_payload.get("race_name", "ND"),
        "course_level_context_sync_status": context_payload.get("course_level_context_sync_status", "ND"),
        "course_level_official_join_ok_oui_non": context_payload.get("course_level_official_join_ok_oui_non", "ND"),
        "course_level_context_force_ok_oui_non": context_payload.get("course_level_context_force_ok_oui_non", "ND"),
        "course_level_context_force_missing_fields": context_payload.get("course_level_context_force_missing_fields", []),
        "course_level_context_force_source_preview_used_oui_non": context_payload.get("course_level_context_force_source_preview_used_oui_non", "ND"),
    }

    read_result = _p37_read_text_local_or_gcs(
        local_candidates=[
            "data/output/official_runner_status_latest.csv",
            "data/output/official_runner_status.csv",
            "data/input/official_runner_status_latest.csv",
            "data/input/official_runner_status.csv",
        ],
        gcs_candidates=[
            "gpi-lite/outputs/official_runner_status_latest.csv",
            "gpi-lite/outputs/official_runner_status.csv",
        ],
    )

    if not read_result.get("ok"):
        return {
            "ok": True,
            "phase": "official_runner_status_course_level_preview",
            "preview_version": "official_runner_status_course_level_preview_v0_9_31b2_7",
            "checked_at_utc": checked_at_utc,
            "course_uid": context_payload.get("course_uid", "ND"),
            "context_course_fields": context_course_fields,
            "official_status_source_read_ok": "non",
            "official_status_read_errors": read_result.get("errors", []),
            "official_runner_status_available_oui_non": "non",
            "official_runner_status_unavailable_reason": "OFFICIAL_RUNNER_STATUS_SOURCE_ABSENT",
            "official_join_ok_oui_non": "non",
            "strict_allowed_oui_non": "non",
            "strict_usable_oui_non": "non",
            "ticket_strict": "NO_BET",
            "Segment_ROI": "NO_BET",
        }

    csv_payload = _p37_csv_rows_from_text(read_result.get("text", ""))

    fieldnames = csv_payload.get("fieldnames", [])
    rows = csv_payload.get("rows", [])
    column_audit = _p37_classify_official_runner_status_columns(fieldnames)
    source_diagnostics = _p371_build_official_status_source_diagnostics(rows, context_payload)

    matched = []
    rejected_sample = []

    for row in rows:
        match_info = _p37_row_matches_course(row, context_payload)
        if match_info.get("match"):
            normalized = _p37_normalize_runner_status_row(row)
            normalized["match_score"] = match_info.get("score", 0)
            normalized["match_reason_codes"] = match_info.get("reason_codes", [])
            matched.append(normalized)
        elif len(rejected_sample) < 8:
            rejected_sample.append({
                "match_score": match_info.get("score", 0),
                "match_reason_codes": match_info.get("reason_codes", []),
                "course_uid": _p37_pick(row, ["course_uid", "course_key"], "ND"),
                "reunion_no": _p37_pick(row, ["reunion_no", "reunion", "num_reunion", "reunion_num", "r"], "ND"),
                "course_no": _p37_pick(row, ["course_no", "num_course", "course_num", "c", "course_no_int"], "ND"),
                "scheduled_time": _p37_pick(row, ["scheduled_time", "course_time", "start_time", "heure_depart", "heure"], "ND"),
                "race_name": _p37_pick(row, ["race_name", "course_name", "prix", "libelle"], "ND"),
            })

    missing_runner_columns = []

    for flag, logical in [
        ("has_numero", "numero"),
        ("has_cheval", "cheval"),
        ("has_statut", "statut"),
        ("has_is_np", "is_np"),
    ]:
        if not column_audit.get(flag):
            missing_runner_columns.append(logical)

    statut_missing_count = sum(
        1 for r in matched
        if r.get("statut") in (None, "", "ND")
    )
    is_np_missing_count = sum(
        1 for r in matched
        if r.get("is_np") in (None, "", "ND")
    )
    numero_missing_count = sum(
        1 for r in matched
        if r.get("numero") in (None, "", "ND")
    )

    np_count = sum(1 for r in matched if r.get("is_np") == "oui")

    official_source_values = sorted({
        str(r.get("official_source", "ND"))
        for r in matched
        if str(r.get("official_source", "ND")).strip()
    })

    official_runner_status_available_oui_non = "non"
    official_status_join_status = "OFFICIAL_RUNNER_STATUS_NOT_AVAILABLE"
    reason_codes = []

    if missing_runner_columns:
        reason_codes.append("OFFICIAL_STATUS_REQUIRED_COLUMNS_MISSING")
        official_status_join_status = "OFFICIAL_STATUS_REQUIRED_COLUMNS_MISSING"
    elif not matched:
        reason_codes.append("NO_OFFICIAL_STATUS_ROWS_MATCH_COURSE")
        for diag_reason in source_diagnostics.get("reason_codes", []):
            if diag_reason not in reason_codes:
                reason_codes.append(diag_reason)
        official_status_join_status = "NO_OFFICIAL_STATUS_ROWS_MATCH_COURSE"
    elif statut_missing_count > 0 or is_np_missing_count > 0 or numero_missing_count > 0:
        reason_codes.append("OFFICIAL_STATUS_ROWS_INCOMPLETE")
        official_status_join_status = "OFFICIAL_STATUS_ROWS_INCOMPLETE"
    else:
        official_runner_status_available_oui_non = "oui"
        official_status_join_status = "OFFICIAL_STATUS_ROWS_MATCHED_COMPLETE"
        reason_codes.append("OFFICIAL_STATUS_ROWS_MATCHED_COMPLETE")

    return {
        "ok": True,
        "phase": "official_runner_status_course_level_preview",
        "preview_version": "official_runner_status_course_level_preview_v0_9_31b2_7",
        "checked_at_utc": checked_at_utc,
        "course_uid": context_payload.get("course_uid", "ND"),
        "context_course_fields": context_course_fields,
        "official_status_source_read_ok": "oui",
        "official_status_source": read_result.get("source", "ND"),
        "official_status_source_path": read_result.get("path", "ND"),
        "official_status_source_gcs_uri": read_result.get("gcs_uri", "ND"),
        "official_status_csv_fieldnames": fieldnames,
        "official_status_column_audit": column_audit,
        "official_status_source_diagnostics": source_diagnostics,
        "official_status_source_alignment_status": source_diagnostics.get("source_alignment_status", "ND"),
        "official_status_source_diagnostics_reason_codes": source_diagnostics.get("reason_codes", []),
        "official_status_rows_total": len(rows),
        "official_status_rows_matched_course": len(matched),
        "official_status_join_status": official_status_join_status,
        "official_runner_status_available_oui_non": official_runner_status_available_oui_non,
        "official_join_ok_oui_non": "oui" if official_runner_status_available_oui_non == "oui" else "non",
        "reason_codes": reason_codes,
        "missing_runner_columns": missing_runner_columns,
        "statut_missing_count": statut_missing_count,
        "is_np_missing_count": is_np_missing_count,
        "numero_missing_count": numero_missing_count,
        "np_count": np_count,
        "official_source_values": official_source_values,
        "matched_runner_status_sample": matched[:20],
        "rejected_rows_sample": rejected_sample,
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "Segment_ROI": "NO_BET",
        "doctrine": (
            "Preview official runner status course-level. "
            "Ne valide pas proxy/split/calibration/decision_engine_outputs. "
            "Strict ferme."
        ),
    }


@app.get("/action/zeturf-course-level-source-preview")
def action_zeturf_course_level_source_preview():
    """
    GPI V0_9_31B2.4.1
    Audit lecture seule des sources possibles de m?tadonn?es course-level c?t? ZEturf/H5.

    Ne valide aucune course.
    Ne modifie pas GCS.
    N'ouvre pas le strict.
    """
    try:
        context_payload = action_latest_h5_auto_context()
    except Exception as exc:
        context_payload = {
            "ok": False,
            "phase": "latest_h5_auto_context",
            "error": f"{type(exc).__name__}:{exc}",
        }

    audit = _p34_extract_course_level_source_from_context(context_payload)

    return {
        "ok": True,
        "phase": "zeturf_course_level_source_preview",
        "preview_version": "zeturf_course_level_source_preview_v0_9_31b2_4_1",
        "source_context_phase": context_payload.get("phase", "ND") if isinstance(context_payload, dict) else "ND",
        "source_context_ok": context_payload.get("ok", "ND") if isinstance(context_payload, dict) else "ND",
        **audit,
        "official_join_ok_oui_non": "oui" if audit.get("course_level_official_join_ok_oui_non") == "oui" else "non",
        "strict_allowed_oui_non": "non",
        "strict_usable_oui_non": "non",
        "ticket_strict": "NO_BET",
        "Segment_ROI": "NO_BET",
        "doctrine": (
            "Audit source course-level ZEturf/H5. "
            "Une source disponible ne vaut pas jointure officielle. "
            "Strict ferm? tant que la course pr?cise et les partants/NP officiels d?taill?s ne sont pas valid?s."
        ),
    }
# <<< GPI V0_9_31B2_4_1 ZETURF COURSE LEVEL SOURCE AUDIT <<<


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
