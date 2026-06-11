from pathlib import Path
import tempfile
import os
from datetime import datetime, timezone
import re
import pandas as pd


OUTPUT_DIR = Path("data/output")
CONFIG_DIR = Path("data/config")


PAPER_COLUMNS = [
    "paper_id",
    "decision_id",
    "date",
    "course_uid",
    "reunion_course",
    "hippodrome",
    "discipline",
    "partants_reels",
    "snapshot_time",
    "engine_version",
    "ruleset_version",
    "input_hash",
    "qualite",
    "score_jouabilite",
    "proxy_status",
    "segment_id",
    "sample_n",
    "confidence",
    "fallback_level",
    "C_prudent",
    "R_prudent",
    "erreur_proxy",
    "taux_surestimation",
    "segment_degrade_oui_non",
    "CP_SEC_selection",
    "CP_SEC_domine_oui_non",
    "CP3_selection",
    "SP_selection",
    "cp_sec_vs_cp3_controlled_oui_non",
    "backtest_pre_course_validated_oui_non",
    "structure_retenue",
    "structure_max",
    "verdict_pre_course",
    "ticket_exact",
    "mise_paper",
    "segment_ROI",
    "edge_status",
    "reason_codes",
    "kill_switch_codes",
    "split",
    "calibration_version",
    "leakage_check_ok",
    "decision_before_race_oui_non",
    "output_replayed_oui_non",
    "replay_match_oui_non",
    "strict_allowed_oui_non",
    "arrivee",
    "rapport_officiel",
    "retour_paper",
    "gain_net_paper",
    "ROI_paper",
    "ratio_retour",
    "cause_resultat",
    "created_at",
    "updated_at",
]


def _as_text(value, default=""):
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


def _read_csv(path):
    path = Path(path)

    if not path.exists():
        return pd.DataFrame(columns=PAPER_COLUMNS)

    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception:
        return pd.DataFrame(columns=PAPER_COLUMNS)

    df.columns = [str(c).replace("\\ufeff", "").strip() for c in df.columns]

    for col in PAPER_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    return df[PAPER_COLUMNS].copy()


def _download_cloud_paper_df():
    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
    )

    if not bucket_name:
        return pd.DataFrame(columns=PAPER_COLUMNS)

    tmp_path = Path(tempfile.gettempdir()) / "_paper_trading_50_decisions_cloud_tmp.csv"

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob("gpi-lite/outputs/paper_trading_50_decisions.csv")

        if not blob.exists():
            return pd.DataFrame(columns=PAPER_COLUMNS)

        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(tmp_path))

        df = _read_csv(tmp_path)

        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

        return df

    except Exception:
        return pd.DataFrame(columns=PAPER_COLUMNS)


def _merge_paper_frames(*frames):
    cleaned = []

    for df in frames:
        if df is None or df.empty:
            continue

        for col in PAPER_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        cleaned.append(df[PAPER_COLUMNS].copy())

    if not cleaned:
        return pd.DataFrame(columns=PAPER_COLUMNS)

    merged = pd.concat(cleaned, ignore_index=True)
    merged["decision_id"] = merged["decision_id"].astype(str).str.strip()

    has_id = merged["decision_id"] != ""
    with_id = merged[has_id].drop_duplicates(subset=["decision_id"], keep="last")
    without_id = merged[~has_id]

    return pd.concat([with_id, without_id], ignore_index=True)[PAPER_COLUMNS].copy()


def _read_decision_engine(course_uid):
    expected = str(course_uid).strip()
    course_path = OUTPUT_DIR / f"{course_uid}_decision_engine_outputs.csv"

    candidates = []

    if course_path.exists():
        candidates.append(course_path)

    for p in OUTPUT_DIR.glob("*_decision_engine_outputs.csv"):
        if p not in candidates:
            candidates.append(p)

    if not candidates:
        raise FileNotFoundError(f"decision_engine_outputs introuvable pour : {course_uid}")

    errors = []

    for path in candidates:
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
            continue

        if df.empty:
            errors.append(f"{path}: fichier vide")
            continue

        row = df.iloc[0].to_dict()
        found = str(row.get("course_uid", "")).strip()

        if found == expected:
            return row, path

        errors.append(f"{path}: course_uid={found}")

    raise FileNotFoundError(
        "aucun decision_engine_outputs ne correspond à "
        + expected
        + " | candidats="
        + " ; ".join(errors[:10])
    )


def _date_from_course_uid(course_uid):
    text = _as_text(course_uid)

    m = re.match(r"(\d{2})_(\d{2})_(\d{4})", text)

    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    m = re.match(r"(\d{4})_(\d{2})_(\d{2})", text)

    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return "ND"


def _hippodrome_from_course_uid(course_uid):
    parts = _as_text(course_uid).split(" - ")

    if len(parts) >= 2:
        return parts[1].strip()

    return "ND"


def _reunion_course_from_course_uid(course_uid):
    text = _as_text(course_uid)

    m = re.search(r"\bR\d+\s*C\d+\b", text, flags=re.IGNORECASE)

    if m:
        return m.group(0).upper().replace(" ", "")

    return "ND"


