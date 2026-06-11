from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.paper_results import write_paper_results

results_path, latest_path, audit_csv_path, audit_json_path, audit = write_paper_results()

print("Paper Results créés")
print(results_path)
print(latest_path)
print(audit_csv_path)
print(audit_json_path)

print("")
print("Résumé")
print(f"paper_count : {audit.get('paper_count')}")
print(f"result_input_count : {audit.get('result_input_count')}")
print(f"results_joined_count : {audit.get('results_joined_count')}")
print(f"result_ok_count : {audit.get('result_ok_count')}")
print(f"result_no_bet_count : {audit.get('result_no_bet_count')}")
print(f"result_pending_count : {audit.get('result_pending_count')}")
print(f"result_simulation_no_stake_count : {audit.get('result_simulation_no_stake_count')}")
