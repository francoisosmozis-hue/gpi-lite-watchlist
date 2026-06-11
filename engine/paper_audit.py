from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd


OUTPUT_DIR = Path("data/output")


BUSINESS_KEY_COLS = ["course_uid", "snapshot_time", "split"]


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


def _business_key(row):
    return "|".join([
        _as_text(row.get("course_uid")).lower(),
        _as_text(row.get("snapshot_time")).lower(),
        _as_text(row.get("split")).lower(),
    ])


def _read_paper():
    path = OUTPUT_DIR / "paper_trading_50_decisions.csv"

    if not path.exists():
        return pd.DataFrame(), path

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [str(c).replace("\ufeff", "").strip() for c in df.columns]

    return df, path


def build_paper_audit():
    df, path = _read_paper()
    now = datetime.now(timezone.utc).isoformat()

    if df.empty:
        return {
            "ok": False,
            "audit_version": "GPI_LITE_V0_9_6_PREVIEW",
            "created_at": now,
            "paper_file": str(path).replace("\\", "/"),
            "paper_trading_count": 0,
            "paper_trading_ready_50": False,
            "recommendation": "CONTINUE_PAPER_TRADING",
            "blocking_reason": "paper_trading_50_decisions.csv absent ou vide",
        }

    for col in BUSINESS_KEY_COLS:
        if col not in df.columns:
            df[col] = ""

    df["_business_key"] = df.apply(lambda r: _business_key(r), axis=1)

    total_rows = int(len(df))
    unique_business_keys = int(df["_business_key"].nunique())
    duplicate_rows = int(total_rows - unique_business_keys)

    no_bet_count = int((df.get("ticket_exact", "") == "NO_BET").sum()) if "ticket_exact" in df.columns else 0
    strict_allowed_count = int((df.get("strict_allowed_oui_non", "") == "oui").sum()) if "strict_allowed_oui_non" in df.columns else 0

    result_cols = ["arrivee", "rapport_officiel", "retour_paper", "gain_net_paper", "ROI_paper", "ratio_retour"]
    result_leak_count = 0

    for col in result_cols:
        if col in df.columns:
            result_leak_count += int((df[col].astype(str).str.strip() != "").sum())

    replay_ok_count = int((df.get("replay_match_oui_non", "") == "oui").sum()) if "replay_match_oui_non" in df.columns else 0
    replay_ko_count = int(total_rows - replay_ok_count)

    latest = df.iloc[-1].to_dict()

    ready_50 = unique_business_keys >= 50

    if result_leak_count > 0:
        recommendation = "BLOCKED_RESULT_LEAK"
        blocking_reason = "des champs resultat sont remplis dans le paper pré-course"
    elif duplicate_rows > 0:
        recommendation = "BLOCKED_DUPLICATES"
        blocking_reason = "doublons business_key détectés"
    elif strict_allowed_count > 0:
        recommendation = "BLOCKED_STRICT_ALLOWED"
        blocking_reason = "strict_allowed oui détecté avant validation"
    elif ready_50:
        recommendation = "READY_FOR_AUDIT_V0_9_6"
        blocking_reason = ""
    else:
        recommendation = "CONTINUE_PAPER_TRADING"
        blocking_reason = f"{unique_business_keys}/50 decisions uniques"

    return {
        "ok": True,
        "audit_version": "GPI_LITE_V0_9_6_PREVIEW",
        "created_at": now,
        "paper_file": str(path).replace("\\", "/"),
        "paper_trading_count": total_rows,
        "paper_unique_count": unique_business_keys,
        "paper_trading_ready_50": ready_50,
        "duplicate_rows": duplicate_rows,
        "no_bet_count": no_bet_count,
        "strict_allowed_count": strict_allowed_count,
        "result_leak_count": result_leak_count,
        "replay_ok_count": replay_ok_count,
        "replay_ko_count": replay_ko_count,
        "latest_course_uid": _as_text(latest.get("course_uid"), "ND"),
        "latest_decision_id": _as_text(latest.get("decision_id"), "ND"),
        "latest_ticket_exact": _as_text(latest.get("ticket_exact"), "ND"),
        "latest_verdict_pre_course": _as_text(latest.get("verdict_pre_course"), "ND"),
        "recommendation": recommendation,
        "blocking_reason": blocking_reason,
    }


def write_paper_audit():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    audit = build_paper_audit()

    json_path = OUTPUT_DIR / "latest_paper_trading_audit.json"
    csv_path = OUTPUT_DIR / "paper_trading_audit.csv"

    json_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pd.DataFrame([audit]).to_csv(csv_path, index=False, encoding="utf-8")

    return csv_path, json_path, audit


if __name__ == "__main__":
    csv_path, json_path, audit = write_paper_audit()

    print(csv_path)
    print(json_path)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
