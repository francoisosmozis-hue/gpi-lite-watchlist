from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"

REPORTS_CSV = OUTPUT_DIR / "reports.csv"
COURSES_RESULTS_CSV = OUTPUT_DIR / "courses_results.csv"

REPORTS_LABELED_CSV = OUTPUT_DIR / "reports_labeled.csv"
REPORTS_LABEL_AUDIT_CSV = OUTPUT_DIR / "reports_label_audit.csv"
REPORTS_LABEL_AUDIT_JSON = OUTPUT_DIR / "latest_reports_label_audit.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(str(path))

    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_nums(value: str) -> List[int]:
    nums: List[int] = []

    for part in str(value or "").replace(" ", "").split("-"):
        if part.isdigit():
            nums.append(int(part))

    return nums


def nums_key(nums: List[int]) -> str:
    return "-".join(str(x) for x in sorted(nums))


def arrival_info(arrivee: str) -> Tuple[List[int], Dict[int, int]]:
    nums = parse_nums(arrivee)
    ranks = {n: i + 1 for i, n in enumerate(nums)}
    return nums, ranks


def all_in(values: List[int], pool: List[int]) -> bool:
    return bool(values) and all(x in pool for x in values)


def same_set(values: List[int], pool: List[int]) -> bool:
    return set(values) == set(pool)


def label_report(
    row: Dict[str, str],
    report_order: int,
    simple_pos: int,
    pair_pos: int,
    first_trio_order: int | None,
    first_four_order: int | None,
    arrivee_nums: List[int],
    ranks: Dict[int, int],
) -> Dict[str, str]:
    bet_type = row.get("bet_type", "")
    selection = row.get("selection_sorted", "")
    nums = parse_nums(selection)

    top2 = arrivee_nums[:2]
    top3 = arrivee_nums[:3]
    top4 = arrivee_nums[:4]
    top5 = arrivee_nums[:5]

    rank_list = []
    for n in nums:
        rank_list.append(str(ranks.get(n, 0)))

    label = "UNRESOLVED_RAW"
    label_family = "UNRESOLVED"
    confidence = "LOW"
    strict_usable = "non"
    reason = "raw_unlabeled"

    if not nums:
        label = "INVALID_SELECTION"
        label_family = "INVALID"
        confidence = "HIGH"
        reason = "selection_empty"

    elif bet_type == "SIMPLE_RAW":
        n = nums[0]
        rank = ranks.get(n, 0)

        if rank == 1 and simple_pos == 1:
            label = "SG_CANDIDATE"
            label_family = "SG"
            confidence = "MEDIUM"
            reason = "first_simple_raw_and_rank_1"
        elif rank in {1, 2, 3}:
            label = "SP_OR_SIMPLE_CANDIDATE_NEEDS_VALIDATION"
            label_family = "SP_CANDIDATE"
            confidence = "LOW"
            reason = "simple_raw_rank_top3_but_table_role_uncertain"
        elif rank == 4:
            label = "ZECOUILLON_CANDIDATE"
            label_family = "ZECOUILLON"
            confidence = "MEDIUM"
            reason = "simple_raw_rank_4"
        else:
            label = "SIMPLE_RAW_OUTSIDE_ARRIVEE"
            label_family = "SIMPLE_OTHER"
            confidence = "LOW"
            reason = "simple_raw_not_top4"

    elif bet_type == "PAIR_RAW":
        before_trio = first_trio_order is not None and report_order < first_trio_order
        after_trio = first_trio_order is not None and report_order > first_trio_order

        if all_in(nums, top3) and before_trio:
            label = "CP_OR_JUMELE_CANDIDATE_TOP3_PRE_TRIO"
            label_family = "CP_CANDIDATE"
            confidence = "MEDIUM"
            reason = "pair_top3_before_first_trio"
        elif all_in(nums, top4) and after_trio:
            label = "ZE_2SUR4_CANDIDATE_TOP4_POST_TRIO"
            label_family = "2SUR4_CANDIDATE"
            confidence = "MEDIUM"
            reason = "pair_top4_after_first_trio"
        elif same_set(nums, top2):
            label = "PAIR_TOP2_CANDIDATE"
            label_family = "PAIR_TOP2"
            confidence = "LOW"
            reason = "pair_equals_top2_but_sequence_uncertain"
        elif all_in(nums, top3):
            label = "PAIR_TOP3_CANDIDATE_SEQUENCE_UNCERTAIN"
            label_family = "CP_CANDIDATE"
            confidence = "LOW"
            reason = "pair_top3_sequence_uncertain"
        elif all_in(nums, top4):
            label = "PAIR_TOP4_CANDIDATE_SEQUENCE_UNCERTAIN"
            label_family = "2SUR4_CANDIDATE"
            confidence = "LOW"
            reason = "pair_top4_sequence_uncertain"
        else:
            label = "PAIR_RAW_OUTSIDE_TOP4"
            label_family = "PAIR_OTHER"
            confidence = "LOW"
            reason = "pair_not_in_top4"

    elif bet_type == "TRIO_RAW":
        if same_set(nums, top3):
            label = "TRIO_CANDIDATE_TOP3"
            label_family = "TRIO"
            confidence = "HIGH"
            reason = "trio_raw_equals_top3"
        else:
            label = "TRIO_RAW_NOT_TOP3"
            label_family = "TRIO_OTHER"
            confidence = "LOW"
            reason = "trio_raw_not_equal_top3"

    elif bet_type == "ZE4_OR_2SUR4_RAW":
        if same_set(nums, top4):
            label = "ZE4_CANDIDATE_TOP4"
            label_family = "ZE4"
            confidence = "HIGH"
            reason = "four_raw_equals_top4"
        elif all_in(nums, top4):
            label = "EXOTIC4_CANDIDATE_TOP4_SUBSET"
            label_family = "EXOTIC4"
            confidence = "MEDIUM"
            reason = "four_raw_subset_top4"
        else:
            label = "EXOTIC4_RAW_SEQUENCE_UNCERTAIN"
            label_family = "EXOTIC4_OTHER"
            confidence = "LOW"
            reason = "four_raw_uncertain"

    elif bet_type == "ZE5_RAW":
        if same_set(nums, top5):
            label = "ZE5_CANDIDATE_TOP5"
            label_family = "ZE5"
            confidence = "MEDIUM"
            reason = "ze5_raw_equals_top5"
        else:
            label = "ZE5_RAW_SEQUENCE_UNCERTAIN"
            label_family = "ZE5_OTHER"
            confidence = "LOW"
            reason = "ze5_raw_uncertain"

    else:
        label = "UNKNOWN_BET_TYPE"
        label_family = "UNKNOWN"
        confidence = "LOW"
        reason = f"unknown_bet_type:{bet_type}"

    enriched = dict(row)
    enriched.update(
        {
            "report_order": str(report_order),
            "selection_count": str(len(nums)),
            "arrival_ranks": "-".join(rank_list),
            "label": label,
            "label_family": label_family,
            "label_confidence": confidence,
            "strict_usable_oui_non": strict_usable,
            "label_reason": reason,
        }
    )

    return enriched


