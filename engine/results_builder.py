from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"

DEFAULT_RESULTS_DIRS = [
    PROJECT_ROOT / "data" / "results_pdf",
    PROJECT_ROOT / "data" / "results",
    PROJECT_ROOT / "uploads" / "results_pdf",
    PROJECT_ROOT / "uploads" / "results",
]


BET_TYPE_ALIASES = {
    "Simple Gagnant": "SG",
    "Simple Place": "SP",
    "Simple Placé": "SP",
    "Couple Gagnant": "CG",
    "Couplé Gagnant": "CG",
    "Couple Place": "CP",
    "Couplé Placé": "CP",
    "Jumele Place": "CP",
    "Jumelé Placé": "CP",
    "2sur4": "2SUR4",
    "2 sur 4": "2SUR4",
    "Trio": "TRIO",
    "ZE4": "ZE4",
    "ZE5": "ZE5",
}


@dataclass
class SourceFile:
    source_pdf_id: str
    file_name: str
    file_path: str
    pdf_type: str
    content_hash: str
    size_bytes: int
    parsed_status: str
    error_reason: str
    created_at: str


@dataclass
class ExtractedText:
    source_pdf_id: str
    file_name: str
    text_length: int
    text_preview: str
    extracted_text: str
    created_at: str


@dataclass
class CourseResult:
    course_uid: str
    source_pdf_id: str
    date_course: str
    hippodrome: str
    nom_course: str
    arrivee: str
    non_partants: str
    disqualifies: str
    report_quality: str
    created_at: str


@dataclass
class Report:
    report_id: str
    course_uid: str
    source_pdf_id: str
    bet_type: str
    bet_label_raw: str
    selection_sorted: str
    rapport_officiel: str
    mise_reference: str
    retour_reference: str
    report_quality: str
    created_at: str


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def source_pdf_id(path: Path) -> str:
    return "PDF_" + sha256_file(path)[:20]


def normalize_decimal(value: str) -> str:
    value = value.strip()
    value = value.replace("\u00a0", " ")
    value = value.replace("€", "")
    value = value.replace(",", ".")
    value = re.sub(r"[^0-9.]", "", value)
    return value


def selection_sorted(selection: str) -> str:
    nums = re.findall(r"\d+", selection)
    if not nums:
        return ""
    nums_int = sorted({int(x) for x in nums})
    return "-".join(str(x) for x in nums_int)


def extract_text_pdf(path: Path) -> str:
    """
    Extraction texte PDF robuste.

    Priorité PyMuPDF, plus stable sur les PDF ZEturf.
    pdfplumber reste en fallback uniquement.
    Aucune OCR ici.
    """
    errors = []

    # 1) PyMuPDF prioritaire
    try:
        import fitz

        chunks = []
        doc = fitz.open(str(path))

        for page_index in range(len(doc)):
            try:
                page = doc[page_index]
                chunks.append(page.get_text("text") or "")
            except Exception as page_exc:
                errors.append(
                    f"fitz_page_{page_index + 1}:{type(page_exc).__name__}:{page_exc}"
                )

        text = "\n".join(chunks).strip()

        if text:
            return text

        errors.append("fitz:empty_text")
    except Exception as exc:
        errors.append(f"fitz:{type(exc).__name__}:{exc}")

    # 2) pdfplumber fallback, page par page
    try:
        import logging
        logging.getLogger("pdfminer").setLevel(logging.ERROR)

        import pdfplumber

        chunks = []
        with pdfplumber.open(str(path)) as pdf:
            for page_index, page in enumerate(pdf.pages):
                try:
                    chunks.append(page.extract_text() or "")
                except Exception as page_exc:
                    errors.append(
                        f"pdfplumber_page_{page_index + 1}:{type(page_exc).__name__}:{page_exc}"
                    )

        text = "\n".join(chunks).strip()

        if text:
            return text

        errors.append("pdfplumber:empty_text")
    except Exception as exc:
        errors.append(f"pdfplumber:{type(exc).__name__}:{exc}")

    raise RuntimeError("PDF text extraction failed: " + " | ".join(errors))



