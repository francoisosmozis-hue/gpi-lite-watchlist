from pathlib import Path
import re
import pandas as pd


OUTPUT_DIR = Path("data/output")


def _as_text(value, default="ND"):
    if value is None:
        return default

    try:
        if pd.isna(value):
            return default
    except Exception:
        pass

    text = str(value).strip()

    if text == "" or text.lower() in ["nan", "none"]:
        return default

    return text


def _to_float(value):
    try:
        text = _as_text(value, default="")
        text = text.replace(",", ".")
        text = re.sub(r"[^0-9.]", "", text)

        if text == "":
            return None

        return float(text)
    except Exception:
        return None


def _first(row, names, default="ND"):
    if row is None:
        return default

    for name in names:
        if name in row:
            value = _as_text(row.get(name), default="")
            if value != "":
                return value

    return default


def _read_csv_first_row(path):
    if not path.exists():
        return None

    df = pd.read_csv(path)

    if df.empty:
        return None

    return df.iloc[0]


def _read_csv_df(path):
    if not path.exists():
        return None

    df = pd.read_csv(path)

    if df.empty:
        return None

    return df


def _read_decision_row(course_uid):
    candidates = [
        OUTPUT_DIR / f"{course_uid}_decision_lite.csv",
        OUTPUT_DIR / "latest_decision_lite.csv",
        OUTPUT_DIR / "decision_lite.csv",
    ]

    errors = []

    for candidate in candidates:
        row = _read_csv_first_row(candidate)

        if row is None:
            errors.append(f"absent ou vide: {candidate}")
            continue

        found_course = _first(row, ["course_uid", "COURSE", "course_key"], "")

        if candidate.name in ["latest_decision_lite.csv", "decision_lite.csv"]:
            if found_course and found_course != course_uid:
                errors.append(f"mauvaise course dans {candidate}: {found_course}")
                continue

        return row

    raise FileNotFoundError(
        "decision_lite introuvable ou non correspondant. "
        + " | ".join(errors)
    )


def _read_h5_update_row(course_uid):
    return _read_csv_first_row(OUTPUT_DIR / f"{course_uid}_h5_update.csv")


def _read_course_h5_df(course_uid):
    return _read_csv_df(OUTPUT_DIR / f"{course_uid}_course_h5_clean.csv")


def _is_np_row(row):
    statut = _as_text(row.get("statut", ""), default="").upper()
    is_np = _as_text(row.get("is_np", ""), default="").lower()

    if statut in ["NP", "NON_PARTANT", "NON-PARTANT"]:
        return True

    if is_np in ["1", "true", "oui", "yes"]:
        return True

    return False


def _partants_from_h5_df(df):
    if df is None or df.empty:
        return "ND"

    rows = []

    for _, row in df.iterrows():
        if not _is_np_row(row):
            rows.append(row)

    return str(len(rows))


def _top6_from_h5_df(df):
    if df is None or df.empty:
        return "ND"

    work = df.copy()

    if "SG" not in work.columns or "numero" not in work.columns:
        return "ND"

    work["SG_num"] = work["SG"].apply(_to_float)
    work = work[work["SG_num"].notna()]

    if "statut" in work.columns or "is_np" in work.columns:
        keep_index = []
        for idx, row in work.iterrows():
            if not _is_np_row(row):
                keep_index.append(idx)
        work = work.loc[keep_index]

    if work.empty:
        return "ND"

    work = work.sort_values("SG_num", ascending=True).head(6)

    return ",".join(str(x).strip() for x in work["numero"].tolist())


def _favori_from_h5_df(df):
    if df is None or df.empty:
        return "ND", "ND"

    work = df.copy()

    required = ["SG", "numero", "cheval"]
    for col in required:
        if col not in work.columns:
            return "ND", "ND"

    work["SG_num"] = work["SG"].apply(_to_float)
    work = work[work["SG_num"].notna()]

    keep_index = []
    for idx, row in work.iterrows():
        if not _is_np_row(row):
            keep_index.append(idx)

    work = work.loc[keep_index]

    if work.empty:
        return "ND", "ND"

    fav = work.sort_values("SG_num", ascending=True).iloc[0]

    numero = _as_text(fav.get("numero"), "")
    cheval = _as_text(fav.get("cheval"), "")
    sg = fav.get("SG_num")

    try:
        sg_text = f"{float(sg):g}"
    except Exception:
        sg_text = _as_text(sg, "ND")

    favori = f"{numero} {cheval} SG {sg_text}".strip()

    if sg is None:
        favori_fragile = "ND"
    elif float(sg) < 1.8 or float(sg) > 6:
        favori_fragile = "oui"
    else:
        favori_fragile = "non"

    return favori, favori_fragile


