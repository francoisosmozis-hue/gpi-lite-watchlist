from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from typing import Any, Dict, List


ENGINE_VERSION = "H5_DECISION_ENGINE_V0_1"
RULESET_VERSION = "GPI_V19_1_4_P9_1_SECURITY_FIRST"


REQUIRED_H5_FIELDS = [
    "sg_h5_ok",
    "np_ok",
    "statut_ok",
    "decision_before_race_oui_non",
    "input_hash",
]


STRICT_BLOCKING_FIELDS = [
    "segment_roi",
    "edge_status",
    "proxy_status",
    "volatilite_status",
]


@dataclass
class H5DecisionResult:
    decision_id: str
    input_hash: str
    engine_version: str
    ruleset_version: str
    decision_before_race_oui_non: str

    verdict: str
    structure_max: str
    ticket_exact: str
    segment_roi: str
    edge_status: str
    strict_allowed_oui_non: str
    decision_block_reason: str
    kill_switch_codes: str

    cp_sec_result: str
    sp_result: str
    cp3_result: str
    cp_sec_domine: str
    validation_economique: str

    reason_codes: str


def _s(ctx: Dict[str, Any], key: str, default: str = "ND") -> str:
    value = ctx.get(key, default)
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def _is_yes(value: str) -> bool:
    return str(value).strip().lower() in ("oui", "yes", "true", "1", "ok")


def _is_ok(value: str) -> bool:
    return str(value).strip().upper() in ("OK", "OUI", "YES", "TRUE", "1")


def _stable_input_hash(ctx: Dict[str, Any]) -> str:
    raw = "|".join(f"{k}={ctx.get(k, 'ND')}" for k in sorted(ctx.keys()))
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def _decision_id(input_hash: str) -> str:
    return f"H5-{ENGINE_VERSION}-{input_hash}"


def _collect_blockers(ctx: Dict[str, Any]) -> List[str]:
    blockers: List[str] = []

    for field in REQUIRED_H5_FIELDS:
        value = _s(ctx, field)
        if value == "ND":
            blockers.append(f"{field.upper()}_MISSING")

    if not _is_yes(_s(ctx, "decision_before_race_oui_non")):
        blockers.append("DECISION_NOT_CONFIRMED_PRE_RACE")

    if not _is_ok(_s(ctx, "sg_h5_ok")):
        blockers.append("SG_H5_NOT_OK")

    if not _is_ok(_s(ctx, "np_ok")):
        blockers.append("NP_NOT_OK")

    if not _is_ok(_s(ctx, "statut_ok")):
        blockers.append("STATUT_NOT_OK")

    volatilite = _s(ctx, "volatilite_status").upper()
    if volatilite in ("KILL", "ROUGE"):
        blockers.append("VOLATILITE_KILL")

    kill = _s(ctx, "kill_switch_codes", "aucun")
    if kill.upper() not in ("AUCUN", "ND", "NONE", "NON", ""):
        blockers.append(f"KILL_SWITCH:{kill}")

    for field in STRICT_BLOCKING_FIELDS:
        value = _s(ctx, field)
        if value == "ND":
            blockers.append(f"{field.upper()}_ND")

    proxy_status = _s(ctx, "proxy_status").upper()
    if proxy_status in ("ROUGE", "ND"):
        blockers.append("PROXY_NOT_USABLE")

    edge_status = _s(ctx, "edge_status").upper()
    if edge_status in ("EDGE_ABSENT", "EDGE_FRAGILE", "EDGE_NON_DETERMINE", "EDGE_NOT_DETERMINED", "ND"):
        blockers.append("EDGE_NOT_NET")

    segment_roi = _s(ctx, "segment_roi").upper()
    if segment_roi in ("ND", "NO_BET"):
        blockers.append("SEGMENT_ROI_NOT_USABLE")

    return list(dict.fromkeys(blockers))


def evaluate_h5_decision(ctx: Dict[str, Any]) -> Dict[str, str]:
    input_hash = _s(ctx, "input_hash")
    if input_hash == "ND":
        input_hash = _stable_input_hash(ctx)

    blockers = _collect_blockers(ctx)

    decision_before = _s(ctx, "decision_before_race_oui_non")
    kill_switch = _s(ctx, "kill_switch_codes", "aucun")
    segment_roi = _s(ctx, "segment_roi")
    edge_status = _s(ctx, "edge_status", "EDGE_ABSENT")

    if blockers:
        result = H5DecisionResult(
            decision_id=_decision_id(input_hash),
            input_hash=input_hash,
            engine_version=ENGINE_VERSION,
            ruleset_version=RULESET_VERSION,
            decision_before_race_oui_non=decision_before,

            verdict="NO_BET",
            structure_max="NO_BET",
            ticket_exact="NO_BET",
            segment_roi=segment_roi,
            edge_status=edge_status if edge_status != "ND" else "EDGE_ABSENT",
            strict_allowed_oui_non="non",
            decision_block_reason=" | ".join(blockers),
            kill_switch_codes=kill_switch,

            cp_sec_result="REFUS",
            sp_result="REFUS",
            cp3_result="REFUS",
            cp_sec_domine="ND",
            validation_economique="NON_VALIDEE",

            reason_codes=" | ".join(blockers),
        )
        return {k: str(v) for k, v in asdict(result).items()}

    result = H5DecisionResult(
        decision_id=_decision_id(input_hash),
        input_hash=input_hash,
        engine_version=ENGINE_VERSION,
        ruleset_version=RULESET_VERSION,
        decision_before_race_oui_non=decision_before,

        verdict="NO_BET",
        structure_max="CP_SEC_AUDIT_SEULEMENT",
        ticket_exact="NO_BET",
        segment_roi=segment_roi,
        edge_status=edge_status,
        strict_allowed_oui_non="non",
        decision_block_reason="P9_1_SECURITY_MODE_NO_STRICT_TICKET",
        kill_switch_codes=kill_switch,

        cp_sec_result="A_AUDITER_H5",
        sp_result="REFUS",
        cp3_result="REFUS_CP_SEC_PRIORITAIRE",
        cp_sec_domine="oui",
        validation_economique="NON_VALIDEE_P9_1",

        reason_codes="P9_1_SECURITY_MODE",
    )
    return {k: str(v) for k, v in asdict(result).items()}


