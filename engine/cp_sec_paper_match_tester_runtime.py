
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from engine.cp_sec_paper_rule_runtime import decorate_payload_with_cp_sec_paper_rule


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"

ADMISSIBLE_PATH = OUTPUT_DIR / "cp_sec_admissible_paper_candidates.csv"
LATEST_TEST_OUTPUT = OUTPUT_DIR / "latest_cp_sec_paper_match_test.json"

TESTER_VERSION = "GPI_CP_SEC_PAPER_MATCH_TESTER_RUNTIME_V0_1"


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []

    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except Exception:
            continue

    return []


def _safe(row: Dict[str, str], key: str, default: str = "") -> str:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip()


def _first_non_empty(row: Dict[str, str], keys: List[str], default: str = "") -> str:
    for key in keys:
        value = _safe(row, key)
        if value:
            return value
    return default


def _build_synthetic_payload(row: Dict[str, str], row_index: int) -> Dict[str, Any]:
    course_uid = _first_non_empty(row, ["course_uid", "source_course_uid", "candidate_course_uid"], "ND")
    source_file = _first_non_empty(row, ["source_file", "candidate_source_file", "file_name"], "ND")
    decision_id = _safe(row, "decision_id", "ND")
    input_hash = _safe(row, "input_hash", "ND")
    selection_sorted = _first_non_empty(
        row,
        ["selection_sorted", "candidate_selection_sorted", "selection_cp_sec", "selection_CP_SEC"],
        "ND",
    )

    brief = (
        "VERDICT_PYTHON : NO_BET\n"
        f"COURSE : {course_uid}\n"
        "QUALITE : TEST\n"
        "STRUCTURE_MAX : CP_SEC_SIMULATION\n"
        "TICKET_EXACT : NO_BET\n"
        f"SELECTION_CP_SEC : {selection_sorted}\n"
        "REASON_CODES : CP_SEC_PAPER_MATCH_TEST_SYNTHETIC_PAYLOAD\n"
        "KILL_SWITCHS : ND\n"
        "SEGMENT_ROI : NO_BET\n"
        "EDGE : EDGE_ABSENT\n"
        "ACTION_GPT : Test runtime. Ne jamais transformer en ticket strict.\n"
        "\n"
        "REGLE_GPI : test paper match uniquement. Ticket strict interdit.\n"
    )

    # Inclure plusieurs chemins de match potentiels, car le runtime indexe decision/input,
    # course/selection et source/selection.
    return {
        "ok": True,
        "source": "cp_sec_paper_match_tester",
        "tester_version": TESTER_VERSION,
        "test_row_index": row_index,
        "course_uid": course_uid,
        "source_file": source_file,
        "decision_id": decision_id,
        "input_hash": input_hash,
        "selection_sorted": selection_sorted,
        "structure_max": "CP_SEC_SIMULATION",
        "ticket_exact": "NO_BET",
        "segment_roi": "NO_BET",
        "edge_status": "EDGE_ABSENT",
        "brief": brief,
        "candidate_row_tokens": {
            "course_uid": course_uid,
            "source_file": source_file,
            "decision_id": decision_id,
            "input_hash": input_hash,
            "selection_sorted": selection_sorted,
        },
    }


def get_cp_sec_paper_match_test(row_index: int = 0) -> Dict[str, Any]:
    rows = _read_csv(ADMISSIBLE_PATH)

    if not rows:
        payload = {
            "ok": False,
            "tester_version": TESTER_VERSION,
            "error": "ADMISSIBLE_FILE_MISSING_OR_EMPTY",
            "admissible_path": str(ADMISSIBLE_PATH),
            "ticket_strict": "NO_BET",
            "strict_allowed_oui_non": "non",
        }
        LATEST_TEST_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    if row_index < 0:
        row_index = 0

    if row_index >= len(rows):
        row_index = len(rows) - 1

    row = rows[row_index]
    payload = _build_synthetic_payload(row, row_index=row_index)
    decorated = decorate_payload_with_cp_sec_paper_rule(payload)

    block = decorated.get("cp_sec_paper_rule", {}) or {}
    match_ok = (
        decorated.get("paper_allowed_oui_non") == "oui"
        and block.get("paper_allowed_oui_non") == "oui"
        and block.get("structure_max") == "CP_SEC_PAPER_ONLY"
        and decorated.get("ticket_strict") == "NO_BET"
        and decorated.get("strict_allowed_oui_non") == "non"
    )

    result = {
        "ok": bool(match_ok),
        "tester_version": TESTER_VERSION,
        "test_status": "CP_SEC_PAPER_MATCH_OK" if match_ok else "CP_SEC_PAPER_MATCH_FAILED",
        "row_index": row_index,
        "admissible_rows": len(rows),
        "admissible_path": str(ADMISSIBLE_PATH),
        "ticket_strict": decorated.get("ticket_strict", "NO_BET"),
        "strict_allowed_oui_non": decorated.get("strict_allowed_oui_non", "non"),
        "strict_usable_oui_non": decorated.get("strict_usable_oui_non", "non"),
        "paper_allowed_oui_non": decorated.get("paper_allowed_oui_non", "non"),
        "paper_rule_id": decorated.get("paper_rule_id", "ND"),
        "paper_rule_match_method": decorated.get("paper_rule_match_method", "ND"),
        "structure_max": decorated.get("structure_max", "ND"),
        "segment_roi": decorated.get("segment_roi", "ND"),
        "edge_status": decorated.get("edge_status", "ND"),
        "cp_sec_paper_rule": block,
        "candidate_row_tokens": decorated.get("candidate_row_tokens", {}),
        "brief": decorated.get("brief", ""),
        "control": {
            "paper_allowed_must_be_oui": decorated.get("paper_allowed_oui_non") == "oui",
            "paper_structure_must_be_cp_sec_paper_only": block.get("structure_max") == "CP_SEC_PAPER_ONLY",
            "top_level_structure_must_be_cp_sec_paper_only": decorated.get("structure_max") == "CP_SEC_PAPER_ONLY",
            "ticket_strict_must_be_no_bet": decorated.get("ticket_strict") == "NO_BET",
            "strict_allowed_must_be_non": decorated.get("strict_allowed_oui_non") == "non",
        },
        "warning": "Payload synthétique de test match paper. Ne vaut pas ticket réel. Ne modifie pas les décisions live.",
    }

    LATEST_TEST_OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
