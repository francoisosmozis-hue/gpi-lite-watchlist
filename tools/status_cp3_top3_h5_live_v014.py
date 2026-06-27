import csv
import subprocess
import sys
from pathlib import Path
ROOT = Path(".").resolve()
FILES = {
    "live_log": ROOT / "data" / "output" / "paper_cp3_top3_h5_live_log.csv",
    "live_summary": ROOT / "data" / "output" / "paper_cp3_top3_h5_live_summary.csv",
    "paper_builder_output": ROOT / "data" / "output" / "cp3_top3_h5_paper_v014.csv",
    "doc_live": ROOT / "docs" / "CP3_TOP3_H5_PAPER_LIVE_V0_1_4.md",
    "audit_doc": ROOT / "docs" / "AUDIT_ROI_STUDY_V0_1_4.md",
    "smoke_test": ROOT / "tools" / "smoke_test_cp3_top3_h5_live_v014.py",
    "validator": ROOT / "tools" / "validate_paper_cp3_top3_h5_live_log_v014.py",
    "summarizer": ROOT / "tools" / "summarize_paper_cp3_top3_h5_live_roi_v014.py",
}
SEGMENT = "CP3_TOP3_ETUDE_H5_PAPER"
def s(v):
    return "" if v is None else str(v).strip()
def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))
def git_last_commit():
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else "ND"
    except Exception:
        return "ND"
def git_status_clean():
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return "ND"
        return "OUI" if result.stdout.strip() == "" else "NON"
    except Exception:
        return "ND"
print("")
print("STATUS CP3 TOP3 H5 LIVE V0_1_4")
print("==============================")
print(f"Root : {ROOT}")
print(f"Dernier commit : {git_last_commit()}")
print(f"Working tree clean : {git_status_clean()}")
print("")
print("FICHIERS")
for name, path in FILES.items():
    print(f"{name} : {'OK' if path.exists() else 'MANQUANT'}")
live_rows = read_csv(FILES["live_log"])
summary_rows = read_csv(FILES["live_summary"])
paper_rows = read_csv(FILES["paper_builder_output"])
print("")
print("PAPER RETRO")
print(f"Candidats CP3 H5 paper : {len(paper_rows)}")
print("ROI retro conservateur : +29,82 %")
print("Statut retro : PAPER_CANDIDATE")
print("")
print("LIVE LOG")
print(f"Lignes live : {len(live_rows)}")
pending = 0
resolved = 0
bad_real = 0
bad_strict = 0
bad_segment = 0
for row in live_rows:
    if s(row.get("result_status")) == "PENDING":
        pending += 1
    if s(row.get("result_status")) == "RESOLVED":
        resolved += 1
    if s(row.get("ticket_real")) != "NO_BET":
        bad_real += 1
    if s(row.get("strict_allowed")) != "NON":
        bad_strict += 1
    if s(row.get("segment_roi")) != SEGMENT:
        bad_segment += 1
print(f"Pending : {pending}")
print(f"Resolved : {resolved}")
print(f"Anomalies ticket_real : {bad_real}")
print(f"Anomalies strict_allowed : {bad_strict}")
print(f"Anomalies segment : {bad_segment}")
print("")
print("LIVE SUMMARY")
if summary_rows:
    row = summary_rows[-1]
    print(f"segment_roi : {s(row.get('segment_roi'))}")
    print(f"total_decisions : {s(row.get('total_decisions'))}")
    print(f"pending : {s(row.get('pending'))}")
    print(f"resolved : {s(row.get('resolved'))}")
    print(f"roi_countable : {s(row.get('roi_countable'))}")
    print(f"hits : {s(row.get('hits'))}")
    print(f"stake_total : {s(row.get('stake_total'))}")
    print(f"return_total : {s(row.get('return_total'))}")
    print(f"gain_total : {s(row.get('gain_total'))}")
    print(f"roi_live : {s(row.get('roi'))}")
    print(f"ticket_real : {s(row.get('ticket_real'))}")
    print(f"strict_allowed : {s(row.get('strict_allowed'))}")
    print(f"audit_status : {s(row.get('audit_status'))}")
else:
    print("Summary : ND")
errors = []
for name, path in FILES.items():
    if not path.exists():
        errors.append(f"Fichier manquant : {name}")
if bad_real:
    errors.append("ticket_real different de NO_BET dans le live log")
if bad_strict:
    errors.append("strict_allowed different de NON dans le live log")
if bad_segment:
    errors.append("segment_roi invalide dans le live log")
if summary_rows:
    last = summary_rows[-1]
    if s(last.get("ticket_real")) != "NO_BET":
        errors.append("summary ticket_real different de NO_BET")
    if s(last.get("strict_allowed")) != "NON":
        errors.append("summary strict_allowed different de NON")
    if s(last.get("audit_status")) != "OK":
        errors.append("summary audit_status different de OK")
print("")
print("VERDICT")
if errors:
    print("STATUS : KO")
    for err in errors:
        print(f"- {err}")
    raise SystemExit(1)
print("STATUS : OK")
print("Segment : CP3_TOP3_ETUDE_H5_PAPER")
print("Ticket reel : NO_BET")
print("Strict allowed : NON")
