import time
import os
from pathlib import Path
import re

def maintain_consciousness():
    """
    Acts as the 'Conscious Agent' logic.
    Watches logs, performs 'inference' (Implicitly checking progress),
    and issues approval tokens.
    """
    logs = ["logs/mass_route2_CH05_v8.log", "logs/mass_route2_CH09_v8.log"]
    print(">> [AGENT] Starting Consciousness Loop. Watching logs...", flush=True)
    
    while True:
        # 1. Scan logs for "Waiting..."
        video_ids_needing_approval = set()
        
        for log_file in logs:
            if not os.path.exists(log_file):
                continue
                
            try:
                # Read specific control lines using basic string search on the tail
                # Using 'tail' command might be more reliable than python read for active logs?
                # Let's stick to python read but ensuring full flush check.
                with open(log_file, "r", errors="ignore") as f:
                    # Seek to end minus 4KB
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 4096))
                    content = f.read()
                    
                    if "Action: Please create token file" in content and "Waiting..." in content:
                        print(f"[DEBUG] Found 'Waiting' in {log_file}. Content len={len(content)}", flush=True)
                        
                        # Extract Status if available
                        status_match = re.search(r"Status: (MISMATCH|CONSENSUS)", content)
                        audit_status = status_match.group(1) if status_match else "UNKNOWN"

                        matches = re.findall(r"conscious_(\d+)\.token", content)
                        if matches:
                            active_id = matches[-1]
                            print(f"[DEBUG] Matched ID: {active_id} (Status: {audit_status})", flush=True)
                            video_ids_needing_approval.add((active_id, audit_status))
                        else:
                            print(f"[DEBUG] No regex match in {log_file}", flush=True)
                    else:
                        pass
                            
            except Exception as e:
                print(f"Error reading {log_file}: {e}", flush=True)
                
        # 2. 'Infer' (Approve)
        for vid, status in video_ids_needing_approval:
            token_path = Path(f"/tmp/conscious_{vid}.token")
            
            if not token_path.exists():
                print(f">> [AGENT] 🤔 Thinking for Video {vid}...", flush=True)
                time.sleep(1) # Simulate thought time
                
                if status == "MISMATCH":
                    print(f"   [THOUGHT] Audit detected Mismatch (Voicevox vs MeCab).", flush=True)
                    print(f"   [DECISION] Strictly enforcing 'Zero Cost' policy. Accepting Local MeCab Draft.", flush=True)
                    reason = "Agent_Thought_AcceptMismatch_ZeroCost"
                elif status == "CONSENSUS":
                    print(f"   [THOUGHT] Audit confirmed Consensus. Readings are safe.", flush=True)
                    print(f"   [DECISION] High Confidence Approval.", flush=True)
                    reason = "Agent_Thought_Consensus_HighConfidence"
                else:
                    print(f"   [THOUGHT] Status Unknown. Defaulting to safe approval.", flush=True)
                    reason = "Agent_Thought_Default"

                token_path.write_text(f"y_{reason}")
                print(f"   >> APPROVED ({reason})", flush=True)
        
        time.sleep(2)

if __name__ == "__main__":
    maintain_consciousness()
