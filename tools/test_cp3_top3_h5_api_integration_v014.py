import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import api_main
def fail(reason):
    print(f"TEST : KO - {reason}")
    raise SystemExit(1)
print("")
print("TEST API CP3 TOP3 H5 LIVE V0_1_4")
print("================================")
if not hasattr(api_main, "action_cp3_top3_h5_live_status"):
    fail("route action_cp3_top3_h5_live_status absente")
if not hasattr(api_main, "action_decision_dashboard"):
    fail("route action_decision_dashboard absente")
status = api_main.action_cp3_top3_h5_live_status()
if not isinstance(status, dict):
    fail("status route ne retourne pas un dict")
print("")
print("ROUTE /action/cp3-top3-h5-live-status")
print(f"ok : {status.get('ok')}")
print(f"source : {status.get('source')}")
print(f"ready_for_h5_paper : {status.get('ready_for_h5_paper')}")
print(f"ticket_real : {status.get('ticket_real')}")
print(f"strict_allowed : {status.get('strict_allowed')}")
if status.get("ok") is not True:
    fail("status.ok != True")
if status.get("ready_for_h5_paper") != "OUI":
    fail("status.ready_for_h5_paper != OUI")
if status.get("real_allowed") != "NON":
    fail("status.real_allowed != NON")
if status.get("ticket_real") != "NO_BET":
    fail("status.ticket_real != NO_BET")
if status.get("strict_allowed") != "NON":
    fail("status.strict_allowed != NON")
dashboard = api_main.action_decision_dashboard()
if not isinstance(dashboard, dict):
    fail("decision-dashboard ne retourne pas un dict")
cp3 = dashboard.get("cp3_top3_h5_live_status")
if not isinstance(cp3, dict):
    fail("cp3_top3_h5_live_status absent du decision-dashboard")
print("")
print("ROUTE /action/decision-dashboard")
print(f"dashboard.ok : {dashboard.get('ok')}")
print(f"cp3.ok : {cp3.get('ok')}")
print(f"cp3.ready_for_h5_paper : {cp3.get('ready_for_h5_paper')}")
print(f"cp3.ticket_real : {cp3.get('ticket_real')}")
print(f"cp3.strict_allowed : {cp3.get('strict_allowed')}")
print(f"cp3.live_roi : {cp3.get('live_roi')}")
if dashboard.get("ok") is not True:
    fail("decision-dashboard.ok != True")
if cp3.get("ready_for_h5_paper") != "OUI":
    fail("dashboard.cp3.ready_for_h5_paper != OUI")
if cp3.get("real_allowed") != "NON":
    fail("dashboard.cp3.real_allowed != NON")
if cp3.get("ticket_real") != "NO_BET":
    fail("dashboard.cp3.ticket_real != NO_BET")
if cp3.get("strict_allowed") != "NON":
    fail("dashboard.cp3.strict_allowed != NON")
print("")
print("TEST : OK")
print("READY_FOR_H5_PAPER : OUI")
print("REAL_ALLOWED : NON")
print("TICKET_REAL : NO_BET")
print("STRICT_ALLOWED : NON")
print("ACTION : paper live uniquement")
