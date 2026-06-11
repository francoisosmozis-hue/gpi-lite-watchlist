from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd

DATA_DIR = Path("data")
OUTPUT_DIR = DATA_DIR / "output"

PAPER_PATH = OUTPUT_DIR / "paper_trading_50_decisions.csv"

VOL_AUDIT_CSV = OUTPUT_DIR / "volatilite_audit.csv"
VOL_AUDIT_JSON = OUTPUT_DIR / "volatilite_audit.json"
LATEST_VOL_AUDIT_CSV = OUTPUT_DIR / "latest_volatilite_audit.csv"
LATEST_VOL_AUDIT_JSON = OUTPUT_DIR / "latest_volatilite_audit.json"


def _first(row, keys, default="ND"):
    for key in keys:
        try:
            value = row.get(key, "")
        except Exception:
            value = ""

        if value is None:
            continue

        text = str(value).strip()
        if text != "":
            return text

    return default


def _num(value):
    try:
        text = str(value).strip().replace(",", ".")
        if text.upper() in {"", "ND", "NONE", "NULL", "NAN"}:
            return None
        return float(text)
    except Exception:
        return None


def _norm_status(value):
    text = str(value).strip().upper()
    if text in {"OK", "DEGRADE", "KILL"}:
        return text
    if text in {"", "ND", "NONE", "NULL", "NAN"}:
        return "ND"
    return "OTHER"


def _is_oui(value):
    return str(value).strip().lower() in {"oui", "yes", "true", "1"}


def _has_volatilite_data(df):
    if df is None or df.empty:
        return False

    if "volatilite_status" not in df.columns and "VOLATILITE_STATUS" not in df.columns:
        return False

    numeric_count = 0
    status_count = 0

    for _, row in df.iterrows():
        status = _norm_status(_first(row, ["volatilite_status", "VOLATILITE_STATUS"], "ND"))
        pct = _num(_first(row, ["volatilite_top6_max_pct", "VOLATILITE_TOP6_MAX_PCT"], "ND"))

        if status in {"OK", "DEGRADE", "KILL"}:
            status_count += 1

        if pct is not None:
            numeric_count += 1

    return status_count > 0 or numeric_count > 0


def _load_decision_engine_outputs_history():
    rows = []
    seen = set()

    files = list(OUTPUT_DIR.glob("*_decision_engine_outputs.csv"))

    for file in sorted(files):
        name = file.name.lower()

        if name in {"latest_decision_engine_outputs.csv", "decision_engine_outputs.csv"}:
            continue

        try:
            df = pd.read_csv(file, dtype=str, keep_default_na=False).fillna("")
        except Exception:
            continue

        if df.empty:
            continue

        row = df.iloc[0].to_dict()

        key = (
            str(row.get("decision_id", "")).strip()
            or str(row.get("course_uid", "")).strip()
            or file.name
        )

        if key in seen:
            continue

        seen.add(key)
        rows.append(row)

    if rows:
        return pd.DataFrame(rows).fillna("")

    for file in [OUTPUT_DIR / "latest_decision_engine_outputs.csv", OUTPUT_DIR / "decision_engine_outputs.csv"]:
        if not file.exists():
            continue

        try:
            df = pd.read_csv(file, dtype=str, keep_default_na=False).fillna("")
        except Exception:
            continue

        if not df.empty:
            return df

    return pd.DataFrame()


def _load_best_source():
    paper_df = pd.DataFrame()
    paper_registry_count = 0

    if PAPER_PATH.exists():
        try:
            paper_df = pd.read_csv(PAPER_PATH, dtype=str, keep_default_na=False).fillna("")
            paper_registry_count = int(len(paper_df))
        except Exception:
            paper_df = pd.DataFrame()

    if _has_volatilite_data(paper_df):
        return paper_df, "paper_trading_50_decisions", paper_registry_count

    deo_df = _load_decision_engine_outputs_history()

    if _has_volatilite_data(deo_df):
        return deo_df, "decision_engine_outputs_history", paper_registry_count

    if not paper_df.empty:
        return paper_df, "paper_trading_without_volatilite_fields", paper_registry_count

    return pd.DataFrame(), "none", paper_registry_count


