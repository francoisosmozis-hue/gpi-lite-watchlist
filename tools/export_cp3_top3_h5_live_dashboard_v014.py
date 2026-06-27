import csv
import json
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(".").resolve()
LIVE_LOG = ROOT / "data" / "output" / "paper_cp3_top3_h5_live_log.csv"
LIVE_SUMMARY = ROOT / "data" / "output" / "paper_cp3_top3_h5_live_summary.csv"
PAPER_CANDIDATES = ROOT / "data" / "output" / "cp3_top3_h5_paper_v014.csv"
OUT = ROOT / "data" / "output" / "latest_cp3_top3_h5_live_status.json"
SEGMENT = "CP3_TOP3_ETUDE_H5_PAPER"
RETRO_ROI = 0.29824561403508776
RETRO_ROI_LABEL = "+29,82 %"
def s(v):
    return "" if v is None else str(v).strip()
def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))
live_rows = read_csv(LIVE_LOG)
summary_rows = read_csv(LIVE_SUMMARY)
candidate_rows = read_csv(PAPER_CANDIDATES)
summary = summary_rows[-1] if summary_rows else {}
errors = []
warnings = []
if not LIVE_LOG.exists():
    errors.append("LIVE_LOG_MISSING")
if not LIVE_SUMMARY.exists():
    errors.append("LIVE_SUMMARY_MISSING")
if not PAPER_CANDIDATES.exists():
    warnings.append("PAPER_CANDIDATES_MISSING")
bad_real = [r for r in live_rows if s(r.get("ticket_real")) != "NO_BET"]
bad_strict = [r for r in live_rows if s(r.get("strict_allowed")) != "NON"]
bad_segment = [r for r in live_rows if s(r.get("segment_roi")) != SEGMENT]
if bad_real:
    errors.append("LIVE_TICKET_REAL_NOT_NO_BET")
if bad_strict:
    errors.append("LIVE_STRICT_ALLOWED_NOT_NON")
if bad_segment:
    errors.append("LIVE_SEGMENT_MISMATCH")
summary_ticket_real = s(summary.get("ticket_real"))
summary_strict_allowed = s(summary.get("strict_allowed"))
summary_audit_status = s(summary.get("audit_status"))
if summary_rows:
    if summary_ticket_real != "NO_BET":
        errors.append("SUMMARY_TICKET_REAL_NOT_NO_BET")
    if summary_strict_allowed != "NON":
        errors.append("SUMMARY_STRICT_ALLOWED_NOT_NON")
    if summary_audit_status != "OK":
        errors.append("SUMMARY_AUDIT_STATUS_NOT_OK")
else:
    warnings.append("SUMMARY_EMPTY")
pending = sum(1 for r in live_rows if s(r.get("result_status")) == "PENDING")
resolved = sum(1 for r in live_rows if s(r.get("result_status")) == "RESOLVED")
ready_for_h5_paper = not errors
payload = {
    "generated_ts_utc": datetime.now(timezone.utc).isoformat(),
    "module": "CP3_TOP3_H5_LIVE_V0_1_4",
    "segment_roi": SEGMENT,
    "status": "OK" if ready_for_h5_paper else "KO",
    "ready_for_h5_paper": "OUI" if ready_for_h5_paper else "NON",
    "real_allowed": "NON",
    "ticket_real": "NO_BET",
    "strict_allowed": "NON",
    "action": "PAPER_LIVE_UNIQUEMENT",
    "retro": {
        "candidate_count": len(candidate_rows),
        "roi": RETRO_ROI,
        "roi_label": RETRO_ROI_LABEL,
        "status": "PAPER_CANDIDATE",
        "sample_note": "retro_audit_conservateur_h5",
    },
    "live": {
        "total_decisions": s(summary.get("total_decisions")) or str(len(live_rows)),
        "pending": s(summary.get("pending")) or str(pending),
        "resolved": s(summary.get("resolved")) or str(resolved),
        "roi_countable": s(summary.get("roi_countable")) or "0",
        "hits": s(summary.get("hits")) or "0",
        "stake_total": s(summary.get("stake_total")) or "0.00",
        "return_total": s(summary.get("return_total")) or "0.00",
        "gain_total": s(summary.get("gain_total")) or "0.00",
        "roi": s(summary.get("roi")) or "0.000000",
        "audit_status": summary_audit_status or "ND",
    },
    "guards": {
        "decision_before_race_required": "OUI",
        "result_known_at_decision_required": "NON",
        "historical_injection_allowed": "NON",
        "max_cp_reports_per_resolution": 3,
        "zeturf_2sur4_reports_allowed": "NON",
    },
    "files": {
        "live_log": str(LIVE_LOG.relative_to(ROOT)).replace("\\", "/"),
        "live_summary": str(LIVE_SUMMARY.relative_to(ROOT)).replace("\\", "/"),
        "paper_candidates": str(PAPER_CANDIDATES.relative_to(ROOT)).replace("\\", "/"),
    },
    "errors": errors,
    "warnings": warnings,
    "reason_codes": [
        "CP3_TOP3_H5_PAPER_READY" if ready_for_h5_paper else "CP3_TOP3_H5_PAPER_BLOCKED",
        "REAL_CLOSED",
        "STRICT_ALLOWED_NON",
        "SEGMENT_SEPARATED",
    ],
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print("")
print("EXPORT CP3 TOP3 H5 LIVE DASHBOARD V0_1_4")
print("=======================================")
print(f"Output : {OUT}")
print(f"Status : {payload['status']}")
print(f"Ready for H5 paper : {payload['ready_for_h5_paper']}")
print(f"Ticket réel : {payload['ticket_real']}")
print(f"Strict allowed : {payload['strict_allowed']}")
print(f"Live decisions : {payload['live']['total_decisions']}")
print(f"ROI live : {payload['live']['roi']}")
print(f"Errors : {len(errors)}")
if errors:
    for err in errors:
        print(f"- {err}")
    raise SystemExit(1)
print("Statut : OK")

