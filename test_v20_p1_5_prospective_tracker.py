import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from v20_p1_5_prospective_tracker import (
    build_capture,
    run_post_h5_capture,
    run_post_results_settlement,
    settle_capture,
)


class P15ProspectiveTrackerTests(unittest.TestCase):
    def _decision(self, **updates):
        decision = {
            "decision_id": "D1",
            "course_uid": "23_07_2026 - CABOURG - Prix Test",
            "created_at": "2026-07-23T08:00:00+00:00",
            "paper_candidate_ticket": "1-2",
            "paper_candidate_type": "CP_SEC",
            "input_hash": "abc",
            "engine_version": "ENGINE_1",
            "ruleset_version": "GPI_ZEturf_V19_1_4",
            "split": "TEST_LIVE",
            "calibration_version": "CAL_1",
            "leakage_check_ok": "oui",
            "split_manifest_id": "SPLIT_1",
            "leakage_audit_id": "LEAK_1",
            "replay_run_id": "REPLAY_1",
            "proof_bundle_hash": "PROOF_1",
            "p_model": "0.25",
            "ev": "0.10",
        }
        decision.update(updates)
        return decision

    def test_complete_pre_race_cp_sec_capture(self):
        capture = build_capture(
            self._decision(),
            {"heure_depart": "12:00"},
            datetime(2026, 7, 23, 9, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(capture["capture_valid_oui_non"], "oui")
        self.assertEqual(capture["proof_complete_oui_non"], "oui")
        self.assertEqual(capture["decision_before_race_oui_non"], "oui")
        self.assertEqual(capture["settlement_contract"], "CP_SEC_JUMELE_PLACE")
        self.assertEqual(capture["stake_eur"], 1.0)
        self.assertFalse(capture["can_open_strict"])
        self.assertEqual(capture["ticket_strict"], "NO_BET")

    def test_missing_proof_is_captured_but_not_statistically_complete(self):
        capture = build_capture(
            self._decision(split_manifest_id="ND"),
            {"heure_depart": "12:00"},
            datetime(2026, 7, 23, 9, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(capture["capture_valid_oui_non"], "oui")
        self.assertEqual(capture["proof_complete_oui_non"], "non")
        self.assertEqual(capture["statistical_inputs_complete_oui_non"], "non")

    def test_post_start_capture_is_rejected(self):
        capture = build_capture(
            self._decision(),
            {"heure_depart": "12:00"},
            datetime(2026, 7, 23, 10, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(capture["capture_valid_oui_non"], "non")
        self.assertIn("CAPTURE_NOT_PRE_RACE", capture["capture_errors"])

    def test_cp3_stake_and_three_pair_settlement(self):
        capture = build_capture(
            self._decision(
                paper_candidate_ticket="1-2-3",
                paper_candidate_type="CP3",
            ),
            {"heure_depart": "12:00"},
            datetime(2026, 7, 23, 9, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(capture["stake_eur"], 3.0)
        result = {
            "result_status": "OK",
            "official_reports_parse_status": "OK_CORE_REPORTS",
            "official_jumele_place_json": '{"1-2": 4, "1-3": 5, "2-3": 6}',
        }
        settlement = settle_capture(
            capture,
            result,
            datetime(2026, 7, 23, 13, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(settlement["settlement_status"], "SETTLED")
        self.assertEqual(settlement["return_eur"], 15.0)
        self.assertEqual(settlement["net_eur"], 12.0)
        self.assertEqual(settlement["roi"], 4.0)

    def test_no_bet_is_settled_without_stake(self):
        capture = build_capture(
            self._decision(
                paper_candidate_ticket="NO_BET",
                paper_candidate_type="NO_BET",
            ),
            {"heure_depart": "12:00"},
            datetime(2026, 7, 23, 9, 0, tzinfo=timezone.utc),
        )
        settlement = settle_capture(capture, {"result_status": "OK"})
        self.assertEqual(settlement["settlement_status"], "SETTLED_NO_BET")
        self.assertEqual(settlement["return_eur"], 0.0)
        self.assertIsNone(settlement["roi"])

    def test_bad_result_does_not_settle(self):
        capture = build_capture(
            self._decision(),
            {"heure_depart": "12:00"},
            datetime(2026, 7, 23, 9, 0, tzinfo=timezone.utc),
        )
        settlement = settle_capture(capture, {"result_status": "ND"})
        self.assertEqual(
            settlement["settlement_status"], "PENDING_RESULT_NOT_OK"
        )
        self.assertIsNone(settlement["return_eur"])

    def test_h5_hook_is_non_blocking(self):
        with patch(
            "v20_p1_5_prospective_tracker.run_capture_latest",
            side_effect=RuntimeError("storage unavailable"),
        ):
            response = run_post_h5_capture(
                h5_run_ok=True, capture_count=1, briefs_created=0
            )
        self.assertTrue(response["attempted"])
        self.assertFalse(response["ok"])
        self.assertEqual(response["ticket_strict"], "NO_BET")

    def test_results_hook_runs_only_after_valid_pipeline(self):
        with patch(
            "v20_p1_5_prospective_tracker.run_settle_due",
            return_value={"ok": True},
        ) as runner:
            skipped = run_post_results_settlement(results_pipeline_ok=False)
            executed = run_post_results_settlement(results_pipeline_ok=True)
        self.assertFalse(skipped["attempted"])
        self.assertTrue(executed["attempted"])
        self.assertEqual(runner.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
