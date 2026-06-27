import csv
import re
import unicodedata
from pathlib import Path
ROOT = Path(".").resolve()
WATCHLIST = ROOT / "data" / "output" / "study_watchlist_v014.csv"
OUT = ROOT / "data" / "output" / "study_watchlist_v014_result_match.csv"
SEARCH_ROOTS = [
    ROOT / "data" / "results_pdf",
    ROOT / "data" / "output" / "cloud_traces_pre_20260627" / "gcs",
    ROOT / "data" / "output" / "cloud_traces_pre_20260627",
    ROOT / "data" / "output",
    ROOT / "data",
]
def s(v):
    return "" if v is None else str(v).strip()
def strip_accents(txt):
    txt = s(txt)
    txt = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in txt if not unicodedata.combining(c))
def norm(txt):
    txt = strip_accents(txt).lower()
    txt = txt.replace("&", " ")
    txt = txt.replace("_", " ")
    txt = txt.replace("-", " ")
    txt = re.sub(r"\bpronostics\b", " ", txt)
    txt = re.sub(r"\bcotes\b", " ", txt)
    txt = re.sub(r"\bresultats\b", " ", txt)
    txt = re.sub(r"\brapports\b", " ", txt)
    txt = re.sub(r"\bpdf\b", " ", txt)
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()
def raw_norm(txt):
    txt = strip_accents(txt).lower()
    txt = txt.replace("&", " ")
    txt = txt.replace("_", " ")
    txt = txt.replace("-", " ")
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()
def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))
def is_result_pdf(path):
    if path.suffix.lower() != ".pdf":
        return False
    raw = raw_norm(path.name)
    has_result = "resultat" in raw or "resultats" in raw
    has_report = "rapport" in raw or "rapports" in raw
    return has_result and has_report
def collect_result_pdfs():
    pdfs = []
    seen = set()
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*.pdf"):
            if not is_result_pdf(p):
                continue
            key = str(p.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            pdfs.append(p)
    return pdfs
watch_rows = read_csv(WATCHLIST)
pdfs = collect_result_pdfs()
pdf_index = []
for p in pdfs:
    pdf_index.append({
        "path": p,
        "key": norm(p.stem),
        "raw": raw_norm(p.stem),
    })
out_rows = []
for r in watch_rows:
    uid = s(r.get("course_uid"))
    key = norm(uid)
    found = None
    for item in pdf_index:
        pk = item["key"]
        if key == pk or key in pk or pk in key:
            found = item["path"]
            break
    out = dict(r)
    out["result_file_found"] = "OUI" if found else "NON"
    out["result_file"] = str(found.relative_to(ROOT)) if found else "ND"
    out_rows.append(out)
fieldnames = list(out_rows[0].keys()) if out_rows else []
with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(out_rows)
print("")
print("MATCH RESULTATS STUDY WATCHLIST V0_1_4")
print("=====================================")
print(f"Courses watchlist : {len(watch_rows)}")
print(f"PDF resultats detectes : {len(pdfs)}")
print(f"Resultats matches : {sum(1 for r in out_rows if r['result_file_found'] == 'OUI')}")
print(f"Resultats manquants : {sum(1 for r in out_rows if r['result_file_found'] != 'OUI')}")
print(f"Fichier : {OUT}")
