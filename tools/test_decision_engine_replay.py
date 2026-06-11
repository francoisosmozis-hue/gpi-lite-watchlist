from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.decision_engine_replay import replay_decision_engine_outputs

course_uid = "26_05_2026 - VINCENNES - Prix Kalmia_ Pronostics & Cotes"

paths = replay_decision_engine_outputs(course_uid)

for p in paths:
    print(p)
