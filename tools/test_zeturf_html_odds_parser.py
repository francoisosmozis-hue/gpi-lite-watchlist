from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine.odds.zeturf_html_odds_parser import (
    extract_odds_from_html,
    write_odds_csv,
    write_odds_json,
)


def main() -> None:
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        html_path = tmp_path / "demo_odds.html"
        csv_path = tmp_path / "demo_odds.csv"
        json_path = tmp_path / "demo_odds.json"

        html_path.write_text(
            """
            <html>
            <body>
            <table>
            <tr><td>1</td><td>Alpha Test</td><td>3,4</td><td>1,4-2,0</td></tr>
            <tr><td>2</td><td>Bravo Test</td><td>8,7</td><td>2,1-3,5</td></tr>
            <tr><td>3</td><td>Charlie d'Or</td><td>12,5</td><td>3,0-5,2</td></tr>
            </table>
            </body>
            </html>
            """,
            encoding="utf-8",
        )

        rows = extract_odds_from_html(
            html_path=html_path,
            course_uid="demo_course_uid",
            snapshot_label="H5",
        )

        assert len(rows) == 3, f"Nombre de lignes incorrect : {len(rows)}"

        assert rows[0].numero == 1
        assert rows[0].cheval == "Alpha Test"
        assert rows[0].cote_sg == 3.4
        assert rows[0].cote_sp_min == 1.4
        assert rows[0].cote_sp_max == 2.0

        assert rows[1].numero == 2
        assert rows[1].cheval == "Bravo Test"
        assert rows[1].cote_sg == 8.7
        assert rows[1].cote_sp_min == 2.1
        assert rows[1].cote_sp_max == 3.5

        assert rows[2].numero == 3
        assert rows[2].cheval == "Charlie d'Or"
        assert rows[2].cote_sg == 12.5
        assert rows[2].cote_sp_min == 3.0
        assert rows[2].cote_sp_max == 5.2

        write_odds_csv(rows, csv_path)
        write_odds_json(rows, json_path)

        assert csv_path.exists()
        assert json_path.exists()

    print("OK test_zeturf_html_odds_parser")


if __name__ == "__main__":
    main()
