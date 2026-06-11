from pathlib import Path
from datetime import datetime, timezone
import pandas as pd


OUTPUT_DIR = Path("data/output")
CONFIG_DIR = Path("data/config")

SPLIT_COLUMNS = [
    "course_uid",
    "split",
    "calibration_version",
    "leakage_check_ok",
    "decision_before_race_oui_non",
    "split_source",
    "created_at",
    "updated_at",
]


def _as_text(value, default=""):
    if value is None:
        return default

    text = str(value).strip()

    if text == "" or text.lower() in ["nan", "none"]:
        return default

    return text


def _bool_text(value, default="false"):
    text = _as_text(value, default).lower()

    if text in ["true", "1", "yes", "oui"]:
        return "true"

    if text in ["false", "0", "no", "non"]:
        return "false"

    return default


def _decision_before_text(value, default="non"):
    text = _as_text(value, default).lower()

    if text in ["oui", "yes", "true", "1"]:
        return "oui"

    if text in ["non", "no", "false", "0"]:
        return "non"

    return default


def _normalize_df(df):
    for col in SPLIT_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df[SPLIT_COLUMNS].copy()

    for col in SPLIT_COLUMNS:
        df[col] = df[col].astype(str).fillna("").str.strip()

    df["leakage_check_ok"] = df["leakage_check_ok"].apply(_bool_text)
    df["decision_before_race_oui_non"] = df["decision_before_race_oui_non"].apply(_decision_before_text)

    return df


def _split_paths():
    return [
        OUTPUT_DIR / "train_test_split.csv",
        CONFIG_DIR / "train_test_split.csv",
        Path("train_test_split.csv"),
    ]


def _result_file_exists(course_uid):
    candidates = [
        OUTPUT_DIR / f"{course_uid}_result_input.csv",
        OUTPUT_DIR / f"{course_uid}_result.csv",
        OUTPUT_DIR / f"{course_uid}_audit.csv",
        OUTPUT_DIR / "result_input.csv",
    ]

    for path in candidates:
        if path.exists():
            return True

    return False


def _default_row(course_uid):
    now = datetime.now(timezone.utc).isoformat()
    result_exists = _result_file_exists(course_uid)

    return {
        "course_uid": course_uid,
        "split": "TEST_LIVE",
        "calibration_version": "GPI_CALIBRATION_V0_9_2",
        "leakage_check_ok": "false" if result_exists else "true",
        "decision_before_race_oui_non": "non" if result_exists else "oui",
        "split_source": "AUTO_TEST_LIVE_DEFAULT",
        "created_at": now,
        "updated_at": now,
    }


def _load_existing_split():
    for path in _split_paths():
        if path.exists():
            try:
                df = pd.read_csv(path, dtype=str, keep_default_na=False)
            except Exception:
                continue

            df = _normalize_df(df)
            return df, path

    return pd.DataFrame(columns=SPLIT_COLUMNS), OUTPUT_DIR / "train_test_split.csv"


def ensure_split_for_course(course_uid):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df, source_path = _load_existing_split()

    if df.empty:
        df = pd.DataFrame(columns=SPLIT_COLUMNS)

    mask = df["course_uid"].astype(str) == str(course_uid)

    if mask.any():
        row = df[mask].iloc[0].to_dict()
    else:
        row = _default_row(course_uid)
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    df = _normalize_df(df)

    out_path = OUTPUT_DIR / "train_test_split.csv"
    df.to_csv(out_path, index=False, encoding="utf-8")

    mask = df["course_uid"].astype(str) == str(course_uid)
    row = df[mask].iloc[0].to_dict()

    return row, out_path


def get_split_info(course_uid):
    row, path = ensure_split_for_course(course_uid)

    split = _as_text(row.get("split"), "ND")
    calibration_version = _as_text(row.get("calibration_version"), "ND")
    leakage_check_ok = _bool_text(row.get("leakage_check_ok"), "false")
    decision_before = _decision_before_text(row.get("decision_before_race_oui_non"), "non")
    split_source = _as_text(row.get("split_source"), "ND")

    return {
        "split": split,
        "calibration_version": calibration_version,
        "leakage_check_ok": leakage_check_ok,
        "decision_before_race_oui_non": decision_before,
        "split_source": split_source,
        "split_file": str(path).replace("\\", "/"),
    }
