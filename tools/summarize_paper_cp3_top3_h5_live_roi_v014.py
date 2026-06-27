import csv
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(".").resolve()
LOG = ROOT / "data" / "output" / "paper_cp3_top3_h5_live_log.csv"
OUT = ROOT / "data" / "output" / "paper_cp3_top3_h5_live_summary.csv"
SEGMENT = "CP3_TOP3_ETUDE_H5_PAPER"
SUMMARY_FIELDS = [
    "run_ts_utc",
    "segment_roi",
    "total_decisions",
    "pending",
    "resolved",
    "roi_countable",
    "hits",
    "stake_total",
    "return_total",
    "gain_total",
    "roi",
    "ticket_real",
    "strict_allowed",
    "audit_status",
    "error_count",
]
def s(v):
    return "" if v is None else str(v).strip()
def f(v):
    raw = s(v).replace(",", ".")
    if raw == "":
        return 0.0
    return float(raw)
if not LOG.exists():
    raise FileNotFoundError(f"Journal live introuvable : {LOG}")
with LOG.open("r", encoding="utf-8-sig", newline="") as fh:
    rows = list(csv.DictReader(fh))
errors = []
total = 0
pending = 0
resolved = 0
roi_countable = 0
stake_total = 0.0
return_total = 0.0
gain_total = 0.0
hits = 0
for i, row in enumerate(rows, start=2):
    course_uid = s(row.get("course_uid"))
    if s(row.get("segment_roi")) != SEGMENT:
        errors.append(f"Ligne {i} : segment invalide : {course_uid}")
    if s(row.get("ticket_real")) != "NO_BET":
        errors.append(f"Ligne {i} : ticket_real doit rester NO_BET : {course_uid}")
    if s(row.get("strict_allowed")) != "NON":
        errors.append(f"Ligne {i} : strict_allowed doit rester NON : {course_uid}")
    if s(row.get("result_known_at_decision")) != "NON":
        errors.append(f"Ligne {i} : result_known_at_decision doit rester NON : {course_uid}")
    if s(row.get("decision_before_race")) != "OUI":
        errors.append(f"Ligne {i} : decision_before_race doit etre OUI pour compter live : {course_uid}")
    total += 1
    status = s(row.get("result_status"))
    if status == "PENDING":
        pending += 1
    elif status == "RESOLVED":
        resolved += 1
        roi_countable += 1
        stake = f(row.get("paper_stake"))
        ret = f(row.get("cp3_return"))
        gain = f(row.get("gain_net"))
        stake_total += stake
        return_total += ret
        gain_total += gain
        if ret > 0:
            hits += 1
    else:
        errors.append(f"Ligne {i} : result_status invalide : {status} / {course_uid}")
roi = gain_total / stake_total if stake_total else 0.0
audit_status = "OK" if not errors else "ERROR"
summary = {
    "run_ts_utc": datetime.now(timezone.utc).isoformat(),
    "segment_roi": SEGMENT,
    "total_decisions": total,
    "pending": pending,
    "resolved": resolved,
    "roi_countable": roi_countable,
    "hits": hits,
    "stake_total": f"{stake_total:.2f}",
    "return_total": f"{return_total:.2f}",
    "gain_total": f"{gain_total:.2f}",
    "roi": f"{roi:.6f}",
    "ticket_real": "NO_BET",
    "strict_allowed": "NON",
    "audit_status": audit_status,
    "error_count": len(errors),
}
OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
    writer.writeheader()
    writer.writerow(summary)
print("")
print("SUMMARY PAPER CP3 TOP3 H5 LIVE V0_1_4")
print("=====================================")
print(f"Journal : {LOG}")
print(f"Sortie : {OUT}")
print(f"Decisions totales : {total}")
print(f"Pending : {pending}")
print(f"Resolved : {resolved}")
print(f"ROI countable : {roi_countable}")
print(f"Hits : {hits}")
print(f"Mise totale : {stake_total:.2f}")
print(f"Retour total : {return_total:.2f}")
print(f"Gain total : {gain_total:.2f}")
print(f"ROI live : {roi:.6f}")
print(f"Ticket reel : NO_BET")
print(f"Erreurs : {len(errors)}")
if errors:
    print("")
    print("ERREURS")
    for err in errors[:50]:
        print(f"- {err}")
    raise SystemExit(1)
print("Statut : OK")
