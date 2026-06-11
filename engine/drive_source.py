from pathlib import Path
from typing import Dict, List
import os
import io

from google.auth import default
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


SCOPES = ["https://www.googleapis.com/auth/drive"]


def get_drive_service():
    credentials, _ = default(scopes=SCOPES)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def list_drive_pdfs(folder_id: str) -> List[Dict]:
    if not folder_id:
        raise ValueError("folder_id absent")

    service = get_drive_service()

    query = (
        f"'{folder_id}' in parents "
        "and mimeType='application/pdf' "
        "and trashed=false"
    )

    files = []
    page_token = None

    while True:
        response = service.files().list(
            q=query,
            spaces="drive",
            fields="nextPageToken, files(id, name, mimeType, size, md5Checksum, modifiedTime)",
            pageToken=page_token,
            pageSize=100
        ).execute()

        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return files


def download_drive_file(file_id: str, target_path: Path) -> Dict:
    service = get_drive_service()

    target_path.parent.mkdir(parents=True, exist_ok=True)

    request = service.files().get_media(fileId=file_id)

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False

    while not done:
        _, done = downloader.next_chunk()

    target_path.write_bytes(buffer.getvalue())

    return {
        "ok": True,
        "saved_as": str(target_path).replace("\\", "/"),
        "bytes": target_path.stat().st_size
    }


def sync_drive_folder_to_local(folder_id: str, local_dir: str, clean_target: bool = True) -> Dict:
    target_dir = Path(local_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if clean_target:
        for old_pdf in target_dir.glob("*.pdf"):
            old_pdf.unlink()

    files = list_drive_pdfs(folder_id)

    downloads = []

    for item in files:
        name = item["name"]
        file_id = item["id"]

        safe_name = name.replace("/", "_").replace("\\", "_")
        target_path = target_dir / safe_name

        result = download_drive_file(file_id, target_path)
        result["drive_file_id"] = file_id
        result["drive_name"] = name

        downloads.append(result)

    return {
        "ok": True,
        "folder_id": folder_id,
        "local_dir": str(target_dir).replace("\\", "/"),
        "files_count": len(files),
        "downloads": downloads
    }


def get_drive_folder_id(kind: str) -> str:
    mapping = {
        "morning": "DRIVE_MORNING_FOLDER_ID",
        "h5": "DRIVE_H5_FOLDER_ID",
        "results": "DRIVE_RESULTS_FOLDER_ID",
        "archive": "DRIVE_ARCHIVE_FOLDER_ID",
    }

    env_name = mapping.get(kind)

    if not env_name:
        raise ValueError(f"Type Drive inconnu : {kind}")

    value = os.getenv(env_name, "").strip()

    if not value:
        raise ValueError(f"Variable absente : {env_name}")

    return value

def move_drive_file_to_folder(file_id: str, target_folder_id: str) -> Dict:
    if not file_id:
        raise ValueError("file_id absent")

    if not target_folder_id:
        raise ValueError("target_folder_id absent")

    service = get_drive_service()

    file_data = service.files().get(
        fileId=file_id,
        fields="id, name, parents"
    ).execute()

    previous_parents = ",".join(file_data.get("parents", []))

    updated = service.files().update(
        fileId=file_id,
        addParents=target_folder_id,
        removeParents=previous_parents,
        fields="id, name, parents"
    ).execute()

    return {
        "ok": True,
        "file_id": file_id,
        "name": updated.get("name", file_data.get("name", "")),
        "target_folder_id": target_folder_id,
        "parents": updated.get("parents", [])
    }


def archive_downloaded_files(downloads: List[Dict], archive_folder_id: str) -> Dict:
    archived = []
    errors = []

    for item in downloads:
        file_id = item.get("drive_file_id", "")
        name = item.get("drive_name", "")

        try:
            result = move_drive_file_to_folder(file_id, archive_folder_id)
            result["drive_name"] = name
            archived.append(result)
        except Exception as exc:
            errors.append({
                "drive_file_id": file_id,
                "drive_name": name,
                "error": str(exc)
            })

    return {
        "ok": len(errors) == 0,
        "archived_count": len(archived),
        "error_count": len(errors),
        "archived": archived,
        "errors": errors
    }


def get_archive_folder_id(kind: str) -> str:
    mapping = {
        "morning": "DRIVE_ARCHIVE_MORNING_FOLDER_ID",
        "h5": "DRIVE_ARCHIVE_H5_FOLDER_ID",
        "results": "DRIVE_ARCHIVE_RESULTS_FOLDER_ID",
    }

    env_name = mapping.get(kind)

    if not env_name:
        raise ValueError(f"Type archive inconnu : {kind}")

    value = os.getenv(env_name, "").strip()

    if not value:
        raise ValueError(f"Variable absente : {env_name}")

    return value


def archive_enabled() -> bool:
    return os.getenv("DRIVE_ARCHIVE_AFTER_SUCCESS", "false").lower() == "true"