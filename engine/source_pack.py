from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACK_DIR = PROJECT_ROOT / "project_sources" / "GPI_ZEturf_V19_1_4"
MANIFEST_NAME = "manifest_GPI_V19_1_4.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "_read_error": f"{type(exc).__name__}: {exc}",
        }


def build_source_pack_status() -> Dict[str, Any]:
    """
    Audit documentaire du pack GPI actif.

    Ne décide aucun pari.
    Ne modifie aucun fichier.
    Sert uniquement à prouver que les sources V19.1.4 sont présentes,
    hashées et cohérentes avec le manifest.
    """
    manifest_path = SOURCE_PACK_DIR / MANIFEST_NAME

    status: Dict[str, Any] = {
        "ok": False,
        "source_pack_version": "ND",
        "source_pack_dir": str(SOURCE_PACK_DIR),
        "manifest_file": MANIFEST_NAME,
        "manifest_exists": manifest_path.exists(),
        "manifest_sha256": "ND",
        "source_documents_count": 0,
        "source_documents_loaded": False,
        "source_documents_hash_ok": False,
        "source_documents_missing": [],
        "source_documents_mismatch": [],
        "source_documents": [],
        "error": "",
    }

    if not SOURCE_PACK_DIR.exists():
        status["error"] = "source pack directory absent"
        return status

    if not manifest_path.exists():
        status["error"] = "manifest_GPI_V19_1_4.json absent"
        return status

    status["manifest_sha256"] = _sha256_file(manifest_path)

    manifest = _safe_read_json(manifest_path)

    if "_read_error" in manifest:
        status["error"] = manifest["_read_error"]
        return status

    status["source_pack_version"] = manifest.get("pack", "ND")

    files = manifest.get("files", [])

    if not isinstance(files, list) or not files:
        status["error"] = "manifest files vide ou invalide"
        return status

    documents: List[Dict[str, Any]] = []
    missing: List[str] = []
    mismatch: List[Dict[str, Any]] = []

    for item in files:
        file_name = item.get("file")
        expected_sha = str(item.get("sha256", "")).strip().lower()
        expected_bytes = item.get("bytes")

        if not file_name:
            mismatch.append({
                "file": "ND",
                "reason": "file field absent dans manifest",
            })
            continue

        file_path = SOURCE_PACK_DIR / file_name

        doc_info: Dict[str, Any] = {
            "file": file_name,
            "exists": file_path.exists(),
            "expected_sha256": expected_sha or "ND",
            "actual_sha256": "ND",
            "expected_bytes": expected_bytes,
            "actual_bytes": 0,
            "hash_ok": False,
            "bytes_ok": False,
        }

        if not file_path.exists():
            missing.append(file_name)
            documents.append(doc_info)
            continue

        actual_sha = _sha256_file(file_path)
        actual_bytes = file_path.stat().st_size

        doc_info["actual_sha256"] = actual_sha
        doc_info["actual_bytes"] = actual_bytes
        doc_info["hash_ok"] = bool(expected_sha and actual_sha == expected_sha)
        doc_info["bytes_ok"] = bool(expected_bytes is None or actual_bytes == expected_bytes)

        if not doc_info["hash_ok"]:
            mismatch.append({
                "file": file_name,
                "reason": "sha256 mismatch",
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
            })

        documents.append(doc_info)

    status["source_documents"] = documents
    status["source_documents_count"] = len([d for d in documents if d.get("exists")])
    status["source_documents_missing"] = missing
    status["source_documents_mismatch"] = mismatch

    status["source_documents_loaded"] = len(missing) == 0
    status["source_documents_hash_ok"] = len(mismatch) == 0 and len(missing) == 0

    status["ok"] = (
        status["source_pack_version"] == "GPI_ZEturf_V19_1_4"
        and status["source_documents_loaded"]
        and status["source_documents_hash_ok"]
    )

    if not status["ok"] and not status["error"]:
        status["error"] = "source pack incomplet ou hash non conforme"

    return status


if __name__ == "__main__":
    print(json.dumps(build_source_pack_status(), ensure_ascii=False, indent=2))