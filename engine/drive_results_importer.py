from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "results_pdf"
DEFAULT_AUDIT_DIR = PROJECT_ROOT / "data" / "output"

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

PDF_MIME = "application/pdf"
FOLDER_MIME = "application/vnd.google-apps.folder"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_drive_id(value: str) -> str:
    """
    Accepte :
    - URL dossier Drive
    - URL fichier Drive
    - ID brut
    """
    value = (value or "").strip()

    patterns = [
        r"/folders/([a-zA-Z0-9_-]+)",
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
    ]

    for pattern in patterns:
        m = re.search(pattern, value)
        if m:
            return m.group(1)

    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", value):
        return value

    raise ValueError(f"Impossible d'extraire un ID Drive depuis : {value}")


def safe_filename(name: str) -> str:
    name = unicodedata.normalize("NFKC", name or "drive_file.pdf")
    name = name.replace("/", "_").replace("\\", "_").replace(":", " -")
    name = re.sub(r"[<>|?*\x00-\x1F]", "_", name)
    name = re.sub(r"\s+", " ", name).strip()

    if not name.lower().endswith(".pdf"):
        name += ".pdf"

    return name[:180]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_drive_service():
    try:
        import google.auth
        from googleapiclient.discovery import build
    except Exception as exc:
        raise RuntimeError(
            "Librairies Google manquantes. Installe : pip install google-api-python-client google-auth"
        ) from exc

    credentials, _ = google.auth.default(scopes=SCOPES)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def list_children(service, folder_id: str, recursive: bool = True) -> List[Dict[str, str]]:
    """
    Liste PDF + sous-dossiers d'un dossier Drive.
    Compatible My Drive et Shared Drives.
    """
    found: List[Dict[str, str]] = []
    folders_to_scan = [folder_id]
    scanned_folders = set()

    while folders_to_scan:
        current_folder = folders_to_scan.pop(0)

        if current_folder in scanned_folders:
            continue

        scanned_folders.add(current_folder)

        page_token = None

        while True:
            query = (
                f"'{current_folder}' in parents and trashed = false and "
                f"(mimeType = '{PDF_MIME}' or mimeType = '{FOLDER_MIME}')"
            )

            response = (
                service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id,name,mimeType,modifiedTime,size,webViewLink)",
                    pageToken=page_token,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    corpora="allDrives",
                    pageSize=1000,
                )
                .execute()
            )

            for item in response.get("files", []):
                mime = item.get("mimeType", "")

                if mime == FOLDER_MIME and recursive:
                    folders_to_scan.append(item["id"])
                    continue

                if mime == PDF_MIME:
                    found.append(item)

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    return found


def search_results_pdfs(service, query_text: str, topn: int = 500) -> List[Dict[str, str]]:
    """
    Fallback si le listing par dossier ne retourne rien.
    Recherche globale Drive.
    """
    found: List[Dict[str, str]] = []
    page_token = None

    tokens = [x for x in re.split(r"\s+", query_text.strip()) if x]
    name_clauses = " and ".join([f"name contains '{x.replace(chr(39), chr(92)+chr(39))}'" for x in tokens])

    if not name_clauses:
        name_clauses = "(name contains 'Résultats' or name contains 'Resultats' or name contains 'Rapports')"

    query = f"mimeType = '{PDF_MIME}' and trashed = false and ({name_clauses})"

    while True:
        response = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id,name,mimeType,modifiedTime,size,webViewLink)",
                pageToken=page_token,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                corpora="allDrives",
                pageSize=min(1000, topn),
            )
            .execute()
        )

        found.extend(response.get("files", []))

        if len(found) >= topn:
            return found[:topn]

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return found


