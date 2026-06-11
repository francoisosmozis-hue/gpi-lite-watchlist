from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine.odds.zeturf_html_odds_parser import (
    extract_odds_from_html,
    write_odds_csv,
    write_odds_json,
)
from engine.odds.odds_movement_builder import (
    build_odds_movements,
    write_movements_csv,
    write_movements_json,
)


OUTPUT_DIR = PROJECT_ROOT / "data" / "output" / "odds"


def safe_slug(value: str) -> str:
    cleaned = "".join(
        c.lower() if c.isalnum() else "_"
        for c in value.strip()
    )

    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")

    return cleaned.strip("_") or "nd"


def build_output_prefix(course_uid: str, snapshot_label: str) -> Path:
    return OUTPUT_DIR / f"{safe_slug(course_uid)}_{safe_slug(snapshot_label)}"


def parse_snapshot(
    html_path: str,
    course_uid: str,
    snapshot_label: str,
) -> tuple[Path, Path, int]:
    prefix = build_output_prefix(course_uid, snapshot_label)

    rows = extract_odds_from_html(
        html_path=html_path,
        course_uid=course_uid,
        snapshot_label=snapshot_label,
    )

    csv_path = write_odds_csv(rows, f"{prefix}_odds.csv")
    json_path = write_odds_json(rows, f"{prefix}_odds.json")

    return csv_path, json_path, len(rows)


def build_movement(
    previous_csv: Path,
    current_csv: Path,
    course_uid: str,
    previous_label: str,
    current_label: str,
) -> tuple[Path, Path, int, int]:
    prefix = OUTPUT_DIR / (
        f"{safe_slug(course_uid)}_"
        f"{safe_slug(previous_label)}_to_{safe_slug(current_label)}"
    )

    rows = build_odds_movements(
        previous_csv=previous_csv,
        current_csv=current_csv,
        previous_snapshot_label=previous_label,
        current_snapshot_label=current_label,
    )

    csv_path = write_movements_csv(rows, f"{prefix}_movements.csv")
    json_path = write_movements_json(rows, f"{prefix}_movements.json")

    kill_switch_count = sum(
        1 for row in rows
        if row.statut_marche == "KILL_SWITCH"
    )

    return csv_path, json_path, len(rows), kill_switch_count


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Pipeline local snapshots ZEturf : HTML vers cotes, puis volatilité SG."
    )
    parser.add_argument("--course-uid", required=True)
    parser.add_argument("--current-html", required=True)
    parser.add_argument("--current-label", required=True)
    parser.add_argument("--previous-html", required=False)
    parser.add_argument("--previous-label", required=False)

    args = parser.parse_args()

    current_csv, current_json, current_count = parse_snapshot(
        html_path=args.current_html,
        course_uid=args.course_uid,
        snapshot_label=args.current_label,
    )

    print(f"Snapshot courant : {args.current_label}")
    print(f"Lignes extraites : {current_count}")
    print(f"CSV courant : {current_csv}")
    print(f"JSON courant : {current_json}")

    if args.previous_html and args.previous_label:
        previous_csv, previous_json, previous_count = parse_snapshot(
            html_path=args.previous_html,
            course_uid=args.course_uid,
            snapshot_label=args.previous_label,
        )

        print(f"Snapshot précédent : {args.previous_label}")
        print(f"Lignes extraites : {previous_count}")
        print(f"CSV précédent : {previous_csv}")
        print(f"JSON précédent : {previous_json}")

        movements_csv, movements_json, movement_count, kill_switch_count = build_movement(
            previous_csv=previous_csv,
            current_csv=current_csv,
            course_uid=args.course_uid,
            previous_label=args.previous_label,
            current_label=args.current_label,
        )

        print(f"Lignes mouvement : {movement_count}")
        print(f"Kill-switch volatilité : {kill_switch_count}")
        print(f"CSV mouvements : {movements_csv}")
        print(f"JSON mouvements : {movements_json}")


if __name__ == "__main__":
    main()
