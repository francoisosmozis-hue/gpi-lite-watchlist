from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.prompt_integrity import validate_prompt_file

course_uid = "26_05_2026 - VINCENNES - Prix Kalmia_ Pronostics & Cotes"
prompt_path = Path("data/output") / f"{course_uid}_prompt_gpt.txt"

ok, errors = validate_prompt_file(prompt_path)
print("prompt_ok:", ok)
print("errors:", errors)
