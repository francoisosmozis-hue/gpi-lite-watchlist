import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from v20_p1_3_prospective import build_capture, run_post_h5_capture, settle_capture


class P13ProspectiveTests(unittest.TestCase):
    def test_valid_pre_race_capture_is_observation_only(self):
        decision = {
            "decision_id": "D1", "course_uid": "20_07_2026 - TEST - Prix X",
            "created_at": "2026-07-20T08:00:00+00:00",
            "paper_candidate_ticket": "1-2", "paper_candidate_type": "CP_SEC",
        }
        capture = build_capture(
            decision, {"heure_depart": "12:00"},
            datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(capture["capture_valid"])
        self.assertFalse(capture["can_open_strict"])
        self.assertEqual(capture["ticket_strict"], "NO_BET")
        self.assertEqual(capture["stake_eur"], 1)

    def test_missing_course_time_blocks_capture_with_horses(self):
        capture = build_capture({
            "decision_id": "D1", "course_uid": "20_07_2026 - TEST - Prix X",
            "created_at": "2026-07-20T08:00:00+00:00",
            "paper_candidate_ticket": "1-2",
        }, {}, datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc))
        self.assertFalse(capture["capture_valid"])
        self.assertIn("COURSE_TIME_MISSING", capture["capture_errors"])

    def test_cp3_stake_and_settlement_are_three_pairs(self):
        capture = {
            "decision_id": "D1", "course_uid": "C1", "horses": ["1", "2", "3"],
            "stake_eur": 3, "settlement_contract": "CP3_JUMELE_PLACE_3_PAIRS",
        }
        result = {"result_status": "OK", "official_jumele_place_json": '{"1-2": 4, "1-3": 5, "2-3": 6}'}
        settled = settle_capture(capture, result)
        self.assertEqual(settled["return_eur"], 15)
        self.assertEqual(settled["net_eur"], 12)

    def test_missing_independent_inputs_never_claims_statistical_completeness(self):
        capture = build_capture({
            "decision_id": "D1", "course_uid": "20_07_2026 - TEST - Prix X",
            "created_at": "2026-07-20T08:00:00+00:00", "paper_candidate_ticket": "NO_BET",
        }, {"heure_depart": "12:00"}, datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc))
        self.assertFalse(capture["statistical_inputs_complete"])

    def test_post_h5_hook_runs_once_after_productive_success(self):
        with patch("v20_p1_3_prospective.run_capture_latest", return_value={"ok": True}) as runner:
            result = run_post_h5_capture(h5_run_ok=True, capture_count=1, briefs_created=1)
        self.assertTrue(result["attempted"])
        self.assertTrue(result["ok"])
        self.assertEqual(runner.call_count, 1)
        self.assertFalse(result["can_open_strict"])

    def test_post_h5_hook_skips_idle_cycle(self):
        with patch("v20_p1_3_prospective.run_capture_latest") as runner:
            result = run_post_h5_capture(h5_run_ok=True, capture_count=0, briefs_created=0)
        self.assertFalse(result["attempted"])
        runner.assert_not_called()

    def test_post_h5_capture_failure_is_non_blocking_and_no_bet(self):
        with patch("v20_p1_3_prospective.run_capture_latest", side_effect=RuntimeError("storage unavailable")):
            result = run_post_h5_capture(h5_run_ok=True, capture_count=1, briefs_created=0)
        self.assertFalse(result["ok"])
        self.assertTrue(result["attempted"])
        self.assertEqual(result["ticket_strict"], "NO_BET")


if __name__ == "__main__":
    unittest.main(verbosity=2)
