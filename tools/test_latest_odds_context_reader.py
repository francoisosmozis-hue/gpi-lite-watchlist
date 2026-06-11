from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine.odds.latest_odds_context_reader import (
    build_odds_context_reason_codes,
    odds_context_blocks_strict,
    read_latest_odds_context,
)


def main() -> None:
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        context_path = tmp_path / "latest_odds_context.json"

        missing_context = read_latest_odds_context(tmp_path / "missing.json")

        assert missing_context["odds_context_found"] == "non"
        assert missing_context["strict_allowed_by_odds_context"] == "non"
        assert odds_context_blocks_strict(missing_context) is True
        assert "ODDS_CONTEXT_ABSENT" in build_odds_context_reason_codes(missing_context)

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

        context = read_latest_odds_context(context_path)

        assert context["odds_context_found"] == "oui"
        assert context["course_uid"] == "demo_course_uid"
        assert context["course_statut_marche"] == "KILL_SWITCH_TOPN"
        assert context["strict_allowed_by_odds_context"] == "non"
        assert context["edge_status_by_odds_context"] == "EDGE_ABSENT"
        assert odds_context_blocks_strict(context) is True

        reason_codes = build_odds_context_reason_codes(context)

        assert "VOLATILITE_KILL_SWITCH_TOP6" in reason_codes
        assert "ODDS_CONTEXT_STRICT_BLOCKED" in reason_codes

    print("OK test_latest_odds_context_reader")


if __name__ == "__main__":
    main()
