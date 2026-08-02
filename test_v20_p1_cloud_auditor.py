import unittest

from v20_p1_cloud_auditor import _csv_rows, build_audit, normalize_document


PROOFS = {
    "evidence_level": "P1_STATISTICAL_EDGE",
    "split_manifest_id": "split-1",
    "leakage_audit_id": "leak-1",
    "replay_run_id": "replay-1",
    "proof_bundle_hash": "abc123",
    "decision_at_utc": "2026-07-19T10:00:00+00:00",
    "start_at_utc": "2026-07-19T11:00:00+00:00",
    "frozen_odds": 4.0,
    "p_model": 0.30,
}


class V20P1CloudAuditorTests(unittest.TestCase):
    def test_self_declared_or_missing_proofs_never_demonstrate_edge(self):
        row = normalize_document({
            "ticket_strict": "1-2",
            "stake_eur": 1,
            "return_eur": 0,
            "evidence_level": "P1_STATISTICAL_EDGE",
            "leakage_check_ok": True,
        })
        self.assertFalse(row["statistical_edge_demonstrated"])
        self.assertTrue(row["ticket_without_edge"])
        self.assertTrue(row["avoidable_loss"])

    def test_cp3_uses_three_euros_and_standard_uses_one(self):
        cp3 = normalize_document({**PROOFS, "ticket_strict": "1-2-3", "ticket_type": "CP3", "stake_eur": 3})
        standard = normalize_document({**PROOFS, "ticket_strict": "1-2", "ticket_type": "CP_SEC", "stake_eur": 1})
        self.assertTrue(cp3["stake_policy_ok"])
        self.assertTrue(standard["stake_policy_ok"])
        self.assertEqual(cp3["expected_stake_eur"], 3)
        self.assertEqual(standard["expected_stake_eur"], 1)

    def test_false_no_bet_requires_independent_pre_race_p1(self):
        unverifiable = normalize_document({"ticket_strict": "NO_BET", "return_eur": 8})
        proven = normalize_document({**PROOFS, "ticket_strict": "NO_BET", "return_eur": 8})
        self.assertFalse(unverifiable["false_no_bet"])
        self.assertTrue(proven["false_no_bet"])

    def test_structural_ex_post_is_not_live_evidence(self):
        row = normalize_document({
            **PROOFS,
            "ticket_strict": "1-2",
            "decision_pre_course": "STRUCTURAL_EX_POST_TOP3_NO_LIVE_DECISION",
            "mode_backtest": "STRUCTURAL_PAYOFF_AUDIT",
        })
        self.assertTrue(row["structural_ex_post_only"])

    def test_report_is_audit_only_and_never_opens_strict(self):
        report = build_audit([("gs://test/a.json", {**PROOFS, "ticket_strict": "1-2", "stake_eur": 1, "return_eur": 5})])
        self.assertEqual(report["mode"], "AUDIT_ONLY")
        self.assertFalse(report["can_open_strict"])
        self.assertEqual(report["ticket_strict"], "NO_BET")
        self.assertEqual(report["bankroll_eur"], 1000)

    def test_decision_and_result_are_joined_only_by_course_uid(self):
        decision = {**PROOFS, "course_uid": "C1", "ticket_strict": "1-2", "stake_eur": 1}
        result = {"course_uid": "C1", "return_eur": 5}
        report = build_audit([("decision.json", decision), ("result.json", result)])
        self.assertEqual(report["documents_scanned"], 2)
        self.assertEqual(report["courses_consolidated"], 1)
        self.assertEqual(report["overall"]["net_eur"], 4)

    def test_real_paper_csv_aliases_are_normalized(self):
        row = normalize_document({
            "decision_id": "D1",
            "course_uid": "C1",
            "ticket_exact": "1-2-3",
            "structure_retenue": "CP3",
            "mise_paper": "3",
            "retour_paper": "12",
            "gain_net_paper": "9",
        })
        self.assertEqual(row["ticket_type"], "CP3")
        self.assertEqual(row["stake_eur"], 3)
        self.assertEqual(row["return_eur"], 12)
        self.assertEqual(row["net_eur"], 9)

    def test_csv_reader_preserves_json_report_columns(self):
        rows = _csv_rows(
            'course_uid,official_simple_gagnant_json\n'
            'C1,"{""13"": 8.2}"\n'
        )
        self.assertEqual(rows[0]["course_uid"], "C1")
        self.assertIn('"13"', rows[0]["official_simple_gagnant_json"])

    def test_decision_id_deduplicates_aliases_and_course_result_enriches(self):
        decision = {
            "decision_id": "D1", "course_uid": "C1", "ticket_exact": "1-2",
            "mise_paper": 1,
        }
        result = {"course_uid": "C1", "return_eur": 5, "result_status": "OK"}
        report = build_audit([
            ("alias.csv", decision),
            ("archive.csv", decision),
            ("result.csv", result),
        ])
        self.assertEqual(report["courses_consolidated"], 1)
        self.assertEqual(report["overall"]["settled_tickets"], 1)
        self.assertEqual(report["overall"]["net_eur"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
