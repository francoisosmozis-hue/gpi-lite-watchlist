import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.archive_h5_history import classify_archive_status


def test_classify_archive_status_live_valid_pre_race():
    result = classify_archive_status(
        now_paris="2026-07-04T10:00:00+02:00",
        depart_dt="2026-07-04T10:30:00+02:00",
        decision_before_race_oui_non="oui",
        result_known_at_decision="non",
        input_hash="hash_test",
        engine_version="ENGINE_TEST",
        ruleset_version="RULESET_TEST",
    )

    assert result["status"] == "LIVE_VALID_PRE_RACE"
    assert result["reason"] == "LIVE_VALID_PRE_RACE"
    assert result["live_eligible_oui_non"] == "oui"
    assert result["retro_audit_only_oui_non"] == "non"


def test_classify_archive_status_after_race_is_retro_audit_only():
    result = classify_archive_status(
        now_paris="2026-07-04T10:31:00+02:00",
        depart_dt="2026-07-04T10:30:00+02:00",
        decision_before_race_oui_non="oui",
        result_known_at_decision="non",
        input_hash="hash_test",
        engine_version="ENGINE_TEST",
        ruleset_version="RULESET_TEST",
    )

    assert result["status"] == "RETRO_AUDIT_ONLY"
    assert result["reason"] == "REFUSED_AFTER_RACE"
    assert result["live_eligible_oui_non"] == "non"
    assert result["retro_audit_only_oui_non"] == "oui"


def test_classify_archive_status_result_known_blocks_live():
    result = classify_archive_status(
        now_paris="2026-07-04T10:00:00+02:00",
        depart_dt="2026-07-04T10:30:00+02:00",
        decision_before_race_oui_non="oui",
        result_known_at_decision="oui",
        input_hash="hash_test",
        engine_version="ENGINE_TEST",
        ruleset_version="RULESET_TEST",
    )

    assert result["status"] == "REFUSED_RESULT_RISK"
    assert result["reason"] == "REFUSED_RESULT_RISK"
    assert result["live_eligible_oui_non"] == "non"
    assert result["retro_audit_only_oui_non"] == "non"
