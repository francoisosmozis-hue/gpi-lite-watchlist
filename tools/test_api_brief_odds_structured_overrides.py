from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api_main import build_brief_from_decision
from engine.odds.latest_odds_context_reader import DEFAULT_LATEST_ODDS_CONTEXT_PATH


def main() -> None:
    context_path = DEFAULT_LATEST_ODDS_CONTEXT_PATH
    context_path.parent.mkdir(parents=True, exist_ok=True)

    previous_content = None
    previous_exists = context_path.exists()

    if previous_exists:
        previous_content = context_path.read_text(encoding="utf-8")

    try:
        context_path.write_text(
            json.dumps(
                {
                    "context_version": "latest_odds_context_v0_1",
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
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        decision = {
            "course_uid": "demo_course_uid",
            "qualite": "B",
            "verdict": "TICKET",
            "structure_max": "CP_SEC_STRICT",
            "selection_cp_sec": "1-2",
            "selection_cp3": "",
            "selection_sp": "",
            "reason_codes": "TEST_REASON",
            "kill_switch_codes": "",
            "segment_roi": "STRICT",
            "edge_status": "EDGE_NON_DETERMINE",
        }

        built = build_brief_from_decision(
            decision=decision,
            prompt_text="",
            source_decision="test_source",
        )

        assert built["ticket_exact"] == "NO_BET"
        assert built["edge_status"] == "EDGE_ABSENT"
        assert "TEST_REASON" in built["reason_codes"]
        assert "VOLATILITE_KILL_SWITCH_TOP6" in built["reason_codes"]
        assert "ODDS_CONTEXT_STRICT_BLOCKED" in built["kill_switch_codes"]
        assert "ACTION_GPT : Kill-switch actif. Confirmer NO BET." in built["brief"]
        assert "ODDS_CONTEXT_GPI :" in built["brief"]

    finally:
        if previous_exists:
            context_path.write_text(previous_content or "", encoding="utf-8")
        elif context_path.exists():
            context_path.unlink()

    print("OK test_api_brief_odds_structured_overrides")


if __name__ == "__main__":
    main()