def parse_filename_course_uid(path: Path) -> Tuple[str, str, str, str]:
    """
    Format attendu proche :
    10_06_2026 - MARSEILLE BORELY - Prix de la Pomme_ Résultats & Rapports.pdf
    """
    stem = path.stem

    date_course = "ND"
    hippodrome = "ND"
    nom_course = stem

    m = re.match(r"(?P<date>\d{2}_\d{2}_\d{4})\s*-\s*(?P<hippo>.*?)\s*-\s*(?P<course>.*)", stem)

    if m:
        date_course = m.group("date").replace("_", "-")
        hippodrome = m.group("hippo").strip()
        nom_course = m.group("course").strip()

    nom_course = re.sub(r"[_-]?\s*Résultats.*$", "", nom_course, flags=re.I)
    nom_course = re.sub(r"[_-]?\s*Resultats.*$", "", nom_course, flags=re.I)
    nom_course = nom_course.strip(" _-")

    course_uid = f"{date_course} - {hippodrome} - {nom_course}"

    return course_uid, date_course, hippodrome, nom_course


def parse_arrivee(text: str) -> str:
    """
    Extraction conservatrice de l'arrivée.

    Règle de sécurité :
    - moins de 3 numéros = ND
    - ne jamais accepter un numéro isolé comme arrivée
    """
    patterns = [
        r"Arriv[ée]e\s*(?:définitive|officielle|provisoire)?\s*[:\-]?\s*([0-9][0-9\s\-\.\/]{4,80})",
        r"Classement\s*(?:définitif|officiel)?\s*[:\-]?\s*([0-9][0-9\s\-\.\/]{4,80})",
        r"Ordre\s+d['’]arriv[ée]e\s*[:\-]?\s*([0-9][0-9\s\-\.\/]{4,80})",
    ]

    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if not m:
            continue

        nums = re.findall(r"\d+", m.group(1))

        # Protection contre horaires, pages, fragments isolés.
        if len(nums) >= 3:
            return "-".join(nums[:8])

    return "ND"



def parse_non_partants(text: str) -> str:
    patterns = [
        r"Non[\s\-]?partants?\s*[:\-]?\s*([0-9,\s\-]+)",
        r"NP\s*[:\-]?\s*([0-9,\s\-]+)",
    ]

    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            found.extend(re.findall(r"\d+", m.group(1)))

    if not found:
        return ""

    return "-".join(sorted({str(int(x)) for x in found}))


def parse_disqualifies(text: str) -> str:
    patterns = [
        r"Disqualifi[ée]s?\s*[:\-]?\s*([0-9,\s\-]+)",
        r"Disqualifications?\s*[:\-]?\s*([0-9,\s\-]+)",
        r"DAI\s*[:\-]?\s*([0-9,\s\-]+)",
    ]

    found = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            found.extend(re.findall(r"\d+", m.group(1)))

    if not found:
        return ""

    return "-".join(sorted({str(int(x)) for x in found}))


def detect_bet_type(line: str) -> Tuple[str, str]:
    for label, code in BET_TYPE_ALIASES.items():
        if re.search(re.escape(label), line, flags=re.I):
            return code, label
    return "", ""


def _is_amount_line(line: str) -> bool:
    """
    Détecte une ligne montant ZEturf.
    Compatible affichage euro normal ou mojibake Windows.
    """
    line = line.strip()
    return bool(re.search(r"\b\d{1,4}(?:\s?\d{3})*[,.]\d{1,2}\b", line))


def _is_numeric_selection_line(line: str) -> bool:
    """
    Sélection officielle sans lettres.
    Accepte :
    13
    13-10
    13-10-9
    13-10-9-11
    Refuse les bonus type 5-6-7-A-A.
    """
    line = line.strip()
    if re.search(r"[A-Za-z]", line):
        return False
    return bool(re.fullmatch(r"\d{1,2}(?:-\d{1,2}){0,6}", line))


def _bet_type_raw_from_selection(selection: str) -> str:
    n = len(re.findall(r"\d+", selection))

    if n == 1:
        return "SIMPLE_RAW"
    if n == 2:
        return "PAIR_RAW"
    if n == 3:
        return "TRIO_RAW"
    if n == 4:
        return "ZE4_OR_2SUR4_RAW"
    if n == 5:
        return "ZE5_RAW"

    return "EXOTIC_RAW"


