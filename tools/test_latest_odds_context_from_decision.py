from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine.odds.latest_odds_context_from_decision import (
    build_latest_odds_context_from_decision,
    write_latest_odds_context_from_decision,
)
from engine.odds.latest_odds_context_reader import (
    build_odds_context_reason_codes,
    odds_context_blocks_strict,
)


def main() -> None:
    decision = {
        "course_uid": "10_06_2026 - CAEN - Prix de Clécy_ Pronostics & Cotes",
        "partants_reels": "11",
        "top6_sg": "10,7,6,13,8,11",
        "kill_switch_codes": "VOLATILITE_KILL_SWITCH_TOP6",
        "volatilite_status": "KILL",
        "volatilite_top6_max_numero": "8",
        "volatilite_top6_max_cheval": "MOJITO DE BANVILLE P",
        "volatilite_top6_max_pct": "79.41",
    }

    context = build_latest_odds_context_from_decision(decision)

    assert context["odds_context_found"] == "decision_fallback"
    assert context["course_statut_marche"] == "KILL_SWITCH_TOPN"
    assert context["kill_switch_topn_oui_non"] == "oui"
    assert context["top_n_numbers"] == "10-7-6-13-8-11"
    assert context["top_n_kill_switch_numbers"] == "8"
    assert context["strict_allowed_by_odds_context"] == "non"
    assert context["edge_status_by_odds_context"] == "EDGE_ABSENT"
    assert odds_context_blocks_strict(context) is True

    reason_codes = build_odds_context_reason_codes(context)

    assert "VOLATILITE_KILL_SWITCH_TOP6" in reason_codes
    assert "ODDS_CONTEXT_STRICT_BLOCKED" in reason_codes
    assert "ODDS_CONTEXT_ABSENT" not in reason_codes

    with TemporaryDirectory() as tmp:
        output = Path(tmp) / "latest_odds_context.json"
        written = write_latest_odds_context_from_decision(context, output)
        assert written.exists()

    print("OK test_latest_odds_context_from_decision")


if __name__ == "__main__":
    main()
