from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api_main import build_brief_from_decision


def main() -> None:
    decision = {
        "course_uid": "demo_course_uid",
        "qualite": "B",
        "verdict": "NO_BET",
        "structure_max": "CP_SEC_SIMULATION",
        "selection_cp_sec": "",
        "selection_cp3": "",
        "selection_sp": "",
        "reason_codes": "TEST_REASON",
        "kill_switch_codes": "",
        "segment_roi": "NO_BET",
        "edge_status": "EDGE_ABSENT",
    }

    built = build_brief_from_decision(
        decision=decision,
        prompt_text="",
        source_decision="test_source",
    )

    brief = built["brief"]

    assert "ODDS_CONTEXT_GPI :" in brief
    assert "Volatilite SG statut" in brief
    assert "Strict allowed odds" in brief
    assert "Odds reason codes" in brief

    print("OK test_api_brief_odds_context")


if __name__ == "__main__":
    main()
