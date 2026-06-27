from pathlib import Path
path = Path("api_main.py")
text = path.read_text(encoding="utf-8-sig")
helper = r'''
def _cp3_top3_h5_live_dashboard_summary():
    """Read-only compact CP3 TOP3 H5 paper live status for dashboards.
    Never opens a real ticket.
    Never changes strict_allowed.
    """
    try:
        func = globals().get("action_cp3_top3_h5_live_status")
        if not callable(func):
            return {
                "ok": False,
                "status": "ND",
                "ready_for_h5_paper": "NON",
                "real_allowed": "NON",
                "ticket_real": "NO_BET",
                "strict_allowed": "NON",
                "error": "CP3_TOP3_H5_LIVE_STATUS_ROUTE_NOT_FOUND",
            }
        payload = func()
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        live = payload.get("live", {})
        if not isinstance(live, dict):
            live = {}
        retro = payload.get("retro", {})
        if not isinstance(retro, dict):
            retro = {}
        return {
            "ok": bool(payload.get("ok") is True),
            "status": payload.get("status", "ND"),
            "source": payload.get("source", "ND"),
            "segment_roi": payload.get("segment_roi", "CP3_TOP3_ETUDE_H5_PAPER"),
            "ready_for_h5_paper": payload.get("ready_for_h5_paper", "NON"),
            "real_allowed": "NON",
            "ticket_real": "NO_BET",
            "strict_allowed": "NON",
            "live_total_decisions": live.get("total_decisions", "0"),
            "live_pending": live.get("pending", "0"),
            "live_resolved": live.get("resolved", "0"),
            "live_roi": live.get("roi", "0.000000"),
            "live_audit_status": live.get("audit_status", "ND"),
            "retro_candidate_count": retro.get("candidate_count", "ND"),
            "retro_roi_label": retro.get("roi_label", "ND"),
            "policy": "Paper live uniquement. Aucun ticket reel. Aucun strict.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "KO",
            "ready_for_h5_paper": "NON",
            "real_allowed": "NON",
            "ticket_real": "NO_BET",
            "strict_allowed": "NON",
            "error": f"CP3_TOP3_H5_LIVE_SUMMARY_ERROR:{type(exc).__name__}:{exc}",
            "policy": "Erreur lecture module CP3 H5. NO_BET par defaut.",
        }
'''
marker = '# >>> GPI ACTION DECISION DASHBOARD V0_9_25 >>>'
if helper.strip() not in text:
    text = text.replace(marker, helper + "\n\n" + marker)
old = '''            "policy": "Synthèse décisionnelle courte. Ne valide aucun ticket réel.",
            **extra,
        }'''
new = '''            "cp3_top3_h5_live_status": _cp3_top3_h5_live_dashboard_summary(),
            "policy": "Synthèse décisionnelle courte. Ne valide aucun ticket réel.",
            **extra,
        }'''
if old not in text:
    raise SystemExit("PATCH_REFUS: bloc return decision-dashboard introuvable")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("PATCH OK : decision-dashboard enrichi avec cp3_top3_h5_live_status")
