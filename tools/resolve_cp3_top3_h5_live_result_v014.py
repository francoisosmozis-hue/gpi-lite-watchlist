import argparse
import csv
from pathlib import Path
ROOT = Path(".").resolve()
LOG = ROOT / "data" / "output" / "paper_cp3_top3_h5_live_log.csv"
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
def normalize_nums(value):
    raw = s(value).replace(";", "-").replace(",", "-").replace("|", "-")
    nums = [x.strip() for x in raw.split("-") if x.strip()]
    if len(nums) != 3:
        return []
    if len(set(nums)) != 3:
        return []
    if not all(x.isdigit() for x in nums):
        return []
    return nums
def pair_key(a, b):
    a = s(a)
    b = s(b)
    return "-".join(sorted([a, b], key=lambda x: int(x)))
def parse_pairs(value):
    out = []
    for part in s(value).split("|"):
        part = part.strip()
        if not part:
            continue
        bits = [x.strip() for x in part.replace(";", "-").split("-") if x.strip()]
        if len(bits) != 2:
            continue
        if not all(x.isdigit() for x in bits):
            continue
        out.append(pair_key(bits[0], bits[1]))
    return out
def parse_cp_reports(value):
    reports = {}
    for part in s(value).split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise SystemExit(f"REFUS : rapport CP invalide : {part}")
        pair_raw, report_raw = part.split(":", 1)
        nums = [x.strip() for x in pair_raw.replace("|", "-").split("-") if x.strip()]
        if len(nums) != 2:
            raise SystemExit(f"REFUS : paire CP invalide : {part}")
        if not all(x.isdigit() for x in nums):
            raise SystemExit(f"REFUS : paire CP non numerique : {part}")
        try:
            report = float(report_raw.strip().replace(",", "."))
        except ValueError:
            raise SystemExit(f"REFUS : rapport CP non numerique : {part}")
        reports[pair_key(nums[0], nums[1])] = report
    return reports
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
parser.add_argument("--arrival-top3", required=True)
parser.add_argument("--cp-place-reports", required=True)
parser.add_argument("--notes", default="")
args = parser.parse_args()
course_uid = s(args.course_uid)
arrival = normalize_nums(args.arrival_top3)
if not arrival:
    raise SystemExit("REFUS : arrival-top3 invalide")
cp_reports = parse_cp_reports(args.cp_place_reports)
if len(cp_reports) > 3:
    raise SystemExit("REFUS : plus de 3 rapports CP fournis, risque ZE 2 sur 4")
rows = read_log()
matches = [i for i, r in enumerate(rows) if s(r.get("course_uid")) == course_uid]
if not matches:
    raise SystemExit(f"REFUS : course_uid absent du journal live : {course_uid}")
if len(matches) > 1:
    raise SystemExit(f"REFUS : course_uid duplique dans le journal live : {course_uid}")
idx = matches[0]
row = rows[idx]
if s(row.get("decision_before_race")) != "OUI":
    raise SystemExit("REFUS : decision_before_race doit etre OUI")
if s(row.get("result_known_at_decision")) != "NON":
    raise SystemExit("REFUS : result_known_at_decision doit etre NON")
if s(row.get("ticket_real")) != "NO_BET":
    raise SystemExit("REFUS : ticket_real doit rester NO_BET")
if s(row.get("strict_allowed")) != "NON":
    raise SystemExit("REFUS : strict_allowed doit rester NON")
if s(row.get("result_status")) != "PENDING":
    raise SystemExit("REFUS : result_status doit etre PENDING")
paper_pairs = parse_pairs(row.get("paper_pairs"))
arrival_pairs = {
    pair_key(arrival[0], arrival[1]),
    pair_key(arrival[0], arrival[2]),
    pair_key(arrival[1], arrival[2]),
}
hit_pairs = []
cp3_return = 0.0
for p in paper_pairs:
    if p in arrival_pairs:
        if p not in cp_reports:
            raise SystemExit(f"REFUS : rapport CP manquant pour paire gagnante : {p}")
        hit_pairs.append(p)
        cp3_return += cp_reports[p]
stake = float(s(row.get("paper_stake")).replace(",", "."))
gain_net = cp3_return - stake
roi = gain_net / stake if stake else 0.0
row["arrival_top3"] = "-".join(arrival)
row["cp3_hit_pairs"] = "|".join(hit_pairs)
row["cp3_return"] = f"{cp3_return:.2f}"
row["gain_net"] = f"{gain_net:.2f}"
row["roi"] = f"{roi:.6f}"
row["result_status"] = "RESOLVED"
row["audit_status"] = "LIVE_PAPER_RESOLVED"
row["notes"] = (s(row.get("notes")) + " | " + s(args.notes)).strip(" |")
rows[idx] = row
write_log(rows)
print("")
print("RESOLVE CP3 TOP3 H5 LIVE RESULT V0_1_4")
print("=====================================")
print(f"Course : {course_uid}")
print(f"Arrivee top3 : {'-'.join(arrival)}")
print(f"Paires jouees : {'|'.join(paper_pairs)}")
print(f"Paires gagnantes : {'|'.join(hit_pairs) if hit_pairs else 'AUCUNE'}")
print(f"Retour CP3 : {cp3_return:.2f}")
print(f"Mise : {stake:.2f}")
print(f"Gain net : {gain_net:.2f}")
print(f"ROI : {roi:.6f}")
print("Ticket reel : NO_BET")
print("Statut : OK")
