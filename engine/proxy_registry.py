from pathlib import Path
import os
import unicodedata
import pandas as pd


OUTPUT_DIR = Path("data/output")
CONFIG_DIR = Path("data/config")


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


def _norm(value):
    text = _as_text(value, "").lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("+", "")
    text = text.replace("-", "_")
    text = text.replace(" ", "_")
    text = text.replace("é", "e")
    text = text.replace("è", "e")
    text = text.replace("ê", "e")
    return text.strip("_")


def _to_float(value):
    text = _as_text(value, "").replace(",", ".")
    try:
        return float(text)
    except Exception:
        return None


def _to_int(value):
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _proxy_paths():
    return [
        CONFIG_DIR / "proxy_error_by_segment.csv",
        OUTPUT_DIR / "proxy_error_by_segment.csv",
        Path("proxy_error_by_segment.csv"),
        Path("sources/calibration/proxy_error_by_segment.csv"),
    ]


def _download_proxy_from_gcs(target_path):
    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
    )

    if not bucket_name:
        return None

    blob_names = [
        "gpi-lite/config/proxy_error_by_segment.csv",
        "gpi-lite/outputs/proxy_error_by_segment.csv",
    ]

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)

        for blob_name in blob_names:
            blob = bucket.blob(blob_name)

            if blob.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                blob.download_to_filename(str(target_path))
                return target_path

    except Exception:
        return None

    return None


def _read_proxy_df():
    for path in _proxy_paths():
        if path.exists():
            df = pd.read_csv(path, dtype=str, keep_default_na=False)
            return df, path

    downloaded_path = _download_proxy_from_gcs(CONFIG_DIR / "proxy_error_by_segment.csv")

    if downloaded_path and downloaded_path.exists():
        df = pd.read_csv(downloaded_path, dtype=str, keep_default_na=False)
        return df, downloaded_path

    return None, None


def _first_col(df, names):
    lower = {c.lower(): c for c in df.columns}

    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]

    return None


def _parse_selection(selection):
    text = _as_text(selection, "")
    text = text.replace(" ", "")

    if text == "":
        return []

    nums = []

    for part in text.replace("/", "-").replace("_", "-").split("-"):
        n = _to_int(part)
        if n is not None:
            nums.append(n)

    return nums


def _partants_bucket(n):
    if n is None:
        return "ND"

    if n <= 11:
        return "8-11"

    if n <= 15:
        return "12-15"

    return "16+"


def _discipline_bucket(course_uid, h5_df):
    for col in ["discipline", "type_course", "specialite", "race_type"]:
        if h5_df is not None and col in h5_df.columns:
            values = h5_df[col].dropna().astype(str).str.strip()
            if len(values) > 0:
                text = _norm(values.iloc[0])
                if "monte" in text:
                    return "monté"
                if "plat" in text:
                    return "plat"
                if "obstacle" in text or "haie" in text or "steeple" in text:
                    return "obstacle"
                if "attele" in text:
                    return "attelé"

    text = _norm(course_uid)

    if "monte" in text:
        return "monté"

    if "haies" in text or "steeple" in text or "auteuil" in text:
        return "obstacle"

    return "attelé"


def _market_ranks(h5_df):
    if h5_df is None or h5_df.empty:
        return {}, "ND"

    num_col = _first_col(h5_df, ["numero", "num", "N", "n"])
    sg_col = _first_col(h5_df, ["SG", "sg", "cote_sg", "cote_SG"])

    if not num_col or not sg_col:
        return {}, "ND"

    rows = []

    for _, row in h5_df.iterrows():
        numero = _to_int(row.get(num_col))
        sg = _to_float(row.get(sg_col))

        if numero is None or sg is None or sg <= 0:
            continue

        rows.append({"numero": numero, "SG": sg})

    if not rows:
        return {}, "ND"

    rows = sorted(rows, key=lambda x: x["SG"])
    ranks = {r["numero"]: i + 1 for i, r in enumerate(rows)}

    q_values = [1.0 / r["SG"] for r in rows]
    q_sum = sum(q_values)

    if q_sum <= 0:
        return ranks, "ND"

    p_values = [q / q_sum for q in q_values]
    top3 = sum(p_values[:3])
    top4 = sum(p_values[:4])

    if top3 >= 0.45 or top4 >= 0.56:
        market_bucket = "concentré"
    elif top3 >= 0.35 or top4 >= 0.48:
        market_bucket = "équilibré"
    else:
        market_bucket = "ouvert"

    return ranks, market_bucket


