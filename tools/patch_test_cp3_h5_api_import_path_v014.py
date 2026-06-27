from pathlib import Path
path = Path("tools/test_cp3_top3_h5_api_integration_v014.py")
text = path.read_text(encoding="utf-8-sig")
old = """import sys
import api_main
"""
new = """import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import api_main
"""
if old not in text:
    raise SystemExit("PATCH_REFUS: bloc import initial introuvable")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("PATCH OK : sys.path racine ajouté au test API")
