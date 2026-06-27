import csv
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(".").resolve()
INPUT = ROOT / "data" / "output" / "study_watchlist_v014.csv"
OUTPUT = ROOT / "data" / "output" / "cp3_top3_h5_paper_v014.csv"
ENGINE_VERSION = "V0_1_4"
SEGMENT_ROI = "CP3_TOP3_ETUDE_H5_PAPER"
PAPER_STRUCTURE = "CP3_TOP3"
PAPER_STAKE = 3.0
def s(value):
    if value is None:
        return ""
    return str(value).strip()
def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))
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
rows = read_csv(INPUT)
out_rows = []
rejected = []
run_ts = datetime.now(timezone.utc).isoformat()
for row in rows:
    course_uid = s(row.get("course_uid"))
    source_type = s(row.get("source_type"))
    top3_etude = s(row.get("top3_etude"))
    if source_type != "H5":
        rejected.append({
            "course_uid": course_uid,
            "reason": "SOURCE_NOT_H5",
        })
        continue
    nums = normalize_top3(top3_etude)
    if not nums:
        rejected.append({
            "course_uid": course_uid,
            "reason": "TOP3_ETUDE_INVALID",
        })
        continue
    pairs = build_pairs(nums)
    out_rows.append({
        "run_ts_utc": run_ts,
        "course_uid": course_uid,
        "source_type": source_type,
        "source_file": s(row.get("source_file")),
        "engine_version": ENGINE_VERSION,
        "segment_roi": SEGMENT_ROI,
        "paper_allowed": "OUI",
        "paper_structure": PAPER_STRUCTURE,
        "paper_selection": "-".join(nums),
        "paper_pairs": "|".join(pairs),
        "paper_stake": f"{PAPER_STAKE:.2f}",
        "decision_before_race": "A_VALIDER_LIVE",
        "result_known_at_decision": "NON",
        "strict_allowed": "NON",
        "ticket_real": "NO_BET",
        "audit_status": "PAPER_CANDIDATE",
        "reason_codes": "SOURCE_H5;TOP3_ETUDE_VALID;REAL_CLOSED;SEGMENT_SEPARATED",
    })
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
fieldnames = [
    "run_ts_utc",
    "course_uid",
    "source_type",
    "source_file",
    "engine_version",
    "segment_roi",
    "paper_allowed",
    "paper_structure",
    "paper_selection",
    "paper_pairs",
    "paper_stake",
    "decision_before_race",
    "result_known_at_decision",
    "strict_allowed",
    "ticket_real",
    "audit_status",
    "reason_codes",
]
with OUTPUT.open("w", encoding="utf-8-sig", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(out_rows)
print("")
print("CP3 TOP3 H5 PAPER V0_1_4")
print("========================")
print(f"Input : {INPUT}")
print(f"Output : {OUTPUT}")
print(f"Courses lues : {len(rows)}")
print(f"Candidats paper CP3 H5 : {len(out_rows)}")
print(f"Rejets : {len(rejected)}")
print(f"Segment ROI : {SEGMENT_ROI}")
print(f"Ticket réel : NO_BET")
print("Statut : OK")
