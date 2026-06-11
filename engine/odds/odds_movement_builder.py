from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import csv
import json
from typing import Any


MOVEMENT_BUILDER_VERSION = "odds_movement_builder_v0_1"


@dataclass(frozen=True)
class OddsMovementRow:
    course_uid: str
    numero: int
    cheval: str
    previous_snapshot_label: str
    current_snapshot_label: str
    sg_previous: float | None
    sg_current: float | None
    variation_abs: float | None
    variation_pct: float | None
    direction: str
    statut_marche: str
    impact_marche: str
    movement_builder_version: str
    computed_at_utc: str


def _to_float(value: Any) -> float | None:
    if value is None:
        return None

    cleaned = str(value).strip().replace(",", ".")
    if cleaned == "":
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None

    cleaned = str(value).strip()
    if cleaned == "":
        return None

    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _read_odds_csv(path: str | Path) -> list[dict[str, str]]:
    source = Path(path)

    if not source.exists():
        raise FileNotFoundError(f"CSV introuvable : {source}")

    with source.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def classify_market_status(variation_pct: float | None) -> str:
    if variation_pct is None:
        return "ND"

    if variation_pct <= 0.10:
        return "STABLE"

    if variation_pct <= 0.20:
        return "SURVEILLABLE"

    if variation_pct <= 0.35:
        return "INSTABLE"

    return "KILL_SWITCH"


def classify_direction(sg_previous: float | None, sg_current: float | None) -> str:
    if sg_previous is None or sg_current is None:
        return "ND"

    if sg_current < sg_previous:
        return "STEAM"

    if sg_current > sg_previous:
        return "DRIFT"

    return "STABLE"


def classify_impact(statut_marche: str, direction: str) -> str:
    if statut_marche == "ND":
        return "VOLATILITE_ND"

    if statut_marche == "KILL_SWITCH":
        return f"VOLATILITE_ROUGE_{direction}"

    if statut_marche == "INSTABLE":
        return f"VOLATILITE_INSTABLE_{direction}"

    if statut_marche == "SURVEILLABLE":
        return f"VOLATILITE_SURVEILLABLE_{direction}"

    return f"VOLATILITE_STABLE_{direction}"


def build_odds_movements(
    previous_csv: str | Path,
    current_csv: str | Path,
    previous_snapshot_label: str,
    current_snapshot_label: str,
) -> list[OddsMovementRow]:
    previous_rows = _read_odds_csv(previous_csv)
    current_rows = _read_odds_csv(current_csv)

    previous_by_num: dict[int, dict[str, str]] = {}

    for row in previous_rows:
        numero = _to_int(row.get("numero"))
        if numero is None:
            continue
        previous_by_num[numero] = row

    computed_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    movements: list[OddsMovementRow] = []

    for current in current_rows:
        numero = _to_int(current.get("numero"))
        if numero is None:
            continue

        previous = previous_by_num.get(numero)

        course_uid = current.get("course_uid") or (previous or {}).get("course_uid") or "ND"
        cheval = current.get("cheval") or (previous or {}).get("cheval") or "ND"

        sg_previous = _to_float((previous or {}).get("cote_sg"))
        sg_current = _to_float(current.get("cote_sg"))

        if sg_previous is None or sg_current is None or sg_previous <= 0:
            variation_abs = None
            variation_pct = None
        else:
            variation_abs = sg_current - sg_previous
            variation_pct = abs(variation_abs) / sg_previous

        direction = classify_direction(sg_previous, sg_current)
        statut_marche = classify_market_status(variation_pct)
        impact_marche = classify_impact(statut_marche, direction)

        movements.append(
            OddsMovementRow(
                course_uid=course_uid,
                numero=numero,
                cheval=cheval,
                previous_snapshot_label=previous_snapshot_label,
                current_snapshot_label=current_snapshot_label,
                sg_previous=sg_previous,
                sg_current=sg_current,
                variation_abs=variation_abs,
                variation_pct=variation_pct,
                direction=direction,
                statut_marche=statut_marche,
                impact_marche=impact_marche,
                movement_builder_version=MOVEMENT_BUILDER_VERSION,
                computed_at_utc=computed_at_utc,
            )
        )

    return sorted(movements, key=lambda item: item.numero)


def write_movements_csv(rows: list[OddsMovementRow], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(asdict(rows[0]).keys()) if rows else [
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

    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    return output


def write_movements_json(rows: list[OddsMovementRow], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "movement_builder_version": MOVEMENT_BUILDER_VERSION,
        "rows_count": len(rows),
        "rows": [asdict(row) for row in rows],
    }

    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return output


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare deux snapshots SG ZEturf et calcule la volatilité marché."
    )
    parser.add_argument("--previous-csv", required=True)
    parser.add_argument("--current-csv", required=True)
    parser.add_argument("--previous-label", required=True)
    parser.add_argument("--current-label", required=True)
    parser.add_argument("--csv-out", required=True)
    parser.add_argument("--json-out", required=True)

    args = parser.parse_args()

    rows = build_odds_movements(
        previous_csv=args.previous_csv,
        current_csv=args.current_csv,
        previous_snapshot_label=args.previous_label,
        current_snapshot_label=args.current_label,
    )

    csv_path = write_movements_csv(rows, args.csv_out)
    json_path = write_movements_json(rows, args.json_out)

    kill_switch_count = sum(1 for row in rows if row.statut_marche == "KILL_SWITCH")

    print(f"Lignes mouvement : {len(rows)}")
    print(f"Kill-switch volatilité : {kill_switch_count}")
    print(f"CSV : {csv_path}")
    print(f"JSON : {json_path}")


if __name__ == "__main__":
    main()
