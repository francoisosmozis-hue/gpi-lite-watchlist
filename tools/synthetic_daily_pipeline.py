"""Reproducible OFFLINE demonstration of one entirely fictional GPI racing day.

Only uses existing GPI engines. Not a prospective capture, credential, bet,
calibration, paper result, or operational proof. No network/Cloud calls.
"""
from __future__ import annotations

import argparse
import csv
from hashlib import sha256
from html import escape
from io import StringIO
import json
from pathlib import Path
from typing import Any

FIXTURE_VERSION = "SYNTHETIC_DAILY_V1"
PROOF_CLASS = "SYNTHETIC_DEVELOPMENT_ONLY"
DAY = "2030-01-02"  # Fictional scenario; NOT an observed race date.
SOURCE_COMMIT_BASE = "13f6fd38f5fd0b71a4823a45d310bcc0ad735b3e"
STAGES = ("09H", "H_MINUS_30", "H_MINUS_5")
TIMES = {"09H": "09:00:00", "H_MINUS_30": "14:30:00", "H_MINUS_5": "14:55:00"}


def _canonical(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _sha(obj: Any) -> str:
    return sha256(_canonical(obj)).hexdigest()


def _snapshot(race: str, stage: str, odds: list[float | None]) -> dict:
    return {"source_kind": PROOF_CLASS, "race_uid": race, "stage": stage,
            "fixture_timestamp": f"{DAY}T{TIMES[stage]}+01:00",
            "runners": [{"numero": i+1, "cheval": f"FICTIF_{i+1:02d}", "SG": odds[i],
                         "statut": "PARTANT", "is_np": 0} for i in range(10)]}


def _fixtures() -> list[dict]:
    a = [2.9, 3.6, 4.2, 5.5, 6.3, 7.1, 8.2, 10.0, 12.0, 15.0]
    b = [3.5, 4.0, 5.0, 6.3, 7.0, 8.5, 10.0, 12.0, 15.0, 18.0]
    return [
        {"race_uid": "SYNTHETIC_R1C1", "race_start": "15:00:00",
         "snapshots": [_snapshot("SYNTHETIC_R1C1", "09H", a),
                       _snapshot("SYNTHETIC_R1C1", "H_MINUS_30", [round(x*1.02, 3) for x in a]),
                       _snapshot("SYNTHETIC_R1C1", "H_MINUS_5", [round(x*1.03, 3) for x in a])],
         "fictional_arrival": [4, 2, 1]},
        {"race_uid": "SYNTHETIC_R1C2", "race_start": "15:00:00",
         "snapshots": [_snapshot("SYNTHETIC_R1C2", "09H", b),
                       _snapshot("SYNTHETIC_R1C2", "H_MINUS_30", [3.7, *b[1:]]),
                       _snapshot("SYNTHETIC_R1C2", "H_MINUS_5", [9.8, 4.2, None, None, None, None, None, None, None, None])],
         "fictional_arrival": [3, 1, 8]},
    ]


def _validate_fixture(race: dict) -> None:
    if not race["race_uid"].startswith("SYNTHETIC_"):
        raise ValueError("SYNTHETIC_ONLY_RACE_UID_REQUIRED")
    snaps = race["snapshots"]
    if [s["stage"] for s in snaps] != list(STAGES):
        raise ValueError("INVALID_SNAPSHOT_SEQUENCE")
    for s in snaps:
        if s["source_kind"] != PROOF_CLASS or s["race_uid"] != race["race_uid"]:
            raise ValueError("SOURCE_PROVENANCE_MISMATCH")
        if s["fixture_timestamp"] != f"{DAY}T{TIMES[s['stage']]}+01:00":
            raise ValueError("NONDETERMINISTIC_SNAPSHOT_TIME")
        numbers = [r["numero"] for r in s["runners"]]
        if numbers != list(range(1, 11)) or any(not r["cheval"].startswith("FICTIF_") for r in s["runners"]):
            raise ValueError("INVALID_FICTIONAL_RUNNERS")
        for r in s["runners"]:
            sg = r["SG"]
            if sg is not None and (not isinstance(sg, (int, float)) or isinstance(sg, bool) or sg <= 1):
                raise ValueError("INVALID_SYNTHETIC_ODDS")


def _movements(prev: dict, curr: dict, classify_status, classify_direction) -> list[dict]:
    old = {row["numero"]: row["SG"] for row in prev["runners"]}
    rows = []
    for now in curr["runners"]:
        previous, current = old[now["numero"]], now["SG"]
        change = None if previous is None or current is None else round(abs(current-previous)/previous, 8)
        rows.append({"race_uid": curr["race_uid"], "numero": now["numero"],
                     "previous_stage": prev["stage"], "current_stage": curr["stage"],
                     "previous_sg": previous, "current_sg": current,
                     "variation_pct": change,
                     "statut_marche": classify_status(change),
                     "direction": classify_direction(previous, current)})
    return rows


def build_day(*, fixture_override: list[dict] | None = None) -> dict:
    # Imports happen only on an explicitly requested synthetic run; these are the
    # existing GPI functions, not replacements of the betting/decision engines.
    import pandas as pd
    from engine.gpi_lite import build_watchlist_row
    from engine.h5_decision import evaluate_h5_decision_from_brief
    from engine.odds.odds_movement_builder import classify_market_status, classify_direction

    fixtures = _fixtures() if fixture_override is None else fixture_override
    if not fixtures or len({x["race_uid"] for x in fixtures}) != len(fixtures):
        raise ValueError("INVALID_SYNTHETIC_RACE_SET")
    program, snapshots, watchlist, movements, decisions, settlements = [], [], [], [], [], []
    for race in fixtures:
        _validate_fixture(race)
        uid = race["race_uid"]
        program.append({"race_uid": uid, "date": DAY, "start": race["race_start"],
                        "source_kind": PROOF_CLASS})
        snaps = race["snapshots"]
        snapshots.extend(snaps)
        morning = build_watchlist_row(pd.DataFrame(snaps[0]["runners"]), uid)
        watchlist.append({"race_uid": uid, "source_kind": PROOF_CLASS,
                          "stage": "09H", "engine_output": morning})
        first = _movements(snaps[0], snaps[1], classify_market_status, classify_direction)
        last = _movements(snaps[1], snaps[2], classify_market_status, classify_direction)
        movements += first + last
        unstable = any(r["statut_marche"] == "KILL_SWITCH" for r in last)
        missing = any(r["SG"] is None for r in snaps[2]["runners"])
        # All real source validation is deliberately ND; no pre-race claim.
        brief = {"input_hash": "SYNTHETIC_" + _sha(snaps), "sg_h5_ok": "ND" if missing else "OK",
                 "np_ok": "ND", "statut_ok": "ND",
                 "decision_before_race_oui_non": "non",
                 "segment_roi": "ND", "edge_status": "EDGE_ABSENT", "proxy_status": "ND",
                 "volatilite_status": "KILL" if unstable else "ND",
                 "kill_switch_codes": "SYNTHETIC_VOLATILITY_KILL" if unstable else "SYNTHETIC_ONLY"}
        h5 = evaluate_h5_decision_from_brief(brief)
        if h5.get("verdict") != "NO_BET" or h5.get("ticket_exact") != "NO_BET" or h5.get("strict_allowed_oui_non") != "non":
            raise RuntimeError("ENGINE_BET_GATE_BREACH_FAIL_CLOSED")
        decisions.append({"race_uid": uid, "source_kind": PROOF_CLASS,
                          "stage": "H_MINUS_5", "h30_snapshot_sha256": _sha(snaps[1]),
                          "h5_snapshot_sha256": _sha(snaps[2]), "engine_output": h5,
                          "stake_eur": 0, "ticket_real": "NO_BET"})
        settlements.append({"race_uid": uid, "source_kind": PROOF_CLASS,
                            "fictional_arrival": race["fictional_arrival"],
                            "stakes_eur": 0, "payout_eur": 0, "bet_placed": False,
                            "roi": None, "roi_status": "NOT_APPLICABLE_NO_BET"})
    return {"scenario_version": FIXTURE_VERSION, "proof_class": PROOF_CLASS,
            "operational_proof": False, "prospective_proof": False,
            "daily_pipeline_qualified": False, "real_bet_allowed": False,
            "strict_allowed": False, "governance_transition": "NONE",
            "program": program, "snapshots": snapshots, "watchlist": watchlist,
            "movements": movements, "decisions": decisions,
            "fictional_results": settlements}


def _csv(rows: list[dict], fields: list[str]) -> bytes:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in fields})
    return output.getvalue().encode("utf-8")


