from __future__ import annotations

import csv
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine.odds.odds_movement_builder import (
    build_odds_movements,
    write_movements_csv,
    write_movements_json,
)


FIELDNAMES = [
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


def write_demo_odds(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        previous_csv = tmp_path / "h30.csv"
        current_csv = tmp_path / "h5.csv"
        output_csv = tmp_path / "movements.csv"
        output_json = tmp_path / "movements.json"

        base = {
            "course_uid": "demo_course_uid",
            "source_html_path": "demo.html",
            "parser_version": "test",
            "confidence": "test",
            "raw_fragment_hash": "hash",
            "extracted_at_utc": "2026-06-11T08:00:00+00:00",
            "cote_sp_min": "",
            "cote_sp_max": "",
        }

        write_demo_odds(
            previous_csv,
            [
                {**base, "snapshot_label": "H30", "numero": "1", "cheval": "Alpha Test", "cote_sg": "10.0"},
                {**base, "snapshot_label": "H30", "numero": "2", "cheval": "Bravo Test", "cote_sg": "10.0"},
                {**base, "snapshot_label": "H30", "numero": "3", "cheval": "Charlie Test", "cote_sg": "10.0"},
                {**base, "snapshot_label": "H30", "numero": "4", "cheval": "Delta Test", "cote_sg": "10.0"},
            ],
        )

        write_demo_odds(
            current_csv,
            [
                {**base, "snapshot_label": "H5", "numero": "1", "cheval": "Alpha Test", "cote_sg": "10.5"},
                {**base, "snapshot_label": "H5", "numero": "2", "cheval": "Bravo Test", "cote_sg": "11.5"},
                {**base, "snapshot_label": "H5", "numero": "3", "cheval": "Charlie Test", "cote_sg": "13.0"},
                {**base, "snapshot_label": "H5", "numero": "4", "cheval": "Delta Test", "cote_sg": "14.0"},
            ],
        )

        rows = build_odds_movements(
            previous_csv=previous_csv,
            current_csv=current_csv,
            previous_snapshot_label="H30",
            current_snapshot_label="H5",
        )

        assert len(rows) == 4

        assert rows[0].statut_marche == "STABLE"
        assert rows[1].statut_marche == "SURVEILLABLE"
        assert rows[2].statut_marche == "INSTABLE"
        assert rows[3].statut_marche == "KILL_SWITCH"

        assert round(rows[3].variation_pct or 0, 4) == 0.4
        assert rows[3].direction == "DRIFT"

        write_movements_csv(rows, output_csv)
        write_movements_json(rows, output_json)

        assert output_csv.exists()
        assert output_json.exists()

    print("OK test_odds_movement_builder")


if __name__ == "__main__":
    main()
