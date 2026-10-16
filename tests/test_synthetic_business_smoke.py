"""Tests of the actual watchlist/H5 engines using clearly synthetic data."""
import json

from tools.synthetic_business_smoke import (
    COURSE_UID, FIXTURE_ID, run_synthetic_business_smoke,
)


def test_watchlist_then_h5_is_fail_closed():
    result = run_synthetic_business_smoke()
    assert result["fixture_id"] == FIXTURE_ID
    assert result["course_uid"] == COURSE_UID
    assert result["synthetic_input_rows"] == 10
    assert result["watchlist"]["partants_reels"] == 10
    assert result["h5"]["verdict"] == "NO_BET"
    assert result["h5"]["ticket_exact"] == "NO_BET"
    assert result["h5"]["strict_allowed_oui_non"] == "non"
    assert result["calibration_proven"] is False
    assert result["prospective_proven"] is False


def test_incomplete_synthetic_odds_kill_switch_stays_closed():
    result = run_synthetic_business_smoke(incomplete_odds=True)
    assert "SG_INCOMPLETES" in result["watchlist"]["kill_switchs_matin"]
    assert result["watchlist"]["decision_matin"] == "NO_BET_DIRECT"
    assert result["h5"]["verdict"] == "NO_BET"
    assert result["real_bet_allowed"] is False


def test_synthetic_is_deterministic_and_creates_no_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = run_synthetic_business_smoke()
    second = run_synthetic_business_smoke()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert not list(tmp_path.iterdir())


def test_fixture_changes_its_input_hash():
    first = run_synthetic_business_smoke()
    second = run_synthetic_business_smoke(incomplete_odds=True)
    assert first["input_sha256"] != second["input_sha256"]
    assert second["proof_class"] == "SYNTHETIC_REGRESSION_ONLY"