def _paper_id(decision_id, course_uid):
    decision_id = _as_text(decision_id)

    if decision_id:
        return "PAPER_" + decision_id.replace("DEO_", "")

    safe = re.sub(r"[^A-Za-z0-9]+", "_", _as_text(course_uid))[:80]
    return "PAPER_" + safe


def _mise_paper(row):
    verdict = _as_text(row.get("verdict")).upper()
    ticket = _as_text(row.get("ticket_exact")).upper()
    structure = _as_text(row.get("structure_max")).upper()

    if verdict == "NO_BET" or ticket == "NO_BET":
        return "0"

    if "SIMULATION" in structure:
        return "0"

    return _as_text(row.get("mise_theorique"), "0")


def _build_paper_row(decision_row):
    now = datetime.now(timezone.utc).isoformat()
    course_uid = _as_text(decision_row.get("course_uid"))

    return {
        "paper_id": _paper_id(decision_row.get("decision_id"), course_uid),
        "decision_id": _as_text(decision_row.get("decision_id")),
        "date": _date_from_course_uid(course_uid),
        "course_uid": course_uid,
        "reunion_course": _reunion_course_from_course_uid(course_uid),
        "hippodrome": _hippodrome_from_course_uid(course_uid),
        "discipline": _as_text(decision_row.get("discipline_bucket_detected"), "ND"),
        "partants_reels": _as_text(decision_row.get("partants_reels"), "ND"),
        "snapshot_time": _as_text(decision_row.get("snapshot_time"), "ND"),
        "engine_version": _as_text(decision_row.get("engine_version"), "ND"),
        "ruleset_version": _as_text(decision_row.get("ruleset_version"), "ND"),
        "input_hash": _as_text(decision_row.get("input_hash"), "ND"),
        "qualite": _as_text(decision_row.get("qualite"), "ND"),
        "score_jouabilite": _as_text(decision_row.get("score_jouabilite"), "ND"),
        "proxy_status": _as_text(decision_row.get("proxy_status"), "ND"),
        "segment_id": _as_text(decision_row.get("segment_id"), "ND"),
        "sample_n": _as_text(decision_row.get("sample_n"), "ND"),
        "confidence": _as_text(decision_row.get("confidence"), "ND"),
        "fallback_level": _as_text(decision_row.get("fallback_level"), "ND"),
        "C_prudent": _as_text(decision_row.get("C_prudent"), "ND"),
        "R_prudent": _as_text(decision_row.get("R_prudent"), "ND"),
        "erreur_proxy": _as_text(decision_row.get("erreur_proxy"), "ND"),
        "taux_surestimation": _as_text(decision_row.get("taux_surestimation"), "ND"),
        "segment_degrade_oui_non": _as_text(decision_row.get("segment_degrade_oui_non"), "ND"),
        "CP_SEC_selection": _as_text(decision_row.get("selection_CP_SEC"), ""),
        "CP_SEC_domine_oui_non": _as_text(decision_row.get("CP_SEC_domine_oui_non"), "ND"),
        "CP3_selection": _as_text(decision_row.get("selection_CP3"), ""),
        "SP_selection": _as_text(decision_row.get("selection_SP"), ""),
        "cp_sec_candidate": _as_text(decision_row.get("cp_sec_candidate") or decision_row.get("selection_cp_sec") or decision_row.get("selection_CP_SEC"), ""),
        "cp_sg_pair_sum": _as_text(decision_row.get("cp_sg_pair_sum"), "ND"),
        "cp_sg_filter_status": _as_text(decision_row.get("cp_sg_filter_status"), "ND"),
        "cp3_candidate": _as_text(decision_row.get("cp3_candidate") or decision_row.get("selection_cp3") or decision_row.get("selection_CP3"), ""),
        "cp3_sg_min_pair_sum": _as_text(decision_row.get("cp3_sg_min_pair_sum"), "ND"),
        "cp3_sg_filter_status": _as_text(decision_row.get("cp3_sg_filter_status"), "ND"),
        "paper_candidate_allowed": _as_text(decision_row.get("paper_candidate_allowed"), "non"),
        "paper_candidate_type": _as_text(decision_row.get("paper_candidate_type"), "NO_BET"),
        "paper_candidate_ticket": _as_text(decision_row.get("paper_candidate_ticket"), "NO_BET"),
        "paper_candidate_segment": _as_text(decision_row.get("paper_candidate_segment"), "PAPER_TRADING"),
        "paper_candidate_mise": _as_text(decision_row.get("paper_candidate_mise"), "0"),
        "paper_candidate_reason": _as_text(decision_row.get("paper_candidate_reason"), "ND"),
        "volatilite_scope": _as_text(decision_row.get("volatilite_scope"), "ND"),
        "volatilite_top6_max_pct": _as_text(decision_row.get("volatilite_top6_max_pct"), "ND"),
        "volatilite_top6_max_numero": _as_text(decision_row.get("volatilite_top6_max_numero"), "ND"),
        "volatilite_top6_max_cheval": _as_text(decision_row.get("volatilite_top6_max_cheval"), "ND"),
        "volatilite_top6_sg_9h": _as_text(decision_row.get("volatilite_top6_sg_9h"), "ND"),
        "volatilite_top6_sg_h5": _as_text(decision_row.get("volatilite_top6_sg_h5"), "ND"),
        "volatilite_top6_direction": _as_text(decision_row.get("volatilite_top6_direction"), "ND"),
        "volatilite_status": _as_text(decision_row.get("volatilite_status"), "ND"),
        "cp_sec_vs_cp3_controlled_oui_non": _as_text(decision_row.get("cp_sec_vs_cp3_controlled_oui_non"), "non"),
        "backtest_pre_course_validated_oui_non": _as_text(decision_row.get("backtest_pre_course_validated_oui_non"), "non"),
        "structure_retenue": _as_text(decision_row.get("structure_retenue"), "NO_BET"),
        "structure_max": _as_text(decision_row.get("structure_max"), "NO_BET"),
        "verdict_pre_course": _as_text(decision_row.get("verdict"), "NO_BET"),
        "ticket_exact": _as_text(decision_row.get("ticket_exact"), "NO_BET"),
        "mise_paper": _mise_paper(decision_row),
        "segment_ROI": _as_text(decision_row.get("segment_ROI"), "NO_BET"),
        "edge_status": _as_text(decision_row.get("edge_status"), "EDGE_ABSENT"),
        "reason_codes": _as_text(decision_row.get("reason_codes"), "NO_BET_DEFAULT"),
        "kill_switch_codes": _as_text(decision_row.get("kill_switch_codes"), "ND"),
        "split": _as_text(decision_row.get("split"), "ND"),
        "calibration_version": _as_text(decision_row.get("calibration_version"), "ND"),
        "leakage_check_ok": _as_text(decision_row.get("leakage_check_ok"), "false").lower(),
        "decision_before_race_oui_non": _as_text(decision_row.get("decision_before_race_oui_non"), "non"),
        "output_replayed_oui_non": _as_text(decision_row.get("output_replayed_oui_non"), "non"),
        "replay_match_oui_non": _as_text(decision_row.get("replay_match_oui_non"), "non"),
        "strict_allowed_oui_non": _as_text(decision_row.get("strict_allowed_oui_non"), "non"),
        "arrivee": "",
        "rapport_officiel": "",
        "retour_paper": "",
        "gain_net_paper": "",
        "ROI_paper": "",
        "ratio_retour": "",
        "cause_resultat": "",
        "created_at": now,
        "updated_at": now,
    }



