import csv
import re
import sys
from pathlib import Path

ROOT = Path(".").resolve()
sys.path.insert(0, str(ROOT))

from engine.bet_selection_optimizer import optimize_bet_selection

BASE = ROOT / "data" / "output" / "cloud_traces_pre_20260627" / "gcs_analysis"
OUT = ROOT / "data" / "output" / "cloud_traces_pre_20260627" / "backtest_cloud_v013_purified_pre_20260627.csv"
CUTOFF = 20260627

def s(v):
    return "" if v is None else str(v).strip()

def date_int(uid):
    m = re.search(r"(\d{2})[_/-](\d{2})[_/-](\d{4})", s(uid))
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return int(f"{yyyy}{mm}{dd}")

def read_csv(path):
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []

def purified_context(row, uid):
    ctx = {
        "course_uid": uid,
        "qualite": row.get("qualite") or row.get("quality") or "B",
        "cp_sec_candidate": (
            row.get("cp_sec_candidate")
            or row.get("selection_CP_SEC")
            or row.get("CP_SEC_selection")
            or row.get("paper_selection")
            or ""
        ),
        "cp_sg_pair_sum": (
            row.get("cp_sg_pair_sum")
            or row.get("CP_SEC_sg_sum")
            or row.get("cp_sg_sum")
            or row.get("latest_cp_sg_pair_sum")
            or ""
        ),
        "cp_sg_filter_status": row.get("cp_sg_filter_status") or "OK",
        "paper_candidate_reason": "CLOUD_PURIFIED_REPLAY",
    }
    return ctx

files = sorted(BASE.rglob("*decision_engine_outputs.csv"))
seen = set()
rows_out = []

for path in files:
    for row in read_csv(path):
        uid = s(row.get("course_uid")) or path.name.replace("_decision_engine_outputs.csv", "")
        di = date_int(uid)

        if not uid or di is None or di >= CUTOFF:
            continue

        key = uid.upper()
        if key in seen:
            continue
        seen.add(key)

        ctx = purified_context(row, uid)
        out = optimize_bet_selection(ctx)

        rows_out.append({
            "course_uid": uid,
            "source_file": str(path.relative_to(ROOT)),
            "qualite": ctx.get("qualite", ""),
            "cp_sec_candidate": ctx.get("cp_sec_candidate", ""),
            "cp_sg_pair_sum": ctx.get("cp_sg_pair_sum", ""),
            "cp_sg_filter_status": ctx.get("cp_sg_filter_status", ""),
            "best_structure": out.get("best_structure", ""),
            "ticket_paper": out.get("ticket_paper", ""),
            "ticket_real": out.get("ticket_real", ""),
            "segment_roi": out.get("segment_roi", ""),
            "cp_sec_priority_score": out.get("cp_sec_priority_score", ""),
            "cp_sec_priority_level": out.get("cp_sec_priority_level", ""),
            "reason_codes": "|".join(out.get("reason_codes", [])),
            "rejected_structures": "|".join(out.get("rejected_structures", [])),
            "hard_blocks": "|".join(out.get("hard_blocks", [])),
            "interpretation": "CLOUD_PURIFIED_NOT_STRICT_ROI",
        })

fieldnames = [
    "course_uid",
    "source_file",
    "qualite",
    "cp_sec_candidate",
    "cp_sg_pair_sum",
    "cp_sg_filter_status",
    "best_structure",
    "ticket_paper",
    "ticket_real",
    "segment_roi",
    "cp_sec_priority_score",
    "cp_sec_priority_level",
    "reason_codes",
    "rejected_structures",
    "hard_blocks",
    "interpretation",
]

with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows_out)

print("")
print("BACKTEST CLOUD PURIFIED V0_1_3 PRE 2026-06-27")
print("=============================================")
print(f"Fichiers decision_engine_outputs : {len(files)}")
print(f"Decisions rejouees : {len(rows_out)}")
print(f"NO_BET : {sum(1 for r in rows_out if r['best_structure'] == 'NO_BET')}")
print(f"CP_SEC_SIMULATION_LOW : {sum(1 for r in rows_out if r['best_structure'] == 'CP_SEC_SIMULATION_LOW')}")
print(f"CP_SEC_PAPER : {sum(1 for r in rows_out if r['ticket_paper'] != 'NO_BET')}")
print(f"Fichier : {OUT}")
