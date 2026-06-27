# ============================================================
# BET_SELECTION_OPTIMIZER_V0_1_1
# Optimiseur économique CP_SEC + SP
# CP3 conservé uniquement en audit papier 100 décisions.
# Doctrine : NO_BET par défaut, aucun ticket réel automatique.
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


OPTIMIZER_VERSION = "BET_SELECTION_OPTIMIZER_V0_1_3"
RULESET_SCOPE = "CP_SEC_SP_ACTIVE__CP3_AUDIT_ONLY"
CP3_AUDIT_SCOPE = "PAPER_ONLY_100_DECISIONS"

ND_VALUES = {"", "ND", "NA", "N/A", None}


def _s(value: Any, default: str = "ND") -> str:
    if value in ND_VALUES:
        return default
    return str(value).strip()


def _upper(value: Any, default: str = "ND") -> str:
    return _s(value, default).upper()


def _f(value: Any) -> Optional[float]:
    if value in ND_VALUES:
        return None
    try:
        return float(str(value).replace(",", ".").strip())
    except Exception:
        return None


def _is_ok_status(value: Any) -> bool:
    return _upper(value) in {
        "OK",
        "VALID",
        "VALIDE",
        "PASS",
        "PASSE",
        "UTILISABLE",
        "GREEN",
        "VERT",
    }


def _has_kill_switch(context: Dict[str, Any]) -> bool:
    kill_codes = _upper(context.get("kill_switch_codes", ""))
    reason_codes = _upper(context.get("reason_codes", ""))
    decision_block = _upper(context.get("decision_block_reason", ""))

    odds = context.get("odds_context") or {}
    odds_status = _upper(odds.get("odds_volatilite_sg_statut", ""))
    odds_blocks = _upper(odds.get("odds_blocks_strict", ""))

    haystack = "|".join([kill_codes, reason_codes, decision_block, odds_status])

    if "KILL_SWITCH" in haystack:
        return True
    if "VOLATILITE_KILL" in haystack:
        return True
    if odds_status in {"KILL_SWITCH_TOPN", "KILL_SWITCH_TOP6", "ROUGE"}:
        return True
    if odds_blocks == "OUI" and "VOLATILITE" in haystack:
        return True

    return False


def _coherence_mismatch(context: Dict[str, Any]) -> bool:
    course_uid = _s(context.get("course_uid"))
    course_key = _s(context.get("course_key"))

    if course_uid == "ND" or course_key == "ND":
        return False

    return course_uid != course_key


@dataclass
class BetCandidate:
    structure: str
    ticket: str
    stake: float
    segment_roi: str
    confidence: str
    reason: str
    score: float


@dataclass
class Cp3AuditCandidate:
    allowed: str
    ticket: str
    stake: float
    segment_roi: str
    reason: str
    scope: str
    status: str


@dataclass
class BetSelectionOutput:
    optimizer_version: str
    ruleset_scope: str

    best_structure: str
    ticket_paper: str
    ticket_real: str
    stake_paper: float
    segment_roi: str

    edge_status: str
    economic_confidence: str
    variance_status: str

    cp_sec_status: str
    sp_status: str

    cp3_status: str
    cp3_audit_allowed: str
    cp3_audit_ticket: str
    cp3_audit_stake: float
    cp3_audit_segment_roi: str
    cp3_audit_reason: str
    cp3_audit_scope: str

    rejected_structures: List[str]
    reason_codes: List[str]
    hard_blocks: List[str]


