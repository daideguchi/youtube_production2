#!/usr/bin/env python3
"""
[Route 1] Batch LLM Inference (Non-Interactive)
===============================================
Definition:
  - Voicevox API: Generates Kana.
  - LLM API: Audits/Corrects Kana automatically (Twin-Engine).
  - User: Can sleep. (Fully Automated).

Usage:
  python scripts/run_route1_batch.py --channel CH05 --start 1 --end 10
"""
import sys
import subprocess
from pathlib import Path

def main():
    args = sys.argv[1:]
    project_root = Path(__file__).resolve().parents[1]
    core_script = project_root / "scripts" / "_core_audio.py"
    
    # --mode batch => FORCE LLM Audit (skip_annotation=False) in _core_audio.py
    cmd = [
        "python", str(core_script),
        "--mode", "batch", 
        *args
    ]
    
    print("\n" + "="*60)
    print(" 🏭 ROUTE 1: BATCH LLM INFERENCE (Non-Interactive)")
    print("    - Voicevox: ON")
    print("    - LLM Audit: ON (Automated)")
    print("    - User: SLEEP")
    print("="*60 + "\n")
    
    import os
    env = os.environ.copy()
    env["IS_WRAPPER_CALL"] = "1"
    
    try:
        subprocess.run(cmd, cwd=project_root, check=True, env=env)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        sys.exit(130)

if __name__ == "__main__":
    main()
