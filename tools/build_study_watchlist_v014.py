import csv
import re
from pathlib import Path
ROOT = Path(".").resolve()
BASE = ROOT / "data" / "output" / "cloud_traces_pre_20260627" / "gcs_analysis"
OUT = ROOT / "data" / "output" / "study_watchlist_v014.csv"
CUTOFF = 20260627
ODDS_KEYS = ["SG", "sg", "cote", "cote_gagnant", "cote_zeturf", "rapport_probable", "gagnant"]
NUM_KEYS = ["numero", "num", "N", "n", "cheval_numero", "No"]
NAME_KEYS = ["cheval", "nom", "partant", "horse", "runner"]
def s(v):
    return "" if v is None else str(v).strip()
def to_float(v):
    raw = s(v).replace(",", ".")
    raw = re.sub(r"[^0-9.]", "", raw)
    try:
        return float(raw)
    except Exception:
        return None
def date_int(uid):
    m = re.search(r"(\d{2})[_/-](\d{2})[_/-](\d{4})", uid)
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return int(f"{yyyy}{mm}{dd}")
def pick(row, keys):
    for k in keys:
        if k in row and s(row.get(k)):
            return s(row.get(k))
    return ""
def read_csv(path):
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []
def course_uid_from_file(path):
    name = path.name
    for suffix in ["_course_h5_clean.csv", "_course_9h_clean.csv", "_h5_update.csv"]:
        if name.endswith(suffix):
            return name.replace(suffix, "")
    return name
def source_rank(path):
    name = path.name
    if name.endswith("_course_h5_clean.csv"):
        return 1
    if name.endswith("_h5_update.csv"):
        return 2
    if name.endswith("_course_9h_clean.csv"):
        return 3
    return 9
def detect_source_type(path):
    name = path.name
    if name.endswith("_course_h5_clean.csv"):
        return "H5"
    if name.endswith("_h5_update.csv"):
        return "H5_UPDATE"
    if name.endswith("_course_9h_clean.csv"):
        return "9H"
    return "ND"
def is_np(row):
    joined = " ".join(str(v) for v in row.values()).upper()
    return "NON PARTANT" in joined or re.search(r"\bNP\b", joined) is not None
def build_for_file(path):
    rows = read_csv(path)
    uid = course_uid_from_file(path)
    di = date_int(uid)
    if di is not None and di >= CUTOFF:
        return None
    runners = []
    for row in rows:
        num = pick(row, NUM_KEYS)
        name = pick(row, NAME_KEYS)
        odds = None
        for k in ODDS_KEYS:
            if k in row:
                odds = to_float(row.get(k))
                if odds is not None:
                    break
        if not num or odds is None:
            continue
        if is_np(row):
            continue
        runners.append({
            "num": num,
            "name": name,
            "sg": odds,
        })
    if not runners:
        return None
    runners = sorted(runners, key=lambda x: x["sg"])
    top3 = runners[:3]
    outsiders = [r for r in runners if 8.0 <= r["sg"] <= 25.0]
    outsider = outsiders[0] if outsiders else None
    return {
        "course_uid": uid,
        "source_file": str(path.relative_to(ROOT)),
        "source_type": detect_source_type(path),
        "top3_etude": "-".join(r["num"] for r in top3),
        "top3_etude_names": " | ".join(f"{r['num']} {r['name']} ({r['sg']})" for r in top3),
        "outsider_etude": outsider["num"] if outsider else "ND",
        "outsider_etude_name": f"{outsider['num']} {outsider['name']} ({outsider['sg']})" if outsider else "ND",
        "top1_sg": top3[0]["sg"] if len(top3) >= 1 else "ND",
        "top2_sg": top3[1]["sg"] if len(top3) >= 2 else "ND",
        "top3_sg": top3[2]["sg"] if len(top3) >= 3 else "ND",
        "study_allowed": "OUI",
        "paper_allowed": "NON_PAR_DEFAUT",
        "strict_allowed": "NON",
        "ticket_real": "NO_BET",
        "segment_roi_top3": "STUDY_TOP3",
        "segment_roi_outsider": "STUDY_OUTSIDER",
    }
all_files = []
all_files.extend(BASE.rglob("*course_h5_clean.csv"))
all_files.extend(BASE.rglob("*h5_update.csv"))
all_files.extend(BASE.rglob("*course_9h_clean.csv"))
by_course = {}
for path in all_files:
    uid = course_uid_from_file(path)
    if uid not in by_course or source_rank(path) < source_rank(by_course[uid]):
        by_course[uid] = path
records = []
for uid, path in sorted(by_course.items()):
    rec = build_for_file(path)
    if rec:
        records.append(rec)
OUT.parent.mkdir(parents=True, exist_ok=True)
fieldnames = [
    "course_uid",
    "source_file",
    "source_type",
    "top3_etude",
    "top3_etude_names",
    "outsider_etude",
    "outsider_etude_name",
    "top1_sg",
    "top2_sg",
    "top3_sg",
    "study_allowed",
    "paper_allowed",
    "strict_allowed",
    "ticket_real",
    "segment_roi_top3",
    "segment_roi_outsider",
]
with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(records)
print("")
print("STUDY WATCHLIST V0_1_4")
print("=====================")
print(f"Courses sources uniques : {len(by_course)}")
print(f"Courses avec watchlist etude : {len(records)}")
print(f"Fichier : {OUT}")