def build_reports_labeled() -> Dict[str, object]:
    reports = read_csv(REPORTS_CSV)
    courses = read_csv(COURSES_RESULTS_CSV)

    course_arrivees = {
        row.get("course_uid", ""): row.get("arrivee", "")
        for row in courses
    }

    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)

    for i, row in enumerate(reports, start=1):
        row["_global_order"] = str(i)
        grouped[row.get("course_uid", "")].append(row)

    labeled_rows: List[Dict[str, str]] = []

    for course_uid, rows in grouped.items():
        arrivee_nums, ranks = arrival_info(course_arrivees.get(course_uid, ""))

        first_trio_order = None
        first_four_order = None

        for local_order, row in enumerate(rows, start=1):
            if row.get("bet_type") == "TRIO_RAW" and first_trio_order is None:
                first_trio_order = local_order
            if row.get("bet_type") == "ZE4_OR_2SUR4_RAW" and first_four_order is None:
                first_four_order = local_order

        simple_pos = 0
        pair_pos = 0

        for local_order, row in enumerate(rows, start=1):
            if row.get("bet_type") == "SIMPLE_RAW":
                simple_pos += 1
            if row.get("bet_type") == "PAIR_RAW":
                pair_pos += 1

            enriched = label_report(
                row=row,
                report_order=local_order,
                simple_pos=simple_pos,
                pair_pos=pair_pos,
                first_trio_order=first_trio_order,
                first_four_order=first_four_order,
                arrivee_nums=arrivee_nums,
                ranks=ranks,
            )

            labeled_rows.append(enriched)

    base_fields = list(reports[0].keys()) if reports else []
    base_fields = [x for x in base_fields if x != "_global_order"]

    extra_fields = [
        "report_order",
        "selection_count",
        "arrival_ranks",
        "label",
        "label_family",
        "label_confidence",
        "strict_usable_oui_non",
        "label_reason",
    ]

    fieldnames = base_fields + [x for x in extra_fields if x not in base_fields]

    write_csv(REPORTS_LABELED_CSV, labeled_rows, fieldnames)

    by_label = Counter(row.get("label", "ND") for row in labeled_rows)
    by_family = Counter(row.get("label_family", "ND") for row in labeled_rows)
    by_confidence = Counter(row.get("label_confidence", "ND") for row in labeled_rows)

    audit_rows: List[Dict[str, str]] = []

    for label, count in by_label.most_common():
        audit_rows.append(
            {
                "metric": "by_label",
                "name": label,
                "count": str(count),
            }
        )

    for family, count in by_family.most_common():
        audit_rows.append(
            {
                "metric": "by_label_family",
                "name": family,
                "count": str(count),
            }
        )

    for confidence, count in by_confidence.most_common():
        audit_rows.append(
            {
                "metric": "by_label_confidence",
                "name": confidence,
                "count": str(count),
            }
        )

    write_csv(
        REPORTS_LABEL_AUDIT_CSV,
        audit_rows,
        ["metric", "name", "count"],
    )

    payload: Dict[str, object] = {
        "ok": True,
        "created_at": now_utc(),
        "reports_count": len(reports),
        "labeled_count": len(labeled_rows),
        "courses_count": len(grouped),
        "by_label": dict(by_label),
        "by_label_family": dict(by_family),
        "by_label_confidence": dict(by_confidence),
        "strict_usable_count": 0,
        "strict_usable_oui_non": "non",
        "warning": "RAW labels are candidates only. No strict calibration allowed from this file alone.",
        "files": {
            "reports_labeled": str(REPORTS_LABELED_CSV),
            "reports_label_audit_csv": str(REPORTS_LABEL_AUDIT_CSV),
            "reports_label_audit_json": str(REPORTS_LABEL_AUDIT_JSON),
        },
    }

    write_json(REPORTS_LABEL_AUDIT_JSON, payload)

    return payload


def main() -> None:
    payload = build_reports_labeled()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()