from __future__ import annotations

from typing import Any

from engine.odds.latest_odds_context_reader import (
    build_odds_context_reason_codes,
    odds_context_blocks_strict,
    read_latest_odds_context,
)


ODDS_BRIEF_ENRICHER_VERSION = "brief_odds_enricher_v0_1"


def build_odds_brief_lines(context: dict[str, Any] | None = None) -> list[str]:
    odds_context = context if context is not None else read_latest_odds_context()

    reason_codes = build_odds_context_reason_codes(odds_context)
    blocks_strict = odds_context_blocks_strict(odds_context)

    return [
        f"Odds context version : {odds_context.get('context_version', 'ND')}",
        f"Odds context found : {odds_context.get('odds_context_found', 'ND')}",
        f"Volatilite SG statut : {odds_context.get('course_statut_marche', 'ND')}",
        f"Volatilite SG top{odds_context.get('top_n', 6)} : {odds_context.get('top_n_numbers', '')}",
        f"Volatilite SG kill-switch top{odds_context.get('top_n', 6)} : {odds_context.get('top_n_kill_switch_numbers', '')}",
        f"Volatilite SG instable top{odds_context.get('top_n', 6)} : {odds_context.get('top_n_instable_numbers', '')}",
        f"Strict allowed odds : {odds_context.get('strict_allowed_by_odds_context', 'non')}",
        f"Edge odds : {odds_context.get('edge_status_by_odds_context', 'EDGE_FRAGILE')}",
        f"Odds blocks strict : {'oui' if blocks_strict else 'non'}",
        f"Odds reason codes : {';'.join(reason_codes)}",
        f"Odds brief enricher version : {ODDS_BRIEF_ENRICHER_VERSION}",
    ]


def build_odds_brief_block(context: dict[str, Any] | None = None) -> str:
    lines = build_odds_brief_lines(context)
    return "\n".join(lines)


def enrich_brief_text_with_odds_context(
    brief_text: str,
    context: dict[str, Any] | None = None,
) -> str:
    block = build_odds_brief_block(context)

    if not brief_text.strip():
        return block

    return f"{brief_text.rstrip()}\n\n{block}\n"


def build_odds_decision_overrides(context: dict[str, Any] | None = None) -> dict[str, Any]:
    odds_context = context if context is not None else read_latest_odds_context()
    reason_codes = build_odds_context_reason_codes(odds_context)
    blocks_strict = odds_context_blocks_strict(odds_context)

    if blocks_strict:
        return {
            "strict_allowed_oui_non": "non",
            "edge_status": odds_context.get("edge_status_by_odds_context", "EDGE_FRAGILE"),
            "reason_codes_to_add": reason_codes,
            "kill_switch_codes_to_add": [
                code for code in reason_codes
                if "KILL_SWITCH" in code or "STRICT_BLOCKED" in code
            ],
        }

    return {
        "strict_allowed_oui_non": "oui",
        "edge_status": odds_context.get("edge_status_by_odds_context", "EDGE_NON_DETERMINE"),
        "reason_codes_to_add": reason_codes,
        "kill_switch_codes_to_add": [],
    }


def main() -> None:
    context = read_latest_odds_context()
    print(build_odds_brief_block(context))


if __name__ == "__main__":
    main()
