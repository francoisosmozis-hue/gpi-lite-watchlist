import csv
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(".").resolve()
OUT = ROOT / "data" / "output" / "paper_cp3_top3_h5_live_log.csv"
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
OUT.parent.mkdir(parents=True, exist_ok=True)
if OUT.exists():
    with OUT.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    print("")
    print("PAPER CP3 TOP3 H5 LIVE LOG V0_1_4")
    print("=================================")
    print(f"Fichier deja existant : {OUT}")
    print(f"Lignes existantes : {len(rows)}")
    print("Action : aucune modification")
else:
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
    print("")
    print("PAPER CP3 TOP3 H5 LIVE LOG V0_1_4")
    print("=================================")
    print(f"Fichier cree : {OUT}")
    print("Lignes live : 0")
    print("Ticket reel : NO_BET")
    print("Statut : OK")
