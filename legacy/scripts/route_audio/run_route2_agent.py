#!/usr/bin/env python3
"""
[ENTRY POINT 1] Agent Mode (AI Agent Thinks)
============================================
ACTORS: Human (Starts) -> AI Agent (Thinks/Audits) -> API (Speaks)
Use this for High Quality Production.

Usage:
  python scripts/run_agent_tts.py --channel CH05 --start 1 --end 10
"""
import sys
import subprocess
from pathlib import Path

def main():
    args = sys.argv[1:]
    project_root = Path(__file__).resolve().parents[1]
    core_script = project_root / "scripts" / "_core_audio.py"
    
    # Enable Agent Logic (LLM Audit)
    # We map this to 'interactive' in the core script because strictly speaking
    # 'interactive' meant 'Enable Twin-Engine' in previous logic.
    # We will ensure _core_audio does NOT pause unless explicitly asked.
    
    cmd = [
        "python", str(core_script),
        "--mode", "interactive",
        "--skip-annotation", # CRITICAL: Disable Automated Azure LLM Audit (Cost Saving)
        *args
    ]
    
    print("\n" + "="*60)
    print(" 🧠 ROUTE 2: AGENT MODE (Manual Operation)")
    print("    - Actors: AI Agent (Me) -> Local Engine")
    print("    - Auto LLM Audit: OFF (No API Cost)")
    print("    - Logic: Manual verification by Operator")
    print("="*60 + "\n")
    
    import os
    env = os.environ.copy()
    env["IS_WRAPPER_CALL"] = "1"
    
    subprocess.run(cmd, cwd=project_root, env=env)

if __name__ == "__main__":
    main()
