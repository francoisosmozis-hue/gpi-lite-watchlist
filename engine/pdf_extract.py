import re
from pathlib import Path

import fitz
import pandas as pd


def extract_text_from_pdf(pdf_path):
    pdf_path = Path(pdf_path)
    doc = fitz.open(pdf_path)

    pages = []
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text")
        pages.append({
            "page": i,
            "text": text
        })

    return pages


def save_extracted_text(pdf_path, output_path):
    pages = extract_text_from_pdf(pdf_path)

    content = []
    for p in pages:
        content.append(f"--- PAGE {p['page']} ---")
        content.append(p["text"])

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(content), encoding="utf-8")

    return output_path


def clean_pdf_text(text):
    replacements = {
        "ï„…": " ",
        "ï„´": " ",
        "ï„³": " ",
        "ï„": " ",
        "ï…“": " ",
        "ï„Š": " ",
        "ï„©": " ",
        "ï…Œ": " ",
        "ï„º": " ",
        "ï„†": " ",
        "ï…Ž": " ",
        "ï„¾": " ",
        "ï„«": " ",
        "ï„‚": " ",
        "ï„ƒ": " ",
        "ï„’": " ",
        "ï„„": " ",
    }

    cleaned = text
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)

    return cleaned


def is_number_line(line):
    return re.fullmatch(r"\d{1,2}", line.strip()) is not None


def is_odds_like(line):
    value = line.strip().replace(",", ".")
    return re.fullmatch(r"\d+(\.\d+)?", value) is not None


def is_not_real_horse_name(line):
    value = line.strip().upper()

    if value in ["SG", "SP", "ZS", "ZC"]:
        return True

    if value in ["PARIER COTES", "COTES", "PARIER"]:
        return True

    if is_odds_like(value):
        return True

    if "RAFRAÃŽCHIR" in value:
        return True

    if "PRÃ‰SENCE DE CHEVAUX FAVORIS" in value:
        return True

    if "SELECTIONNEZ" in value or "SÃ‰LECTIONNEZ" in value:
        return True

    return False


def parse_float(value):
    if value is None:
        return None

    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def extract_odds_from_block(block):
    odds = re.findall(r"\d+[,.]\d+", block)
    return [parse_float(x) for x in odds]


def parse_zeturf_pronostics_text(text, course_uid="ND", snapshot_time="ND"):
    rows = []

    cleaned = clean_pdf_text(text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]

    try:
        start = next(
            i for i, line in enumerate(lines)
            if "PARIER COTES" in line.upper()
        )
    except StopIteration:
        start = 0

    try:
        end = next(
            i for i, line in enumerate(lines)
            if "NOTRE PRONOSTIC" in line.upper()
        )
    except StopIteration:
        end = len(lines)

    useful = lines[start:end]

    i = 0
    while i < len(useful):
        line = useful[i]

        if not is_number_line(line):
            i += 1
            continue

        numero = int(line)

        if i + 1 >= len(useful):
            i += 1
            continue

        cheval = useful[i + 1].strip()

        if is_not_real_horse_name(cheval):
            i += 1
            continue

        is_np = 1 if "(NP)" in cheval.upper() else 0
        statut = "NP" if is_np else "PARTANT"
        cheval_clean = cheval.replace("(NP)", "").strip()

        block_lines = []
        j = i + 2

        while j < len(useful):
            current = useful[j].strip()

            if "PRÃ‰SENCE DE CHEVAUX FAVORIS" in current.upper():
                break

            if "PRESENCE DE CHEVAUX FAVORIS" in current.upper():
                break

            if is_number_line(current) and j + 1 < len(useful):
                next_line = useful[j + 1].strip()

                if not is_not_real_horse_name(next_line):
                    break

            block_lines.append(current)
            j += 1

        block = " ".join(block_lines)
        odds = extract_odds_from_block(block)

        sg = None
        sp_min = None
        sp_max = None

        if not is_np:
            if len(odds) >= 2:
                sg = odds[1]
            elif len(odds) == 1:
                sg = odds[0]

            if len(odds) >= 4:
                sp_min = odds[2]
                sp_max = odds[3]

        rows.append({
            "course_uid": course_uid,
            "snapshot_time": snapshot_time,
            "numero": numero,
            "cheval": cheval_clean,
            "SG": sg,
            "SP_min": sp_min,
            "SP_max": sp_max,
            "statut": statut,
            "is_np": is_np,
            "extraction_confidence": "A_CONTROLER"
        })

        i = j

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.drop_duplicates(subset=["numero"], keep="first")
        df = df.sort_values("numero").reset_index(drop=True)

    return df
