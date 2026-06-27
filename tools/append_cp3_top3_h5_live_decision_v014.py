import argparse
import csv
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(".").resolve()
LOG = ROOT / "data" / "output" / "paper_cp3_top3_h5_live_log.csv"
SEGMENT_ROI = "CP3_TOP3_ETUDE_H5_PAPER"
ENGINE_VERSION = "V0_1_4"
PAPER_STRUCTURE = "CP3_TOP3"
PAPER_STAKE = "3.00"
FIELDNAMES = [
    "created_ts_utc",
    "decision_ts_utc",
    "course_uid",
    "source_type",
    "engine_version",
    "segment_roi",
    "paper_structure",
    "paper_selection",
    "paper_pairs",
    "paper_stake",
    "decision_before_race",
    "result_known_at_decision",
    "strict_allowed",
    "ticket_real",
    "result_status",
    "arrival_top3",
    "cp3_hit_pairs",
    "cp3_return",
    "gain_net",
    "roi",
    "audit_status",
    "reason_codes",
    "notes",
]
def s(v):
    return "" if v is None else str(v).strip()
def normalize_top3(value):
    raw = s(value).replace(";", "-").replace(",", "-").replace("|", "-")
    nums = [x.strip() for x in raw.split("-") if x.strip()]
    if len(nums) != 3:
        return []
    if len(set(nums)) != 3:
        return []
    if not all(x.isdigit() for x in nums):
        return []
    return nums
def build_pairs(nums):
    return [
        f"{nums[0]}-{nums[1]}",
        f"{nums[0]}-{nums[2]}",
        f"{nums[1]}-{nums[2]}",
    ]
def read_log():
    if not LOG.exists():
        raise FileNotFoundError(f"Journal live introuvable : {LOG}")
    with LOG.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))
def write_log(rows):
    with LOG.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
parser = argparse.ArgumentParser()
parser.add_argument("--course-uid", required=True)
parser.add_argument("--top3", required=True)
parser.add_argument("--source-type", required=True)
parser.add_argument("--confirm-before-race", required=True, choices=["OUI"])
parser.add_argument("--notes", default="")
args = parser.parse_args()
course_uid = s(args.course_uid)
source_type = s(args.source_type)
nums = normalize_top3(args.top3)
if source_type != "H5":
    raise SystemExit("REFUS : source_type doit etre H5")
if not nums:
    raise SystemExit("REFUS : top3 invalide")
rows = read_log()
existing = {s(r.get("course_uid")) for r in rows}
if course_uid in existing:
    raise SystemExit(f"REFUS : course_uid deja present dans le journal live : {course_uid}")
now = datetime.now(timezone.utc).isoformat()
pairs = build_pairs(nums)
new_row = {
    "created_ts_utc": now,
    "decision_ts_utc": now,
    "course_uid": course_uid,
    "source_type": source_type,
    "engine_version": ENGINE_VERSION,
    "segment_roi": SEGMENT_ROI,
    "paper_structure": PAPER_STRUCTURE,
    "paper_selection": "-".join(nums),
    "paper_pairs": "|".join(pairs),
    "paper_stake": PAPER_STAKE,
    "decision_before_race": "OUI",
    "result_known_at_decision": "NON",
    "strict_allowed": "NON",
    "ticket_real": "NO_BET",
    "result_status": "PENDING",
    "arrival_top3": "",
    "cp3_hit_pairs": "",
    "cp3_return": "",
    "gain_net": "",
    "roi": "",
    "audit_status": "LIVE_PAPER_PENDING_RESULT",
    "reason_codes": "SOURCE_H5;TOP3_ETUDE_VALID;DECISION_BEFORE_RACE;REAL_CLOSED",
    "notes": s(args.notes),
}
rows.append(new_row)
write_log(rows)
print("")
print("APPEND CP3 TOP3 H5 LIVE DECISION V0_1_4")
print("======================================")
print(f"Course : {course_uid}")
print(f"Selection : {'-'.join(nums)}")
print(f"Paires : {'|'.join(pairs)}")
print(f"Mise paper : {PAPER_STAKE}")
print("decision_before_race : OUI")
print("ticket_real : NO_BET")
print("Statut : OK")
