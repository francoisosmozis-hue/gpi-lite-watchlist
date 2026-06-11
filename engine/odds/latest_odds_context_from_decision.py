from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any, Mapping

from engine.odds.latest_odds_context_reader import DEFAULT_LATEST_ODDS_CONTEXT_PATH


ODDS_CONTEXT_FROM_DECISION_VERSION = "latest_odds_context_from_decision_v0_1"


def _get_any(row: Mapping[str, Any], keys: list[str], default: str = "ND") -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _has_text_token(value: Any, token: str) -> bool:
    return token.upper() in str(value or "").upper()


def _is_kill_switch(decision: Mapping[str, Any]) -> bool:
    kill_switch_codes = _get_any(decision, ["kill_switch_codes", "KILL_SWITCHS"], "")
    decision_block_reason = _get_any(decision, ["decision_block_reason", "DECISION_BLOCK_REASON"], "")
    strict_gate_summary = _get_any(decision, ["strict_gate_summary", "STRICT_GATE_SUMMARY"], "")
    volatilite_status = _get_any(decision, ["volatilite_status", "VOLATILITE_STATUS"], "")
    variation_rouge_top6 = _get_any(decision, ["variation_rouge_top6", "VARIATION_ROUGE_TOP6"], "")

    return (
        _has_text_token(kill_switch_codes, "VOLATILITE_KILL_SWITCH_TOP6")
        or _has_text_token(decision_block_reason, "VOLATILITE_KILL_SWITCH_TOP6")
        or _has_text_token(strict_gate_summary, "VOLATILITE_KILL_SWITCH_TOP6")
        or volatilite_status.upper() == "KILL"
        or variation_rouge_top6.lower() == "oui"
    )


def _is_instable(decision: Mapping[str, Any]) -> bool:
    volatilite_status = _get_any(decision, ["volatilite_status", "VOLATILITE_STATUS"], "")
    decision_block_reason = _get_any(decision, ["decision_block_reason", "DECISION_BLOCK_REASON"], "")

    return (
        volatilite_status.upper() in {"INSTABLE", "DEGRADE", "ORANGE"}
        or _has_text_token(decision_block_reason, "VOLATILITE_INSTABLE")
    )


def build_latest_odds_context_from_decision(decision: Mapping[str, Any]) -> dict[str, Any]:
    course_uid = _get_any(decision, ["course_uid", "COURSE", "course_key"])
    top_n_numbers = _get_any(decision, ["top6_sg", "TOP6_SG"], "")
    max_numero = _get_any(decision, ["volatilite_top6_max_numero", "VOLATILITE_TOP6_MAX_NUMERO"], "")

    kill = _is_kill_switch(decision)
    instable = _is_instable(decision)

    if kill:
        course_statut_marche = "KILL_SWITCH_TOPN"
        reason_codes = "VOLATILITE_KILL_SWITCH_TOP6"
        strict_allowed = "non"
        edge_status = "EDGE_ABSENT"
        kill_switch_topn = "oui"
        top_n_kill_switch_numbers = max_numero
        top_n_instable_numbers = ""
        global_kill_switch_count = 1
        top_n_kill_switch_count = 1
        global_instable_count = 0
        top_n_instable_count = 0
    elif instable:
        course_statut_marche = "INSTABLE_TOPN"
        reason_codes = "VOLATILITE_INSTABLE_TOP6"
        strict_allowed = "non"
        edge_status = "EDGE_FRAGILE"
        kill_switch_topn = "non"
        top_n_kill_switch_numbers = ""
        top_n_instable_numbers = max_numero
        global_kill_switch_count = 0
        top_n_kill_switch_count = 0
        global_instable_count = 1
        top_n_instable_count = 1
    elif top_n_numbers:
        course_statut_marche = "STABLE"
        reason_codes = "VOLATILITE_STABLE"
        strict_allowed = "oui"
        edge_status = "EDGE_NON_DETERMINE"
        kill_switch_topn = "non"
        top_n_kill_switch_numbers = ""
        top_n_instable_numbers = ""
        global_kill_switch_count = 0
        top_n_kill_switch_count = 0
        global_instable_count = 0
        top_n_instable_count = 0
    else:
        course_statut_marche = "ND"
        reason_codes = "ODDS_CONTEXT_FROM_DECISION_ND"
        strict_allowed = "non"
        edge_status = "EDGE_FRAGILE"
        kill_switch_topn = "non"
        top_n_kill_switch_numbers = ""
        top_n_instable_numbers = ""
        global_kill_switch_count = 0
        top_n_kill_switch_count = 0
        global_instable_count = 0
        top_n_instable_count = 0

    generated_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    return {
        "context_version": ODDS_CONTEXT_FROM_DECISION_VERSION,
        "odds_context_found": "decision_fallback",
        "course_uid": course_uid,
        "current_snapshot_label": "H5",
        "runners_count": _as_int(_get_any(decision, ["partants_reels", "PARTANTS_REELS"], "0")),
        "top_n": 6,
        "top_n_numbers": top_n_numbers.replace(",", "-"),
        "top_n_kill_switch_numbers": top_n_kill_switch_numbers,
        "top_n_instable_numbers": top_n_instable_numbers,
        "global_kill_switch_count": global_kill_switch_count,
        "global_instable_count": global_instable_count,
        "top_n_kill_switch_count": top_n_kill_switch_count,
        "top_n_instable_count": top_n_instable_count,
        "kill_switch_topn_oui_non": kill_switch_topn,
        "course_statut_marche": course_statut_marche,
        "reason_codes": reason_codes,
        "strict_allowed_by_odds_context": strict_allowed,
        "edge_status_by_odds_context": edge_status,
        "current_odds_csv": "ND",
        "movements_csv": "ND",
        "gate_json": "ND",
        "generated_at_utc": generated_at_utc,
    }


def write_latest_odds_context_from_decision(
    context: Mapping[str, Any],
    output_path: str | Path | None = None,
) -> Path:
    output = Path(output_path) if output_path is not None else DEFAULT_LATEST_ODDS_CONTEXT_PATH
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(dict(context), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return output