def _cp_sec_candidate(context: Dict[str, Any]) -> Optional[BetCandidate]:
    cp_sec = _s(
        context.get("cp_sec_candidate")
        or context.get("latest_cp_sec_candidate")
        or context.get("paper_selection")
    )

    pair_sum = _f(
        context.get("cp_sg_pair_sum")
        or context.get("latest_cp_sg_pair_sum")
        or context.get("cp_sec_pair_sum")
        or context.get("cp_sg_sum")
    )

    filter_status = _s(
        context.get("cp_sg_filter_status")
        or context.get("latest_cp_sg_filter_status")
        or context.get("cp_sec_status")
    )

    if cp_sec == "ND":
        return None

    if pair_sum is None:
        return None

    if pair_sum < 7.5:
        return None

    if filter_status != "ND" and not _is_ok_status(filter_status):
        return None

    return BetCandidate(
        structure="CP_SEC_PAPER",
        ticket=f"CP_SEC {cp_sec}",
        stake=1.0,
        segment_roi="CP_SEC",
        confidence="MOYENNE",
        reason=f"CP_SEC admissible ; somme SG={pair_sum}",
        score=70.0 + min(pair_sum, 20.0),
    )


def _sp_candidate(context: Dict[str, Any]) -> Optional[BetCandidate]:
    sp_numero = _s(
        context.get("sp_candidate_numero")
        or context.get("sp_numero")
        or context.get("sp_candidate")
    )

    sp_nom = _s(context.get("sp_candidate_cheval") or context.get("sp_cheval"), "")

    sp_min = _f(
        context.get("sp_min")
        or context.get("sp_min_h5")
        or context.get("sp_candidate_min")
    )

    segment_sp_status = _s(
        context.get("segment_SP_status")
        or context.get("segment_sp_status")
        or context.get("sp_segment_status")
    )

    proba_basse_score = _f(
        context.get("proba_basse_score")
        or context.get("sp_proba_basse_score")
    )

    if sp_numero == "ND":
        return None

    if sp_min is None:
        return None

    if sp_min < 1.8:
        return None

    if _is_ok_status(segment_sp_status):
        label = f"SP {sp_numero}"
        if sp_nom:
            label += f" {sp_nom}"

        return BetCandidate(
            structure="SP_PAPER",
            ticket=label,
            stake=1.0,
            segment_roi="SP",
            confidence="MOYENNE",
            reason=f"SP admissible ; segment_SP={segment_sp_status} ; SP_min={sp_min}",
            score=68.0 + min(sp_min * 2.0, 20.0),
        )

    if proba_basse_score is not None and proba_basse_score >= 2:
        label = f"MICRO_SP {sp_numero}"
        if sp_nom:
            label += f" {sp_nom}"

        return BetCandidate(
            structure="MICRO_SP_PAPER",
            ticket=label,
            stake=1.0,
            segment_roi="MICRO_SP",
            confidence="FAIBLE",
            reason=f"SP non calibré ; micro-SP seulement ; score proba_basse={proba_basse_score}",
            score=52.0 + min(sp_min, 10.0),
        )

    return None


def _cp3_audit_candidate(context: Dict[str, Any], hard_blocked: bool) -> Cp3AuditCandidate:
    if hard_blocked:
        return Cp3AuditCandidate(
            allowed="non",
            ticket="NO_BET",
            stake=0.0,
            segment_roi="NO_BET",
            reason="CP3 audit bloqué par hard block",
            scope=CP3_AUDIT_SCOPE,
            status="REJECTED_HARD_BLOCK",
        )

    cp3 = _s(
        context.get("cp3_candidate")
        or context.get("latest_cp3_candidate")
        or context.get("cp3_selection")
    )

    cp3_filter_status = _s(
        context.get("cp3_sg_filter_status")
        or context.get("latest_cp3_sg_filter_status")
        or context.get("cp3_status")
    )

    if cp3 == "ND":
        return Cp3AuditCandidate(
            allowed="non",
            ticket="NO_BET",
            stake=0.0,
            segment_roi="NO_BET",
            reason="CP3_SELECTION_ABSENTE",
            scope=CP3_AUDIT_SCOPE,
            status="REJECTED_NO_SELECTION",
        )

    if cp3_filter_status != "ND" and not _is_ok_status(cp3_filter_status):
        return Cp3AuditCandidate(
            allowed="non",
            ticket="NO_BET",
            stake=0.0,
            segment_roi="NO_BET",
            reason=f"CP3 filtre SG non admissible : {cp3_filter_status}",
            scope=CP3_AUDIT_SCOPE,
            status="REJECTED_FILTER",
        )

    return Cp3AuditCandidate(
        allowed="oui",
        ticket=f"CP3 {cp3}",
        stake=1.0,
        segment_roi="CP3_AUDIT",
        reason="CP3 conservé en audit papier uniquement ; aucune autorisation réelle",
        scope=CP3_AUDIT_SCOPE,
        status="PAPER_AUDIT_ONLY",
    )



