import subprocess
import sys
from pathlib import Path
ROOT = Path(".").resolve()
SCRIPTS = [
    ROOT / "tools" / "init_paper_cp3_top3_h5_live_log_v014.py",
    ROOT / "tools" / "append_cp3_top3_h5_live_decision_v014.py",
    ROOT / "tools" / "validate_paper_cp3_top3_h5_live_log_v014.py",
    ROOT / "tools" / "resolve_cp3_top3_h5_live_result_v014.py",
    ROOT / "tools" / "summarize_paper_cp3_top3_h5_live_roi_v014.py",
]
LOG = ROOT / "data" / "output" / "paper_cp3_top3_h5_live_log.csv"
SUMMARY = ROOT / "data" / "output" / "paper_cp3_top3_h5_live_summary.csv"
def run(cmd):
    print("")
    print("RUN:", " ".join(str(x) for x in cmd))
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
print("")
print("SMOKE TEST CP3 TOP3 H5 LIVE V0_1_4")
print("==================================")
missing = [p for p in SCRIPTS if not p.exists()]
if missing:
    print("FICHIERS MANQUANTS")
    for p in missing:
        print(f"- {p}")
    raise SystemExit(1)
print("Scripts presents : OK")
for script in SCRIPTS:
    run([sys.executable, "-m", "py_compile", str(script)])
print("Compilation : OK")
if not LOG.exists():
    run([sys.executable, str(ROOT / "tools" / "init_paper_cp3_top3_h5_live_log_v014.py")])
if not LOG.exists():
    raise SystemExit("REFUS : journal live introuvable apres init")
run([sys.executable, str(ROOT / "tools" / "validate_paper_cp3_top3_h5_live_log_v014.py")])
run([sys.executable, str(ROOT / "tools" / "summarize_paper_cp3_top3_h5_live_roi_v014.py")])
if not SUMMARY.exists():
    raise SystemExit("REFUS : summary live introuvable apres summarize")
content = SUMMARY.read_text(encoding="utf-8-sig")
required_markers = [
    "CP3_TOP3_ETUDE_H5_PAPER",
    "NO_BET",
    "NON",
    "OK",
]
missing_markers = [m for m in required_markers if m not in content]
if missing_markers:
    print("MARQUEURS MANQUANTS DANS SUMMARY")
    for m in missing_markers:
        print(f"- {m}")
    raise SystemExit(1)
print("")
print("Smoke test : OK")
print(f"Journal : {LOG}")
print(f"Summary : {SUMMARY}")
print("Ticket reel : NO_BET")
print("Strict allowed : NON")
print("Statut : OK")
