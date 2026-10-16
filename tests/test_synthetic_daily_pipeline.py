"""Offline integration checks; on checkout they import the *actual* GPI engines."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import socket

import pytest

from tools.synthetic_daily_pipeline import (
    PROOF_CLASS, STAGES, _fixtures, _validate_fixture, artifacts,
    build_day, run, verify_bundle,
)


def test_complete_fictional_day_has_all_stages():
    day = build_day()
    assert len(day["program"]) == 2
    assert len(day["snapshots"]) == 6
    assert len(day["watchlist"]) == len(day["decisions"]) == len(day["fictional_results"]) == 2
    assert len(day["movements"]) == 40
    for uid in ["SYNTHETIC_R1C1", "SYNTHETIC_R1C2"]:
        snaps = [s for s in day["snapshots"] if s["race_uid"] == uid]
        assert [s["stage"] for s in snaps] == list(STAGES)
        assert all(s["source_kind"] == PROOF_CLASS for s in snaps)


def test_existing_engine_outputs_forbidden_to_place_bet():
    day = build_day()
    assert all(d["engine_output"]["verdict"] == "NO_BET" for d in day["decisions"])
    assert all(d["engine_output"]["ticket_exact"] == "NO_BET" for d in day["decisions"])
    assert all(d["engine_output"]["strict_allowed_oui_non"] == "non" for d in day["decisions"])
    assert all(d["stake_eur"] == 0 and d["ticket_real"] == "NO_BET" for d in day["decisions"])
    assert day["strict_allowed"] is False and day["real_bet_allowed"] is False
    assert day["governance_transition"] == "NONE"


def test_synthetic_results_cannot_count_as_real_settlement():
    results = build_day()["fictional_results"]
    assert all(row["stakes_eur"] == row["payout_eur"] == 0 for row in results)
    assert all(row["bet_placed"] is False and row["roi"] is None for row in results)
    assert all(row["roi_status"] == "NOT_APPLICABLE_NO_BET" for row in results)


def test_two_runs_byte_identical(tmp_path):
    run(outdir=tmp_path / "synthetic_one")
    run(outdir=tmp_path / "synthetic_two")
    a = tmp_path / "synthetic_one"
    b = tmp_path / "synthetic_two"
    assert {p.name: p.read_bytes() for p in a.iterdir()} == {p.name: p.read_bytes() for p in b.iterdir()}
    assert verify_bundle(a) and verify_bundle(b)


def test_manifest_refuses_tampering(tmp_path):
    out = tmp_path / "synthetic_test"
    run(outdir=out)
    assert verify_bundle(out)
    p = out / "decisions.json"
    p.write_bytes(p.read_bytes() + b"\n")
    assert not verify_bundle(out)


def test_output_cannot_overwrite_preexisting_files(tmp_path):
    out = tmp_path / "synthetic_existing"
    out.mkdir()
    sentinel = out / "sentinel.txt"
    sentinel.write_text("not touched")
    with pytest.raises(FileExistsError):
        run(outdir=out)
    assert sentinel.read_text() == "not touched"


def test_output_requires_synthetic_namespace(tmp_path):
    with pytest.raises(ValueError, match="SYNTHETIC"):
        run(outdir=tmp_path / "data_output")
    assert not (tmp_path / "data_output").exists()


def test_browser_dashboard_explicitly_marks_fictional(tmp_path):
    out = tmp_path / "synthetic_dashboard"
    run(outdir=out)
    html = (out / "dashboard.html").read_text()
    assert "DÉMONSTRATION SYNTHÉTIQUE" in html
    assert "aucun pari autorisé" in html
    assert html.count("NO_BET") == 2
    assert json.loads((out / "summary.json").read_text())["all_no_bet"] is True


def test_no_socket_calls_in_day_generation(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("NETWORK_OR_SOCKET_ATTEMPT_FORBIDDEN")
    monkeypatch.setattr(socket, "socket", forbidden)
    run(outdir=tmp_path / "synthetic_nosocket")


def test_bad_fixture_race_provenance_fails():
    fixtures = _fixtures()
    fixtures[0]["race_uid"] = "R1C1_REAL"
    with pytest.raises(ValueError, match="SYNTHETIC"):
        build_day(fixture_override=fixtures)


def test_missing_stage_fails():
    fixtures = _fixtures()
    fixtures[0]["snapshots"] = fixtures[0]["snapshots"][:2]
    with pytest.raises(ValueError, match="SNAPSHOT_SEQUENCE"):
        build_day(fixture_override=fixtures)


def test_corrupted_runner_number_fails():
    fixtures = _fixtures()
    fixtures[0]["snapshots"][1]["runners"][0]["numero"] = 15
    with pytest.raises(ValueError, match="FICTIONAL_RUNNERS"):
        build_day(fixture_override=fixtures)


def test_missing_h5_odds_does_not_open_gate():
    day = build_day()
    bad = next(d for d in day["decisions"] if d["race_uid"] == "SYNTHETIC_R1C2")
    assert bad["engine_output"]["verdict"] == "NO_BET"
    assert any(m["statut_marche"] == "KILL_SWITCH" for m in day["movements"] if m["race_uid"] == "SYNTHETIC_R1C2")


def test_malicious_engine_output_is_rejected(monkeypatch):
    import engine.h5_decision
    monkeypatch.setattr(engine.h5_decision, "evaluate_h5_decision_from_brief",
                        lambda brief: {"verdict": "BET", "ticket_exact": "SP 1", "strict_allowed_oui_non": "oui"})
    with pytest.raises(RuntimeError, match="FAIL_CLOSED"):
        build_day()


def test_artifacts_contain_no_real_credentials():
    text = b"".join(artifacts(build_day()).values())
    assert b"SYNTHETIC" in text
    for forbidden in (b"GPI_API_KEY", b"https://", b"gs://", b"REAL_BET_ALLOWED=true"):
        assert forbidden not in text


def test_refuse_operational_output_directories(tmp_path):
    with pytest.raises(ValueError, match="OPERATIONAL_OUTPUT"):
        run(outdir=tmp_path / "data" / "output" / "synthetic_bad")
    assert not (tmp_path / "data" / "output" / "synthetic_bad").exists()


def test_refuse_symlink_parent(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="SYMLINK_OUTPUT_PARENT"):
        run(outdir=link / "synthetic_bad")


def test_post_race_arrival_cannot_change_pre_race_decision():
    normal = build_day()
    changed = _fixtures()
    changed[0]["fictional_arrival"] = [9, 8, 7]
    variant = build_day(fixture_override=changed)
    assert normal["decisions"] == variant["decisions"]
    assert normal["watchlist"] == variant["watchlist"]
    assert normal["fictional_results"] != variant["fictional_results"]


def test_invalid_snapshot_provenance_refused_before_outputs(tmp_path):
    fixtures = _fixtures()
    fixtures[0]["snapshots"][1]["source_kind"] = "UNAUTHORIZED_REAL_DATA"
    with pytest.raises(ValueError, match="SOURCE_PROVENANCE"):
        build_day(fixture_override=fixtures)