def artifacts(day: dict) -> dict[str, bytes]:
    files = {f"{part}.json": _canonical(day[part]) for part in
             ("program", "snapshots", "watchlist", "movements", "decisions", "fictional_results")}
    rows = [{"race_uid": x["race_uid"], "stage": x["stage"],
             "verdict": x["engine_output"]["verdict"], "ticket_real": x["ticket_real"],
             "stake_eur": x["stake_eur"], "proof_class": PROOF_CLASS}
            for x in day["decisions"]]
    files["tracking.csv"] = _csv(rows, ["race_uid", "stage", "verdict", "ticket_real", "stake_eur", "proof_class"])
    summary = {key: value for key, value in day.items() if key not in
               ("program", "snapshots", "watchlist", "movements", "decisions", "fictional_results")}
    summary.update({"race_count": len(day["program"]), "snapshot_count": len(day["snapshots"]),
                    "h30_movement_count": len(day["movements"])//2,
                    "h5_movement_count": len(day["movements"])//2,
                    "all_no_bet": all(x["engine_output"]["verdict"] == "NO_BET" for x in day["decisions"])})
    files["summary.json"] = _canonical(summary)
    body = "".join(f"<tr><td>{escape(x['race_uid'])}</td><td>H-5</td><td>NO_BET</td><td>0 €</td></tr>" for x in day["decisions"])
    html = ("<!doctype html><html lang='fr'><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>GPI — Démonstration fictive</title>"
            "<style>body{font:16px system-ui;max-width:760px;margin:2em auto;padding:1em}"
            "table{border-collapse:collapse;width:100%}td,th{padding:.7em;border:1px solid #aaa}</style>"
            "<h1>GPI LITE — DÉMONSTRATION SYNTHÉTIQUE</h1><p>Aucune donnée réelle, "
            "aucune preuve pré-course, aucun pari autorisé.</p><table><tr><th>Course fictive</th>"
            f"<th>Étape</th><th>Décision</th><th>Mise</th></tr>{body}</table></html>")
    files["dashboard.html"] = html.encode("utf-8")
    return files