def _selection_bucket(r1, r2):
    if r1 is None or r2 is None:
        return "ND"

    a, b = sorted([r1, r2])

    if b <= 6:
        return "favoris"

    if a <= 6 and b <= 12:
        return "mixte"

    if a <= 6 and b > 12:
        return "outsider"

    return "outsider"


def _rangs_bucket(r1, r2):
    if r1 is None or r2 is None:
        return "ND"

    a, b = sorted([r1, r2])

    if a == 1 and b == 2:
        return "1-2"

    if a == 1 and b in [3, 4]:
        return "1_3_4"

    if a == 2 and b in [4, 5]:
        return "2_4_5"

    if a >= 3 and b <= 6:
        return "3_6"

    if a == 1 and b > 6:
        return "favorioutsider"

    return "autre"


def _classify_status(row):
    existing = _as_text(row.get("segment_statut_proxy", ""), "")
    if existing != "":
        return existing.upper()

    sample_n = _to_float(row.get("sample_n"))
    taux = _to_float(row.get("taux_surestimation"))
    err_rel = _to_float(row.get("erreur_relative_mediane"))
    calibration = _as_text(row.get("calibration_version", ""), "")
    leakage = _as_text(row.get("leakage_check_ok", "true"), "true").lower()

    if sample_n is None or sample_n < 10:
        return "ND"

    if calibration == "":
        return "ND"

    if leakage in ["false", "0", "non"]:
        return "ND"

    if sample_n < 50:
        return "FRAGILE"

    if taux is not None and taux > 0.45:
        return "ROUGE"

    if err_rel is not None and err_rel < -0.10:
        return "ROUGE"

    if taux is not None and taux > 0.35:
        return "ORANGE"

    if err_rel is not None and err_rel < 0:
        return "ORANGE"

    return "VERT"


def _row_to_info(row, proxy_path, fallback_used):
    status = _classify_status(row)

    return {
        "proxy_status": status,
        "segment_id": _as_text(row.get("segment_id")),
        "sample_n": _as_text(row.get("sample_n")),
        "confidence": _as_text(row.get("confidence")),
        "fallback_level": fallback_used or _as_text(row.get("fallback_level"), "ND"),
        "C_prudent": _as_text(row.get("C_prudent")),
        "R_prudent": _as_text(row.get("R_prudent_median"), _as_text(row.get("R_prudent"), "ND")),
        "erreur_proxy": _as_text(row.get("erreur_mediane"), "ND"),
        "erreur_relative_mediane": _as_text(row.get("erreur_relative_mediane"), "ND"),
        "taux_surestimation": _as_text(row.get("taux_surestimation"), "ND"),
        "segment_degrade_oui_non": _as_text(row.get("segment_degrade_oui_non"), "ND"),
        "motif_degradation": _as_text(row.get("motif_degradation"), "ND"),
        "proxy_file": str(proxy_path).replace("\\", "/"),
    }


def _empty_info(reason):
    return {
        "proxy_status": "PROXY_ND",
        "segment_id": "ND",
        "sample_n": "ND",
        "confidence": "ND",
        "fallback_level": "ND",
        "C_prudent": "ND",
        "R_prudent": "ND",
        "erreur_proxy": "ND",
        "erreur_relative_mediane": "ND",
        "taux_surestimation": "ND",
        "segment_degrade_oui_non": "ND",
        "motif_degradation": reason,
        "proxy_file": "ND",
    }


