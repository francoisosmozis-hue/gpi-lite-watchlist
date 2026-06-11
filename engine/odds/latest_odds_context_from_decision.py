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


def _get_prompt_field(prompt_text: str, field_name: str) -> str:
    wanted = field_name.strip().upper()

    for line in str(prompt_text or "").splitlines():
        cleaned = line.strip()
        if ":" not in cleaned:
            continue

        left, right = cleaned.split(":", 1)

        if left.strip().upper() == wanted:
            return right.strip()

    return ""


def _get_any_with_prompt(
    row: Mapping[str, Any],
    keys: list[str],
    prompt_text: str = "",
    default: str = "ND",
) -> str:
    value = _get_any(row, keys, "")

    if value:
        return value

    for key in keys:
        prompt_value = _get_prompt_field(prompt_text, key)
        if prompt_value:
            return prompt_value

    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _has_text_token(value: Any, token: str) -> bool:
    return token.upper() in str(value or "").upper()


def _is_kill_switch(decision: Mapping[str, Any], prompt_text: str = "") -> bool:
    kill_switch_codes = _get_any_with_prompt(decision, ["kill_switch_codes", "KILL_SWITCHS"], prompt_text, "")
    decision_block_reason = _get_any_with_prompt(decision, ["decision_block_reason", "DECISION_BLOCK_REASON"], prompt_text, "")
    strict_gate_summary = _get_any_with_prompt(decision, ["strict_gate_summary", "STRICT_GATE_SUMMARY"], prompt_text, "")
    volatilite_status = _get_any_with_prompt(decision, ["volatilite_status", "VOLATILITE_STATUS"], prompt_text, "")
    variation_rouge_top6 = _get_any_with_prompt(decision, ["variation_rouge_top6", "VARIATION_ROUGE_TOP6"], prompt_text, "")

    return (
        _has_text_token(kill_switch_codes, "VOLATILITE_KILL_SWITCH_TOP6")
        or _has_text_token(decision_block_reason, "VOLATILITE_KILL_SWITCH_TOP6")
        or _has_text_token(strict_gate_summary, "VOLATILITE_KILL_SWITCH_TOP6")
        or volatilite_status.upper() == "KILL"
        or variation_rouge_top6.lower() == "oui"
    )


def _is_instable(decision: Mapping[str, Any], prompt_text: str = "") -> bool:
    volatilite_status = _get_any_with_prompt(decision, ["volatilite_status", "VOLATILITE_STATUS"], prompt_text, "")
    decision_block_reason = _get_any_with_prompt(decision, ["decision_block_reason", "DECISION_BLOCK_REASON"], prompt_text, "")

    return (
        volatilite_status.upper() in {"INSTABLE", "DEGRADE", "ORANGE"}
        or _has_text_token(decision_block_reason, "VOLATILITE_INSTABLE")
    )


def build_latest_odds_context_from_decision(decision: Mapping[str, Any], prompt_text: str = "") -> dict[str, Any]:
    course_uid = _get_any_with_prompt(decision, ["course_uid", "COURSE", "course_key"], prompt_text)
    top_n_numbers = _get_any_with_prompt(decision, ["top6_sg", "TOP6_SG"], prompt_text, "")
    max_numero = _get_any_with_prompt(decision, ["volatilite_top6_max_numero", "VOLATILITE_TOP6_MAX_NUMERO"], prompt_text, "")

    kill = _is_kill_switch(decision, prompt_text)
    instable = _is_instable(decision, prompt_text)

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
        "runners_count": _as_int(_get_any_with_prompt(decision, ["partants_reels", "PARTANTS_REELS"], prompt_text, "0")),
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
