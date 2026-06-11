from pathlib import Path
import re
import pandas as pd


def _clean_text(value):
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\u202f", " ")
    text = text.replace("\xa0", " ")
    text = re.sub(r"[^\x20-\x7EÀ-ÿ€'’\-\.\s/()]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _to_float(value):
    try:
        if value is None:
            return None
        text = str(value).replace(",", ".")
        text = re.sub(r"[^0-9.]", "", text)
        if not text:
            return None
        return float(text)
    except Exception:
        return None


def _extract_text_pymupdf(pdf_path):
    import fitz

    parts = []

    doc = fitz.open(str(pdf_path))
    for page in doc:
        parts.append(page.get_text("text") or "")

    return "\n".join(parts)


def _is_horse_name(line):
    text = _clean_text(line)

    if not text:
        return False

    reject_words = [
        "PROGRAMME",
        "PRONOSTICS",
        "RESULTATS",
        "RÉSULTATS",
        "RAPPORTS",
        "PARIER",
        "COTES",
        "NOTRE",
        "CHOIX",
        "JEU RESPONSABLE",
        "DISTANCE",
        "MUSIQUE",
        "GAINS",
        "RECORD",
        "PARTANTS",
        "VINCENNES",
        "DEPART",
        "DÉPART",
    ]

    upper = text.upper()

    if any(word in upper for word in reject_words):
        return False

    if re.search(r"\d", text):
        return False

    if len(text) < 2:
        return False

    return True


def _looks_like_driver_line(line):
    text = _clean_text(line)

    return " - " in text or re.search(r"\b[A-Z][a-z]+ [A-Z]\.", text) is not None


def _looks_like_sa(line):
    text = _clean_text(line)
    return re.match(r"^[HFM]/\d+$", text) is not None


def _looks_like_distance(line):
    text = _clean_text(line)
    return re.match(r"^\d{3,4}m$", text) is not None


def _looks_like_record(line):
    text = _clean_text(line)
    return "'" in text and '"' in text


def _looks_like_gains(line):
    text = _clean_text(line)
    return "€" in text or re.search(r"\d+\s+\d+", text) is not None


def _looks_like_musique(line):
    text = _clean_text(line)
    return re.search(r"\b[0-9D][a-zmhs]\b", text, re.IGNORECASE) is not None


def _looks_like_sg(line):
    text = _clean_text(line)
    value = _to_float(text)

    if value is None:
        return False

    return 1.0 <= value <= 300.0


def parse_zeturf_morning_pymupdf(pdf_path, course_uid=None, snapshot_time="09:00"):
    pdf_path = Path(pdf_path)
    text = _extract_text_pymupdf(pdf_path)

    lines = [_clean_text(x) for x in text.splitlines()]
    lines = [x for x in lines if x]

    if course_uid is None:
        course_uid = pdf_path.stem

    rows = []
    i = 0

    while i < len(lines):
        current = lines[i]

        if not re.fullmatch(r"\d{1,2}", current):
            i += 1
            continue

        numero = int(current)

        if not (1 <= numero <= 25):
            i += 1
            continue

        if i + 8 >= len(lines):
            i += 1
            continue

        cheval = lines[i + 1]
        driver_entraineur = lines[i + 2]
        sa = lines[i + 3]
        distance = lines[i + 4]
        record = lines[i + 5]
        gains = lines[i + 6]
        musique = lines[i + 7]
        sg_raw = lines[i + 8]

        if not _is_horse_name(cheval):
            i += 1
            continue

        if not _looks_like_driver_line(driver_entraineur):
            i += 1
            continue

        if not _looks_like_sa(sa):
            i += 1
            continue

        if not _looks_like_distance(distance):
            i += 1
            continue

        if not _looks_like_record(record):
            i += 1
            continue

        if not _looks_like_gains(gains):
            i += 1
            continue

        if not _looks_like_musique(musique):
            i += 1
            continue

        sg = _to_float(sg_raw)

        if sg is None:
            i += 1
            continue

        rows.append({
            "course_uid": course_uid,
            "snapshot_time": snapshot_time,
            "numero": numero,
            "cheval": cheval,
            "SG": sg,
            "SP_min": None,
            "SP_max": None,
            "statut": "PARTANT",
            "is_np": 0,
            "extraction_confidence": "FALLBACK_PYMUPDF",
        })

        i += 9

    if not rows:
        return pd.DataFrame(columns=[
            "course_uid",
            "snapshot_time",
            "numero",
            "cheval",
            "SG",
            "SP_min",
            "SP_max",
            "statut",
            "is_np",
            "extraction_confidence",
        ])

    df = pd.DataFrame(rows)

    df = df.drop_duplicates(subset=["numero"], keep="first")
    df = df.sort_values("numero").reset_index(drop=True)

    return df
