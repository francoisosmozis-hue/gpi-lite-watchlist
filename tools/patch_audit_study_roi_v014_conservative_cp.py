from pathlib import Path
path = Path("tools/audit_study_roi_v014.py")
text = path.read_text(encoding="utf-8")
start = text.index("def parse_reports(text, arrival_top3):")
end = text.index("def eval_sp(selected, arrival_top3, simple_place):")
new_func = r'''def parse_reports(text, arrival_top3):
    simple_place = {}
    cp_place = {}
    section = report_section_lines(text)
    phase = "SIMPLE"
    pair_records = []
    triple_seen = False
    for line in section:
        vals = dec_values(line)
        if not vals:
            continue
        is_triple = re.match(r"^\s*\d{1,2}\s*[-–]\s*\d{1,2}\s*[-–]\s*\d{1,2}\b", line)
        if is_triple:
            triple_seen = True
            phase = "IGNORE_AFTER_TRIO"
            continue
        if phase == "IGNORE_AFTER_TRIO":
            continue
        pair = re.match(r"^\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\b", line)
        if pair:
            phase = "CP"
            a, b = pair.group(1), pair.group(2)
            key = pair_key(a, b)
            pair_records.append((key, vals[-1], line))
            continue
        single = re.match(r"^\s*(\d{1,2})\b", line)
        if single and phase == "SIMPLE":
            num = single.group(1)
            if num in arrival_top3 and num not in simple_place:
                simple_place[num] = vals[-1]
    # Sécurité CP :
    # Un bloc Jumelé/Couplé Placé exploitable doit être court.
    # Si plus de 3 paires sont détectées, risque fort de contamination ZE 2 sur 4.
    # Dans ce cas, on met CP en ND au lieu de gonfler le ROI.
    if len(pair_records) > 3:
        return simple_place, {}
    for key, value, line in pair_records:
        cp_place[key] = value
    return simple_place, cp_place
'''
patched = text[:start] + new_func + text[end:]
path.write_text(patched, encoding="utf-8")
print("parse_reports patched conservative CP")
