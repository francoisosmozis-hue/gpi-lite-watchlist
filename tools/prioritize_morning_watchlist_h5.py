from pathlib import Path
import pandas as pd
import re
import unicodedata

OUTPUT_DIR = Path("data/output")
WATCHLIST_PATH = OUTPUT_DIR / "daily_watchlist.csv"

def _num(value):
    try:
        if value is None:
            return None
        text = str(value).strip().replace(",", ".")
        if not text or text.upper() in {"ND", "NAN", "NONE"}:
            return None
        return float(text)
    except Exception:
        return None

def _parse_pair(value):
    if value is None:
        return []
    text = str(value).strip()
    if not text or text.upper() in {"ND", "NO_BET"}:
        return []
    for sep in ["-", "/", ",", ";", "|"]:
        text = text.replace(sep, " ")
    out = []
    for part in text.split():
        try:
            out.append(int(part))
        except Exception:
            pass
    return out

def _norm(text):
    text = str(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text

def _find_course_9h_file(course_uid):
    exact = OUTPUT_DIR / f"{course_uid}_course_9h_clean.csv"
    if exact.exists():
        return exact

    target = _norm(f"{course_uid}_course_9h_clean.csv")

    candidates = list(OUTPUT_DIR.glob("*_course_9h_clean.csv"))

    for path in candidates:
        if _norm(path.name) == target:
            return path

    course_norm = _norm(course_uid)

    for path in candidates:
        name_norm = _norm(path.name)
        if course_norm in name_norm:
            return path

    return None

def _sg_map_from_course_9h_file(course_uid):
    file_path = _find_course_9h_file(course_uid)

    if file_path is None:
        return {}, "FICHIER_9H_ABSENT"

    try:
        df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
    except Exception:
        return {}, "FICHIER_9H_ILLISIBLE"

    if df.empty:
        return {}, "FICHIER_9H_VIDE"

    num_col = None
    sg_col = None

    for col in df.columns:
        low = str(col).strip().lower()
        if low in {"numero", "num", "n", "no", "n°", "cheval_num", "numero_cheval"}:
            num_col = col
            break

    for col in df.columns:
        low = str(col).strip().lower()
        if low in {"sg", "cote_sg", "simple_gagnant", "sg_9h", "cote", "cotes"}:
            sg_col = col
            break

    if num_col is None:
        for col in df.columns:
            low = str(col).strip().lower()
            if "num" in low or low == "n" or "n°" in low:
                num_col = col
                break

    if sg_col is None:
        for col in df.columns:
            low = str(col).strip().lower()
            if "sg" in low or "gagnant" in low or "cote" in low:
                sg_col = col
                break

    if num_col is None or sg_col is None:
        return {}, "COLONNES_9H_ABSENTES"

    sg_map = {}

    for _, row in df.iterrows():
        try:
            num = int(str(row.get(num_col, "")).strip())
        except Exception:
            continue

        sg = _num(row.get(sg_col, ""))
        if sg is not None:
            sg_map[num] = sg

    if not sg_map:
        return {}, "SG_9H_ABSENTES"

    return sg_map, "OK"

def _compute_cp_sec_sum(row):
    cp = (
        row.get("cp_sec_candidat", "")
        or row.get("CP_SEC_CANDIDAT", "")
        or row.get("selection_cp_sec", "")
        or row.get("selection_CP_SEC", "")
    )

    nums = _parse_pair(cp)

    if len(nums) < 2:
        return "ND", "ND_CP_ABSENT", "aucun CP_SEC candidat"

    course_uid = str(row.get("course_uid", "")).strip()

    sg_map, source_status = _sg_map_from_course_9h_file(course_uid)

    if not sg_map:
        return "ND", "ND_SG_ABSENTE", source_status

    a, b = nums[:2]

    if a not in sg_map or b not in sg_map:
        return "ND", "ND_SG_ABSENTE", f"SG absente pour {a}-{b}"

    pair_sum = round(sg_map[a] + sg_map[b], 2)
    status = "OK" if pair_sum > 6 else "KO"

    return pair_sum, status, f"SG {a}={sg_map[a]}, SG {b}={sg_map[b]}"

def _priority_calc(row):
    priorite_morning = str(row.get("priorite_h5", "")).upper().strip()
    qualite = str(row.get("qualite_matin", row.get("qualite", "ND"))).upper()
    cp_status = str(row.get("cp_sec_sg_filter_status_9h", "ND")).upper()
    cp_sum = _num(row.get("cp_sec_sg_sum_9h", "ND"))
    favori_fragile = str(row.get("favori_fragile", "")).lower()
    kill_switchs = str(row.get("kill_switchs_matin", "")).lower()
    motifs = str(row.get("motifs_filtrage", "")).lower()
    structure = str(row.get("structure_max", "")).upper()

    reasons = []

    qualite_ok = qualite in {"A", "B", "B+"}

    if priorite_morning:
        reasons.append(f"morning_{priorite_morning}")

    if qualite_ok:
        reasons.append("qualite_ok")
    else:
        reasons.append("qualite_faible")

    if cp_status == "OK":
        reasons.append("cp_sec_sg_ok")
    elif cp_status == "KO":
        reasons.append("cp_sec_sg_ko")
    else:
        reasons.append("cp_sec_sg_nd")

    if cp_sum is not None:
        reasons.append(f"cp_sec_sg_sum_{cp_sum}")

    blockers = 0

    if favori_fragile in {"oui", "true", "1"}:
        blockers += 1
        reasons.append("favori_fragile")

    if kill_switchs and kill_switchs not in {"aucun", "none", "nd"}:
        blockers += 2
        reasons.append("kill_switch_matin")

    if "bloquant" in motifs and "aucun" not in motifs:
        blockers += 2
        reasons.append("motif_bloquant")

    if "CP_SEC_ETUDE" in structure:
        reasons.append("structure_cp_sec_etude")

    if cp_status != "OK":
        return 4, "NO_FOCUS_9H", ", ".join(reasons)

    if blockers >= 2:
        return 4, "NO_FOCUS_9H", ", ".join(reasons)

    if not qualite_ok:
        return 3, "H5_SEULEMENT_SI_TEMPS", ", ".join(reasons)

    if (
        priorite_morning == "P1"
        and cp_sum is not None
        and cp_sum >= 8.5
        and "CP_SEC_ETUDE" in structure
        and blockers == 0
    ):
        return 1, "H5_PRIORITAIRE", ", ".join(reasons)

    if priorite_morning in {"P1", "P2"}:
        return 2, "H5_SUIVI_SIMPLE", ", ".join(reasons)

    return 3, "H5_SEULEMENT_SI_TEMPS", ", ".join(reasons)

def main():
    if not WATCHLIST_PATH.exists():
        raise FileNotFoundError(f"{WATCHLIST_PATH} introuvable")

    df = pd.read_csv(WATCHLIST_PATH, dtype=str, keep_default_na=False)

    cp_sums = []
    cp_statuses = []
    cp_details = []

    for _, row in df.iterrows():
        pair_sum, status, detail = _compute_cp_sec_sum(row)
        cp_sums.append(pair_sum)
        cp_statuses.append(status)
        cp_details.append(detail)

    df["cp_sec_sg_sum_9h"] = cp_sums
    df["cp_sec_sg_filter_status_9h"] = cp_statuses
    df["cp_sec_sg_detail_9h"] = cp_details

    priorities = []
    actions = []
    reasons = []

    for _, row in df.iterrows():
        p, action, reason = _priority_calc(row)
        priorities.append(p)
        actions.append(action)
        reasons.append(reason)

    df["priorite_h5_calc"] = priorities
    df["action_h5_calc"] = actions
    df["raison_priorite_h5_calc"] = reasons

    df = df.sort_values(
        by=["priorite_h5_calc"],
        ascending=True,
        kind="stable"
    )

    out = OUTPUT_DIR / "daily_watchlist_priorisee.csv"
    latest = OUTPUT_DIR / "latest_daily_watchlist_priorisee.csv"

    df.to_csv(out, index=False, encoding="utf-8")
    df.to_csv(latest, index=False, encoding="utf-8")

    cols = [
        "priorite_h5",
        "priorite_h5_calc",
        "action_h5_calc",
        "course_uid",
        "cp_sec_candidat",
        "cp_sec_sg_sum_9h",
        "cp_sec_sg_filter_status_9h",
        "cp_sec_sg_detail_9h",
        "raison_priorite_h5_calc",
    ]

    print("WATCHLIST_PRIORISEE_CREATED")
    print(out)
    print(latest)
    print(df[cols].head(25).to_string(index=False))

if __name__ == "__main__":
    main()
