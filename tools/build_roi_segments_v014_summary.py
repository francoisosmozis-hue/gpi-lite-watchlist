import csv
from pathlib import Path
ROOT = Path(".").resolve()
WATCHLIST = ROOT / "data" / "output" / "study_watchlist_v014.csv"
AUDIT_LOW = ROOT / "data" / "output" / "cloud_traces_pre_20260627" / "simulation_low_17_audit_sportif.csv"
OUT = ROOT / "data" / "output" / "roi_segments_v014_summary.csv"
segments = {}
def s(v):
    return "" if v is None else str(v).strip()
def add_segment(name, stake, gain, status):
    if name not in segments:
        segments[name] = {
            "segment_roi": name,
            "decisions": 0,
            "stake_total": 0.0,
            "gain_total": 0.0,
            "roi": "ND",
            "status": status,
        }
    segments[name]["decisions"] += 1
    if stake is not None and gain is not None:
        segments[name]["stake_total"] += float(stake)
        segments[name]["gain_total"] += float(gain)
def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))
watch_rows = read_csv(WATCHLIST)
for r in watch_rows:
    source_type = s(r.get("source_type")) or "ND"
    add_segment("STUDY_TOP3", None, None, "ROI_ND_REPORTS_NOT_JOINED")
    add_segment(f"STUDY_TOP3_{source_type}", None, None, "ROI_ND_REPORTS_NOT_JOINED")
    outsider = s(r.get("outsider_etude"))
    if outsider and outsider != "ND":
        add_segment("STUDY_OUTSIDER", None, None, "ROI_ND_REPORTS_NOT_JOINED")
        add_segment(f"STUDY_OUTSIDER_{source_type}", None, None, "ROI_ND_REPORTS_NOT_JOINED")
for r in read_csv(AUDIT_LOW):
    gain_raw = s(r.get("gain_net_simulation_1e"))
    try:
        gain_net = float(gain_raw.replace(",", "."))
        retour = gain_net + 1.0
        add_segment("SIMULATION_LOW_RETRO", 1.0, retour, "ROI_PARTIAL_IF_REPORT_FOUND")
    except Exception:
        add_segment("SIMULATION_LOW_RETRO", None, None, "ROI_ND_OR_SPORT_ONLY")
out_rows = []
for seg in segments.values():
    if seg["stake_total"] > 0:
        seg["roi"] = (seg["gain_total"] - seg["stake_total"]) / seg["stake_total"]
    else:
        seg["roi"] = "ND"
    out_rows.append(seg)
out_rows = sorted(out_rows, key=lambda r: r["segment_roi"])
with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
    fieldnames = ["segment_roi", "decisions", "stake_total", "gain_total", "roi", "status"]
    writer = csv.DictWriter(fh, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(out_rows)
print("")
print("ROI SEGMENTS V0_1_4")
print("===================")
for r in out_rows:
    print(f"{r['segment_roi']} : decisions={r['decisions']} roi={r['roi']} status={r['status']}")
print(f"Fichier : {OUT}")
