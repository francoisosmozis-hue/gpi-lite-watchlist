from pathlib import Path
path = Path("tools/audit_study_roi_v014.py")
text = path.read_text(encoding="utf-8")
start = text.index("def parse_reports(text, arrival_top3):")
end = text.index("def eval_sp(selected, arrival_top3, simple_place):")
new_func = r'''def parse_reports(text, arrival_top3):
    simple_place = {}
    cp_place = {}
    section = report_section_lines(text)
    # Phases attendues dans le bloc ZEturf :
    # 1. lignes simples : "13 8,20 € 3,10 €"
    # 2. lignes couplés/jumelés : "13-10 28,80 € 12,20 €"
    # 3. ligne trio : "13-10-9 35,10 €"
    # 4. lignes ZE 2 sur 4 : à ignorer pour SP/CP
    phase = "SIMPLE"
    for line in section:
        vals = dec_values(line)
        if not vals:
            continue
        is_triple = re.match(r"^\s*\d{1,2}\s*[-–]\s*\d{1,2}\s*[-–]\s*\d{1,2}\b", line)
        if is_triple:
            phase = "IGNORE_AFTER_TRIO"
            continue
        if phase == "IGNORE_AFTER_TRIO":
            continue
        pair = re.match(r"^\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\b", line)
        if pair:
            phase = "CP"
            a, b = pair.group(1), pair.group(2)
            key = pair_key(a, b)
            # Ligne couplé/jumelé :
            # - 2 valeurs : gagnant + placé, on garde le placé
            # - 1 valeur : placé seul
            cp_place[key] = vals[-1]
            continue
        single = re.match(r"^\s*(\d{1,2})\b", line)
        if single and phase == "SIMPLE":
            num = single.group(1)
            # Simple placé uniquement.
            # Si la ligne contient gagnant + placé, le placé est la dernière valeur.
            if num in arrival_top3 and num not in simple_place:
                simple_place[num] = vals[-1]
    return simple_place, cp_place
'''
patched = text[:start] + new_func + text[end:]
path.write_text(patched, encoding="utf-8")
print("parse_reports patched")