def _cp_sec_watchlist_simulation_block(context: Dict[str, Any]) -> bool:
    reason = _upper(context.get("paper_candidate_reason", ""))
    segment = _upper(context.get("paper_candidate_segment", ""))
    candidate_type = _upper(context.get("paper_candidate_type", ""))
    allowed = _upper(context.get("paper_candidate_allowed", "ND"))

    if "WATCHLIST_SIMULATION_ONLY" in reason:
        return True

    if segment == "SIMULATION" and "CP_SEC" in candidate_type:
        return True

    if allowed == "NON" and "WATCHLIST" in reason and "CP_SEC_ETUDE" not in reason:
        return True

    return False


def _cp_sec_priority_fields(context: Dict[str, Any], best_structure: str) -> Dict[str, Any]:
    reasons: List[str] = []

    if not str(best_structure).startswith("CP_SEC"):
        return {
            "cp_sec_priority_score": "0",
            "cp_sec_priority_level": "ND",
            "cp_sec_priority_reasons": "NOT_CP_SEC",
        }

    pair_sum = _f(
        context.get("cp_sg_pair_sum")
        or context.get("latest_cp_sg_pair_sum")
        or context.get("cp_sec_pair_sum")
        or context.get("cp_sg_sum")
    )

    qualite = _upper(context.get("qualite", context.get("quality", "ND")))

    if pair_sum is None:
        return {
            "cp_sec_priority_score": "0",
            "cp_sec_priority_level": "LOW",
            "cp_sec_priority_reasons": "CP_SG_PAIR_SUM_ND",
        }

    score = 70.0
    reasons.append("BASE_CP_SEC_70")

    if pair_sum >= 12.0:
        score += 15.0
        reasons.append("SG_SUM_GE_12_PLUS_15")
    elif pair_sum >= 10.5:
        score += 10.0
        reasons.append("SG_SUM_GE_10_5_PLUS_10")
    elif pair_sum >= 9.5:
        score += 7.0
        reasons.append("SG_SUM_GE_9_5_PLUS_7")
    elif pair_sum >= 8.5:
        score += 3.0
        reasons.append("SG_SUM_GE_8_5_PLUS_3")
    else:
        reasons.append("SG_SUM_LT_8_5_PLUS_0")

    if qualite == "B":
        score -= 5.0
        reasons.append("QUALITY_B_MINUS_5")
    elif qualite == "A":
        reasons.append("QUALITY_A_NO_PENALTY")
    else:
        score -= 20.0
        reasons.append(f"QUALITY_{qualite}_MINUS_20")

    if _cp_sec_watchlist_simulation_block(context):
        score -= 100.0
        reasons.append("WATCHLIST_SIMULATION_ONLY_MINUS_100")

    if score >= 85:
        level = "HIGH"
    elif score >= 75:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {
        "cp_sec_priority_score": str(round(score, 2)),
        "cp_sec_priority_level": level,
        "cp_sec_priority_reasons": "|".join(reasons),
    }


def _with_priority_fields(context: Dict[str, Any], out: BetSelectionOutput) -> Dict[str, Any]:
    result = asdict(out)
    result.update(_cp_sec_priority_fields(context, result.get("best_structure", "NO_BET")))
    return result



