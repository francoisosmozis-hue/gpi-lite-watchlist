import re
import unicodedata

import pandas as pd

from engine.pdf_extract import extract_text_from_pdf
from engine.detector import detect_pdf_type


def strip_accents(value):
    value = str(value)
    return "".join(
        c for c in unicodedata.normalize("NFD", value)
        if unicodedata.category(c) != "Mn"
    )


def normalize_text(text):
    return (
        text.replace("€", " EUR")
            .replace("\u00a0", " ")
            .replace("", " ")
            .replace("", " ")
            .replace("", " ")
            .replace("", " ")
            .replace("", " ")
    )


def extract_full_text_from_pdf(pdf_path):
    pages = extract_text_from_pdf(pdf_path)
    return "\n".join(p["text"] for p in pages)


def parse_course_uid_from_results(text, default_uid="ND"):
    # Exemple : R10 C1 LA CAPELLE
    match = re.search(r"\bR\s*(\d+)\s*C\s*(\d+)\b", text, flags=re.IGNORECASE)

    if not match:
        return default_uid

    reunion = match.group(1)
    course = match.group(2)

    date_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)

    if date_match:
        day, month, year = date_match.groups()
        return f"{year}{month}{day}_R{reunion}C{course}"

    return f"R{reunion}C{course}"


def parse_arrivee(text):
    text_norm = strip_accents(text)
    text_one_line = " ".join(text_norm.split())

    patterns = [
        r"Arrivee officielle\s*:\s*([0-9]{1,2}(?:\s*-\s*[0-9]{1,2})*)",
        r"Arrivee provisoire\s*:\s*([0-9]{1,2}(?:\s*-\s*[0-9]{1,2})*)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text_one_line, flags=re.IGNORECASE)
        if match:
            raw = match.group(1)
            nums = re.findall(r"\b\d{1,2}\b", raw)
            if nums:
                return "-".join(nums)

    return "ND"


def parse_non_partants(text):
    text_norm = strip_accents(text)

    match = re.search(
        r"Non-partant\s*\(s\)\s*:\s*([0-9\s\-]+)",
        text_norm,
        flags=re.IGNORECASE
    )

    if not match:
        return ""

    nums = re.findall(r"\b\d{1,2}\b", match.group(1))
    return "-".join(nums)


def parse_disqualifies(text):
    text_norm = strip_accents(text)

    match = re.search(
        r"Disqualifie\s*\(s\)\s*:\s*([0-9\s\-]+)",
        text_norm,
        flags=re.IGNORECASE
    )

    if not match:
        return ""

    nums = re.findall(r"\b\d{1,2}\b", match.group(1))
    return "-".join(nums)


def parse_results_pdf(pdf_path, default_uid="ND"):
    raw_text = extract_full_text_from_pdf(pdf_path)
    text = normalize_text(raw_text)

    pdf_type = detect_pdf_type(text)

    if pdf_type != "ZETURF_RESULTATS_RAPPORTS":
        raise ValueError(f"PDF non reconnu comme Resultats & Rapports : {pdf_type}")

    course_uid = parse_course_uid_from_results(text, default_uid=default_uid)
    arrivee = parse_arrivee(text)
    non_partants = parse_non_partants(text)
    disqualifies = parse_disqualifies(text)

    return {
        "course_uid": course_uid,
        "arrivee": arrivee,
        "non_partants": non_partants,
        "disqualifies": disqualifies,
        "rapport_officiel": "ND",
    }


def build_result_input_from_pdf(pdf_path, default_uid="ND"):
    parsed = parse_results_pdf(pdf_path, default_uid=default_uid)

    df = pd.DataFrame([{
        "course_uid": parsed["course_uid"],
        "arrivee": parsed["arrivee"],
        "rapport_officiel": parsed["rapport_officiel"],
        "type_pari": "NO_BET",
        "selection": "",
        "mise": 0,
        "retour": 0,
        "ticket_joue_oui_non": "non",
        "non_partants": parsed["non_partants"],
        "disqualifies": parsed["disqualifies"],
    }])

    return df