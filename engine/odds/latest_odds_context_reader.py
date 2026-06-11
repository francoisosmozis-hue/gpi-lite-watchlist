from __future__ import annotations

from pathlib import Path
import json
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LATEST_ODDS_CONTEXT_PATH = (
    PROJECT_ROOT / "data" / "output" / "odds" / "latest_odds_context.json"
)


DEFAULT_ODDS_CONTEXT: dict[str, Any] = {
    "context_version": "latest_odds_context_reader_default_v0_1",
    "course_uid": "ND",
    "current_snapshot_label": "ND",
    "runners_count": 0,
    "top_n": 6,
    "top_n_numbers": "",
    "top_n_kill_switch_numbers": "",
    "top_n_instable_numbers": "",
    "global_kill_switch_count": 0,
    "global_instable_count": 0,
    "top_n_kill_switch_count": 0,
    "top_n_instable_count": 0,
    "kill_switch_topn_oui_non": "non",
    "course_statut_marche": "ND",
    "reason_codes": "ODDS_CONTEXT_ABSENT",
    "strict_allowed_by_odds_context": "non",
    "edge_status_by_odds_context": "EDGE_FRAGILE",
    "current_odds_csv": "ND",
    "movements_csv": "ND",
    "gate_json": "ND",
    "generated_at_utc": "ND",
    "odds_context_found": "non",
}


def read_latest_odds_context(
    path: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(path) if path is not None else DEFAULT_LATEST_ODDS_CONTEXT_PATH

    if not source.exists():
        return dict(DEFAULT_ODDS_CONTEXT)

    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        context = dict(DEFAULT_ODDS_CONTEXT)
        context["reason_codes"] = "ODDS_CONTEXT_JSON_INVALID"
        return context

    if not isinstance(payload, dict):
        context = dict(DEFAULT_ODDS_CONTEXT)
        context["reason_codes"] = "ODDS_CONTEXT_FORMAT_INVALID"
        return context

    context = dict(DEFAULT_ODDS_CONTEXT)
    context.update(payload)
    context["odds_context_found"] = "oui"

    return context


def odds_context_blocks_strict(context: dict[str, Any]) -> bool:
    return (
        context.get("strict_allowed_by_odds_context") == "non"
        or context.get("kill_switch_topn_oui_non") == "oui"
        or context.get("course_statut_marche") in {
            "KILL_SWITCH_TOPN",
            "KILL_SWITCH_HORS_TOPN",
            "INSTABLE_TOPN",
            "INSTABLE_HORS_TOPN",
            "ND",
        }
    )


def build_odds_context_reason_codes(context: dict[str, Any]) -> list[str]:
    reason_codes: list[str] = []

    raw_reason_codes = str(context.get("reason_codes") or "").strip()

    if raw_reason_codes:
        reason_codes.extend(
            code.strip()
            for code in raw_reason_codes.split(";")
            if code.strip()
        )

    if context.get("odds_context_found") != "oui":
        reason_codes.append("ODDS_CONTEXT_ABSENT")

    if context.get("strict_allowed_by_odds_context") == "non":
        reason_codes.append("ODDS_CONTEXT_STRICT_BLOCKED")

    if context.get("kill_switch_topn_oui_non") == "oui":
        reason_codes.append("VOLATILITE_KILL_SWITCH_TOP6")

    unique_codes: list[str] = []

    for code in reason_codes:
        if code not in unique_codes:
            unique_codes.append(code)

    return unique_codes


def main() -> None:
    context = read_latest_odds_context()

    print(f"Odds context found : {context.get('odds_context_found')}")
    print(f"Course statut marché : {context.get('course_statut_marche')}")
    print(f"Strict allowed odds : {context.get('strict_allowed_by_odds_context')}")
    print(f"Edge odds : {context.get('edge_status_by_odds_context')}")
    print(f"Blocks strict : {odds_context_blocks_strict(context)}")
    print(f"Reason codes : {';'.join(build_odds_context_reason_codes(context))}")


if __name__ == "__main__":
    main()