def verify_bundle(output: Path) -> bool:
    manifest_file = output / "audit_manifest.json"
    if not manifest_file.is_file() or manifest_file.is_symlink():
        return False
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    expected = manifest.get("artifacts", {})
    if manifest.get("proof_class") != PROOF_CLASS or not isinstance(expected, dict):
        return False
    if set(expected) != {p.name for p in output.iterdir()} - {"audit_manifest.json"}:
        return False
    for name, sha in expected.items():
        if not isinstance(name, str) or "/" in name or ".." in name or "\\" in name:
            return False
        p = output / name
        if not p.is_file() or p.is_symlink() or sha256(p.read_bytes()).hexdigest() != sha:
            return False
    return True


def run(*, outdir: Path) -> dict:
    if not outdir.name.startswith("synthetic_"):
        raise ValueError("OUTPUT_DIRECTORY_MUST_START_SYNTHETIC_")
    if outdir.exists() or outdir.is_symlink():
        raise FileExistsError("OUTPUT_EXISTS_REFUSE_OVERWRITE")
    if any(parent.is_symlink() for parent in outdir.parents):
        raise ValueError("SYMLINK_OUTPUT_PARENT_FORBIDDEN")
    path_parts = tuple(outdir.parts)
    protected = (("data", "output"), ("data", "morning"), ("data", "h5"),
                 ("data", "results"), ("sources", "calibration"), ("project_sources",))
    if any(path_parts[i:i+len(prefix)] == prefix
           for prefix in protected for i in range(len(path_parts)-len(prefix)+1)):
        raise ValueError("OPERATIONAL_OUTPUT_PATH_FORBIDDEN")
    day = build_day()
    files = artifacts(day)
    manifest = {"proof_class": PROOF_CLASS, "scenario_version": FIXTURE_VERSION,
                "source_commit_base": SOURCE_COMMIT_BASE,
                "artifacts": {name: sha256(content).hexdigest() for name, content in sorted(files.items())},
                "strict_allowed": False, "real_bet_allowed": False,
                "governance_transition": "NONE"}
    # Check before writing anything; never overwrite a pre-existing directory.
    outdir.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        (outdir / name).write_bytes(content)
    (outdir / "audit_manifest.json").write_bytes(_canonical(manifest))
    if not verify_bundle(outdir):
        raise RuntimeError("ARTIFACT_MANIFEST_VERIFICATION_FAILED")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Strictly synthetic GPI full-day integration demo; NO BET")
    parser.add_argument("--outdir", type=Path, default=Path("data/synthetic_daily_demo"))
    args = parser.parse_args()
    result = run(outdir=args.outdir)
    print(json.dumps({"ok": True, "output": str(args.outdir),
                      "proof_class": PROOF_CLASS, "file_count": len(result["artifacts"]),
                      "strict_allowed": False, "real_bet_allowed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
