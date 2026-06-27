import csv
import itertools
import re
from pathlib import Path
ROOT = Path(".").resolve()
MATCH = ROOT / "data" / "output" / "study_watchlist_v014_result_match.csv"
TEXT_DIR = ROOT / "data" / "output" / "study_watchlist_v014_result_texts"
DETAIL_OUT = ROOT / "data" / "output" / "study_roi_v014_detail.csv"
SUMMARY_OUT = ROOT / "data" / "output" / "study_roi_v014_summary.csv"
def s(v):
    return "" if v is None else str(v).strip()
def safe_name(txt):
    out = []
    for c in s(txt):
        if c.isalnum() or c in " -_.":
            out.append(c)
        else:
            out.append("_")
    return "".join(out)[:180] + ".txt"
def parse_nums(txt):
    return re.findall(r"\d+", s(txt))
def pair_key(a, b):
    aa, bb = str(a), str(b)
    try:
        nums = sorted([int(aa), int(bb)])
        return f"{nums[0]}-{nums[1]}"
    except Exception:
        return "-".join(sorted([aa, bb]))
def dec_values(line):
    vals = []
    for raw in re.findall(r"\d+,\d+", line):
        try:
            vals.append(float(raw.replace(",", ".")))
        except Exception:
            pass
    return vals