def _first_non_nd(ctx: Dict[str, Any], keys: List[str], default: str = "ND") -> str:
    for key in keys:
        value = _s(ctx, key, "ND")
        if value != "ND":
            return value
    return default


def _ok_from_status(value: str) -> str:
    v = str(value).strip().upper()
    if v in ("OK", "OUI", "YES", "TRUE", "1", "PROPRE", "VALIDÉ", "VALIDE"):
        return "OK"
    if v in ("KO", "NON", "NO", "FALSE", "0", "ROUGE", "KILL"):
        return "KO"
    return "ND"


def build_h5_decision_context_from_brief(brief: Dict[str, Any]) -> Dict[str, str]:
    """Convert a latest H-5 brief payload into the normalized decision context.

    Missing fields stay ND on purpose. P9.2 wires data, but does not relax
    the security-first NO_BET policy.
    """
    if not isinstance(brief, dict):
        brief = {}

    ctx: Dict[str, str] = {}

    ctx["sg_h5_ok"] = _ok_from_status(
        _first_non_nd(
            brief,
            [
                "sg_h5_ok",
                "h5_sg_ok",
                "sg_ok",
                "sg_status",
                "sg_fiables",
                "sg_fiable",
                "current_snapshot_label",
            ],
        )
    )

    ctx["np_ok"] = _ok_from_status(
        _first_non_nd(
            brief,
            [
                "np_ok",
                "np_status_ok",
                "np_clean",
                "non_partants_ok",
                "non_partants_status",
            ],
        )
    )

    ctx["statut_ok"] = _ok_from_status(
        _first_non_nd(
            brief,
            [
                "statut_ok",
                "status_ok",
                "statut_partants_ok",
                "statut_course_ok",
                "course_status",
            ],
        )
    )

    ctx["decision_before_race_oui_non"] = _first_non_nd(
        brief,
        [
            "decision_before_race_oui_non",
            "decision_before_race",
            "pre_race_decision",
            "before_race",
        ],
    )

    ctx["input_hash"] = _first_non_nd(
        brief,
        [
            "input_hash",
            "snapshot_hash",
            "brief_hash",
            "source_hash",
        ],
    )

    ctx["segment_roi"] = _first_non_nd(
        brief,
        [
            "segment_roi",
            "roi_segment",
            "segment",
                "Segment_ROI",
        ],
    )

    ctx["edge_status"] = _first_non_nd(
        brief,
        [
            "edge_status",
            "edge",
            "edge_net",
                "Edge",
                "edge_status_by_odds_context",
        ],
        default="EDGE_ABSENT",
    )

    ctx["proxy_status"] = _first_non_nd(
        brief,
        [
            "proxy_status",
            "statut_proxy",
            "proxy_segment_status",
            "cp_proxy_status",
        ],
    )

    ctx["volatilite_status"] = _first_non_nd(
        brief,
        [
            "volatilite_status",
            "volatilite_sg",
            "volatilite_sg_status",
            "odds_volatility_status",
            "volatility_status",
                "course_statut_marche",
        ],
    )

    ctx["kill_switch_codes"] = _first_non_nd(
        brief,
        [
            "kill_switch_codes",
            "kill_switch",
            "kill_switchs",
            "kill_switches",
                "kill_switch_topn_oui_non",
        ],
        default="aucun",
    )

    # latest_h5_auto_brief uses H5 as snapshot label, not an OK flag.
    if _s(brief, "current_snapshot_label").upper() == "H5" and _s(brief, "top_n_numbers") != "ND":
        ctx["sg_h5_ok"] = "OK"

    # Market status STABLE is usable for the odds volatility layer.
    if ctx["volatilite_status"].upper() == "STABLE":
        ctx["volatilite_status"] = "OK"

    # kill_switch_topn_oui_non = non means no volatility kill-switch.
    if ctx["kill_switch_codes"].strip().lower() in ("non", "no", "false", "0"):
        ctx["kill_switch_codes"] = "aucun"

    if _s(brief, "kill_switch_topn_oui_non").strip().lower() in ("oui", "yes", "true", "1"):
        ctx["kill_switch_codes"] = "VOLATILITE_KILL_SWITCH_TOPN"

    return ctx


def evaluate_h5_decision_from_brief(brief: Dict[str, Any]) -> Dict[str, str]:
    ctx = build_h5_decision_context_from_brief(brief)
    return evaluate_h5_decision(ctx)
