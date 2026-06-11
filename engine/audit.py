from pathlib import Path

import pandas as pd


def safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def safe_text(value, default=""):
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    return str(value)


def audit_course(course_uid):
    decision_path = Path("data/output") / f"{course_uid}_decision_lite.csv"
    result_path = Path("data/results") / "result_input.csv"

    if not decision_path.exists():
        raise FileNotFoundError(f"Decision Lite introuvable : {decision_path}")

    if not result_path.exists():
        raise FileNotFoundError(f"Fichier result_input.csv introuvable : {result_path}")

    decisions = pd.read_csv(decision_path)
    results = pd.read_csv(result_path)

    result_row = results[results["course_uid"] == course_uid]

    if result_row.empty:
        raise ValueError(f"Aucun resultat trouve pour course_uid : {course_uid}")

    decision = decisions.iloc[0]
    result = result_row.iloc[0]

    mise = safe_float(result.get("mise"), 0.0)
    retour = safe_float(result.get("retour"), 0.0)

    gain_net = retour - mise

    if mise > 0:
        roi = gain_net / mise
        ratio = retour / mise
    else:
        roi = ""
        ratio = ""

    verdict_python = safe_text(decision.get("verdict"), "ND")
    selection = safe_text(result.get("selection"), "")
    ticket_final = selection if selection else "NO_BET"

    if verdict_python == "NO_BET":
        cause_principale = "NO_BET confirme par decision_lite"
        action = "conserver discipline"
    elif mise == 0:
        cause_principale = "decision non jouee"
        action = "paper ou simulation"
    elif retour > 0:
        cause_principale = "ticket paye"
        action = "auditer sans modifier doctrine"
    else:
        cause_principale = "ticket perdant"
        action = "auditer cause"

    audit = pd.DataFrame([{
        "course_uid": course_uid,
        "verdict_python": verdict_python,
        "structure_max": safe_text(decision.get("structure_max"), "ND"),
        "ticket_final": ticket_final,
        "type_pari": safe_text(result.get("type_pari"), "ND"),
        "selection": selection,
        "mise": mise,
        "retour": retour,
        "gain_net": gain_net,
        "roi": roi,
        "ratio": ratio,
        "segment_roi": safe_text(decision.get("segment_roi"), "ND"),
        "edge_status": safe_text(decision.get("edge_status"), "ND"),
        "arrivee": safe_text(result.get("arrivee"), "ND"),
        "rapport_officiel": safe_text(result.get("rapport_officiel"), "ND"),
        "ticket_joue_oui_non": safe_text(result.get("ticket_joue_oui_non"), "ND"),
        "kill_switch_codes": safe_text(decision.get("kill_switch_codes"), ""),
        "reason_codes": safe_text(decision.get("reason_codes"), ""),
        "cause_principale": cause_principale,
        "cause_secondaire": safe_text(decision.get("kill_switch_codes"), ""),
        "action": action,
    }])

    out = Path("data/output") / f"{course_uid}_journal_audit.csv"
    audit.to_csv(out, index=False, encoding="utf-8-sig")

    return out, audit
