from pathlib import Path


REQUIRED_PROMPT_MARKERS = [
    "Applique GPI V19.1.4.",
    "Contrôle seulement la cohérence.",
    "Résumé Python :",
    "COURSE :",
    "QUALITE :",
    "PARTANTS_REELS :",
    "TOP6_SG :",
    "FAVORI :",
    "VARIATION_ROUGE_TOP6 :",
    "KILL_SWITCHS :",
    "VERDICT_PYTHON :",
    "ACTION_GPT :",
    "Réponds uniquement avec :",
    "Segment ROI",
    "Edge",
]


def validate_prompt_text(prompt_text):
    errors = []

    if prompt_text is None:
        return False, ["prompt_text vide"]

    text = str(prompt_text)

    for marker in REQUIRED_PROMPT_MARKERS:
        if marker not in text:
            errors.append(f"marqueur absent : {marker}")

    if "VERDICT_PYsimulation" in text:
        errors.append("coupure détectée autour de VERDICT_PYTHON")

    if "ACTION_GPT : contrôler uniquement" not in text:
        errors.append("ACTION_GPT incomplet ou absent")

    return len(errors) == 0, errors


def validate_prompt_file(prompt_path):
    path = Path(prompt_path)

    if not path.exists():
        return False, [f"prompt absent : {path}"]

    text = path.read_text(encoding="utf-8", errors="replace")
    return validate_prompt_text(text)