def build_volatilite_audit_from_df(df: pd.DataFrame, audit_source="ND", paper_registry_count=0) -> dict:
    rows_count = int(len(df))

    if rows_count == 0:
        return {
            "ok": False,
            "audit_version": "GPI_LITE_VOLATILITE_AUDIT_V0_2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "audit_source": audit_source,
            "paper_trading_count": 0,
            "paper_registry_count": paper_registry_count,
            "error": "aucune source exploitable",
        }

    statuses = []
    pcts = []
    cp_ok_and_kill = 0
    paper_allowed = 0
    max_item = None

    for _, row in df.iterrows():
        status = _norm_status(_first(row, ["volatilite_status", "VOLATILITE_STATUS"], "ND"))
        pct = _num(_first(row, ["volatilite_top6_max_pct", "VOLATILITE_TOP6_MAX_PCT"], "ND"))

        cp_status = str(_first(
            row,
            ["cp_sec_sg_status", "CP_SEC_SG_STATUS", "cp_sg_filter_status", "CP_SG_FILTER_STATUS"],
            "ND"
        )).strip().upper()

        paper_candidate = _first(
            row,
            ["paper_candidate_allowed", "PAPER_CANDIDATE_ALLOWED"],
            "non"
        )

        statuses.append(status)

        if pct is not None:
            pcts.append(pct)

            item = {
                "pct": pct,
                "course_uid": _first(row, ["course_uid", "COURSE_UID"], "ND"),
                "decision_id": _first(row, ["decision_id", "DECISION_ID"], "ND"),
                "numero": _first(row, ["volatilite_top6_max_numero", "VOLATILITE_TOP6_MAX_NUMERO"], "ND"),
                "cheval": _first(row, ["volatilite_top6_max_cheval", "VOLATILITE_TOP6_MAX_CHEVAL"], "ND"),
                "sg_9h": _first(row, ["volatilite_top6_sg_9h", "VOLATILITE_TOP6_SG_9H"], "ND"),
                "sg_h5": _first(row, ["volatilite_top6_sg_h5", "VOLATILITE_TOP6_SG_H5"], "ND"),
                "direction": _first(row, ["volatilite_top6_direction", "VOLATILITE_TOP6_DIRECTION"], "ND"),
                "scope": _first(row, ["volatilite_scope", "VOLATILITE_SCOPE"], "ND"),
            }

            if max_item is None or pct > max_item["pct"]:
                max_item = item

        if cp_status == "OK" and status == "KILL":
            cp_ok_and_kill += 1

        if _is_oui(paper_candidate):
            paper_allowed += 1

    s = pd.Series(pcts, dtype="float64") if pcts else pd.Series([], dtype="float64")

    audit = {
        "ok": True,
        "audit_version": "GPI_LITE_VOLATILITE_AUDIT_V0_2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "audit_source": audit_source,
        "paper_trading_count": rows_count,
        "paper_registry_count": int(paper_registry_count),
        "volatilite_fields_available": bool(len(pcts) > 0 or statuses.count("OK") + statuses.count("DEGRADE") + statuses.count("KILL") > 0),
        "volatilite_status_OK_count": int(statuses.count("OK")),
        "volatilite_status_DEGRADE_count": int(statuses.count("DEGRADE")),
        "volatilite_status_KILL_count": int(statuses.count("KILL")),
        "volatilite_status_ND_count": int(statuses.count("ND")),
        "volatilite_status_OTHER_count": int(statuses.count("OTHER")),
        "volatilite_pct_numeric_count": int(len(pcts)),
        "volatilite_coverage_rate": round(len(pcts) / rows_count, 4) if rows_count else 0,
        "volatilite_pct_mean": round(float(s.mean()), 2) if len(s) else "ND",
        "volatilite_pct_median": round(float(s.median()), 2) if len(s) else "ND",
        "volatilite_pct_max": round(float(s.max()), 2) if len(s) else "ND",
        "cp_sec_sg_ok_and_volatilite_kill_count": int(cp_ok_and_kill),
        "paper_candidate_allowed_oui_count": int(paper_allowed),
        "paper_candidate_allowed_rate": round(paper_allowed / rows_count, 4) if rows_count else 0,
        "max_volatilite_course_uid": max_item.get("course_uid", "ND") if max_item else "ND",
        "max_volatilite_decision_id": max_item.get("decision_id", "ND") if max_item else "ND",
        "max_volatilite_numero": max_item.get("numero", "ND") if max_item else "ND",
        "max_volatilite_cheval": max_item.get("cheval", "ND") if max_item else "ND",
        "max_volatilite_sg_9h": max_item.get("sg_9h", "ND") if max_item else "ND",
        "max_volatilite_sg_h5": max_item.get("sg_h5", "ND") if max_item else "ND",
        "max_volatilite_direction": max_item.get("direction", "ND") if max_item else "ND",
        "max_volatilite_scope": (
            max_item.get("scope", "ND")
            if max_item and str(max_item.get("scope", "ND")).strip().upper() not in {"", "ND", "NONE", "NULL"}
            else ("LEGACY_NO_SCOPE" if max_item else "ND")
        ),
    }

    return audit


def write_volatilite_audit():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df, audit_source, paper_registry_count = _load_best_source()
    audit = build_volatilite_audit_from_df(
        df=df,
        audit_source=audit_source,
        paper_registry_count=paper_registry_count,
    )

    audit_df = pd.DataFrame([audit])

    for path in [VOL_AUDIT_CSV, LATEST_VOL_AUDIT_CSV]:
        audit_df.to_csv(path, index=False, encoding="utf-8")

    for path in [VOL_AUDIT_JSON, LATEST_VOL_AUDIT_JSON]:
        path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    return VOL_AUDIT_CSV, VOL_AUDIT_JSON, LATEST_VOL_AUDIT_CSV, LATEST_VOL_AUDIT_JSON, audit


if __name__ == "__main__":
    csv_path, json_path, latest_csv_path, latest_json_path, audit = write_volatilite_audit()
    print("VOLATILITE_AUDIT_CREATED")
    print(csv_path)
    print(json_path)
    print(latest_csv_path)
    print(latest_json_path)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