def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))
def extract_arrival(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    joined = " ".join(lines)
    patterns = [
        r"Arriv\S*(?:\s+o\S*)?\s*[:\-]?\s*([0-9]{1,2}(?:\s*[-–]\s*[0-9]{1,2}){2,8})",
        r"Arrivee(?:\s+officielle)?\s*[:\-]?\s*([0-9]{1,2}(?:\s*[-–]\s*[0-9]{1,2}){2,8})",
        r"([0-9]{1,2}\s*[-–]\s*[0-9]{1,2}\s*[-–]\s*[0-9]{1,2}(?:\s*[-–]\s*[0-9]{1,2}){0,5})",
    ]
    for p in patterns:
        m = re.search(p, joined, flags=re.IGNORECASE)
        if m:
            raw = m.group(1)
            nums = re.findall(r"\d+", raw)
            if len(nums) >= 3:
                return raw, nums[:5], nums[:3]
    return "ND", [], []
def report_section_lines(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    start = None
    for i, line in enumerate(lines):
        low = line.lower()
        if "favoris" in low or ("pr" in low and "sence" in low):
            start = i + 1
            break
    if start is None:
        for i, line in enumerate(lines):
            if "arriv" in line.lower():
                start = i + 1
                break
    if start is None:
        start = 0
    end = len(lines)
    for j in range(start, len(lines)):
        up = lines[j].upper()
        if "RAPPORTS" in up:
            end = j
            break
    return lines[start:end]
def parse_reports(text, arrival_top3):
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
def eval_sp(selected, arrival_top3, simple_place):
    selected = s(selected)
    if not selected or selected == "ND":
        return 0.0, None, "ND_SELECTION", "ND"
    if not arrival_top3:
        return 1.0, None, "ND_ARRIVAL", "ND"
    hit = selected in arrival_top3
    if hit:
        if selected in simple_place:
            return 1.0, simple_place[selected], "HIT", "OUI"
        return 1.0, None, "HIT_REPORT_ND", "OUI"
    return 1.0, 0.0, "LOST", "NON"
def eval_cp(pair_nums, arrival_top3, cp_place):
    if len(pair_nums) < 2:
        return 0.0, None, "ND_SELECTION", "ND"
    if not arrival_top3:
        return 1.0, None, "ND_ARRIVAL", "ND"
    a, b = pair_nums[0], pair_nums[1]
    hit = a in arrival_top3 and b in arrival_top3
    if hit:
        key = pair_key(a, b)
        if key in cp_place:
            return 1.0, cp_place[key], "HIT", "OUI"
        return 1.0, None, "HIT_REPORT_ND", "OUI"
    return 1.0, 0.0, "LOST", "NON"
def eval_cp3(top3_nums, arrival_top3, cp_place):
    if len(top3_nums) < 3:
        return 0.0, None, "ND_SELECTION", "ND", "ND"
    if not arrival_top3:
        return 3.0, None, "ND_ARRIVAL", "ND", "ND"
    total_return = 0.0
    hit_pairs = []
    missing_reports = []
    for a, b in itertools.combinations(top3_nums[:3], 2):
        if a in arrival_top3 and b in arrival_top3:
            key = pair_key(a, b)
            hit_pairs.append(key)
            if key in cp_place:
                total_return += cp_place[key]
            else:
                missing_reports.append(key)
    if missing_reports:
        return 3.0, None, "HIT_REPORT_ND", "OUI", ",".join(hit_pairs)
    if hit_pairs:
        return 3.0, total_return, "HIT", "OUI", ",".join(hit_pairs)
    return 3.0, 0.0, "LOST", "NON", ""
segments = {}
def add_segment(name, stake, ret, hit, status):
    if name not in segments:
        segments[name] = {
            "segment_roi": name,
            "decisions": 0,
            "resolved": 0,
            "nd": 0,
            "hits": 0,
            "stake_total": 0.0,
            "return_total": 0.0,
            "gain_net": 0.0,
            "roi": "ND",
        }
    seg = segments[name]
    seg["decisions"] += 1
    if ret is None:
        seg["nd"] += 1
        return
    seg["resolved"] += 1
    seg["stake_total"] += float(stake)
    seg["return_total"] += float(ret)
    seg["gain_net"] += float(ret) - float(stake)
    if hit == "OUI":
        seg["hits"] += 1
def add_both(base, source_type, stake, ret, hit, status):
    add_segment(base, stake, ret, hit, status)
    add_segment(f"{base}_{source_type}", stake, ret, hit, status)
rows = read_csv(MATCH)
detail_rows = []
for r in rows:
    uid = s(r.get("course_uid"))
    source_type = s(r.get("source_type")) or "ND"
    if s(r.get("result_file_found")) != "OUI":
        continue
    text_path = TEXT_DIR / safe_name(uid)
    if not text_path.exists():
        continue
    text = text_path.read_text(encoding="utf-8", errors="ignore")
    arrival_raw, arrival_nums, arrival_top3 = extract_arrival(text)
    simple_place, cp_place = parse_reports(text, arrival_top3)
    top3_nums = parse_nums(r.get("top3_etude"))[:3]
    top1 = top3_nums[0] if len(top3_nums) >= 1 else "ND"
    top2_pair = top3_nums[:2]
    outsider = s(r.get("outsider_etude"))
    sp_top1_stake, sp_top1_ret, sp_top1_status, sp_top1_hit = eval_sp(top1, arrival_top3, simple_place)
    sp_out_stake, sp_out_ret, sp_out_status, sp_out_hit = eval_sp(outsider, arrival_top3, simple_place)
    cp_stake, cp_ret, cp_status, cp_hit = eval_cp(top2_pair, arrival_top3, cp_place)
    cp3_stake, cp3_ret, cp3_status, cp3_hit, cp3_pairs = eval_cp3(top3_nums, arrival_top3, cp_place)
    add_both("SP_TOP1_ETUDE", source_type, sp_top1_stake, sp_top1_ret, sp_top1_hit, sp_top1_status)
    if outsider and outsider != "ND":
        add_both("SP_OUTSIDER_ETUDE", source_type, sp_out_stake, sp_out_ret, sp_out_hit, sp_out_status)
    add_both("CP_TOP2_ETUDE", source_type, cp_stake, cp_ret, cp_hit, cp_status)
    add_both("CP3_TOP3_ETUDE", source_type, cp3_stake, cp3_ret, cp3_hit, cp3_status)
    detail_rows.append({
        "course_uid": uid,
        "source_type": source_type,
        "arrival_raw": arrival_raw,
        "arrival_top3": "-".join(arrival_top3) if arrival_top3 else "ND",
        "top3_etude": s(r.get("top3_etude")),
        "outsider_etude": outsider,
        "simple_place_reports": ";".join(f"{k}:{v}" for k, v in sorted(simple_place.items())),
        "cp_place_reports": ";".join(f"{k}:{v}" for k, v in sorted(cp_place.items())),
        "sp_top1_selection": top1,
        "sp_top1_hit": sp_top1_hit,
        "sp_top1_status": sp_top1_status,
        "sp_top1_return": sp_top1_ret if sp_top1_ret is not None else "ND",
        "sp_outsider_selection": outsider,
        "sp_outsider_hit": sp_out_hit,
        "sp_outsider_status": sp_out_status,
        "sp_outsider_return": sp_out_ret if sp_out_ret is not None else "ND",
        "cp_top2_selection": "-".join(top2_pair) if len(top2_pair) >= 2 else "ND",
        "cp_top2_hit": cp_hit,
        "cp_top2_status": cp_status,
        "cp_top2_return": cp_ret if cp_ret is not None else "ND",
        "cp3_top3_selection": "-".join(top3_nums) if len(top3_nums) >= 3 else "ND",
        "cp3_hit_pairs": cp3_pairs,
        "cp3_status": cp3_status,
        "cp3_return": cp3_ret if cp3_ret is not None else "ND",
        "ticket_real": "NO_BET",
    })
for seg in segments.values():
    if seg["stake_total"] > 0:
        seg["roi"] = seg["gain_net"] / seg["stake_total"]
    else:
        seg["roi"] = "ND"
summary_rows = sorted(segments.values(), key=lambda x: x["segment_roi"])
DETAIL_OUT.parent.mkdir(parents=True, exist_ok=True)
detail_fields = list(detail_rows[0].keys()) if detail_rows else []
with DETAIL_OUT.open("w", encoding="utf-8-sig", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=detail_fields)
    writer.writeheader()
    writer.writerows(detail_rows)
summary_fields = [
    "segment_roi",
    "decisions",
    "resolved",
    "nd",
    "hits",
    "stake_total",
    "return_total",
    "gain_net",
    "roi",
]
with SUMMARY_OUT.open("w", encoding="utf-8-sig", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=summary_fields)
    writer.writeheader()
    writer.writerows(summary_rows)
print("")
print("AUDIT ROI STUDY V0_1_4")
print("======================")
print(f"Courses avec resultats textes : {len(detail_rows)}")
print(f"Fichier detail : {DETAIL_OUT}")
print(f"Fichier synthese : {SUMMARY_OUT}")
print("")
for r in summary_rows:
    print(
        f"{r['segment_roi']} : decisions={r['decisions']} "
        f"resolved={r['resolved']} nd={r['nd']} hits={r['hits']} roi={r['roi']}"
    )
