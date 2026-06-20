from __future__ import annotations

import csv
import json
import re
import unicodedata
import os
from pathlib import Path

try:
    from google.cloud import storage
except Exception:
    storage = None

try:
    import fitz
except Exception as exc:
    raise SystemExit(f"PyMuPDF/fitz indisponible : {exc}")

RESULTS_DIR = Path("data/results")
OUTPUT_DIR = Path("data/output")
PAPER_FILE = OUTPUT_DIR / "paper_trading_50_decisions.csv"

RESULT_INPUT = RESULTS_DIR / "result_input.csv"
AUDIT_CSV = OUTPUT_DIR / "results_pdf_parse_audit.csv"
AUDIT_JSON = OUTPUT_DIR / "latest_results_pdf_parse_audit.json"


def strip_accents(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def normalize_key(value: str) -> str:
    value = strip_accents(value or "").lower()
    value = value.replace("&", " et ")
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def result_pdf_to_course_uid(pdf_name: str) -> str:
    name = Path(pdf_name).stem

    replacements = [
        "_ Résultats & Rapports",
        " Résultats & Rapports",
        "_ Resultats & Rapports",
        " Resultats & Rapports",
        "_ R├®sultats & Rapports",
        " R├®sultats & Rapports",
    ]

    for item in replacements:
        name = name.replace(item, "")

    return f"{name}_ Pronostics & Cotes"


def read_pdf_text(pdf_path: Path, max_pages: int = 5) -> str:
    doc = fitz.open(str(pdf_path))
    chunks = []

    for idx, page in enumerate(doc):
        if idx >= max_pages:
            break
        chunks.append(page.get_text("text") or "")

    return "\n".join(chunks)


def extract_arrivee(text: str) -> str:
    raw = text or ""
    compact = re.sub(r"\s+", " ", raw)

    patterns = [
        r"Arriv[ée]e\s*(?:officielle|définitive|provisoire)?\s*[:\-]?\s*((?:\d+\s*[- ]\s*){2,}\d+)",
        r"Arriv[ée]e\s*[:\-]?\s*((?:\d+\s*[- ]\s*){2,}\d+)",
        r"Classement\s*[:\-]?\s*((?:\d+\s*[- ]\s*){2,}\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if match:
            nums = re.findall(r"\d+", match.group(1))
            if len(nums) >= 3:
                return "-".join(nums[:8])

    lines = [line.strip() for line in raw.splitlines() if line.strip()]

    for index, line in enumerate(lines):
        if re.search(r"arriv", line, flags=re.IGNORECASE):
            zone = " ".join(lines[index:index + 8])
            nums = re.findall(r"\b\d{1,2}\b", zone)
            if len(nums) >= 3:
                return "-".join(nums[:8])

    return ""


def extract_reports_preview(text: str) -> str:
    raw = text or ""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    report_lines = []

    keywords = [
        "Simple Gagnant",
        "Simple Plac",
        "Coupl",
        "Jumel",
        "2sur4",
        "2 sur 4",
        "Trio",
        "ZE4",
        "Quart",
    ]

    for line in lines:
        normalized = strip_accents(line).lower()
        if any(strip_accents(keyword).lower() in normalized for keyword in keywords):
            report_lines.append(re.sub(r"\s+", " ", line))

    return " | ".join(report_lines[:25])


def extract_numeric_reports(text: str) -> dict:
    raw = text or ""
    compact = re.sub(r"\s+", " ", raw)
    normalized = strip_accents(compact).upper()

    labels = [
        "SIMPLE GAGNANT",
        "SIMPLE PLACE",
        "SIMPLE PLACÉ",
        "JUMELE GAGNANT",
        "JUMELÉ GAGNANT",
        "JUMELE PLACE",
        "JUMELÉ PLACÉ",
        "ZE 2 SUR 4",
        "2 SUR 4",
    ]

    found = []

    for label in labels:
        key = strip_accents(label).upper()
        pos = normalized.find(key)
        if pos < 0:
            continue

        window = compact[pos:pos + 1200]
        nums = re.findall(r"\b\d{1,3}[,.]\d{1,2}\b", window)

        for n in nums:
            v = n.replace(",", ".")
            try:
                f = float(v)
            except Exception:
                continue

            if 1.01 <= f <= 999.99:
                found.append(v)

    out = []
    seen = set()
    for v in found:
        if v not in seen:
            out.append(v)
            seen.add(v)

    return {
        "reports_numeric_found_oui_non": "oui" if out else "non",
        "reports_numeric_blob": "|".join(out[:50]),
        "reports_parse_quality": "NUMERIC_FOUND" if out else "NUMERIC_NOT_FOUND",
    }


def ensure_paper_file() -> None:
    if PAPER_FILE.exists():
        return

    if storage is None:
        raise SystemExit(f"Fichier paper absent : {PAPER_FILE}")

    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or "gpi-lite-watchlist-data"
    )

    blob_name = "gpi-lite/outputs/paper_trading_50_decisions.csv"

    PAPER_FILE.parent.mkdir(parents=True, exist_ok=True)

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    if not blob.exists():
        raise SystemExit(f"Fichier paper absent local et cloud : gs://{bucket_name}/{blob_name}")

    blob.download_to_filename(str(PAPER_FILE))


def load_paper_courses() -> dict:
    ensure_paper_file()

    if not PAPER_FILE.exists():
        raise SystemExit(f"Fichier paper absent : {PAPER_FILE}")

    rows = {}

    with PAPER_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            course_uid = (row.get("course_uid") or "").strip()
            if course_uid:
                rows[normalize_key(course_uid)] = row

    return rows


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    paper_by_key = load_paper_courses()
    pdfs = sorted(RESULTS_DIR.glob("*.pdf"))

    result_rows = []
    audit_rows = []

    for pdf in pdfs:
        expected_uid = result_pdf_to_course_uid(pdf.name)
        expected_key = normalize_key(expected_uid)

        matched_paper = paper_by_key.get(expected_key)
        matched_uid = matched_paper.get("course_uid", "") if matched_paper else ""

        status = "OK" if matched_paper else "RESULTS_COURSE_UID_NOT_MATCHED"
        parse_error = ""

        try:
            text = read_pdf_text(pdf)
        except Exception as exc:
            text = ""
            parse_error = str(exc)
            status = "RESULTS_PDF_PARSE_ERROR"

        arrivee = extract_arrivee(text)
        reports_preview = extract_reports_preview(text)
        numeric_reports = extract_numeric_reports(text)

        if not text.strip() and status == "OK":
            status = "RESULTS_PDF_PARSE_EMPTY"

        if not arrivee and status == "OK":
            status = "RESULTS_ARRIVEE_MISSING"

        result_rows.append({
            "course_uid": matched_uid or expected_uid,
            "arrivee": arrivee,
            "rapport_officiel": reports_preview,
            "result_status": "OK" if status == "OK" else status,
            "cause_resultat": "PDF_RESULTS_AUTO_PARSE" if status == "OK" else status,
            "reports_numeric_found_oui_non": numeric_reports.get("reports_numeric_found_oui_non", "non"),
            "reports_numeric_blob": numeric_reports.get("reports_numeric_blob", ""),
            "reports_parse_quality": numeric_reports.get("reports_parse_quality", "NUMERIC_NOT_FOUND"),
        })

        audit_rows.append({
            "pdf": pdf.name,
            "expected_course_uid": expected_uid,
            "matched_course_uid": matched_uid,
            "status": status,
            "arrivee": arrivee,
            "reports_preview": reports_preview[:500],
            "reports_numeric_found_oui_non": numeric_reports.get("reports_numeric_found_oui_non", "non"),
            "reports_numeric_blob": numeric_reports.get("reports_numeric_blob", "")[:1000],
            "reports_parse_quality": numeric_reports.get("reports_parse_quality", "NUMERIC_NOT_FOUND"),
            "parse_error": parse_error,
            "text_chars": len(text),
        })

    with RESULT_INPUT.open("w", encoding="utf-8-sig", newline="") as file:
        fieldnames = [
            "course_uid",
            "arrivee",
            "rapport_officiel",
            "result_status",
            "cause_resultat",
            "reports_numeric_found_oui_non",
            "reports_numeric_blob",
            "reports_parse_quality",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(result_rows)

    with AUDIT_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        fieldnames = [
            "pdf",
            "expected_course_uid",
            "matched_course_uid",
            "status",
            "arrivee",
            "reports_preview",
            "reports_numeric_found_oui_non",
            "reports_numeric_blob",
            "reports_parse_quality",
            "parse_error",
            "text_chars",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(audit_rows)

    audit = {
        "ok": True,
        "version": "GPI_LITE_V0_9_19B_REPORTS_NUMERIC_A",
        "pdf_count": len(pdfs),
        "result_input_created": str(RESULT_INPUT),
        "audit_csv": str(AUDIT_CSV),
        "ok_count": sum(1 for row in audit_rows if row["status"] == "OK"),
        "unmatched_count": sum(1 for row in audit_rows if row["status"] == "RESULTS_COURSE_UID_NOT_MATCHED"),
        "missing_arrivee_count": sum(1 for row in audit_rows if row["status"] == "RESULTS_ARRIVEE_MISSING"),
        "empty_count": sum(1 for row in audit_rows if row["status"] == "RESULTS_PDF_PARSE_EMPTY"),
        "parse_error_count": sum(1 for row in audit_rows if row["status"] == "RESULTS_PDF_PARSE_ERROR"),
        "numeric_report_found_count": sum(1 for row in audit_rows if row.get("reports_numeric_found_oui_non") == "oui"),
        "numeric_report_missing_count": sum(1 for row in audit_rows if row.get("reports_numeric_found_oui_non") != "oui"),
    }

    AUDIT_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    print("RESULT_INPUT_CREATED")
    print(str(RESULT_INPUT))
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
