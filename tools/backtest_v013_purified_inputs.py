import csv
import re
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.bet_selection_optimizer import optimize_bet_selection

SRC_DIR = ROOT / "data" / "output"
OUT = ROOT / "data" / "output" / "backtests" / "backtest_v013_purified_inputs_pre_20260627.csv"
OUT.parent.mkdir(parents=True, exist_ok=True)

CUTOFF = 20260627


def s(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def date_int(uid: str) -> Optional[int]:
    m = re.search(r"(\d{2})[_/-](\d{2})[_/-](\d{4})", s(uid))
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return int(f"{yyyy}{mm}{dd}")


def read_csv(path: Path):
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def clean_context(row):
    allowed = {
        "course_uid",
        "qualite",
        "quality",
        "cp_sec_candidate",
        "paper_selection",
        "cp_sg_pair_sum",
        "cp_sg_filter_status",
        "paper_candidate_reason",
        "sp_candidate",
        "sp_min",
        "segment_sp_status",
    }

    ctx = {}

    for k, v in row.items():
        if k in allowed:
            ctx[k] = v

    if not ctx.get("course_uid"):
        ctx["course_uid"] = row.get("course_uid", "")

    if not ctx.get("cp_sec_candidate"):
        ctx["cp_sec_candidate"] = (
            row.get("selection_CP_SEC")
            or row.get("CP_SEC_selection")
            or row.get("paper_selection")
            or ""
        )

    if not ctx.get("cp_sg_pair_sum"):
        ctx["cp_sg_pair_sum"] = (
            row.get("CP_SEC_sg_sum")
            or row.get("cp_sg_sum")
            or row.get("latest_cp_sg_pair_sum")
            or ""
        )

    if not ctx.get("cp_sg_filter_status"):
        ctx["cp_sg_filter_status"] = "OK"

    if not ctx.get("paper_candidate_reason"):
        ctx["paper_candidate_reason"] = "PURIFIED_REPLAY_PRE_20260627"

    if not ctx.get("qualite"):
        ctx["qualite"] = row.get("quality") or "B"

    return ctx


files = []
files.extend(SRC_DIR.glob("*_decision_engine_outputs.csv"))
files.extend(SRC_DIR.glob("*_decision_lite.csv"))
files.extend(SRC_DIR.glob("decision_engine_outputs.csv"))
files.extend(SRC_DIR.glob("decision_lite.csv"))

seen = set()
rows_out = []

for path in sorted(set(files)):
    if path.name.startswith("latest_"):
        continue

    for raw in read_csv(path):
        uid = s(raw.get("course_uid"))
        di = date_int(uid)

        if not uid or di is None or di >= CUTOFF:
            continue

        key = uid.upper()
        if key in seen:
            continue
        seen.add(key)

        ctx = clean_context(raw)
        out = optimize_bet_selection(ctx)

        rows_out.append({
            "course_uid": uid,
            "source_file": str(path.relative_to(ROOT)),
            "qualite": ctx.get("qualite", ""),
            "cp_sec_candidate": ctx.get("cp_sec_candidate", ""),
            "cp_sg_pair_sum": ctx.get("cp_sg_pair_sum", ""),
            "cp_sg_filter_status": ctx.get("cp_sg_filter_status", ""),
            "paper_candidate_reason": ctx.get("paper_candidate_reason", ""),
            "best_structure": out.get("best_structure", ""),
            "ticket_paper": out.get("ticket_paper", ""),
            "ticket_real": out.get("ticket_real", ""),
            "segment_roi": out.get("segment_roi", ""),
            "cp_sec_priority_score": out.get("cp_sec_priority_score", ""),
            "cp_sec_priority_level": out.get("cp_sec_priority_level", ""),
            "reason_codes": "|".join(out.get("reason_codes", [])),
            "rejected_structures": "|".join(out.get("rejected_structures", [])),
            "hard_blocks": "|".join(out.get("hard_blocks", [])),
            "interpretation": "PURIFIED_INPUT_REPLAY_NOT_STRICT_ROI",
        })

fieldnames = list(rows_out[0].keys()) if rows_out else [
    "course_uid",
    "source_file",
    "qualite",
    "cp_sec_candidate",
    "cp_sg_pair_sum",
    "cp_sg_filter_status",
    "paper_candidate_reason",
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
print("BACKTEST V0_1_3 PURIFIED INPUTS AVANT 2026-06-27")
print("================================================")
print(f"Lignes rejouées : {len(rows_out)}")
print(f"CP_SEC_PAPER : {sum(1 for r in rows_out if r['ticket_paper'] != 'NO_BET')}")
print(f"CP_SEC_SIMULATION_LOW : {sum(1 for r in rows_out if r['best_structure'] == 'CP_SEC_SIMULATION_LOW')}")
print(f"NO_BET : {sum(1 for r in rows_out if r['best_structure'] == 'NO_BET')}")
print("")
print(f"Fichier : {OUT}")
