import subprocess
import sys
from pathlib import Path
ROOT = Path(".").resolve()
STATUS_SCRIPT = ROOT / "tools" / "status_cp3_top3_h5_live_v014.py"
SMOKE_SCRIPT = ROOT / "tools" / "smoke_test_cp3_top3_h5_live_v014.py"
VALIDATOR_SCRIPT = ROOT / "tools" / "validate_paper_cp3_top3_h5_live_log_v014.py"
SUMMARIZER_SCRIPT = ROOT / "tools" / "summarize_paper_cp3_top3_h5_live_roi_v014.py"
REQUIRED = [
    STATUS_SCRIPT,
    SMOKE_SCRIPT,
    VALIDATOR_SCRIPT,
    SUMMARIZER_SCRIPT,
]
def run(label, cmd):
    print("")
    print(f"== {label} ==")
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        print(f"READINESS : KO sur {label}")
        raise SystemExit(result.returncode)
    return result.stdout
print("")
print("READY CHECK CP3 TOP3 H5 LIVE V0_1_4")
print("===================================")
missing = [p for p in REQUIRED if not p.exists()]
if missing:
    print("READINESS : KO")
    print("Fichiers manquants :")
    for p in missing:
        print(f"- {p}")
    raise SystemExit(1)
print("Fichiers requis : OK")
for script in REQUIRED:
    run(f"compile {script.name}", [sys.executable, "-m", "py_compile", str(script)])
run("validate live log", [sys.executable, str(VALIDATOR_SCRIPT)])
run("summarize live ROI", [sys.executable, str(SUMMARIZER_SCRIPT)])
status_output = run("status module", [sys.executable, str(STATUS_SCRIPT)])
smoke_output = run("smoke test", [sys.executable, str(SMOKE_SCRIPT)])
required_markers = [
    "STATUS : OK",
    "Ticket reel : NO_BET",
    "Strict allowed : NON",
]
for marker in required_markers:
    if marker not in status_output and marker not in smoke_output:
        print("READINESS : KO")
        print(f"Marqueur manquant : {marker}")
        raise SystemExit(1)
print("")
print("READINESS VERDICT")
print("=================")
print("READY_FOR_H5_PAPER : OUI")
print("SEGMENT : CP3_TOP3_ETUDE_H5_PAPER")
print("REAL_ALLOWED : NON")
print("TICKET_REAL : NO_BET")
print("STRICT_ALLOWED : NON")
print("ACTION : paper live uniquement")