def download_file_bytes(service, file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload
    import io

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue()


def build_existing_hash_index(output_dir: Path) -> Dict[str, Path]:
    index: Dict[str, Path] = {}

    if not output_dir.exists():
        return index

    for path in output_dir.glob("*.pdf"):
        try:
            h = hashlib.sha256(path.read_bytes()).hexdigest()
            index[h] = path
        except Exception:
            continue

    return index


def unique_path(output_dir: Path, file_name: str) -> Path:
    base = safe_filename(file_name)
    path = output_dir / base

    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix

    for i in range(2, 9999):
        candidate = output_dir / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Impossible de créer un nom unique pour {file_name}")


def import_drive_results(
    folder_url_or_id: str,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    audit_dir: Path = DEFAULT_AUDIT_DIR,
    recursive: bool = True,
    fallback_search: bool = True,
    fallback_query: str = "Résultats Rapports",
    max_files: int = 1000,
) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    service = get_drive_service()
    folder_id = extract_drive_id(folder_url_or_id)

    files = list_children(service, folder_id=folder_id, recursive=recursive)

    source_mode = "folder_listing"

    if not files and fallback_search:
        files = search_results_pdfs(service, query_text=fallback_query, topn=max_files)
        source_mode = "fallback_search"

    files = files[:max_files]

    existing_hashes = build_existing_hash_index(output_dir)

    audit_rows: List[Dict[str, str]] = []

    downloaded = 0
    skipped_duplicate = 0
    errors = 0

    for item in files:
        file_id = item.get("id", "")
        name = item.get("name", "ND.pdf")
        status = "ND"
        error = ""
        local_path = ""
        content_hash = ""

        try:
            raw = download_file_bytes(service, file_id)
            content_hash = sha256_bytes(raw)

            if content_hash in existing_hashes:
                status = "SKIPPED_DUPLICATE_HASH"
                local_path = str(existing_hashes[content_hash])
                skipped_duplicate += 1
            else:
                path = unique_path(output_dir, name)
                path.write_bytes(raw)
                existing_hashes[content_hash] = path
                status = "DOWNLOADED"
                local_path = str(path)
                downloaded += 1

        except Exception as exc:
            status = "ERROR"
            error = f"{type(exc).__name__}: {exc}"
            errors += 1

        audit_rows.append(
            {
                "created_at": now_utc(),
                "source_mode": source_mode,
                "drive_file_id": file_id,
                "drive_name": name,
                "drive_mime_type": item.get("mimeType", ""),
                "drive_modified_time": item.get("modifiedTime", ""),
                "drive_size": str(item.get("size", "")),
                "drive_web_view_link": item.get("webViewLink", ""),
                "content_hash": content_hash,
                "local_path": local_path,
                "status": status,
                "error": error,
            }
        )

    audit_csv = audit_dir / "drive_results_import_audit.csv"
    audit_json = audit_dir / "latest_drive_results_import_audit.json"

    fieldnames = [
        "created_at",
        "source_mode",
        "drive_file_id",
        "drive_name",
        "drive_mime_type",
        "drive_modified_time",
        "drive_size",
        "drive_web_view_link",
        "content_hash",
        "local_path",
        "status",
        "error",
    ]

    with audit_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(audit_rows)

    payload = {
        "ok": errors == 0,
        "created_at": now_utc(),
        "folder_id": folder_id,
        "source_mode": source_mode,
        "found_files": len(files),
        "downloaded": downloaded,
        "skipped_duplicate_hash": skipped_duplicate,
        "errors": errors,
        "output_dir": str(output_dir),
        "audit_csv": str(audit_csv),
        "audit_json": str(audit_json),
        "warning": "Import Drive uniquement. Aucun ticket strict. Relancer results_builder ensuite.",
    }

    audit_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Import PDF Résultats & Rapports ZEturf depuis Google Drive.")
    parser.add_argument("--folder-url", required=True, help="URL ou ID du dossier Google Drive")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Dossier local de destination")
    parser.add_argument("--audit-dir", default=str(DEFAULT_AUDIT_DIR), help="Dossier local des audits")
    parser.add_argument("--no-recursive", action="store_true", help="Ne pas scanner les sous-dossiers")
    parser.add_argument("--no-fallback-search", action="store_true", help="Ne pas faire de recherche globale si dossier vide")
    parser.add_argument("--fallback-query", default="Résultats Rapports", help="Recherche globale Drive de fallback")
    parser.add_argument("--max-files", type=int, default=1000, help="Nombre maximum de PDF")

    args = parser.parse_args()

    payload = import_drive_results(
        folder_url_or_id=args.folder_url,
        output_dir=Path(args.output_dir),
        audit_dir=Path(args.audit_dir),
        recursive=not args.no_recursive,
        fallback_search=not args.no_fallback_search,
        fallback_query=args.fallback_query,
        max_files=args.max_files,
    )

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
