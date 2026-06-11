from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine.odds.latest_odds_context import (
    build_latest_odds_context,
    write_latest_odds_context,
)


ODDS_FIELDS = [
    "course_uid",
    "snapshot_label",
    "numero",
    "cheval",
    "cote_sg",
    "cote_sp_min",
    "cote_sp_max",
    "source_html_path",
    "parser_version",
    "confidence",
    "raw_fragment_hash",
    "extracted_at_utc",
]

MOVEMENT_FIELDS = [
    "course_uid",
    "numero",
    "cheval",
    "previous_snapshot_label",
    "current_snapshot_label",
    "sg_previous",
    "sg_current",
    "variation_abs",
    "variation_pct",
    "direction",
    "statut_marche",
    "impact_marche",
    "movement_builder_version",
    "computed_at_utc",
]


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        current_odds_csv = tmp_path / "current_odds.csv"
        movements_csv = tmp_path / "movements.csv"
        gate_json = tmp_path / "gate.json"
        context_json = tmp_path / "latest_odds_context.json"

        write_csv(
            current_odds_csv,
            ODDS_FIELDS,
            [
                {
                    "course_uid": "demo_course_uid",
                    "snapshot_label": "H5",
                    "numero": "1",
                    "cheval": "Alpha Test",
                    "cote_sg": "2.0",
                    "cote_sp_min": "",
                    "cote_sp_max": "",
                    "source_html_path": "demo.html",
                    "parser_version": "test",
                    "confidence": "test",
                    "raw_fragment_hash": "hash",
                    "extracted_at_utc": "2026-06-11T08:00:00+00:00",
                }
            ],
        )

        write_csv(
            movements_csv,
            MOVEMENT_FIELDS,
            [
                {
                    "course_uid": "demo_course_uid",
                    "numero": "1",
                    "cheval": "Alpha Test",
                    "previous_snapshot_label": "H30",
                    "current_snapshot_label": "H5",
                    "sg_previous": "10.0",
                    "sg_current": "14.0",
                    "variation_abs": "4.0",
                    "variation_pct": "0.40",
                    "direction": "DRIFT",
                    "statut_marche": "KILL_SWITCH",
                    "impact_marche": "VOLATILITE_ROUGE_DRIFT",
                    "movement_builder_version": "test",
                    "computed_at_utc": "2026-06-11T08:00:00+00:00",
                }
            ],
        )

        gate_json.write_text(
            json.dumps(
                {
                    "course_uid": "demo_course_uid",
                    "current_snapshot_label": "H5",
                    "runners_count": 7,
                    "top_n": 6,
                    "top_n_numbers": "1-2-3-4-5-6",
                    "top_n_kill_switch_numbers": "5",
                    "top_n_instable_numbers": "4",
                    "global_kill_switch_count": 2,
                    "global_instable_count": 1,
                    "top_n_kill_switch_count": 1,
                    "top_n_instable_count": 1,
                    "kill_switch_topn_oui_non": "oui",
                    "course_statut_marche": "KILL_SWITCH_TOPN",
                    "reason_codes": "VOLATILITE_KILL_SWITCH_TOP6",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        context = build_latest_odds_context(
            current_odds_csv=current_odds_csv,
            movements_csv=movements_csv,
            gate_json=gate_json,
        )

        assert context.course_uid == "demo_course_uid"
        assert context.course_statut_marche == "KILL_SWITCH_TOPN"
        assert context.kill_switch_topn_oui_non == "oui"
        assert context.strict_allowed_by_odds_context == "non"
        assert context.edge_status_by_odds_context == "EDGE_ABSENT"
        assert context.reason_codes == "VOLATILITE_KILL_SWITCH_TOP6"

        write_latest_odds_context(context, context_json)

        assert context_json.exists()

    print("OK test_latest_odds_context")


if __name__ == "__main__":
    main()