def parse_reports(text: str, course_uid: str, source_id: str) -> List[Report]:
    """
    Parser ZEturf Résultats & Rapports.

    Phase Sprint 2 :
    - extraction RAW fiable des couples sélection + montant
    - les montants sont souvent placés AVANT l'en-tête RAPPORTS
    - ne pas inventer le type exact CP/SP/TRIO si le PDF ne l'expose pas ligne par ligne

    Les lignes RAW serviront ensuite à construire un labeler spécifique.
    """
    created = now_utc()
    reports: List[Report] = []

    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()]
    lines = [x for x in lines if x]

    # 1) Ancien parser conservateur, quand la ligne contient tout.
    report_idx = 0

    for line in lines:
        bet_type, bet_label = detect_bet_type(line)
        if not bet_type:
            continue

        money_candidates = re.findall(r"\b\d{1,4}(?:\s?\d{3})*[,.]\d{1,2}\b", line)
        if not money_candidates:
            continue

        rapport = normalize_decimal(money_candidates[-1])
        if not rapport:
            continue

        before_amount = line[: line.rfind(money_candidates[-1])]
        nums = re.findall(r"\b\d+\b", before_amount)

        if not nums:
            continue

        if bet_type in {"SG", "SP"}:
            selection = nums[-1]
        elif bet_type in {"CG", "CP", "2SUR4"}:
            selection = "-".join(nums[-2:])
        elif bet_type == "TRIO":
            selection = "-".join(nums[-3:])
        elif bet_type == "ZE4":
            selection = "-".join(nums[-4:])
        elif bet_type == "ZE5":
            selection = "-".join(nums[-5:])
        else:
            selection = "-".join(nums)

        sel_sorted = selection_sorted(selection)

        if not sel_sorted:
            continue

        report_idx += 1
        report_id = f"RPT_{source_id}_{report_idx:03d}"

        reports.append(
            Report(
                report_id=report_id,
                course_uid=course_uid,
                source_pdf_id=source_id,
                bet_type=bet_type,
                bet_label_raw=bet_label,
                selection_sorted=sel_sorted,
                rapport_officiel=rapport,
                mise_reference="1",
                retour_reference=rapport,
                report_quality="PARSED_LINE_CONSERVATIVE",
                created_at=created,
            )
        )

    # 2) Fallback principal ZEturf : bloc avant RAPPORTS.
    # Les rapports utiles apparaissent souvent avant l'en-tête final RAPPORTS.
    rapports_indexes = [
        i for i, line in enumerate(lines)
        if line.strip().upper() == "RAPPORTS"
    ]

    seen = {
        (r.bet_type, r.selection_sorted, r.rapport_officiel)
        for r in reports
    }

    for block_id, rapport_index in enumerate(rapports_indexes, start=1):
        # Fenêtre large avant RAPPORTS. 90 capte généralement les simples, jumelés, trio, ZE4/ZE5.
        start_idx = max(0, rapport_index - 90)
        end_idx = rapport_index

        block = lines[start_idx:end_idx]

        i = 0
        while i < len(block) - 1:
            current = block[i].strip()
            nxt = block[i + 1].strip()

            if _is_numeric_selection_line(current) and _is_amount_line(nxt):
                sel_sorted = selection_sorted(current)
                rapport = normalize_decimal(nxt)
                bet_type_raw = _bet_type_raw_from_selection(current)

                if sel_sorted and rapport:
                    key = (bet_type_raw, sel_sorted, rapport)

                    if key not in seen:
                        report_idx += 1
                        report_id = f"RPT_{source_id}_{report_idx:03d}"

                        reports.append(
                            Report(
                                report_id=report_id,
                                course_uid=course_uid,
                                source_pdf_id=source_id,
                                bet_type=bet_type_raw,
                                bet_label_raw=f"RAW_PRE_RAPPORTS_BLOCK_{block_id}",
                                selection_sorted=sel_sorted,
                                rapport_officiel=rapport,
                                mise_reference="1",
                                retour_reference=rapport,
                                report_quality="RAW_PRE_RAPPORTS_NEEDS_LABEL",
                                created_at=created,
                            )
                        )

                        seen.add(key)

                i += 2
                continue

            i += 1

    return reports



