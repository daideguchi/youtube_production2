import json

json_path = "/Users/dd/10_YouTube_Automation/factory_commentary/workspaces/scripts/_reports/dialog_ai_script_audit/20260114T001705Z/scan_rows.json"

with open(json_path, 'r') as f:
    data = json.load(f)

found_ids = [int(row['video']) for row in data['rows']]
target_ids = list(range(42, 92))

missing = [vid for vid in target_ids if vid not in found_ids]
print(f"Missing IDs: {missing}")
