from pathlib import Path
import os
import re


def _clean_course_key_from_pdf_name(file_name: str) -> str:
    stem = Path(file_name).stem
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)
    return stem.strip()


def _expected_9h_clean_name_from_h5_pdf(file_name: str) -> str:
    course_key = _clean_course_key_from_pdf_name(file_name)
    return f"{course_key}_course_9h_clean.csv"


def _download_blob_if_exists(bucket_name: str, blob_name: str, local_path: Path) -> bool:
    try:
        from google.cloud import storage
    except Exception:
        return False

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if not blob.exists():
            return False

        local_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(local_path))
        return True
    except Exception:
        return False


def ensure_9h_context_for_h5(
    h5_dir: str = "data/h5",
    output_dir: str = "data/output",
    bucket_name: str | None = None,
    gcs_prefix: str = "gpi-lite/outputs"
) -> dict:
    h5_path = Path(h5_dir)
    out_path = Path(output_dir)

    bucket = (
        bucket_name
        or os.getenv("GPI_LITE_BUCKET")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET_NAME")
        or "gpi-lite-watchlist-data"
    )

    h5_pdfs = sorted([
        p for p in h5_path.glob("*.pdf")
        if p.is_file()
    ])

    result = {
        "ok": True,
        "bucket": bucket,
        "gcs_prefix": gcs_prefix,
        "h5_files_count": len(h5_pdfs),
        "checked": [],
        "missing": []
    }

    if not h5_pdfs:
        result["ok"] = False
        result["error"] = "aucun PDF H-5 local"
        return result

    for pdf in h5_pdfs:
        course_key = _clean_course_key_from_pdf_name(pdf.name)
        expected_name = f"{course_key}_course_9h_clean.csv"

        local_expected = out_path / expected_name
        gcs_blob_name = f"{gcs_prefix}/{expected_name}"

        item = {
            "h5_pdf": pdf.name,
            "course_key": course_key,
            "expected_9h_clean": str(local_expected),
            "expected_decision_lite": f"{course_key}_decision_lite.csv",
            "expected_prompt_gpt": f"{course_key}_prompt_gpt.txt",
            "gcs_blob": gcs_blob_name,
            "found": False,
            "source": None
        }

        if local_expected.exists():
            item["found"] = True
            item["source"] = "local"
            result["checked"].append(item)
            continue

        downloaded = _download_blob_if_exists(
            bucket_name=bucket,
            blob_name=gcs_blob_name,
            local_path=local_expected
        )

        if downloaded and local_expected.exists():
            item["found"] = True
            item["source"] = "cloud_storage"
            result["checked"].append(item)
            continue

        result["ok"] = False
        item["source"] = "missing"
        result["missing"].append(item)
        result["checked"].append(item)

    if not result["ok"]:
        result["error"] = "fichier 9h correspondant absent pour H-5"

    return result
