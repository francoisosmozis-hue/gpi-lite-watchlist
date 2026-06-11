from pathlib import Path
from datetime import datetime, timezone
import json
import os
import pandas as pd


OUTPUT_DIR = Path("data/output")

GROUP_FIELDS = [
    "segment_ROI",
    "structure_max",
    "proxy_status",
    "qualite",
    "edge_status",
    "result_status",
]

KPI_COLUMNS = [
    "kpi_version",
    "created_at",
    "group_by",
    "group_value",
    "decisions",
    "tickets",
    "no_bet_count",
    "mise_total",
    "retour_total",
    "gain_net_total",
    "ROI_total",
    "ROI_median",
    "ratio_median",
    "drawdown_max",
    "result_ok_count",
    "result_no_bet_count",
    "result_pending_count",
    "replay_ko_count",
    "strict_allowed_count",
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
        return 0.0

    try:
        return float(text)
    except Exception:
        return 0.0


def _download_from_gcs(blob_name, target_path):
    bucket_name = (
        os.getenv("GPI_BUCKET_NAME")
        or os.getenv("BUCKET_NAME")
        or os.getenv("GPI_BUCKET")
        or "gpi-lite-watchlist-data"
    )

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


def _read_results():
    path = OUTPUT_DIR / "paper_trading_results.csv"

    _download_from_gcs(
        "gpi-lite/outputs/paper_trading_results.csv",
        path,
    )

    if not path.exists():
        raise FileNotFoundError("paper_trading_results.csv absent")

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [str(c).replace("\ufeff", "").strip() for c in df.columns]

    if df.empty:
        raise ValueError("paper_trading_results.csv vide")

    return df, path


def _prepare_numeric(df):
    df = df.copy()

    for col in ["mise_paper", "retour_paper", "gain_net_paper", "ROI_paper", "ratio_retour"]:
        if col not in df.columns:
            df[col] = ""

        df[col + "_num"] = df[col].apply(_to_float)

    for col in ["ticket_exact", "result_status", "replay_match_oui_non", "strict_allowed_oui_non"]:
        if col not in df.columns:
            df[col] = ""

    return df


def _max_drawdown(gains):
    equity = 0.0
    peak = 0.0
    max_dd = 0.0

    for gain in gains:
        equity += float(gain)

        if equity > peak:
            peak = equity

        drawdown = equity - peak

        if drawdown < max_dd:
            max_dd = drawdown

    return max_dd


def _build_group_row(df, group_by, group_value):
    decisions = int(len(df))
    tickets = int((df["mise_paper_num"] > 0).sum())
    no_bet_count = int((df["ticket_exact"].astype(str).str.upper() == "NO_BET").sum())

    mise_total = float(df["mise_paper_num"].sum())
    retour_total = float(df["retour_paper_num"].sum())
    gain_net_total = float(df["gain_net_paper_num"].sum())

    ROI_total = gain_net_total / mise_total if mise_total > 0 else 0.0
    ROI_median = float(df["ROI_paper_num"].median()) if decisions > 0 else 0.0
    ratio_median = float(df["ratio_retour_num"].median()) if decisions > 0 else 0.0
    drawdown_max = float(_max_drawdown(df["gain_net_paper_num"].tolist()))

    result_ok_count = int((df["result_status"] == "RESULT_OK").sum())
    result_no_bet_count = int((df["result_status"] == "RESULT_NO_BET").sum())
    result_pending_count = int((df["result_status"] == "RESULT_PENDING_RETURN").sum())
    replay_ko_count = int((df["replay_match_oui_non"] != "oui").sum())
    strict_allowed_count = int((df["strict_allowed_oui_non"] == "oui").sum())

    return {
        "kpi_version": "GPI_LITE_V0_9_8",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "group_by": group_by,
        "group_value": group_value,
        "decisions": decisions,
        "tickets": tickets,
        "no_bet_count": no_bet_count,
        "mise_total": mise_total,
        "retour_total": retour_total,
        "gain_net_total": gain_net_total,
        "ROI_total": ROI_total,
        "ROI_median": ROI_median,
        "ratio_median": ratio_median,
        "drawdown_max": drawdown_max,
        "result_ok_count": result_ok_count,
        "result_no_bet_count": result_no_bet_count,
        "result_pending_count": result_pending_count,
        "replay_ko_count": replay_ko_count,
        "strict_allowed_count": strict_allowed_count,
    }


def build_paper_kpi():
    df, source_path = _read_results()
    df = _prepare_numeric(df)

    rows = [_build_group_row(df, "GLOBAL", "ALL")]

    for field in GROUP_FIELDS:
        if field not in df.columns:
            continue

        values = sorted(set(_as_text(value, "ND") for value in df[field].tolist()))

        for value in values:
            sub = df[df[field].apply(lambda x: _as_text(x, "ND")) == value]
            rows.append(_build_group_row(sub, field, value))

    kpi_df = pd.DataFrame(rows, columns=KPI_COLUMNS)

    audit = {
        "ok": True,
        "kpi_version": "GPI_LITE_V0_9_8",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_results": str(source_path).replace("\\", "/"),
        "rows_input": int(len(df)),
        "rows_kpi": int(len(kpi_df)),
        "global_decisions": int(len(df)),
        "global_tickets": int((df["mise_paper_num"] > 0).sum()),
        "global_no_bet_count": int((df["ticket_exact"].astype(str).str.upper() == "NO_BET").sum()),
        "global_result_pending_count": int((df["result_status"] == "RESULT_PENDING_RETURN").sum()),
        "global_strict_allowed_count": int((df["strict_allowed_oui_non"] == "oui").sum()),
        "global_replay_ko_count": int((df["replay_match_oui_non"] != "oui").sum()),
    }

    return kpi_df, audit


def write_paper_kpi():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    kpi_df, audit = build_paper_kpi()

    kpi_path = OUTPUT_DIR / "paper_kpi_by_segment.csv"
    audit_csv_path = OUTPUT_DIR / "paper_kpi_audit.csv"
    audit_json_path = OUTPUT_DIR / "latest_paper_kpi_audit.json"

    kpi_df.to_csv(kpi_path, index=False, encoding="utf-8")
    pd.DataFrame([audit]).to_csv(audit_csv_path, index=False, encoding="utf-8")

    audit_json_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return kpi_path, audit_csv_path, audit_json_path, audit


if __name__ == "__main__":
    kpi_path, audit_csv_path, audit_json_path, audit = write_paper_kpi()

    print(kpi_path)
    print(audit_csv_path)
    print(audit_json_path)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
