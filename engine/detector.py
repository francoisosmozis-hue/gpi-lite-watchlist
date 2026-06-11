import re
import unicodedata


def _strip_accents(value):
    value = str(value or "")
    value = unicodedata.normalize("NFKD", value)
    return "".join(c for c in value if not unicodedata.combining(c))


def _repair_common_mojibake(value):
    text = str(value or "")

    replacements = {
        "Ã©": "é",
        "Ã¨": "è",
        "Ãª": "ê",
        "Ã«": "ë",
        "Ã ": "à",
        "Ã¢": "â",
        "Ã´": "ô",
        "Ã»": "û",
        "Ã§": "ç",
        "Ã‰": "É",
        "Ãˆ": "È",
        "ÃŠ": "Ê",
        "Â ": " ",
        "Â": " ",
        "â‚¬": "€",
        "ï„": " ",
        "ï…": " ",
    }

    for bad, good in replacements.items():
        text = text.replace(bad, good)

    return text


def _norm(value):
    text = _repair_common_mojibake(value)
    text = _strip_accents(text).lower()
    text = " ".join(text.split())
    return text


def _count_hits(text, patterns):
    hits = 0

    for pattern in patterns:
        if pattern in text:
            hits += 1

    return hits


def detect_pdf_type(text: str) -> str:
    raw = str(text or "")
    t = _norm(raw)

    # 1. ZEturf Resultats & Rapports post-course.
    # Prioritaire pour eviter de classer un PDF resultats en pronostics a cause du menu ZEturf.
    result_hits = _count_hits(t, [
        "arrivee officielle",
        "arrivee provisoire",
        "rapports officiels",
        "resultats et rapports",
        "rapport officiel",
        "disqualifie",
        "non partant",
    ])

    has_result_structure = bool(
        re.search(r"arrivee\s+(officielle|provisoire)\s*:", t)
        or re.search(r"rapport[s]?\s+officiel[s]?", t)
    )

    if has_result_structure and result_hits >= 2:
        return "ZETURF_RESULTATS_RAPPORTS"

    # 2. Boturfers / stats.
    if (
        "boturfers" in t
        or "statistiques" in t and "musique" in t and "driver" in t
    ):
        return "BOTURFERS_STATS"

    # 3. ZEturf Pronostics & Cotes pre-course.
    # Detection tolerante pour les PDF dont le texte est extrait avec accents casses ou blocs menus.
    pre_course_hits = _count_hits(t, [
        "programme et pronostics",
        "pronostics",
        "cotes",
        "parier",
        "notre pronostic",
        "notre choix",
        "selectionnez votre pari",
        "selectionner votre pari",
        "ticket de jeu",
        "depart",
        "partants",
        "terrain",
        "conditions",
        "zeturfpro",
        "z eturf pro",
    ])

    has_race_context = bool(
        re.search(r"\br[0-9]+\s*c[0-9]+\b", t)
        or re.search(r"\bprix\s+[a-z0-9]", t)
        or "partants" in t
    )

    has_betting_context = bool(
        "cotes" in t
        or "parier" in t
        or "selectionnez votre pari" in t
        or "ticket de jeu" in t
        or "notre pronostic" in t
    )

    if pre_course_hits >= 3 and has_race_context and has_betting_context:
        return "ZETURF_PRONOS_COTES"

    # 4. LeTrot partants.
    if (
        "letrot" in t
        or "partants" in t and "driver" in t and "entraineur" in t
    ):
        return "LETROT_PARTANTS"

    return "INCONNU"
