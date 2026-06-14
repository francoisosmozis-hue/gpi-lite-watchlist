import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.h5_decision import evaluate_h5_decision


def test_h5_missing_data_refuses():
    out = evaluate_h5_decision({})

    assert out["verdict"] == "NO_BET"
    assert out["structure_max"] == "NO_BET"
    assert out["ticket_exact"] == "NO_BET"
    assert out["strict_allowed_oui_non"] == "non"
    assert "SG_H5_OK_MISSING" in out["decision_block_reason"]


def test_h5_volatilite_kill_refuses():
    ctx = {
        "sg_h5_ok": "OK",
        "np_ok": "OK",
        "statut_ok": "OK",
        "decision_before_race_oui_non": "oui",
        "input_hash": "abc123",
        "segment_roi": "CP_SEC",
        "edge_status": "EDGE_NET",
        "proxy_status": "VERT",
        "volatilite_status": "KILL",
        "kill_switch_codes": "VOLATILITE_KILL_SWITCH_TOP6",
    }

    out = evaluate_h5_decision(ctx)

    assert out["verdict"] == "NO_BET"
    assert out["structure_max"] == "NO_BET"
    assert out["ticket_exact"] == "NO_BET"
    assert out["strict_allowed_oui_non"] == "non"
    assert "VOLATILITE_KILL" in out["decision_block_reason"]


def test_h5_complete_still_no_strict_in_p9_1_security_mode():
    ctx = {
        "sg_h5_ok": "OK",
        "np_ok": "OK",
        "statut_ok": "OK",
        "decision_before_race_oui_non": "oui",
        "input_hash": "abc123",
        "segment_roi": "CP_SEC",
        "edge_status": "EDGE_NET",
        "proxy_status": "VERT",
        "volatilite_status": "OK",
        "kill_switch_codes": "aucun",
    }

    out = evaluate_h5_decision(ctx)

    assert out["verdict"] == "NO_BET"
    assert out["structure_max"] == "CP_SEC_AUDIT_SEULEMENT"
    assert out["ticket_exact"] == "NO_BET"
    assert out["strict_allowed_oui_non"] == "non"
    assert out["cp_sec_result"] == "A_AUDITER_H5"
    assert out["sp_result"] == "REFUS"
    assert out["cp3_result"] == "REFUS_CP_SEC_PRIORITAIRE"
    assert out["cp_sec_domine"] == "oui"


if __name__ == "__main__":
    test_h5_missing_data_refuses()
    test_h5_volatilite_kill_refuses()
    test_h5_complete_still_no_strict_in_p9_1_security_mode()
    print("P9 h5_decision tests OK")
