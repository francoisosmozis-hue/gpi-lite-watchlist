"""Regression tests for the six independently reproduced counterexamples.

Uses unittest so these cases can run without substituting for missing pytest.
Pytest also collects these same six TestCase methods when available.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.synthetic_daily_pipeline import _fixtures, build_day, run, verify_bundle


class TestSyntheticDailyHardening(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gpi_regression_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def make_bundle(self, name="synthetic_bundle"):
        output = self.root / name
        run(outdir=output)
        self.assertTrue(verify_bundle(output))
        return output

    def test_empty_artifact_manifest_is_rejected(self):
        output = self.make_bundle()
        manifest_path = output / "audit_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for name in manifest["artifacts"]:
            (output / name).unlink()
        manifest["artifacts"] = {}
        manifest_path.write_text(json.dumps(manifest))
        self.assertFalse(verify_bundle(output))

    def test_removing_artifact_and_manifest_entry_is_rejected(self):
        output = self.make_bundle()
        manifest_path = output / "audit_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        del manifest["artifacts"]["decisions.json"]
        (output / "decisions.json").unlink()
        manifest_path.write_text(json.dumps(manifest))
        self.assertFalse(verify_bundle(output))

    def test_inconsistent_manifest_metadata_is_rejected(self):
        output = self.make_bundle()
        manifest_path = output / "audit_manifest.json"
        original = json.loads(manifest_path.read_text())
        for key, bad in [("strict_allowed", True), ("strict_allowed", 0),
                         ("real_bet_allowed", True), ("governance_transition", "UNAUTHORIZED"),
                         ("scenario_version", "UNKNOWN"), ("source_commit_base", "0" * 40)]:
            with self.subTest(field=key, value=bad):
                manifest = dict(original)
                manifest[key] = bad
                manifest_path.write_text(json.dumps(manifest))
                self.assertFalse(verify_bundle(output))

    def test_parent_traversal_cannot_reach_operational_path(self):
        (self.root / "data" / "x").mkdir(parents=True)
        output = self.root / "data" / "x" / ".." / "output" / "synthetic_escape"
        with self.assertRaises(ValueError):
            run(outdir=output)
        self.assertFalse((self.root / "data" / "output").exists())

    def test_departure_must_match_h30_and_h5_snapshots(self):
        for departure in ["14:00:00", "14:55:00", "15:01:00", "bad-time"]:
            with self.subTest(departure=departure):
                fixtures = _fixtures()
                fixtures[0]["race_start"] = departure
                with self.assertRaises(ValueError):
                    build_day(fixture_override=fixtures)

    def test_fictional_arrival_requires_three_distinct_present_runners(self):
        for arrival in [[99, 99, -1], [1, 1, 2], [1, 2], [True, 2, 3],
                        [1.0, 2, 3], "1,2,3", None]:
            with self.subTest(arrival=arrival):
                fixtures = _fixtures()
                fixtures[0]["fictional_arrival"] = arrival
                with self.assertRaises(ValueError):
                    build_day(fixture_override=fixtures)


if __name__ == "__main__":
    unittest.main()
