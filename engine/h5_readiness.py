from __future__ import annotations

from typing import Any, Dict


def _s(row: Dict[str, Any], key: str, default: str = "ND") -> str:
    v = row.get(key, default)
    if v is None:
        return default
    v = str(v).strip()
    return v if v else default


def _f(row: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        v = row.get(key, default)
        if v in (None, "", "ND"):
            return default
        return float(str(v).replace(",", "."))
    except Exception:
        return default


def _i(row: Dict[str, Any], key: str, default: int = 0) -> int:
    try:
        v = row.get(key, default)
        if v in (None, "", "ND"):
            return default
        return int(float(str(v).replace(",", ".")))
    except Exception:
        return default


def _kill(row: Dict[str, Any]) -> str:
    v = _s(row, "kill_switch_codes", _s(row, "kill_switchs_matin", "aucun"))
    if v.upper() in ("", "ND", "AUCUN", "NONE", "NON"):
        return "aucun"
    return v


def derive_h5_shortlist_group(row: Dict[str, Any]) -> str:
    priorite = _s(row, "priorite_h5").upper()
    quality = _s(row, "watchlist_quality", _s(row, "qualite")).upper()
    structure = _s(row, "structure_max").upper()
    strict = _s(row, "strict_ticket_status").upper()
    compact = _s(row, "h5_compact_bucket").upper()

    if priorite == "P9" or quality == "D" or structure == "NO_BET":
        return "REFUS_STRUCTUREL"

    if strict == "NO_BET_SIMULATION_ONLY" or "SIMULATION" in structure or priorite == "P2":
        return "SIMULATION_ONLY"

    if compact == "PRIORITE_H5" or quality == "A":
        return "TOP_H5_AUDIT"

    if compact == "SUIVI_SIMPLE" or quality == "B":
        return "SUIVI_H5_SIMPLE"

    return "SURVEILLANCE_FAIBLE"


def derive_h5_shortlist_rank(row: Dict[str, Any]) -> str:
    group = derive_h5_shortlist_group(row)
    return {
        "TOP_H5_AUDIT": "1",
        "SUIVI_H5_SIMPLE": "2",
        "SIMULATION_ONLY": "3",
        "SURVEILLANCE_FAIBLE": "4",
        "REFUS_STRUCTUREL": "9",
    }.get(group, "9")


def derive_h5_shortlist_cp_sec(row: Dict[str, Any]) -> str:
    cp = _s(row, "cp_sec_candidate", _s(row, "cp_sec_candidat"))
    sg_sum = _s(row, "cp_sg_pair_sum", _s(row, "cp_sec_sg_sum_9h"))
    filt = _s(row, "cp_sg_filter_status", _s(row, "cp_sec_sg_filter_status_9h"))
    return f"{cp} | SGsum={sg_sum} | filter={filt}"


def derive_h5_shortlist_blocker(row: Dict[str, Any]) -> str:
    group = derive_h5_shortlist_group(row)
    kill = _kill(row)

    if group == "REFUS_STRUCTUREL":
        return kill if kill != "aucun" else _s(row, "no_bet_reason", "NO_BET_STRUCTUREL")

    top_refusal = _s(row, "top_refusal_reason_1")
    if top_refusal != "ND":
        return top_refusal

    if _s(row, "course_gate_preview").upper() == "PRE_H5_UNKNOWN":
        return "H5_DATA_MISSING"

    if _s(row, "proxy_status").upper() == "ND":
        return "PROXY_ND_PRE_H5"

    if _s(row, "edge_status").upper() == "ND":
        return "EDGE_ND_PRE_H5"

    return "AUCUN"


def derive_h5_shortlist_action(row: Dict[str, Any]) -> str:
    group = derive_h5_shortlist_group(row)

    if group == "TOP_H5_AUDIT":
        return "Verifier H-5 : SG, SP, NP, statut, volatilite, proxy CP_SEC."
    if group == "SUIVI_H5_SIMPLE":
        return "Suivi H-5 simple. Ne pas jouer sans donnees completes."
    if group == "SIMULATION_ONLY":
        return "Simulation only. Aucun ticket strict."
    if group == "REFUS_STRUCTUREL":
        return "NO BET structurel. Ne pas transformer en surveillance."

    return "Surveillance faible. Aucun ticket strict."


def derive_h5_shortlist_reason(row: Dict[str, Any]) -> str:
    quality = _s(row, "watchlist_quality", _s(row, "qualite"))
    structure = _s(row, "structure_max")
    cp = derive_h5_shortlist_cp_sec(row)
    blocker = derive_h5_shortlist_blocker(row)
    return f"quality={quality} | structure={structure} | {cp} | blocker={blocker}"


def derive_ticket_readiness_score(row: Dict[str, Any]) -> int:
    score = 0

    priorite = _s(row, "priorite_h5").upper()
    quality = _s(row, "watchlist_quality", _s(row, "qualite")).upper()
    structure = _s(row, "structure_max").upper()
    strict = _s(row, "strict_ticket_status").upper()
    cp_filter = _s(row, "cp_sg_filter_status", _s(row, "cp_sec_sg_filter_status_9h")).upper()
    cp_sum = _f(row, "cp_sg_pair_sum", _f(row, "cp_sec_sg_sum_9h", 0.0))
    partants = _i(row, "partants_reels", _i(row, "partants_declares", 0))
    sp_status = _s(row, "sp_status", _s(row, "sp_status_morning")).upper()
    fav_fragile = _s(row, "favorite_fragile", _s(row, "favori_fragile")).lower()
    compact = _s(row, "h5_compact_bucket").upper()

    if quality == "A":
        score += 20
    elif quality == "B":
        score += 15
    elif quality == "C":
        score += 5

    if cp_filter == "OK":
        score += 15

    if cp_sum >= 8:
        score += 15
    elif cp_sum >= 6:
        score += 8

    if partants >= 8:
        score += 10

    if structure == "CP_SEC_ETUDE":
        score += 10

    if sp_status == "PRESENT_PARTIEL":
        score += 5

    if fav_fragile in ("non", "no", "false", "0"):
        score += 5

    if _kill(row) == "aucun":
        score += 10

    if compact == "PRIORITE_H5":
        score += 10
    elif compact == "SUIVI_SIMPLE":
        score += 5

    cap = 100

    if priorite == "P9" or quality == "D" or _kill(row) != "aucun":
        cap = min(cap, 30)

    if "SIMULATION" in structure or strict == "NO_BET_SIMULATION_ONLY":
        cap = min(cap, 50)

    if _s(row, "course_gate_preview").upper() == "PRE_H5_UNKNOWN":
        cap = min(cap, 80)

    if _s(row, "proxy_status").upper() == "ND":
        cap = min(cap, 70)

    if _s(row, "edge_status").upper() == "ND":
        cap = min(cap, 70)

    if _s(row, "volatilite_status").upper() == "ND":
        cap = min(cap, 80)

    if _s(row, "volatilite_status").upper() == "KILL":
        cap = min(cap, 40)

    return max(0, min(int(score), cap))


def derive_ticket_readiness_bucket(score: int) -> str:
    if score <= 30:
        return "NO_BET_STRUCTUREL"
    if score <= 50:
        return "SIMULATION_ONLY"
    if score <= 65:
        return "PAPER_TRADING"
    if score <= 80:
        return "H5_AUDIT_SERIEUX"
    return "CANDIDAT_DECISION_STRICTE"


def derive_ticket_readiness_blocker(row: Dict[str, Any], score: int) -> str:
    group = derive_h5_shortlist_group(row)
    kill = _kill(row)

    if group == "REFUS_STRUCTUREL":
        return kill if kill != "aucun" else "NO_BET_STRUCTUREL"

    if _s(row, "course_gate_preview").upper() == "PRE_H5_UNKNOWN":
        return "H5_DATA_MISSING"

    if _s(row, "proxy_status").upper() == "ND":
        return "PROXY_ND"

    if _s(row, "edge_status").upper() == "ND":
        return "EDGE_ND"

    if _s(row, "volatilite_status").upper() == "KILL":
        return "VOLATILITE_KILL"

    if score < 66:
        return "READINESS_INSUFFISANT"

    return "AUCUN"


def enrich_h5_readiness(row: Dict[str, Any]) -> Dict[str, str]:
    score = derive_ticket_readiness_score(row)

    return {
        "h5_shortlist_rank": derive_h5_shortlist_rank(row),
        "h5_shortlist_group": derive_h5_shortlist_group(row),
        "h5_shortlist_reason": derive_h5_shortlist_reason(row),
        "h5_shortlist_action": derive_h5_shortlist_action(row),
        "h5_shortlist_blocker": derive_h5_shortlist_blocker(row),
        "h5_shortlist_cp_sec": derive_h5_shortlist_cp_sec(row),
        "h5_shortlist_priority_score": str(score),
        "ticket_readiness_score": str(score),
        "ticket_readiness_bucket": derive_ticket_readiness_bucket(score),
        "ticket_readiness_blocker": derive_ticket_readiness_blocker(row, score),
    }
