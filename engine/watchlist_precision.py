from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


ND_VALUES = {"", "ND", "N/A", "NA", "NONE", "NULL", "NAN", "NON", "-"}


def _s(value: Any, default: str = "ND") -> str:
    if value is None:
        return default
    text = str(value).strip()
    if text.upper() in ND_VALUES:
        return default
    return text


def _upper(value: Any) -> str:
    return _s(value, "").upper()


def _f(value: Any) -> Optional[float]:
    text = _s(value, "")
    if not text:
        return None
    text = text.replace(",", ".").replace("%", "").strip()
    try:
        return float(text)
    except Exception:
        return None


def _pick(row: Dict[str, Any], *names: str, default: str = "ND") -> str:
    for name in names:
        if name in row:
            value = _s(row.get(name), default="")
            if value:
                return value
    return default


def _yes(value: Any) -> bool:
    return _upper(value) in {"OUI", "YES", "TRUE", "1", "Y"}


def _contains_any(text: str, patterns: List[str]) -> bool:
    up = text.upper()
    return any(p.upper() in up for p in patterns)


def _unique(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        item = _s(item, "")
        if not item:
            continue
        key = item.upper()
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _split_reasons(raw: str) -> List[str]:
    text = _s(raw, "")
    if not text:
        return []
    parts = re.split(r"\s*\|\s*|\s*;\s*|\n+|\r+", text)
    return _unique([p.strip() for p in parts if p.strip()])


def has_core_data_nd(row: Dict[str, Any]) -> bool:
    """True si la Watchlist ne dispose pas des champs coeur suffisants."""
    core_fields = [
        _pick(row, "qualite", "quality", default="ND"),
        _pick(row, "verdict", "verdict_python", "latest_verdict", default="ND"),
        _pick(row, "edge_status", "edge", "latest_edge", default="ND"),
        _pick(row, "raison", "reason", "reason_codes", default="ND"),
    ]
    return all(_upper(value) == "ND" for value in core_fields)


def derive_volatilite_risk(row: Dict[str, Any]) -> str:
    status = _upper(_pick(
        row,
        "volatilite_status",
        "latest_volatilite_status",
        "odds_volatilite_sg_statut",
        "volatilite_risk_pre_h5",
        default="ND",
    ))
    max_pct = _f(_pick(
        row,
        "volatilite_top6_max_pct",
        "latest_volatilite_top6_max_pct",
        "variation_top6_max_pct",
        default="ND",
    ))

    if "KILL" in status:
        return "KILL_PROBABLE"
    if max_pct is not None:
        if max_pct >= 35:
            return "KILL_PROBABLE"
        if max_pct >= 20:
            return "HIGH"
        if max_pct >= 10:
            return "MEDIUM"
        return "LOW"
    if status in {"OK", "STABLE"}:
        return "LOW"
    return "ND"


def derive_course_gate_preview(row: Dict[str, Any]) -> str:
    strict_allowed = _pick(
        row,
        "strict_allowed_oui_non",
        "decision_strict_allowed",
        "latest_strict_allowed_oui_non",
        "latest_decision_strict_allowed",
        default="ND",
    )
    block_reason = _pick(
        row,
        "decision_block_reason",
        "latest_decision_block_reason",
        "strict_gate_summary",
        "latest_strict_gate_summary",
        default="ND",
    )
    verdict = _upper(_pick(row, "verdict", "verdict_python", "latest_verdict", default="ND"))
    structure = _upper(_pick(row, "structure_max", "latest_structure", default="ND"))

    if _yes(strict_allowed) and verdict != "NO_BET" and "SIMULATION" not in structure:
        return "OPEN"
    if _s(block_reason, "") or verdict == "NO_BET" or "KILL" in derive_volatilite_risk(row):
        return "CLOSED"
    return "PRE_H5_UNKNOWN"


def derive_strict_ticket_status(row: Dict[str, Any]) -> str:
    ticket = _upper(_pick(row, "ticket_exact", "latest_ticket", default="ND"))
    verdict = _upper(_pick(row, "verdict", "verdict_python", "latest_verdict", default="ND"))
    structure = _upper(_pick(row, "structure_max", "latest_structure", default="ND"))

    if derive_course_gate_preview(row) == "OPEN" and ticket not in {"ND", "NO_BET"} and verdict != "NO_BET":
        return "STRICT_ALLOWED"
    if "SIMULATION" in structure:
        return "NO_BET_SIMULATION_ONLY"
    return "NO_BET"


def derive_cp_status(row: Dict[str, Any]) -> Dict[str, str]:
    cp_sec = _pick(row, "cp_sec_candidate", "latest_cp_sec_candidate", default="ND")
    cp3 = _pick(row, "cp3_candidate", "latest_cp3_candidate", default="ND")
    cp_sg_status = _upper(_pick(row, "cp_sg_filter_status", "latest_cp_sg_filter_status", default="ND"))
    cp3_sg_status = _upper(_pick(row, "cp3_sg_filter_status", "latest_cp3_sg_filter_status", default="ND"))
    structure = _upper(_pick(row, "structure_max", "latest_structure", default="ND"))

    if cp_sec != "ND" and cp_sg_status in {"OK", "VERT", "UTILISABLE"}:
        cp_sec_status = "A_TESTER_H5"
    elif cp_sec != "ND":
        cp_sec_status = "FRAGILE"
    else:
        cp_sec_status = "ND"

    if cp3 == "ND" or not cp3:
        cp3_status = "REFUS_ND"
    elif "CP3" not in structure:
        cp3_status = "REFUS_STRUCTURE"
    elif cp3_sg_status in {"OK", "VERT", "UTILISABLE"}:
        cp3_status = "A_TESTER_H5"
    else:
        cp3_status = "FRAGILE"

    if cp_sec != "ND" and (cp3 == "ND" or cp3_status.startswith("REFUS")):
        cp_sec_domine = "oui"
    elif cp_sec != "ND" and cp3 != "ND":
        cp_sec_domine = "a_tester"
    else:
        cp_sec_domine = "ND"

    return {
        "cp_sec_status": cp_sec_status,
        "cp3_status": cp3_status,
        "cp_sec_domine": cp_sec_domine,
    }


def derive_favorite_value_status(row: Dict[str, Any]) -> str:
    fav_sg = _f(_pick(row, "favorite_sg", "favori_sg", "sg_favori", "cote_favori", default="ND"))
    fragile = _upper(_pick(row, "favorite_fragile", "favori_fragile", default="ND"))

    if fav_sg is None:
        return "ND"

    sous_paye = fav_sg <= 2.5
    is_fragile = fragile in {"OUI", "YES", "TRUE", "1", "FRAGILE"}

    if sous_paye and is_fragile:
        return "SOUS_PAYE_FRAGILE"
    if sous_paye:
        return "SOUS_PAYE"
    if is_fragile:
        return "FRAGILE"
    return "OK"


def derive_refusal_reasons(row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []

    raw_reason = " | ".join([
        _pick(row, "decision_block_reason", "latest_decision_block_reason", default=""),
        _pick(row, "strict_gate_summary", "latest_strict_gate_summary", default=""),
        _pick(row, "raison", "reason", "reason_codes", default=""),
        _pick(row, "paper_candidate_reason", "latest_paper_candidate_reason", default=""),
    ])

    raw_up = raw_reason.upper()
    structure = _upper(_pick(row, "structure_max", "latest_structure", default="ND"))
    verdict = _upper(_pick(row, "verdict", "verdict_python", "latest_verdict", default="ND"))
    edge = _upper(_pick(row, "edge_status", "edge", "latest_edge", default="ND"))
    qualite = _upper(_pick(row, "qualite", "quality", default="ND"))
    priority = _upper(_pick(row, "priorite_h5", "priority_h5", default="ND"))

    if "KILL" in derive_volatilite_risk(row) or "VOLATILITE_KILL" in raw_up:
        reasons.append("VOLATILITE_KILL_SWITCH")
    if "SIMULATION" in structure or "STRUCTURE_SIMULATION" in raw_up:
        reasons.append("STRUCTURE_SIMULATION")
    if verdict == "NO_BET":
        reasons.append("VERDICT_NO_BET")
    if edge in {"EDGE_ABSENT", "ABSENT"} or "EDGE_ABSENT" in raw_up:
        reasons.append("EDGE_ABSENT")
    if edge in {"EDGE_FRAGILE", "FRAGILE"}:
        reasons.append("EDGE_FRAGILE")
    if qualite == "D":
        reasons.append("QUALITE_D")
    if "PROXY_ROUGE" in raw_up or "SEGMENT_ROUGE" in raw_up:
        reasons.append("PROXY_ROUGE")
    if "PROXY_ND" in raw_up or "SEGMENT_ND" in raw_up:
        reasons.append("PROXY_ND")
    if "SP_REJETE" in raw_up or "SEGMENT_SP_REJETE" in raw_up:
        reasons.append("SP_SEGMENT_REJETE")
    if "CP_SEC" in structure and _pick(row, "cp3_candidate", "latest_cp3_candidate", default="ND") == "ND":
        reasons.append("CP3_ND_CP_SEC_PRIORITAIRE")

    if has_core_data_nd(row):
        reasons.insert(0, "DATA_ND")

    if priority == "P9":
        reasons.append("PRIORITE_P9_NO_BET")

    reasons.extend(_split_reasons(raw_reason))
    reasons = _unique(reasons)

    while len(reasons) < 3:
        reasons.append("ND")

    return reasons[:3]







def enrich_watchlist_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)

    cp = derive_cp_status(row)
    reasons = derive_refusal_reasons(row)

    out["watchlist_quality"] = derive_watchlist_quality(row)
    out["h5_surveillance_value"] = derive_h5_surveillance_value(row)
    out["course_gate_preview"] = derive_course_gate_preview(row)
    out["strict_ticket_status"] = derive_strict_ticket_status(row)
    out["volatilite_risk_pre_h5"] = derive_volatilite_risk(row)
    out["favorite_value_status"] = derive_favorite_value_status(row)
    out["cp_sec_status"] = cp["cp_sec_status"]
    out["cp3_status"] = cp["cp3_status"]
    out["cp_sec_domine"] = cp["cp_sec_domine"]
    out["top_refusal_reason_1"] = reasons[0]
    out["top_refusal_reason_2"] = reasons[1]
    out["top_refusal_reason_3"] = reasons[2]
    out["action_gpt"] = derive_action_gpt(row)

    return out

# ---------------------------------------------------------------------------
# WATCHLIST PRECISION OVERRIDE V0.2
# Regle metier : P1 + donnees coeur ND = qualite C / surveillance faible.
# ---------------------------------------------------------------------------

def _core_data_nd_v02(row: Dict[str, Any]) -> bool:
    core_fields = [
        _pick(row, "qualite", "quality", default="ND"),
        _pick(row, "verdict", "verdict_python", "latest_verdict", default="ND"),
        _pick(row, "edge_status", "edge", "latest_edge", default="ND"),
        _pick(row, "raison", "reason", "reason_codes", default="ND"),
    ]
    return all(_upper(value) == "ND" for value in core_fields)


def derive_refusal_reasons(row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []

    if _core_data_nd_v02(row):
        reasons.append("DATA_ND")

    raw_reason = " | ".join([
        _pick(row, "decision_block_reason", "latest_decision_block_reason", default=""),
        _pick(row, "strict_gate_summary", "latest_strict_gate_summary", default=""),
        _pick(row, "raison", "reason", "reason_codes", default=""),
        _pick(row, "paper_candidate_reason", "latest_paper_candidate_reason", default=""),
    ])

    raw_up = raw_reason.upper()
    structure = _upper(_pick(row, "structure_max", "latest_structure", default="ND"))
    verdict = _upper(_pick(row, "verdict", "verdict_python", "latest_verdict", default="ND"))
    edge = _upper(_pick(row, "edge_status", "edge", "latest_edge", default="ND"))
    qualite = _upper(_pick(row, "qualite", "quality", default="ND"))
    priority = _upper(_pick(row, "priorite_h5", "priority_h5", default="ND"))

    if "KILL" in derive_volatilite_risk(row) or "VOLATILITE_KILL" in raw_up:
        reasons.append("VOLATILITE_KILL_SWITCH")
    if "SIMULATION" in structure or "STRUCTURE_SIMULATION" in raw_up:
        reasons.append("STRUCTURE_SIMULATION")
    if verdict == "NO_BET":
        reasons.append("VERDICT_NO_BET")
    if edge in {"EDGE_ABSENT", "ABSENT"} or "EDGE_ABSENT" in raw_up:
        reasons.append("EDGE_ABSENT")
    if edge in {"EDGE_FRAGILE", "FRAGILE"}:
        reasons.append("EDGE_FRAGILE")
    if qualite == "D":
        reasons.append("QUALITE_D")
    if "PROXY_ROUGE" in raw_up or "SEGMENT_ROUGE" in raw_up:
        reasons.append("PROXY_ROUGE")
    if "PROXY_ND" in raw_up or "SEGMENT_ND" in raw_up:
        reasons.append("PROXY_ND")
    if "SP_REJETE" in raw_up or "SEGMENT_SP_REJETE" in raw_up:
        reasons.append("SP_SEGMENT_REJETE")
    if "CP_SEC" in structure and _pick(row, "cp3_candidate", "latest_cp3_candidate", default="ND") == "ND":
        reasons.append("CP3_ND_CP_SEC_PRIORITAIRE")
    if priority == "P9":
        reasons.append("PRIORITE_P9_NO_BET")

    reasons.extend(_split_reasons(raw_reason))
    reasons = _unique(reasons)

    while len(reasons) < 3:
        reasons.append("ND")

    return reasons[:3]








def enrich_watchlist_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)

    cp = derive_cp_status(row)
    reasons = derive_refusal_reasons(row)

    out["watchlist_quality"] = derive_watchlist_quality(row)
    out["h5_surveillance_value"] = derive_h5_surveillance_value(row)
    out["course_gate_preview"] = derive_course_gate_preview(row)
    out["strict_ticket_status"] = derive_strict_ticket_status(row)
    out["volatilite_risk_pre_h5"] = derive_volatilite_risk(row)
    out["favorite_value_status"] = derive_favorite_value_status(row)
    out["cp_sec_status"] = cp["cp_sec_status"]
    out["cp3_status"] = cp["cp3_status"]
    out["cp_sec_domine"] = cp["cp_sec_domine"]
    out["top_refusal_reason_1"] = reasons[0]
    out["top_refusal_reason_2"] = reasons[1]
    out["top_refusal_reason_3"] = reasons[2]
    out["action_gpt"] = derive_action_gpt(row)

    return out

# ---------------------------------------------------------------------------
# WATCHLIST PRECISION FIX V0.3 - ND DETECTION
# Cause: _upper("ND") retourne "", donc DATA_ND ne declenchait pas.
# ---------------------------------------------------------------------------

def _is_nd_value_v03(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().upper() in ND_VALUES


def _core_data_nd_v02(row: Dict[str, Any]) -> bool:
    core_fields = [
        _pick(row, "qualite", "quality", default="ND"),
        _pick(row, "verdict", "verdict_python", "latest_verdict", default="ND"),
        _pick(row, "edge_status", "edge", "latest_edge", default="ND"),
        _pick(row, "raison", "reason", "reason_codes", default="ND"),
    ]
    return all(_is_nd_value_v03(value) for value in core_fields)




def derive_refusal_reasons(row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []

    if _core_data_nd_v02(row):
        reasons.append("DATA_ND")

    structure = _upper(_pick(row, "structure_max", "latest_structure", default="ND"))
    cp3 = _pick(row, "cp3_candidate", "latest_cp3_candidate", default="ND")

    if "CP_SEC" in structure and _is_nd_value_v03(cp3):
        reasons.append("CP3_ND_CP_SEC_PRIORITAIRE")

    reasons = _unique(reasons)

    while len(reasons) < 3:
        reasons.append("ND")

    return reasons[:3]





# ---------------------------------------------------------------------------
# WATCHLIST PRECISION FIX V0.4
# A interdit sans filtre CP/SG OK et somme CP/SG exploitable.
# ---------------------------------------------------------------------------

def derive_watchlist_quality(row: Dict[str, Any]) -> str:
    priority = _upper(_pick(row, "priorite_h5", "priority_h5", default="ND"))
    structure = _upper(_pick(row, "structure_max", "latest_structure", default="ND"))
    qualite = str(_pick(row, "qualite", "quality", default="ND")).strip().upper()
    edge = _upper(_pick(row, "edge_status", "edge", "latest_edge", default="ND"))
    vol = derive_volatilite_risk(row)

    cp_filter = str(_pick(
        row,
        "cp_sg_filter_status",
        "latest_cp_sg_filter_status",
        "cp_sec_sg_filter_status_9h",
        "cp_sec_sg_filter_status",
        default="ND"
    )).strip().upper()

    cp_sum = _f(_pick(
        row,
        "cp_sg_pair_sum",
        "latest_cp_sg_pair_sum",
        "cp_sec_sg_sum_9h",
        "cp_sec_sg_sum",
        default="ND"
    ))

    if priority == "P9" or structure == "NO_BET" or qualite == "D":
        return "D"

    if vol == "KILL_PROBABLE":
        return "D"

    if _core_data_nd_v02(row):
        return "C"

    if "SIMULATION" in structure:
        return "C"

    if edge in {"EDGE_ABSENT", "ABSENT"}:
        return "C"

    if edge in {"EDGE_FRAGILE", "FRAGILE"}:
        return "B"

    if structure in {"CP_SEC_ETUDE", "CP3_ETUDE", "SP_ETUDE"} and qualite in {"A", "B"}:
        if cp_filter == "OK" and cp_sum is not None and cp_sum >= 8:
            return "A"
        if cp_filter == "OK":
            return "B"
        return "C"

    if "ETUDE" in structure:
        return "C"

    return "C"

# ---------------------------------------------------------------------------
# WATCHLIST PRECISION OVERRIDE V0.5
# Effective final definitions. Keep at end of file.
# ---------------------------------------------------------------------------

def derive_h5_surveillance_value(row: Dict[str, Any]) -> str:
    quality = derive_watchlist_quality(row)
    structure = _upper(_pick(row, "structure_max", "latest_structure", default="ND"))

    if quality == "D":
        return "NO_BET_STRUCTUREL"
    if quality == "A":
        return "H5_PRIORITAIRE"
    if quality == "B":
        return "H5_SUIVI_SIMPLE"
    if "SIMULATION" in structure:
        return "SURVEILLANCE_FAIBLE"
    return "SURVEILLANCE_FAIBLE"


def derive_action_gpt(row: Dict[str, Any]) -> str:
    strict_status = derive_strict_ticket_status(row)
    quality = derive_watchlist_quality(row)
    h5_value = derive_h5_surveillance_value(row)
    vol = derive_volatilite_risk(row)

    if strict_status == "STRICT_ALLOWED":
        return "Etude stricte possible seulement apres verification H-5 complete."
    if vol == "KILL_PROBABLE":
        return "Kill-switch probable ou actif. Confirmer NO BET."
    if quality == "D":
        return "Course structurellement refusee. NO BET."
    if h5_value == "H5_PRIORITAIRE":
        return "Surveiller H-5 prioritaire. Aucun ticket strict depuis morning."
    if h5_value == "H5_SUIVI_SIMPLE":
        return "Suivi H-5 simple. Aucun ticket strict depuis morning."
    return "Surveillance faible. Aucun ticket strict."
