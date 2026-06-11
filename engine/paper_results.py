from pathlib import Path
from datetime import datetime, timezone
import json
import os
import pandas as pd


OUTPUT_DIR = Path("data/output")
RESULTS_DIR = Path("data/results")


RESULT_INPUT_COLUMNS = [
    "course_uid",
    "snapshot_time",
    "split",
    "arrivee",
    "rapport_officiel",
    "retour_paper",
    "source_resultat",
    "commentaire_resultat",
]


RESULT_COLUMNS = [
    "paper_id",
    "decision_id",
    "course_uid",
    "snapshot_time",
    "split",
    "date",
    "hippodrome",
    "engine_version",
    "ruleset_version",
    "input_hash",
    "qualite",
    "proxy_status",
    "segment_id",
    "structure_max",
    "structure_retenue",
    "verdict_pre_course",
    "ticket_exact",
    "mise_paper",
    "segment_ROI",
    "edge_status",
    "reason_codes",
    "kill_switch_codes",
    "replay_match_oui_non",
    "strict_allowed_oui_non",
    "arrivee",
    "rapport_officiel",
    "retour_paper",
    "gain_net_paper",
    "ROI_paper",
    "ratio_retour",
    "cause_resultat",
    "result_status",
    "source_resultat",
    "commentaire_resultat",
    "result_added_at",
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


def _to_float(value):
    text = _as_text(value, "").replace(",", ".")

    if text == "":
        return None

    try:
        return float(text)
    except Exception:
        return None


def _business_key(row):
    return "|".join([
        _as_text(row.get("course_uid")).lower(),
        _as_text(row.get("snapshot_time"), "H5").lower(),
        _as_text(row.get("split"), "TEST_LIVE").lower(),
    ])


def _download_from_gcs(blob_name, target_path):
    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or "gpi-lite-watchlist-data"
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


def _read_csv(path, columns=None):
    path = Path(path)

    if not path.exists():
        return pd.DataFrame(columns=columns or [])

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [str(c).replace("\ufeff", "").strip() for c in df.columns]

    if columns:
        for col in columns:
            if col not in df.columns:
                df[col] = ""

    return df


def _read_paper():
    path = OUTPUT_DIR / "paper_trading_50_decisions.csv"

    # Phase résultats : toujours tenter de récupérer le paper cloud le plus récent.
    # Si GCS est indisponible, fallback local.
    _download_from_gcs(
        "gpi-lite/outputs/paper_trading_50_decisions.csv",
        path,
    )

    df = _read_csv(path)

    if df.empty:
        raise FileNotFoundError("paper_trading_50_decisions.csv absent ou vide")

    return df, path



def _read_result_input():
    candidates = [
        RESULTS_DIR / "result_input.csv",
        OUTPUT_DIR / "result_input.csv",
    ]

    for path in candidates:
        if path.exists():
            df = _read_csv(path, RESULT_INPUT_COLUMNS)
            return df, path

    return pd.DataFrame(columns=RESULT_INPUT_COLUMNS), candidates[0]


def _build_result_map(result_df):
    if result_df is None or result_df.empty:
        return {}

    result_df = result_df.copy()

    for col in RESULT_INPUT_COLUMNS:
        if col not in result_df.columns:
            result_df[col] = ""

    result_df["_business_key"] = result_df.apply(lambda r: _business_key(r), axis=1)

    out = {}

    for _, row in result_df.iterrows():
        key = _as_text(row.get("_business_key"))
        if key:
            out[key] = row.to_dict()

    return out


def _compute_result(paper_row, result_row):
    mise = _to_float(paper_row.get("mise_paper")) or 0.0
    ticket = _as_text(paper_row.get("ticket_exact"), "NO_BET").upper()
    structure = _as_text(paper_row.get("structure_max"), "").upper()
    verdict = _as_text(paper_row.get("verdict_pre_course"), "").upper()

    manual_return = _to_float(result_row.get("retour_paper")) if result_row else None

    arrivee = _as_text(result_row.get("arrivee"), "") if result_row else ""
    rapport_officiel = _as_text(result_row.get("rapport_officiel"), "") if result_row else ""
    source_resultat = _as_text(result_row.get("source_resultat"), "") if result_row else ""
    commentaire_resultat = _as_text(result_row.get("commentaire_resultat"), "") if result_row else ""

    if ticket == "NO_BET" or verdict == "NO_BET":
        retour = 0.0
        gain = 0.0
        roi = 0.0
        ratio = 0.0
        cause = "NO_BET_PRE_COURSE"
        status = "RESULT_NO_BET"
    elif mise == 0.0:
        retour = 0.0
        gain = 0.0
        roi = 0.0
        ratio = 0.0
        cause = "SIMULATION_NO_STAKE"
        status = "RESULT_SIMULATION_NO_STAKE"
    elif manual_return is None:
        retour = ""
        gain = ""
        roi = ""
        ratio = ""
        cause = "RETOUR_OFFICIEL_ND"
        status = "RESULT_PENDING_RETURN"
    else:
        retour = float(manual_return)
        gain = retour - mise
        roi = gain / mise if mise > 0 else 0.0
        ratio = retour / mise if mise > 0 else 0.0
        cause = "RESULT_MANUAL_RETURN"
        status = "RESULT_OK"

    return {
        "arrivee": arrivee,
        "rapport_officiel": rapport_officiel,
        "retour_paper": retour,
        "gain_net_paper": gain,
        "ROI_paper": roi,
        "ratio_retour": ratio,
        "cause_resultat": cause,
        "result_status": status,
        "source_resultat": source_resultat,
        "commentaire_resultat": commentaire_resultat,
    }


def build_paper_results():
    paper_df, paper_path = _read_paper()
    result_df, result_path = _read_result_input()
    result_map = _build_result_map(result_df)

    result_map_by_course_uid = {}
    if result_df is not None and not result_df.empty:
        for _, result_uid_row in result_df.iterrows():
            uid_key = _as_text(result_uid_row.get("course_uid")).lower()
            if uid_key and uid_key not in result_map_by_course_uid:
                result_map_by_course_uid[uid_key] = result_uid_row.to_dict()

    now = datetime.now(timezone.utc).isoformat()
    rows = []

    for _, paper_row in paper_df.iterrows():
        paper = paper_row.to_dict()
        key = _business_key(paper)
        uid_key = _as_text(paper.get("course_uid")).lower()
        result_row = result_map.get(key, {}) or result_map_by_course_uid.get(uid_key, {})

        computed = _compute_result(paper, result_row)

        row = {}

        for col in RESULT_COLUMNS:
            if col in computed:
                row[col] = computed[col]
            elif col == "result_added_at":
                row[col] = now
            else:
                row[col] = _as_text(paper.get(col), "")

        rows.append(row)

    out = pd.DataFrame(rows, columns=RESULT_COLUMNS)

    return out, {
        "paper_file": str(paper_path).replace("\\", "/"),
        "result_input_file": str(result_path).replace("\\", "/"),
        "paper_count": int(len(paper_df)),
        "result_input_count": int(len(result_df)),
        "results_joined_count": int(len(out)),
        "result_ok_count": int((out["result_status"] == "RESULT_OK").sum()) if not out.empty else 0,
        "result_no_bet_count": int((out["result_status"] == "RESULT_NO_BET").sum()) if not out.empty else 0,
        "result_pending_count": int((out["result_status"] == "RESULT_PENDING_RETURN").sum()) if not out.empty else 0,
        "result_simulation_no_stake_count": int((out["result_status"] == "RESULT_SIMULATION_NO_STAKE").sum()) if not out.empty else 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_paper_results():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    result_df, audit = build_paper_results()

    results_path = OUTPUT_DIR / "paper_trading_results.csv"
    latest_path = OUTPUT_DIR / "latest_paper_result_update.csv"
    audit_csv_path = OUTPUT_DIR / "paper_results_audit.csv"
    audit_json_path = OUTPUT_DIR / "latest_paper_results_audit.json"

    result_df.to_csv(results_path, index=False, encoding="utf-8")

    if not result_df.empty:
        result_df.tail(1).to_csv(latest_path, index=False, encoding="utf-8")
    else:
        pd.DataFrame(columns=RESULT_COLUMNS).to_csv(latest_path, index=False, encoding="utf-8")

    pd.DataFrame([audit]).to_csv(audit_csv_path, index=False, encoding="utf-8")

    audit_json_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return results_path, latest_path, audit_csv_path, audit_json_path, audit


if __name__ == "__main__":
    results_path, latest_path, audit_csv_path, audit_json_path, audit = write_paper_results()

    print(results_path)
    print(latest_path)
    print(audit_csv_path)
    print(audit_json_path)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
