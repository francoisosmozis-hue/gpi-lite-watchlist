import csv
import re
import unicodedata
from pathlib import Path

ROOT = Path(".").resolve()

SIM_FILE = ROOT / "data" / "output" / "cloud_traces_pre_20260627" / "cloud_v013_simulation_low_17.csv"
RESULT_ROOTS = [
    ROOT / "data" / "output" / "cloud_traces_pre_20260627",
    ROOT / "data" / "output",
    ROOT / "data",
]

OUT = ROOT / "data" / "output" / "cloud_traces_pre_20260627" / "simulation_low_17_result_match.csv"

def norm(s):
    if s is None:
        return ""
    s = str(s).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def date_key(s):
    m = re.search(r"(\d{2})[_/-](\d{2})[_/-](\d{4})", str(s))
    if not m:
        return ""
    dd, mm, yyyy = m.groups()
    return f"{dd}_{mm}_{yyyy}"

def course_tokens(uid):
    n = norm(uid)
    tokens = [t for t in n.split() if len(t) >= 4]
    stop = {
        "2026", "pronostics", "cotes", "prix", "course",
        "resultats", "rapports", "groupe"
    }
    return [t for t in tokens if t not in stop]

def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))

def find_result_files():
    files = []
    seen = set()

    for root in RESULT_ROOTS:
        if not root.exists():
            continue

        for p in root.rglob("*"):
            if not p.is_file():
                continue

            name = p.name
            lname = norm(name)

            if (
                p.suffix.lower() in [".pdf", ".csv", ".txt"]
                and (
                    "resultat" in lname
                    or "resultats" in lname
                    or "rapport" in lname
                    or "rapports" in lname
                )
            ):
                key = str(p.resolve()).lower()
                if key not in seen:
                    seen.add(key)
                    files.append(p)

    return files

sim_rows = read_csv(SIM_FILE)
result_files = find_result_files()

out_rows = []

for r in sim_rows:
    uid = r.get("course_uid", "")
    dkey = date_key(uid)
    tokens = course_tokens(uid)

    candidates = []

    for p in result_files:
        pname = norm(p.name)
        score = 0

        if dkey and dkey in p.name:
            score += 10

        for t in tokens:
            if t in pname:
                score += 1

        if score >= 12:
            candidates.append((score, p))

    candidates = sorted(candidates, key=lambda x: x[0], reverse=True)

    best_score = candidates[0][0] if candidates else 0
    best_file = str(candidates[0][1].relative_to(ROOT)) if candidates else "ND"

    out_rows.append({
        "course_uid": uid,
        "cp_sec_candidate": r.get("cp_sec_candidate", ""),
        "cp_sg_pair_sum": r.get("cp_sg_pair_sum", ""),
        "cp_sec_priority_score": r.get("cp_sec_priority_score", ""),
        "cp_sec_priority_level": r.get("cp_sec_priority_level", ""),
        "segment_audit": "SIMULATION_LOW_RETRO",
        "result_file_found": "OUI" if candidates else "NON",
        "result_match_score": best_score,
        "result_file": best_file,
        "audit_status": "RESULT_FILE_FOUND" if candidates else "RESULT_FILE_MISSING",
    })

OUT.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
    writer.writeheader()
    writer.writerows(out_rows)

print("")
print("AUDIT MATCH RESULTATS SIMULATION_LOW_17")
print("======================================")
print(f"Cas simulation low : {len(out_rows)}")
print(f"Résultats trouvés : {sum(1 for r in out_rows if r['result_file_found'] == 'OUI')}")
print(f"Résultats manquants : {sum(1 for r in out_rows if r['result_file_found'] == 'NON')}")
print(f"Fichier : {OUT}")