def optimize_bet_selection(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Entrée : dictionnaire issu d'un brief H-5, watchlist enrichie ou h5-report aplati.
    Sortie : décision économique CP_SEC/SP/NO_BET + CP3 audit séparé.

    Garanties :
    - Aucun ticket réel strict automatique.
    - CP_SEC et SP seuls peuvent être best_structure.
    - CP3 ne peut être qu'un audit papier séparé.
    - NO_BET par défaut.
    """

    rejected: List[str] = []
    reason_codes: List[str] = []
    hard_blocks: List[str] = []

    qualite = _upper(context.get("qualite", context.get("quality", "ND")))

    if _coherence_mismatch(context):
        hard_blocks.append("H5_REPORT_CONTEXT_MISMATCH")

    if qualite in {"D", "ND"}:
        hard_blocks.append(f"QUALITY_{qualite}")

    if _has_kill_switch(context):
        hard_blocks.append("VOLATILITE_KILL_SWITCH")

    hard_blocked = bool(hard_blocks)
    cp3_audit = _cp3_audit_candidate(context, hard_blocked)

    rejected.append("CP3_EXCLUDED_FROM_BEST_STRUCTURE")
    reason_codes.append("CP3_PAPER_AUDIT_ONLY_100_DECISIONS")

    if hard_blocked:
        reason_codes.extend(hard_blocks)
        out = BetSelectionOutput(
            optimizer_version=OPTIMIZER_VERSION,
            ruleset_scope=RULESET_SCOPE,
            best_structure="NO_BET",
            ticket_paper="NO_BET",
            ticket_real="NO_BET",
            stake_paper=0.0,
            segment_roi="NO_BET",
            edge_status="EDGE_ABSENT",
            economic_confidence="NULLE",
            variance_status="BLOQUEE",
            cp_sec_status="REJECTED_HARD_BLOCK",
            sp_status="REJECTED_HARD_BLOCK",
            cp3_status=cp3_audit.status,
            cp3_audit_allowed=cp3_audit.allowed,
            cp3_audit_ticket=cp3_audit.ticket,
            cp3_audit_stake=cp3_audit.stake,
            cp3_audit_segment_roi=cp3_audit.segment_roi,
            cp3_audit_reason=cp3_audit.reason,
            cp3_audit_scope=cp3_audit.scope,
            rejected_structures=rejected,
            reason_codes=reason_codes,
            hard_blocks=hard_blocks,
        )
        return _with_priority_fields(context, out)

    cp_sec = _cp_sec_candidate(context)
    sp = _sp_candidate(context)

    # Discipline ROI :
    # Qualité C = micro/simulation uniquement.
    # On interdit CP_SEC_PAPER et SP_PAPER standard sur qualité C.
    if qualite == "C":
        if cp_sec is not None:
            cp_sec = None
            rejected.append("CP_SEC_REJECTED_QUALITY_C_SIMULATION_ONLY")

        if sp is not None and sp.structure == "SP_PAPER":
            sp = None
            rejected.append("SP_REJECTED_QUALITY_C_STANDARD_FORBIDDEN")

    if cp_sec is not None and _cp_sec_watchlist_simulation_block(context):
        cp_sec = None
        rejected.append("CP_SEC_REJECTED_WATCHLIST_SIMULATION_ONLY")

    if cp_sec is None and "CP_SEC_REJECTED_QUALITY_C_SIMULATION_ONLY" not in rejected:
        rejected.append("CP_SEC_REJECTED_NOT_ADMISSIBLE")

    if sp is None and "SP_REJECTED_QUALITY_C_STANDARD_FORBIDDEN" not in rejected:
        rejected.append("SP_REJECTED_NOT_ADMISSIBLE_OR_NOT_CALIBRATED")

    candidates = [c for c in [cp_sec, sp] if c is not None]

    if not candidates:
        reason_codes.append("NO_ADMISSIBLE_CP_SEC_OR_SP")
        out = BetSelectionOutput(
            optimizer_version=OPTIMIZER_VERSION,
            ruleset_scope=RULESET_SCOPE,
            best_structure="NO_BET",
            ticket_paper="NO_BET",
            ticket_real="NO_BET",
            stake_paper=0.0,
            segment_roi="NO_BET",
            edge_status="EDGE_ABSENT",
            economic_confidence="NULLE",
            variance_status="NON_COUVERTE",
            cp_sec_status="REJECTED",
            sp_status="REJECTED",
            cp3_status=cp3_audit.status,
            cp3_audit_allowed=cp3_audit.allowed,
            cp3_audit_ticket=cp3_audit.ticket,
            cp3_audit_stake=cp3_audit.stake,
            cp3_audit_segment_roi=cp3_audit.segment_roi,
            cp3_audit_reason=cp3_audit.reason,
            cp3_audit_scope=cp3_audit.scope,
            rejected_structures=rejected,
            reason_codes=reason_codes,
            hard_blocks=hard_blocks,
        )
        return _with_priority_fields(context, out)

    candidates = sorted(candidates, key=lambda c: c.score, reverse=True)
    best = candidates[0]

    if cp_sec is not None and sp is not None:
        if cp_sec.score + 5 >= sp.score:
            best = cp_sec
            rejected.append("SP_REJECTED_CP_SEC_PREFERRED")
        else:
            best = sp
            rejected.append("CP_SEC_REJECTED_SP_SCORE_ADVANTAGE")

    cp_sec_priority = _cp_sec_priority_fields(context, best.structure)

    if best.structure == "CP_SEC_PAPER" and cp_sec_priority.get("cp_sec_priority_level") == "LOW":
        rejected.append("CP_SEC_REJECTED_PRIORITY_LOW_SIMULATION_ONLY")
        reason_codes.append("CP_SEC_PRIORITY_LOW_SIMULATION_ONLY")

        out = BetSelectionOutput(
            optimizer_version=OPTIMIZER_VERSION,
            ruleset_scope=RULESET_SCOPE,
            best_structure="CP_SEC_SIMULATION_LOW",
            ticket_paper="NO_BET",
            ticket_real="NO_BET",
            stake_paper=0.0,
            segment_roi="SIMULATION",
            edge_status="EDGE_FRAGILE",
            economic_confidence="FAIBLE",
            variance_status="A_AUDITER",
            cp_sec_status="SIMULATION_LOW",
            sp_status="AVAILABLE" if sp else "REJECTED",
            cp3_status=cp3_audit.status,
            cp3_audit_allowed=cp3_audit.allowed,
            cp3_audit_ticket=cp3_audit.ticket,
            cp3_audit_stake=cp3_audit.stake,
            cp3_audit_segment_roi=cp3_audit.segment_roi,
            cp3_audit_reason=cp3_audit.reason,
            cp3_audit_scope=cp3_audit.scope,
            rejected_structures=rejected,
            reason_codes=reason_codes,
            hard_blocks=hard_blocks,
        )
        return _with_priority_fields(context, out)

    reason_codes.append(f"BEST_{best.structure}")
    if best.structure == "CP_SEC_PAPER":
        reason_codes.append(f"CP_SEC_PRIORITY_{cp_sec_priority.get('cp_sec_priority_level', 'ND')}")

    out = BetSelectionOutput(
        optimizer_version=OPTIMIZER_VERSION,
        ruleset_scope=RULESET_SCOPE,
        best_structure=best.structure,
        ticket_paper=best.ticket,
        ticket_real="NO_BET",
        stake_paper=best.stake,
        segment_roi=best.segment_roi,
        edge_status="EDGE_FRAGILE",
        economic_confidence=best.confidence,
        variance_status="A_AUDITER",
        cp_sec_status="ACCEPTED" if best.structure == "CP_SEC_PAPER" else ("AVAILABLE" if cp_sec else "REJECTED"),
        sp_status="ACCEPTED" if best.structure in {"SP_PAPER", "MICRO_SP_PAPER"} else ("AVAILABLE" if sp else "REJECTED"),
        cp3_status=cp3_audit.status,
        cp3_audit_allowed=cp3_audit.allowed,
        cp3_audit_ticket=cp3_audit.ticket,
        cp3_audit_stake=cp3_audit.stake,
        cp3_audit_segment_roi=cp3_audit.segment_roi,
        cp3_audit_reason=cp3_audit.reason,
        cp3_audit_scope=cp3_audit.scope,
        rejected_structures=rejected,
        reason_codes=reason_codes,
        hard_blocks=hard_blocks,
    )
    return _with_priority_fields(context, out)