def _anomalies_from_h5_df(df):
    if df is None or df.empty:
        return ""

    if "SG" not in df.columns or "numero" not in df.columns or "cheval" not in df.columns:
        return ""

    work = df.copy()
    work["SG_num"] = work["SG"].apply(_to_float)
    work = work[work["SG_num"].notna()]

    keep_index = []
    for idx, row in work.iterrows():
        if not _is_np_row(row):
            keep_index.append(idx)

    work = work.loc[keep_index]

    if len(work) < 7:
        return ""

    work = work.sort_values("SG_num", ascending=True).iloc[6:12]

    parts = []

    for _, row in work.iterrows():
        sg = row.get("SG_num")

        if sg is not None and float(sg) <= 16:
            numero = _as_text(row.get("numero"), "")
            cheval = _as_text(row.get("cheval"), "")
            parts.append(f"{numero} {cheval} SG {float(sg):g}")

    return " | ".join(parts)


def _sp_values_from_h5_df(df):
    if df is None or df.empty:
        return ""

    if "numero" not in df.columns:
        return ""

    if "SP_min" not in df.columns and "SP_max" not in df.columns:
        return ""

    parts = []

    for _, row in df.iterrows():
        numero = _as_text(row.get("numero"), "")
        sp_min = _to_float(row.get("SP_min", None)) if "SP_min" in df.columns else None
        sp_max = _to_float(row.get("SP_max", None)) if "SP_max" in df.columns else None

        if sp_min is None and sp_max is None:
            continue

        sp_min_text = "ND" if sp_min is None else f"{sp_min:g}"
        sp_max_text = "ND" if sp_max is None else f"{sp_max:g}"

        parts.append(f"{numero}:{sp_min_text}-{sp_max_text}")

    return " | ".join(parts)


def _value_decision_h5_course(decision_row, h5_row, h5_df, names, computed=None, default="ND"):
    value = _first(h5_row, names, "")

    if value != "":
        return value

    if computed is not None:
        value = computed()
        if value != "" and value != "ND":
            return value

    value = _first(decision_row, names, "")

    if value != "":
        return value

    return default


