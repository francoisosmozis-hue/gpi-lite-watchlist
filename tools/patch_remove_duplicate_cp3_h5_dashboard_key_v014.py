from pathlib import Path
path = Path("api_main.py")
text = path.read_text(encoding="utf-8-sig")
duplicate = '''            "cp3_top3_h5_live_status": _cp3_top3_h5_live_dashboard_summary(),
            "cp3_top3_h5_live_status": _cp3_top3_h5_live_dashboard_summary(),'''
single = '''            "cp3_top3_h5_live_status": _cp3_top3_h5_live_dashboard_summary(),'''
if duplicate not in text:
    raise SystemExit("PATCH_REFUS: doublon exact introuvable")
text = text.replace(duplicate, single, 1)
path.write_text(text, encoding="utf-8")
print("PATCH OK : doublon cp3_top3_h5_live_status supprimé")
