
import os
import sys
import json
import re
import subprocess
from pathlib import Path
import google.generativeai as genai
import dotenv

# Load env
dotenv.load_dotenv(override=True)

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from factory_common.paths import script_data_root
from audio_tts_v2.tts.orchestrator import _raw_sentence_blocks_for_srt, _merge_numeric_blocks
from audio_tts_v2.tts.llm_adapter import B_TEXT_GEN_PROMPT

def run_agent_inference(missing_videos):
    # Switch to Azure backend (simulating Agent via robust LLM)
    api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] AZURE_OPENAI_API_KEY not found.")
        return

    # No genai configure needed
    # model = genai.GenerativeModel('gemini-1.5-flash') 

    base_data = script_data_root()
    
    for ch, vid in missing_videos:
        print(f"\n[AGENT] Generating B-Text for {ch}-{vid}...")
        prep_dir = base_data / ch / vid / "audio_prep"
        a_text_path = prep_dir / "script_corrected.txt"
        
        if not a_text_path.exists():
            print(f"[SKIP] {a_text_path} not found.")
            continue
            
        a_text = a_text_path.read_text(encoding="utf-8")
        
        # 1. Mechanical Segmentation (Reusing Orchestrator Logic)
        blocks = _raw_sentence_blocks_for_srt(a_text)
        blocks = _merge_numeric_blocks(blocks)
        print(f"  -> Segmented into {len(blocks)} blocks.")
        
        # 2. Agent Inference (Using llm_adapter to ensure robust output)
        print(f"  -> Inferring readings via LLM Adapter...")
        try:
             # We pass the blocks directly. llm_adapter handles the prompting and JSON parsing.
             # Note: generate_reading_for_blocks returns a list of strings (readings).
             all_readings = llm_adapter.generate_reading_for_blocks(blocks, model="gpt-5-mini", api_key=api_key)
        except Exception as e:
             print(f"[ERR] Inference failed: {e}")
             continue

        
        # 3. Construct srt_blocks.json
        full_blocks = []
        for j, blk in enumerate(blocks):
            reading = all_readings[j] if j < len(all_readings) else blk.get("text", "")
            
            # Simple agent fixes (hardcoded override)
            if "NASA" in blk["text"]: reading = reading.replace("NASA", "ナサ")
            
            new_blk = {
                "text": blk["text"], # Display
                "raw_text": blk["text"],
                "b_text": reading,   # Reading
                "duration_sec": 0.0, # Will be filled by TTS
            }
            full_blocks.append(new_blk)
            
        # Write JSON
        target_json = prep_dir / "srt_blocks.json"
        # We write to audio_tts_v2/artifacts/final/.../srt_blocks.json because run_tts looks there for REGEN?
        # run_tts logic: artifact_root = final_root / args.video
        # if regenerate_from_json: srt_json_path = artifact_root / "srt_blocks.json"
        # So we must write to the ARTIFACT location (or the auto-detected location).
        
        # Auto-detect location logic in my recent run_tts.py update:
        # If input is in script_pipeline/data, artifact_root is audio_prep.
        # So yes, writing to prep_dir / "srt_blocks.json" is correct!
        
        target_json.write_text(json.dumps(full_blocks, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  -> Saved {target_json}")
        
        # 4. Trigger Regeneration
        print(f"  -> Triggering Synthesis...")
        cmd = [
            "python3", "audio_tts_v2/scripts/run_tts.py",
            "--channel", ch,
            "--video", vid,
            "--input", str(a_text_path),
            "--regenerate-from-json",
            "--phase", "full" # Generate Audio using the JSON
        ]
        subprocess.run(cmd)

if __name__ == "__main__":
    # Missing CH04 videos (from previous step list)
    missing = [
        ("CH04", "024"), ("CH04", "023"), ("CH04", "015"), ("CH04", "012"), ("CH04", "013"),
        ("CH04", "014"), ("CH04", "022"), ("CH04", "025"), ("CH04", "007"), ("CH04", "009"),
        ("CH04", "030"), ("CH04", "008"), ("CH04", "001"), ("CH04", "006"), ("CH04", "020"),
        ("CH04", "027"), ("CH04", "018"), ("CH04", "011"), ("CH04", "016"), ("CH04", "029"),
        ("CH04", "017"), ("CH04", "028"), ("CH04", "010"), ("CH04", "026"), ("CH04", "019"),
        ("CH04", "021"), ("CH04", "003"), ("CH04", "004"), ("CH04", "005"), ("CH04", "002")
    ]
    # Filter to actual missing/unprocessed if needed, but safe to regen.
    # User asked for "Half", CH04 corresponds to half the problem set.
    run_agent_inference(missing)
