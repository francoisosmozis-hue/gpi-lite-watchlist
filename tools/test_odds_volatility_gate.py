from __future__ import annotations

import csv
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine.odds.odds_volatility_gate import (
    build_volatility_gate,
    write_gate_csv,
    write_gate_json,
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
        odds_csv = tmp_path / "current_odds.csv"
        movements_csv = tmp_path / "movements.csv"
        gate_csv = tmp_path / "gate.csv"
        gate_json = tmp_path / "gate.json"

        odds_base = {
            "course_uid": "demo_course_uid",
            "snapshot_label": "H5",
            "cote_sp_min": "",
            "cote_sp_max": "",
            "source_html_path": "demo.html",
            "parser_version": "test",
            "confidence": "test",
            "raw_fragment_hash": "hash",
            "extracted_at_utc": "2026-06-11T08:00:00+00:00",
        }

        movement_base = {
            "course_uid": "demo_course_uid",
            "previous_snapshot_label": "H30",
            "current_snapshot_label": "H5",
            "sg_previous": "10.0",
            "movement_builder_version": "test",
            "computed_at_utc": "2026-06-11T08:00:00+00:00",
        }

        write_csv(
            odds_csv,
            ODDS_FIELDS,
            [
                {**odds_base, "numero": "1", "cheval": "Alpha Test", "cote_sg": "2.0"},
                {**odds_base, "numero": "2", "cheval": "Bravo Test", "cote_sg": "3.0"},
                {**odds_base, "numero": "3", "cheval": "Charlie Test", "cote_sg": "4.0"},
                {**odds_base, "numero": "4", "cheval": "Delta Test", "cote_sg": "5.0"},
                {**odds_base, "numero": "5", "cheval": "Echo Test", "cote_sg": "6.0"},
                {**odds_base, "numero": "6", "cheval": "Foxtrot Test", "cote_sg": "7.0"},
                {**odds_base, "numero": "7", "cheval": "Golf Test", "cote_sg": "40.0"},
            ],
        )

        write_csv(
            movements_csv,
            MOVEMENT_FIELDS,
            [
                {**movement_base, "numero": "1", "cheval": "Alpha Test", "sg_current": "10.5", "variation_abs": "0.5", "variation_pct": "0.05", "direction": "DRIFT", "statut_marche": "STABLE", "impact_marche": "VOLATILITE_STABLE_DRIFT"},
                {**movement_base, "numero": "2", "cheval": "Bravo Test", "sg_current": "11.0", "variation_abs": "1.0", "variation_pct": "0.10", "direction": "DRIFT", "statut_marche": "STABLE", "impact_marche": "VOLATILITE_STABLE_DRIFT"},
                {**movement_base, "numero": "3", "cheval": "Charlie Test", "sg_current": "11.5", "variation_abs": "1.5", "variation_pct": "0.15", "direction": "DRIFT", "statut_marche": "SURVEILLABLE", "impact_marche": "VOLATILITE_SURVEILLABLE_DRIFT"},
                {**movement_base, "numero": "4", "cheval": "Delta Test", "sg_current": "13.0", "variation_abs": "3.0", "variation_pct": "0.30", "direction": "DRIFT", "statut_marche": "INSTABLE", "impact_marche": "VOLATILITE_INSTABLE_DRIFT"},
                {**movement_base, "numero": "5", "cheval": "Echo Test", "sg_current": "14.0", "variation_abs": "4.0", "variation_pct": "0.40", "direction": "DRIFT", "statut_marche": "KILL_SWITCH", "impact_marche": "VOLATILITE_ROUGE_DRIFT"},
                {**movement_base, "numero": "6", "cheval": "Foxtrot Test", "sg_current": "10.0", "variation_abs": "0.0", "variation_pct": "0.00", "direction": "STABLE", "statut_marche": "STABLE", "impact_marche": "VOLATILITE_STABLE_STABLE"},
                {**movement_base, "numero": "7", "cheval": "Golf Test", "sg_current": "14.0", "variation_abs": "4.0", "variation_pct": "0.40", "direction": "DRIFT", "statut_marche": "KILL_SWITCH", "impact_marche": "VOLATILITE_ROUGE_DRIFT"},
            ],
        )

        result = build_volatility_gate(
            current_odds_csv=odds_csv,
            movements_csv=movements_csv,
            top_n=6,
        )

        assert result.course_statut_marche == "KILL_SWITCH_TOPN"
        assert result.kill_switch_topn_oui_non == "oui"
        assert result.top_n_numbers == "1-2-3-4-5-6"
        assert result.top_n_kill_switch_numbers == "5"
        assert result.top_n_instable_numbers == "4"
        assert result.global_kill_switch_count == 2
        assert result.reason_codes == "VOLATILITE_KILL_SWITCH_TOP6"

        write_gate_csv(result, gate_csv)
        write_gate_json(result, gate_json)

        assert gate_csv.exists()
        assert gate_json.exists()

    print("OK test_odds_volatility_gate")


if __name__ == "__main__":
    main()
