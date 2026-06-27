import csv
import sys
from pathlib import Path

ROOT = Path(".").resolve()
MATCH_FILE = ROOT / "data" / "output" / "cloud_traces_pre_20260627" / "simulation_low_17_result_match.csv"
OUT_DIR = ROOT / "data" / "output" / "cloud_traces_pre_20260627" / "simulation_low_17_result_texts"
OUT_DIR.mkdir(parents=True, exist_ok=True)

try:
    from pypdf import PdfReader
except Exception:
    print("ERREUR: module pypdf absent. Lance : pip install pypdf")
    sys.exit(1)

def safe_name(s):
    out = []
    for c in s:
        if c.isalnum() or c in " -_.":
            out.append(c)
        else:
            out.append("_")
    return "".join(out)[:180]

with MATCH_FILE.open("r", encoding="utf-8-sig", newline="") as fh:
    rows = list(csv.DictReader(fh))

done = 0
missing = 0
failed = 0

for r in rows:
    if r.get("result_file_found") != "OUI":
        continue

    pdf_path = ROOT / r.get("result_file", "")

    if not pdf_path.exists():
        missing += 1
        continue

    try:
        reader = PdfReader(str(pdf_path))
        text_parts = []
        for page in reader.pages:
            text_parts.append(page.extract_text() or "")
        text = "\n".join(text_parts)
    except Exception as e:
        failed += 1
        text = "EXTRACTION_ERROR: " + str(e)

    out_name = safe_name(r.get("course_uid", "course")) + ".txt"
    out_path = OUT_DIR / out_name
    out_path.write_text(text, encoding="utf-8")
    done += 1

print("")
print("EXTRACTION RESULTATS SIMULATION_LOW_17")
print("=====================================")
print(f"PDF extraits : {done}")
print(f"PDF introuvables localement : {missing}")
print(f"Echecs extraction : {failed}")
print(f"Dossier : {OUT_DIR}")
