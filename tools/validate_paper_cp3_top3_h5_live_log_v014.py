import csv
from pathlib import Path
ROOT = Path(".").resolve()
LOG = ROOT / "data" / "output" / "paper_cp3_top3_h5_live_log.csv"
REQUIRED_SEGMENT = "CP3_TOP3_ETUDE_H5_PAPER"
def s(v):
    return "" if v is None else str(v).strip()
if not LOG.exists():
    raise FileNotFoundError(f"Fichier introuvable : {LOG}")
with LOG.open("r", encoding="utf-8-sig", newline="") as fh:
    rows = list(csv.DictReader(fh))
errors = []
roi_countable = 0
for i, row in enumerate(rows, start=2):
    course_uid = s(row.get("course_uid"))
    if s(row.get("segment_roi")) != REQUIRED_SEGMENT:
        errors.append(f"Ligne {i} : segment_roi invalide : {course_uid}")
    if s(row.get("ticket_real")) != "NO_BET":
        errors.append(f"Ligne {i} : ticket_real different de NO_BET : {course_uid}")
    if s(row.get("strict_allowed")) != "NON":
        errors.append(f"Ligne {i} : strict_allowed different de NON : {course_uid}")
    if s(row.get("result_known_at_decision")) != "NON":
        errors.append(f"Ligne {i} : result_known_at_decision different de NON : {course_uid}")
    if s(row.get("decision_before_race")) == "OUI":
        roi_countable += 1
    if s(row.get("decision_before_race")) not in {"OUI", "NON", "A_VALIDER_LIVE"}:
        errors.append(f"Ligne {i} : decision_before_race invalide : {course_uid}")
print("")
print("VALIDATION PAPER CP3 TOP3 H5 LIVE LOG V0_1_4")
print("============================================")
print(f"Lignes totales : {len(rows)}")
print(f"Lignes comptables ROI live : {roi_countable}")
print(f"Erreurs : {len(errors)}")
if errors:
    print("")
    print("ERREURS")
    for err in errors[:50]:
        print(f"- {err}")
    raise SystemExit(1)
print("Statut : OK")
