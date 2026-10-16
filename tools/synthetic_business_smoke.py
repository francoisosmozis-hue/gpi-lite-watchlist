"""Strictly synthetic in-memory watchlist -> H5 decision smoke.

NOT an ingestion test, calibration, paper trade, prospective snapshot or
proof of profitability. This module never writes artifacts or accesses
network, Cloud Storage, schedulers, or real racing data.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd

from engine.gpi_lite import build_watchlist_row
from engine.h5_decision import evaluate_h5_decision_from_brief


FIXTURE_ID = "SYNTHETIC_GPI_LOCAL_SMOKE_V1"
COURSE_UID = "SYNTHETIC_R1C1_NO_REAL_EVENT"


def run_synthetic_business_smoke(*, incomplete_odds: bool = False) -> dict[str, Any]:
    records = [
        {"numero": i, "cheval": f"FICTIF_{i:02d}", "SG": float(i + 1),
         "statut": "PARTANT", "is_np": 0}
        for i in range(1, 11)
    ]
    if incomplete_odds:
        for row in records[3:]:
            row["SG"] = None

    payload = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    synthetic_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    watch = build_watchlist_row(pd.DataFrame(records), COURSE_UID)

    # Dates, genuine snapshots, non-partants, odds provenance, calibration
    # and pre-race timeliness are intentionally NOT certified.
    brief = {
        "input_hash": f"SYNTHETIC_{synthetic_hash}",
        "sg_h5_ok": "ND",
        "np_ok": "ND",
        "statut_ok": "ND",
        "decision_before_race_oui_non": "non",
        "segment_roi": "ND",
        "edge_status": "EDGE_ABSENT",
        "proxy_status": "ND",
        "volatilite_status": "ND",
        "kill_switch_codes": "SYNTHETIC_INPUT_ONLY",
    }
    h5 = evaluate_h5_decision_from_brief(brief)
    if (
        h5.get("verdict") != "NO_BET"
        or h5.get("ticket_exact") != "NO_BET"
        or h5.get("strict_allowed_oui_non") != "non"
    ):
        raise RuntimeError("SYNTHETIC_FAIL_CLOSED_INVARIANT_BROKEN")

    return {
        "fixture_id": FIXTURE_ID,
        "source_kind": "SYNTHETIC_IN_MEMORY_NOT_PROSPECTIVE",
        "course_uid": COURSE_UID,
        "input_sha256": synthetic_hash,
        "synthetic_input_rows": len(records),
        "watchlist": {
            "decision_matin": watch.get("decision_matin"),
            "partants_reels": watch.get("partants_reels"),
            "kill_switchs_matin": watch.get("kill_switchs_matin"),
            "score_watchlist": watch.get("score_watchlist"),
        },
        "h5": {
            "verdict": h5.get("verdict"),
            "ticket_exact": h5.get("ticket_exact"),
            "strict_allowed_oui_non": h5.get("strict_allowed_oui_non"),
            "decision_block_reason": h5.get("decision_block_reason"),
        },
        "proof_class": "SYNTHETIC_REGRESSION_ONLY",
        "calibration_proven": False,
        "prospective_proven": False,
        "daily_pipeline_proven": False,
        "real_bet_allowed": False,
        "governance_transition": "NONE",
    }


def main() -> int:
    print(json.dumps(run_synthetic_business_smoke(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
