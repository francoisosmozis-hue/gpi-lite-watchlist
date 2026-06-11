from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

from engine.decision_engine_outputs import (
    OUTPUT_DIR,
    build_decision_engine_outputs,
)


COMPARE_FIELDS = [
    "input_hash",
    "engine_version",
    "ruleset_version",
    "verdict",
    "structure_max",
    "ticket_exact",
    "reason_codes",
    "kill_switch_codes",
]


def _as_text(value):
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    return str(value).strip()


def _read_first_row(path):
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"fichier replay introuvable : {path}")

    df = pd.read_csv(path)

    if df.empty:
        raise ValueError(f"fichier replay vide : {path}")

    return df.iloc[0].to_dict()


def _strict_allowed_after_replay(row):
    proxy_status = _as_text(row.get("proxy_status", "ND")).upper()

    proxy_ok = proxy_status not in [
        "",
        "ND",
        "PROXY_ND",
        "ROUGE",
        "PROXY_ROUGE",
        "FRAGILE",
    ]

    kill_switch_codes = _as_text(row.get("kill_switch_codes", "")).lower()
    kill_switch_ok = kill_switch_codes in ["", "aucun"]

    checks = [
        _as_text(row.get("input_hash")) not in ["", "ND"],
        _as_text(row.get("split")) not in ["", "ND"],
        _as_text(row.get("calibration_version")) not in ["", "ND"],
        _as_text(row.get("leakage_check_ok")).lower() == "true",
        _as_text(row.get("decision_before_race_oui_non")).lower() == "oui",
        proxy_ok,
        _as_text(row.get("cp_sec_vs_cp3_controlled_oui_non")).lower() == "oui",
        _as_text(row.get("backtest_pre_course_validated_oui_non")).lower() == "oui",
        _as_text(row.get("verdict")) != "NO_BET",
        _as_text(row.get("ticket_exact")) != "NO_BET",
        kill_switch_ok,
        _as_text(row.get("replay_match_oui_non")).lower() == "oui",
    ]

    return "oui" if all(checks) else "non"



def replay_decision_engine_outputs(course_uid):
    specific_path = OUTPUT_DIR / f"{course_uid}_decision_engine_outputs.csv"
    latest_path = OUTPUT_DIR / "latest_decision_engine_outputs.csv"
    canonical_path = OUTPUT_DIR / "decision_engine_outputs.csv"

    original = _read_first_row(specific_path)
    replayed = build_decision_engine_outputs(course_uid)

    mismatches = []

    for field in COMPARE_FIELDS:
        original_value = _as_text(original.get(field))
        replayed_value = _as_text(replayed.get(field))

        if original_value != replayed_value:
            mismatches.append(
                f"{field}: original={original_value} replay={replayed_value}"
            )

    replay_match = "oui" if not mismatches else "non"

    updated = dict(original)
    updated["output_replayed_oui_non"] = "oui"
    updated["replay_match_oui_non"] = replay_match
    updated["replay_mismatch_fields"] = " | ".join(mismatches)
    updated["replayed_at"] = datetime.now(timezone.utc).isoformat()
    updated["strict_allowed_oui_non"] = _strict_allowed_after_replay(updated)

    df = pd.DataFrame([updated])

    df.to_csv(specific_path, index=False, encoding="utf-8")
    df.to_csv(latest_path, index=False, encoding="utf-8")
    df.to_csv(canonical_path, index=False, encoding="utf-8")

    return specific_path, latest_path, canonical_path, replay_match
