from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import csv
import json
from typing import Any


ODDS_CONTEXT_VERSION = "latest_odds_context_v0_1"


@dataclass(frozen=True)
class LatestOddsContext:
    context_version: str
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
    strict_allowed_by_odds_context: str
    edge_status_by_odds_context: str
    current_odds_csv: str
    movements_csv: str
    gate_json: str
    generated_at_utc: str


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    source = Path(path)

    if not source.exists():
        raise FileNotFoundError(f"CSV introuvable : {source}")

    with source.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)

    if not source.exists():
        raise FileNotFoundError(f"JSON introuvable : {source}")

    return json.loads(source.read_text(encoding="utf-8"))


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "ND") -> str:
    if value is None:
        return default

    cleaned = str(value).strip()
    return cleaned if cleaned else default


def classify_strict_allowed(
    course_statut_marche: str,
    kill_switch_topn_oui_non: str,
) -> tuple[str, str]:
    if kill_switch_topn_oui_non == "oui":
        return "non", "EDGE_ABSENT"

    if course_statut_marche in {"KILL_SWITCH_TOPN", "KILL_SWITCH_HORS_TOPN"}:
        return "non", "EDGE_ABSENT"

    if course_statut_marche in {"INSTABLE_TOPN", "INSTABLE_HORS_TOPN"}:
        return "non", "EDGE_FRAGILE"

    if course_statut_marche == "STABLE":
        return "oui", "EDGE_NON_DETERMINE"

    return "non", "EDGE_FRAGILE"


def build_latest_odds_context(
    current_odds_csv: str | Path,
    movements_csv: str | Path,
    gate_json: str | Path,
) -> LatestOddsContext:
    current_rows = _read_csv(current_odds_csv)
    movement_rows = _read_csv(movements_csv)
    gate = _read_json(gate_json)

    course_statut_marche = _as_str(gate.get("course_statut_marche"))
    kill_switch_topn_oui_non = _as_str(gate.get("kill_switch_topn_oui_non"), "non")

    strict_allowed, edge_status = classify_strict_allowed(
        course_statut_marche=course_statut_marche,
        kill_switch_topn_oui_non=kill_switch_topn_oui_non,
    )

    generated_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    return LatestOddsContext(
        context_version=ODDS_CONTEXT_VERSION,
        course_uid=_as_str(gate.get("course_uid")),
        current_snapshot_label=_as_str(gate.get("current_snapshot_label")),
        runners_count=_as_int(gate.get("runners_count"), len(current_rows)),
        top_n=_as_int(gate.get("top_n"), 6),
        top_n_numbers=_as_str(gate.get("top_n_numbers"), ""),
        top_n_kill_switch_numbers=_as_str(gate.get("top_n_kill_switch_numbers"), ""),
        top_n_instable_numbers=_as_str(gate.get("top_n_instable_numbers"), ""),
        global_kill_switch_count=_as_int(gate.get("global_kill_switch_count")),
        global_instable_count=_as_int(gate.get("global_instable_count")),
        top_n_kill_switch_count=_as_int(gate.get("top_n_kill_switch_count")),
        top_n_instable_count=_as_int(gate.get("top_n_instable_count")),
        kill_switch_topn_oui_non=kill_switch_topn_oui_non,
        course_statut_marche=course_statut_marche,
        reason_codes=_as_str(gate.get("reason_codes")),
        strict_allowed_by_odds_context=strict_allowed,
        edge_status_by_odds_context=edge_status,
        current_odds_csv=str(current_odds_csv),
        movements_csv=str(movements_csv),
        gate_json=str(gate_json),
        generated_at_utc=generated_at_utc,
    )


def write_latest_odds_context(
    context: LatestOddsContext,
    output_path: str | Path,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(asdict(context), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return output


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Construit latest_odds_context.json depuis les snapshots ZEturf."
    )
    parser.add_argument("--current-odds-csv", required=True)
    parser.add_argument("--movements-csv", required=True)
    parser.add_argument("--gate-json", required=True)
    parser.add_argument("--json-out", required=True)

    args = parser.parse_args()

    context = build_latest_odds_context(
        current_odds_csv=args.current_odds_csv,
        movements_csv=args.movements_csv,
        gate_json=args.gate_json,
    )

    output_path = write_latest_odds_context(context, args.json_out)

    print(f"Contexte odds : {output_path}")
    print(f"Statut marché : {context.course_statut_marche}")
    print(f"Strict allowed odds : {context.strict_allowed_by_odds_context}")
    print(f"Edge odds : {context.edge_status_by_odds_context}")
    print(f"Reason codes : {context.reason_codes}")


if __name__ == "__main__":
    main()
