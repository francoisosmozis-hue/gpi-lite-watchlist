from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import csv
import json
from typing import Any


VOLATILITY_GATE_VERSION = "odds_volatility_gate_v0_1"


@dataclass(frozen=True)
class OddsVolatilityGateResult:
    course_uid: str
    current_snapshot_label: str
    runners_count: int
    top_n: int
    top_n_numbers: str
    top_n_kill_switch_numbers: str
    top_n_instable_numbers: str
    global_kill_switch_count: int
    global_instable_count: int
    top_n_kill_switch_count: int
    top_n_instable_count: int
    kill_switch_topn_oui_non: str
    course_statut_marche: str
    reason_codes: str
    volatility_gate_version: str
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


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    source = Path(path)

    if not source.exists():
        raise FileNotFoundError(f"CSV introuvable : {source}")

    with source.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _join_numbers(numbers: list[int]) -> str:
    return "-".join(str(number) for number in numbers)


def build_volatility_gate(
    current_odds_csv: str | Path,
    movements_csv: str | Path,
    top_n: int = 6,
) -> OddsVolatilityGateResult:
    current_rows = _read_csv(current_odds_csv)
    movement_rows = _read_csv(movements_csv)

    current_valid: list[tuple[int, float]] = []

    for row in current_rows:
        numero = _to_int(row.get("numero"))
        sg = _to_float(row.get("cote_sg"))

        if numero is None or sg is None:
            continue

        current_valid.append((numero, sg))

    current_valid.sort(key=lambda item: item[1])
    top_numbers = [numero for numero, _ in current_valid[:top_n]]
    top_set = set(top_numbers)

    global_kill_switch_numbers: list[int] = []
    global_instable_numbers: list[int] = []
    top_kill_switch_numbers: list[int] = []
    top_instable_numbers: list[int] = []

    course_uid = "ND"
    current_snapshot_label = "ND"

    for row in movement_rows:
        numero = _to_int(row.get("numero"))
        if numero is None:
            continue

        course_uid = row.get("course_uid") or course_uid
        current_snapshot_label = row.get("current_snapshot_label") or current_snapshot_label
        statut = row.get("statut_marche") or "ND"

        if statut == "KILL_SWITCH":
            global_kill_switch_numbers.append(numero)
            if numero in top_set:
                top_kill_switch_numbers.append(numero)

        if statut == "INSTABLE":
            global_instable_numbers.append(numero)
            if numero in top_set:
                top_instable_numbers.append(numero)

    if top_kill_switch_numbers:
        course_statut_marche = "KILL_SWITCH_TOPN"
        kill_switch_topn = "oui"
        reason_codes = "VOLATILITE_KILL_SWITCH_TOP6"
    elif top_instable_numbers:
        course_statut_marche = "INSTABLE_TOPN"
        kill_switch_topn = "non"
        reason_codes = "VOLATILITE_INSTABLE_TOP6"
    elif global_kill_switch_numbers:
        course_statut_marche = "KILL_SWITCH_HORS_TOPN"
        kill_switch_topn = "non"
        reason_codes = "VOLATILITE_KILL_SWITCH_HORS_TOP6"
    elif global_instable_numbers:
        course_statut_marche = "INSTABLE_HORS_TOPN"
        kill_switch_topn = "non"
        reason_codes = "VOLATILITE_INSTABLE_HORS_TOP6"
    else:
        course_statut_marche = "STABLE"
        kill_switch_topn = "non"
        reason_codes = "VOLATILITE_STABLE"

    computed_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    return OddsVolatilityGateResult(
        course_uid=course_uid,
        current_snapshot_label=current_snapshot_label,
        runners_count=len(current_valid),
        top_n=top_n,
        top_n_numbers=_join_numbers(top_numbers),
        top_n_kill_switch_numbers=_join_numbers(sorted(top_kill_switch_numbers)),
        top_n_instable_numbers=_join_numbers(sorted(top_instable_numbers)),
        global_kill_switch_count=len(global_kill_switch_numbers),
        global_instable_count=len(global_instable_numbers),
        top_n_kill_switch_count=len(top_kill_switch_numbers),
        top_n_instable_count=len(top_instable_numbers),
        kill_switch_topn_oui_non=kill_switch_topn,
        course_statut_marche=course_statut_marche,
        reason_codes=reason_codes,
        volatility_gate_version=VOLATILITY_GATE_VERSION,
        computed_at_utc=computed_at_utc,
    )


def write_gate_json(result: OddsVolatilityGateResult, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return output


def write_gate_csv(result: OddsVolatilityGateResult, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(asdict(result).keys())

    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(asdict(result))

    return output


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Produit le gate volatilité GPI à partir des mouvements SG ZEturf."
    )
    parser.add_argument("--current-odds-csv", required=True)
    parser.add_argument("--movements-csv", required=True)
    parser.add_argument("--top-n", type=int, default=6)
    parser.add_argument("--csv-out", required=True)
    parser.add_argument("--json-out", required=True)

    args = parser.parse_args()

    result = build_volatility_gate(
        current_odds_csv=args.current_odds_csv,
        movements_csv=args.movements_csv,
        top_n=args.top_n,
    )

    csv_path = write_gate_csv(result, args.csv_out)
    json_path = write_gate_json(result, args.json_out)

    print(f"Course statut marché : {result.course_statut_marche}")
    print(f"Kill-switch top{result.top_n} : {result.kill_switch_topn_oui_non}")
    print(f"Reason codes : {result.reason_codes}")
    print(f"CSV : {csv_path}")
    print(f"JSON : {json_path}")


if __name__ == "__main__":
    main()