def build_clean_prompt_from_decision(course_uid):
    decision_row = _read_decision_row(course_uid)
    h5_row = _read_h5_update_row(course_uid)
    h5_df = _read_course_h5_df(course_uid)

    favori_computed, favori_fragile_computed = _favori_from_h5_df(h5_df)

    course = _first(decision_row, ["course_uid", "COURSE", "course_key"], course_uid)
    qualite = _first(decision_row, ["qualite", "QUALITE"], "ND")

    partants_reels = _value_decision_h5_course(
        decision_row,
        h5_row,
        h5_df,
        ["partants_reels", "PARTANTS_REELS"],
        computed=lambda: _partants_from_h5_df(h5_df),
        default="ND",
    )

    top6_sg = _value_decision_h5_course(
        decision_row,
        h5_row,
        h5_df,
        ["top6_sg", "top6_sg_h5", "top6_sg_9h", "TOP6_SG"],
        computed=lambda: _top6_from_h5_df(h5_df),
        default="ND",
    )

    anomalies = _value_decision_h5_course(
        decision_row,
        h5_row,
        h5_df,
        ["anomalies_7_12", "ANOMALIES_7_12"],
        computed=lambda: _anomalies_from_h5_df(h5_df),
        default="",
    )

    favori = _value_decision_h5_course(
        decision_row,
        h5_row,
        h5_df,
        ["favori", "FAVORI"],
        computed=lambda: favori_computed,
        default="ND",
    )

    favori_fragile = _value_decision_h5_course(
        decision_row,
        h5_row,
        h5_df,
        ["favori_fragile", "FAVORI_FRAGILE"],
        computed=lambda: favori_fragile_computed,
        default="ND",
    )

    kill_switchs_for_variation = _first(
        decision_row,
        ["kill_switchs", "kill_switch_codes", "KILL_SWITCHS"],
        "",
    )

    variation_rouge = _value_decision_h5_course(
        decision_row,
        h5_row,
        h5_df,
        ["variation_rouge_top6", "VARIATION_ROUGE_TOP6"],
        computed=lambda: "oui" if "VOLATILITE_KILL_SWITCH_TOP6" in str(kill_switchs_for_variation) else "non",
        default="ND",
    )

    sp_values = _value_decision_h5_course(
        decision_row,
        h5_row,
        h5_df,
        ["sp_values", "SP_VALUES"],
        computed=lambda: _sp_values_from_h5_df(h5_df),
        default="",
    )

    cp_sec = _first(
        decision_row,
        ["cp_sec_candidat", "selection_cp_sec", "CP_SEC_CANDIDAT"],
        "",
    )

    cp_sec_statut = _first(
        decision_row,
        ["cp_sec_statut", "CP_SEC_STATUT"],
        "simulation CP. Filtre SG contrôlé. Ticket réel interdit si kill-switch ou calibration/proxy strict absent",
    )

    cp3_statut = _first(
        decision_row,
        ["cp3_statut", "CP3_STATUT"],
        "SIMULATION CP. Rapport CP pré-course indisponible. Étude basée sur SG ZEturf. Somme SG paire > 6 requise. CP_SEC prioritaire avant CP3",
    )

    kill_switchs = _first(
        decision_row,
        ["kill_switchs", "kill_switch_codes", "KILL_SWITCHS"],
        "aucun",
    )

    verdict = _first(
        decision_row,
        ["verdict_python", "VERDICT_PYTHON"],
        "NO_BET",
    )

    action_gpt = _first(
        decision_row,
        ["action_gpt", "ACTION_GPT"],
        "contrôler uniquement, ne pas transformer une simulation en ticket strict",
    )

    return (
        "Applique GPI V19.1.4.\n\n"
        "Ne change pas le verdict Python.\n"
        "Ne transforme pas une simulation en ticket strict.\n"
        "Si un kill-switch est actif, confirme NO BET.\n"
        "Si kill-switch actif, calibration absente ou proxy CP strict absent, CP réel interdit.\n"
        "Si SP calibration absente, SP standard interdit.\n"
        "Contrôle seulement la cohérence.\n\n"
        "Résumé Python :\n\n"
        f"COURSE : {course}\n"
        f"QUALITE : {qualite}\n"
        f"PARTANTS_REELS : {partants_reels}\n"
        f"TOP6_SG : {top6_sg}\n"
        f"ANOMALIES_7_12 : {anomalies}\n"
        f"FAVORI : {favori}\n"
        f"FAVORI_FRAGILE : {favori_fragile}\n"
        f"VARIATION_ROUGE_TOP6 : {variation_rouge}\n"
        f"SP_VALUES : {sp_values}\n"
        f"CP_SEC_CANDIDAT : {cp_sec}\n"
        f"CP_SEC_STATUT : {cp_sec_statut}\n"
        f"CP_SG_PAIR_SUM : {_first(decision_row, ["cp_sg_pair_sum", "CP_SG_PAIR_SUM"], "ND")}\n"
        f"CP_SG_FILTER_STATUS : {_first(decision_row, ["cp_sg_filter_status", "CP_SG_FILTER_STATUS"], "ND")}\n"
        f"CP3_CANDIDAT : {_first(decision_row, ["cp3_candidate", "CP3_CANDIDATE", "selection_cp3", "selection_CP3"], "ND")}\n"
        f"CP3_SG_MIN_PAIR_SUM : {_first(decision_row, ["cp3_sg_min_pair_sum", "CP3_SG_MIN_PAIR_SUM"], "ND")}\n"
        f"CP3_SG_FILTER_STATUS : {_first(decision_row, ["cp3_sg_filter_status", "CP3_SG_FILTER_STATUS"], "ND")}\n"
        f"DECISION_STRICT_ALLOWED : {_first(decision_row, ["decision_strict_allowed", "DECISION_STRICT_ALLOWED"], "non")}\n"
        f"DECISION_BLOCK_REASON : {_first(decision_row, ["decision_block_reason", "DECISION_BLOCK_REASON"], "ND")}\n"
        f"STRICT_GATE_SUMMARY : {_first(decision_row, ["strict_gate_summary", "STRICT_GATE_SUMMARY"], "ND")}\n"
        f"VOLATILITE_SCOPE : {_first(decision_row, ["volatilite_scope", "VOLATILITE_SCOPE"], "TOP6_UNION")}\n"
        f"VOLATILITE_TOP6_MAX_PCT : {_first(decision_row, ["volatilite_top6_max_pct", "VOLATILITE_TOP6_MAX_PCT"], "ND")}\n"
        f"VOLATILITE_TOP6_MAX_NUMERO : {_first(decision_row, ["volatilite_top6_max_numero", "VOLATILITE_TOP6_MAX_NUMERO"], "ND")}\n"
        f"VOLATILITE_TOP6_MAX_CHEVAL : {_first(decision_row, ["volatilite_top6_max_cheval", "VOLATILITE_TOP6_MAX_CHEVAL"], "ND")}\n"
        f"VOLATILITE_TOP6_SG_9H : {_first(decision_row, ["volatilite_top6_sg_9h", "VOLATILITE_TOP6_SG_9H"], "ND")}\n"
        f"VOLATILITE_TOP6_SG_H5 : {_first(decision_row, ["volatilite_top6_sg_h5", "VOLATILITE_TOP6_SG_H5"], "ND")}\n"
        f"VOLATILITE_TOP6_DIRECTION : {_first(decision_row, ["volatilite_top6_direction", "VOLATILITE_TOP6_DIRECTION"], "ND")}\n"
        f"VOLATILITE_STATUS : {_first(decision_row, ["volatilite_status", "VOLATILITE_STATUS"], "ND")}\n"
        f"CP_SEC_SG_SUM : {_first(decision_row, ["cp_sec_sg_sum", "CP_SEC_SG_SUM", "cp_sg_pair_sum", "CP_SG_PAIR_SUM"], "ND")}\n"
        f"CP_SEC_SG_STATUS : {_first(decision_row, ["cp_sec_sg_status", "CP_SEC_SG_STATUS", "cp_sg_filter_status", "CP_SG_FILTER_STATUS"], "ND")}\n"
        f"PAPER_CANDIDATE_ALLOWED : {_first(decision_row, ["paper_candidate_allowed", "PAPER_CANDIDATE_ALLOWED"], "non")}\n"
        f"PAPER_CANDIDATE_TYPE : {_first(decision_row, ["paper_candidate_type", "PAPER_CANDIDATE_TYPE"], "NO_BET")}\n"
        f"PAPER_CANDIDATE_TICKET : {_first(decision_row, ["paper_candidate_ticket", "PAPER_CANDIDATE_TICKET"], "NO_BET")}\n"
        f"PAPER_CANDIDATE_SEGMENT : {_first(decision_row, ["paper_candidate_segment", "PAPER_CANDIDATE_SEGMENT"], "PAPER_TRADING")}\n"
        f"PAPER_CANDIDATE_MISE : {_first(decision_row, ["paper_candidate_mise", "PAPER_CANDIDATE_MISE"], "0")}\n"
        f"PAPER_CANDIDATE_REASON : {_first(decision_row, ["paper_candidate_reason", "PAPER_CANDIDATE_REASON"], "ND")}\n"
        f"CP3_STATUT : {cp3_statut}\n"
        f"KILL_SWITCHS : {kill_switchs}\n"
        f"VERDICT_PYTHON : {verdict}\n"
        f"ACTION_GPT : {action_gpt}\n\n"
        "Réponds uniquement avec :\n"
        "VERDICT\n"
        "Qualité\n"
        "Structure max\n"
        "Ticket exact ou NO BET\n"
        "Raison principale\n"
        "Segment ROI\n"
        "Edge\n"
    )


def rewrite_prompt_gpt_from_decision(course_uid):
    prompt_text = build_clean_prompt_from_decision(course_uid)
    prompt_path = OUTPUT_DIR / f"{course_uid}_prompt_gpt.txt"
    prompt_path.write_text(prompt_text, encoding="utf-8")
    return prompt_path