def _match_rows(df, bucket_N, bucket_marche, bucket_selection, bucket_rangs, discipline_bucket):
    dfn = df.copy()

    for col in ["bucket_N", "bucket_marche", "bucket_selection", "bucket_rangs", "discipline_bucket", "bet_type"]:
        if col not in dfn.columns:
            dfn[col] = ""

    dfn["_bucket_N"] = dfn["bucket_N"].apply(_norm)
    dfn["_bucket_marche"] = dfn["bucket_marche"].apply(_norm)
    dfn["_bucket_selection"] = dfn["bucket_selection"].apply(_norm)
    dfn["_bucket_rangs"] = dfn["bucket_rangs"].apply(_norm)
    dfn["_discipline_bucket"] = dfn["discipline_bucket"].apply(_norm)
    dfn["_bet_type"] = dfn["bet_type"].apply(_norm)

    N_norms = [_norm(bucket_N)]

    if bucket_N == "16+":
        N_norms.append("16")

    base = dfn[dfn["_bet_type"] == "cp"]

    tests = [
        ("exact", {
            "_bucket_N": N_norms,
            "_bucket_marche": [_norm(bucket_marche)],
            "_bucket_selection": [_norm(bucket_selection)],
            "_bucket_rangs": [_norm(bucket_rangs)],
            "_discipline_bucket": [_norm(discipline_bucket)],
        }),
        ("parent_sans_rangs", {
            "_bucket_N": N_norms,
            "_bucket_marche": [_norm(bucket_marche)],
            "_bucket_selection": [_norm(bucket_selection)],
            "_discipline_bucket": [_norm(discipline_bucket)],
        }),
        ("parent_selection_discipline", {
            "_bucket_N": N_norms,
            "_bucket_selection": [_norm(bucket_selection)],
            "_discipline_bucket": [_norm(discipline_bucket)],
        }),
        ("parent_N_discipline", {
            "_bucket_N": N_norms,
            "_discipline_bucket": [_norm(discipline_bucket)],
        }),
        ("parent_discipline", {
            "_discipline_bucket": [_norm(discipline_bucket)],
        }),
    ]

    for fallback_name, constraints in tests:
        subset = base

        for col, values in constraints.items():
            subset = subset[subset[col].isin(values)]

        if not subset.empty:
            if "sample_n" in subset.columns:
                subset = subset.copy()
                subset["_sample_n_sort"] = subset["sample_n"].apply(lambda x: _to_float(x) or 0)
                subset = subset.sort_values("_sample_n_sort", ascending=False)

            return subset.iloc[0].to_dict(), fallback_name

    return None, None


def get_proxy_info(course_uid, decision_row, h5_df):
    df, proxy_path = _read_proxy_df()

    if df is None:
        return _empty_info("proxy_error_by_segment.csv absent")

    selection = _as_text(decision_row.get("selection_cp_sec", ""), "")

    if selection == "":
        selection = _as_text(decision_row.get("cp_sec_candidat", ""), "")

    if selection == "":
        selection = _as_text(decision_row.get("CP_SEC_CANDIDAT", ""), "")

    nums = _parse_selection(selection)

    if len(nums) < 2:
        return _empty_info("selection CP_SEC absente")

    n_partants = len(h5_df) if h5_df is not None else None
    bucket_N = _partants_bucket(n_partants)

    ranks, bucket_marche = _market_ranks(h5_df)
    r1 = ranks.get(nums[0])
    r2 = ranks.get(nums[1])

    bucket_selection = _selection_bucket(r1, r2)
    bucket_rangs = _rangs_bucket(r1, r2)
    discipline_bucket = _discipline_bucket(course_uid, h5_df)

    row, fallback_used = _match_rows(
        df,
        bucket_N=bucket_N,
        bucket_marche=bucket_marche,
        bucket_selection=bucket_selection,
        bucket_rangs=bucket_rangs,
        discipline_bucket=discipline_bucket,
    )

    if row is None:
        return _empty_info(
            f"segment introuvable bucket_N={bucket_N} marche={bucket_marche} selection={bucket_selection} rangs={bucket_rangs} discipline={discipline_bucket}"
        )

    info = _row_to_info(row, proxy_path, fallback_used)
    info["bucket_N_detected"] = bucket_N
    info["bucket_marche_detected"] = bucket_marche
    info["bucket_selection_detected"] = bucket_selection
    info["bucket_rangs_detected"] = bucket_rangs
    info["discipline_bucket_detected"] = discipline_bucket
    info["selection_cp_sec_detected"] = "-".join(str(x) for x in nums[:2])
    info["rang_1_detected"] = str(r1) if r1 is not None else "ND"
    info["rang_2_detected"] = str(r2) if r2 is not None else "ND"

    return info
