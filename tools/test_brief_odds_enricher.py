from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine.odds.brief_odds_enricher import (
    build_odds_brief_block,
    build_odds_decision_overrides,
    enrich_brief_text_with_odds_context,
)


def main() -> None:
    context = {
        "context_version": "latest_odds_context_v0_1",
        "odds_context_found": "oui",
        "course_uid": "demo_course_uid",
        "current_snapshot_label": "H5",
        "runners_count": 3,
        "top_n": 6,
        "top_n_numbers": "1-2-3",
        "top_n_kill_switch_numbers": "3",
        "top_n_instable_numbers": "",
        "global_kill_switch_count": 1,
        "global_instable_count": 0,
        "top_n_kill_switch_count": 1,
        "top_n_instable_count": 0,
        "kill_switch_topn_oui_non": "oui",
        "course_statut_marche": "KILL_SWITCH_TOPN",
        "reason_codes": "VOLATILITE_KILL_SWITCH_TOP6",
        "strict_allowed_by_odds_context": "non",
        "edge_status_by_odds_context": "EDGE_ABSENT",
    }

    block = build_odds_brief_block(context)

    assert "Volatilite SG statut : KILL_SWITCH_TOPN" in block
    assert "Strict allowed odds : non" in block
    assert "Edge odds : EDGE_ABSENT" in block
    assert "VOLATILITE_KILL_SWITCH_TOP6" in block
    assert "ODDS_CONTEXT_STRICT_BLOCKED" in block

    enriched = enrich_brief_text_with_odds_context("VERDICT : NO_BET", context)

    assert enriched.startswith("VERDICT : NO_BET")
    assert "Odds blocks strict : oui" in enriched

    overrides = build_odds_decision_overrides(context)

    assert overrides["strict_allowed_oui_non"] == "non"
    assert overrides["edge_status"] == "EDGE_ABSENT"
    assert "VOLATILITE_KILL_SWITCH_TOP6" in overrides["reason_codes_to_add"]
    assert "ODDS_CONTEXT_STRICT_BLOCKED" in overrides["kill_switch_codes_to_add"]

    print("OK test_brief_odds_enricher")


if __name__ == "__main__":
    main()
