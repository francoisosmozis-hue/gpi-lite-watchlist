from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.paper_kpi import write_paper_kpi

kpi_path, audit_csv_path, audit_json_path, audit = write_paper_kpi()

print("Paper KPI créés")
print(kpi_path)
print(audit_csv_path)
print(audit_json_path)

print("")
print("Résumé")
print(f"kpi_version : {audit.get('kpi_version')}")
print(f"rows_input : {audit.get('rows_input')}")
print(f"rows_kpi : {audit.get('rows_kpi')}")
print(f"global_decisions : {audit.get('global_decisions')}")
print(f"global_tickets : {audit.get('global_tickets')}")
print(f"global_no_bet_count : {audit.get('global_no_bet_count')}")
print(f"global_result_pending_count : {audit.get('global_result_pending_count')}")
print(f"global_strict_allowed_count : {audit.get('global_strict_allowed_count')}")
print(f"global_replay_ko_count : {audit.get('global_replay_ko_count')}")