def write_csv(path: Path, rows: List[dict], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_results_from_pdfs(pdf_paths: List[Path], output_dir: Path = OUTPUT_DIR) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)

    created = now_utc()

    source_files: List[SourceFile] = []
    extracted_texts: List[ExtractedText] = []
    course_results: List[CourseResult] = []
    reports: List[Report] = []

    for pdf_path in pdf_paths:
        pdf_path = pdf_path.resolve()
        sid = source_pdf_id(pdf_path)
        h = sha256_file(pdf_path)

        try:
            text = extract_text_pdf(pdf_path)
            parsed_status = "OK"
            error_reason = ""
        except Exception as exc:
            text = ""
            parsed_status = "ERROR"
            error_reason = f"{type(exc).__name__}: {exc}"

        source_files.append(
            SourceFile(
                source_pdf_id=sid,
                file_name=pdf_path.name,
                file_path=str(pdf_path),
                pdf_type="ZETURF_RESULTATS_RAPPORTS",
                content_hash=h,
                size_bytes=pdf_path.stat().st_size,
                parsed_status=parsed_status,
                error_reason=error_reason,
                created_at=created,
            )
        )

        if parsed_status != "OK":
            continue

        course_uid, date_course, hippodrome, nom_course = parse_filename_course_uid(pdf_path)

        extracted_texts.append(
            ExtractedText(
                source_pdf_id=sid,
                file_name=pdf_path.name,
                text_length=len(text),
                text_preview=text[:1000],
                extracted_text=text,
                created_at=created,
            )
        )

        arrivee = parse_arrivee(text)
        np = parse_non_partants(text)
        dq = parse_disqualifies(text)

        report_quality = "PARSED"

        if arrivee == "ND":
            report_quality = "REVIEW_ARRIVEE_ND"

        course_results.append(
            CourseResult(
                course_uid=course_uid,
                source_pdf_id=sid,
                date_course=date_course,
                hippodrome=hippodrome,
                nom_course=nom_course,
                arrivee=arrivee,
                non_partants=np,
                disqualifies=dq,
                report_quality=report_quality,
                created_at=created,
            )
        )

        reports.extend(parse_reports(text, course_uid, sid))

    write_csv(
        output_dir / "source_files.csv",
        [asdict(x) for x in source_files],
        list(SourceFile.__dataclass_fields__.keys()),
    )

    write_csv(
        output_dir / "extracted_text.csv",
        [asdict(x) for x in extracted_texts],
        list(ExtractedText.__dataclass_fields__.keys()),
    )

    write_csv(
        output_dir / "courses_results.csv",
        [asdict(x) for x in course_results],
        list(CourseResult.__dataclass_fields__.keys()),
    )

    write_csv(
        output_dir / "reports.csv",
        [asdict(x) for x in reports],
        list(Report.__dataclass_fields__.keys()),
    )

    return {
        "ok": True,
        "pdf_count": len(pdf_paths),
        "source_files_count": len(source_files),
        "courses_results_count": len(course_results),
        "reports_count": len(reports),
        "output_dir": str(output_dir),
        "files": {
            "source_files": str(output_dir / "source_files.csv"),
            "extracted_text": str(output_dir / "extracted_text.csv"),
            "courses_results": str(output_dir / "courses_results.csv"),
            "reports": str(output_dir / "reports.csv"),
        },
    }


def discover_result_pdfs() -> List[Path]:
    paths: List[Path] = []

    for folder in DEFAULT_RESULTS_DIRS:
        if not folder.exists():
            continue

        for pdf in folder.glob("*.pdf"):
            name = pdf.name.lower()
            if "result" in name or "résultat" in name or "rapport" in name:
                paths.append(pdf)

    return sorted(set(paths))


def main() -> None:
    pdfs = discover_result_pdfs()

    if not pdfs:
        print({
            "ok": False,
            "error": "aucun PDF Résultats & Rapports trouvé",
            "searched_dirs": [str(x) for x in DEFAULT_RESULTS_DIRS],
        })
        return

    result = build_results_from_pdfs(pdfs)
    print(result)


if __name__ == "__main__":
    main()