import csv
import json
import re
from pathlib import Path

ROOT = Path(".").resolve()
BASE = ROOT / "data" / "output" / "cloud_traces_pre_20260627" / "gcs_analysis"
OUT = ROOT / "data" / "output" / "cloud_traces_pre_20260627" / "cloud_trace_inventory_summary.csv"

PATTERNS = {
    "course_9h_clean": "course_9h_clean.csv",
    "course_h5_clean": "course_h5_clean.csv",
    "h5_update": "h5_update.csv",
    "decision_engine_outputs": "decision_engine_outputs.csv",
    "decision_lite": "decision_lite.csv",
    "analyse_course_resume": "analyse_course_resume.txt",
    "prompt_gpt": "prompt_gpt.txt",
    "config_backtest": "backtest_cp_sec_vs_cp3.csv",
    "config_proxy": "proxy_error_by_segment.csv",
}

KEYS = [
    "course_uid",
    "decision_id",
    "input_hash",
    "engine_version",
    "ruleset_version",
    "qualite",
    "verdict",
    "structure_retenue",
    "cp_sec_candidate",
    "selection_CP_SEC",
    "paper_selection",
    "paper_candidate_reason",
    "kill_switch_codes",
    "statut_marche",
    "volatilite_status",
    "volatilite_top6_max_pct",
    "ticket_paper",
    "ticket_real",
    "segment_roi",
]

def date_from_name(name: str) -> str:
    m = re.search(r"(\d{2})_(\d{2})_(\d{4})", name)
    if not m:
        return "ND"
    dd, mm, yyyy = m.groups()
    return f"{yyyy}-{mm}-{dd}"

def course_key_from_name(name: str) -> str:
    for suffix in PATTERNS.values():
        if name.endswith("_" + suffix):
            return name[: -(len(suffix) + 1)]
    return name

def detect_type(name: str) -> str:
    for label, suffix in PATTERNS.items():
        if name.endswith(suffix):
            return label
    return "other"

def inspect_csv(path: Path):
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
            cols = reader.fieldnames or []
    except Exception as e:
        return {
            "rows": "ERR",
            "columns": "ERR",
            "found_keys": "",
            "error": str(e),
        }

    found = [k for k in KEYS if k in cols]

    return {
        "rows": len(rows),
        "columns": len(cols),
        "found_keys": "|".join(found),
        "error": "",
    }

def inspect_text(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return {
            "rows": "ND",
            "columns": "ND",
            "found_keys": "",
            "error": str(e),
        }

    found = [k for k in KEYS if k in text]

    return {
        "rows": "ND",
        "columns": "ND",
        "found_keys": "|".join(found),
        "error": "",
    }

records = []

for path in sorted(BASE.rglob("*")):
    if not path.is_file():
        continue

    typ = detect_type(path.name)
    date = date_from_name(path.name)
    course_key = course_key_from_name(path.name)

    if path.suffix.lower() == ".csv":
        meta = inspect_csv(path)
    elif path.suffix.lower() in [".txt", ".json"]:
        meta = inspect_text(path)
    else:
        meta = {
            "rows": "ND",
            "columns": "ND",
            "found_keys": "",
            "error": "",
        }

    records.append({
        "date": date,
        "course_key": course_key,
        "file_type": typ,
        "file_name": path.name,
        "relative_path": str(path.relative_to(ROOT)),
        "size_bytes": path.stat().st_size,
        "rows": meta["rows"],
        "columns": meta["columns"],
        "found_keys": meta["found_keys"],
        "error": meta["error"],
    })

OUT.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
    fieldnames = [
        "date",
        "course_key",
        "file_type",
        "file_name",
        "relative_path",
        "size_bytes",
        "rows",
        "columns",
        "found_keys",
        "error",
    ]
    writer = csv.DictWriter(fh, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(records)

print("")
print("INVENTAIRE TRACES CLOUD")
print("=======================")
print(f"Fichiers inspectés : {len(records)}")
print(f"decision_engine_outputs : {sum(1 for r in records if r['file_type'] == 'decision_engine_outputs')}")
print(f"decision_lite : {sum(1 for r in records if r['file_type'] == 'decision_lite')}")
print(f"course_h5_clean : {sum(1 for r in records if r['file_type'] == 'course_h5_clean')}")
print(f"course_9h_clean : {sum(1 for r in records if r['file_type'] == 'course_9h_clean')}")
print(f"Fichier : {OUT}")
