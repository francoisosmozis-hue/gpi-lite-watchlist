import csv
import sys
from pathlib import Path
ROOT = Path(".").resolve()
MATCH = ROOT / "data" / "output" / "study_watchlist_v014_result_match.csv"
OUT_DIR = ROOT / "data" / "output" / "study_watchlist_v014_result_texts"
OUT_DIR.mkdir(parents=True, exist_ok=True)
try:
    from pypdf import PdfReader
except Exception:
    print("ERREUR: module pypdf absent. Lance : pip install pypdf")
    sys.exit(1)
def s(v):
    return "" if v is None else str(v).strip()
def safe_name(txt):
    out = []
    for c in s(txt):
        if c.isalnum() or c in " -_.":
            out.append(c)
        else:
            out.append("_")
    return "".join(out)[:180] + ".txt"
with MATCH.open("r", encoding="utf-8-sig", newline="") as fh:
    rows = list(csv.DictReader(fh))
done = 0
missing = 0
failed = 0
for r in rows:
    if s(r.get("result_file_found")) != "OUI":
        continue
    uid = s(r.get("course_uid"))
    rel = s(r.get("result_file"))
    pdf_path = ROOT / rel
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
    out_path = OUT_DIR / safe_name(uid)
    out_path.write_text(text, encoding="utf-8")
    done += 1
print("")
print("EXTRACTION TEXTES RESULTATS STUDY V0_1_4")
print("=======================================")
print(f"PDF resultats matchés : {sum(1 for r in rows if s(r.get('result_file_found')) == 'OUI')}")
print(f"Textes extraits : {done}")
print(f"PDF introuvables localement : {missing}")
print(f"Echecs extraction : {failed}")
print(f"Dossier : {OUT_DIR}")