def _download_from_gcs_if_available(blob_name, target_path):
    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
    )

    if not bucket_name:
        return False

    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if not blob.exists():
            return False

        target_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(target_path))
        return True

    except Exception:
        return False


def _hydrate_paper_from_gcs(output_path, latest_path):
    _download_from_gcs_if_available(
        "gpi-lite/outputs/paper_trading_50_decisions.csv",
        output_path,
    )

    _download_from_gcs_if_available(
        "gpi-lite/outputs/latest_paper_trading_decision.csv",
        latest_path,
    )



def _identity_value(row):
    return "|".join([
        _as_text(row.get("course_uid"), "").strip().lower(),
        _as_text(row.get("snapshot_time"), "").strip().lower(),
        _as_text(row.get("split"), "").strip().lower(),
    ])


def _dedupe_by_business_key(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=PAPER_COLUMNS)

    for col in PAPER_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df[PAPER_COLUMNS].copy()

    df["_business_key"] = df.apply(lambda r: _identity_value(r), axis=1)

    has_key = df["_business_key"].astype(str).str.strip() != "||"
    with_key = df[has_key].drop_duplicates(subset=["_business_key"], keep="last")
    without_key = df[~has_key]

    out = pd.concat([with_key, without_key], ignore_index=True)
    out = out.drop(columns=["_business_key"], errors="ignore")

    return out[PAPER_COLUMNS].copy()


def append_paper_trading_decision(course_uid):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    decision_row, decision_path = _read_decision_engine(course_uid)

    row = _build_paper_row(decision_row)

    output_path = OUTPUT_DIR / "paper_trading_50_decisions.csv"
    latest_path = OUTPUT_DIR / "latest_paper_trading_decision.csv"

    local_df = _read_csv(output_path)
    cloud_df = _download_cloud_paper_df()
    df = _merge_paper_frames(cloud_df, local_df)

    row_key = _identity_value(row)

    if not df.empty:
        df["_business_key"] = df.apply(lambda r: _identity_value(r), axis=1)
        mask = df["_business_key"].astype(str) == row_key

        if mask.any():
            for col in PAPER_COLUMNS:
                df.loc[mask, col] = row[col]
        else:
            df = pd.concat([df.drop(columns=["_business_key"], errors="ignore"), pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row], columns=PAPER_COLUMNS)

    df = df.drop(columns=["_business_key"], errors="ignore")
    df = _dedupe_by_business_key(df)
    df.to_csv(output_path, index=False, encoding="utf-8")

    pd.DataFrame([row], columns=PAPER_COLUMNS).to_csv(
        latest_path,
        index=False,
        encoding="utf-8",
    )

    return output_path, latest_path, len(df)
