import argparse
import json
import sys
import os
from pathlib import Path

# Add project root to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

# Legacy script; kept for reference. Do not use in current pipeline.

REVIEW_PROMPT = """
You are a Japanese B-Text Auditor and Corrector.
Your goal is to ensure the "b_text" (reading script for TTS) is perfect for Voicevox.

**Input Format:**
JSON list of blocks: `[{"index": 0, "text": "...", "b_text": "..."}]`

**Rules:**
1. **Remove Middle Dots (・):** Check if `b_text` contains "・". If so, REMOVE it entirely (e.g., "ピリ・レイス" -> "ピリレイス").
2. **Fix Latin Characters:** `b_text` must NOT contain Latin characters (A-Z, a-z) except for specific allowed units if really necessary (but prefer Katakana "センチ", "メートル"). Known terms like "IBM" -> "アイビーエム", "DNA" -> "ディーエヌエー" must be converted.
3. **Fix Unnatural Readings:** If `b_text` looks robotic or clearly wrong (e.g., purely textual Kanji copy when it should be Katakana, though MeCab usually handles this), fix it to natural Katakana/Hiragana reading.
4. **Preserve ID/Structure:** Return the exact same JSON structure with corrected `b_text`. Do NOT change `text` (display text). Do NOT change `index`.

**Output Format:**
Return ONLY the corrected JSON object: `{"blocks": [ ... ]}`.
"""

def audit_b_text(json_path: str):
    path = Path(json_path)
    if not path.exists():
        print(f"[AUDIT_ERROR] File not found: {json_path}")
        return

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Prepare payload (batching if necessary, but try full file first for context)
    # If file is huge, might need splitting. A typical script has ~100-300 blocks. 
    # GPT-5-mini/4o-mini can handle 300 blocks easily in context (~4000-8000 tokens output might be tight for FULL reading, but we only output JSON).
    # To be safe and fast, let's batch by 50 blocks.

    blocks = data if isinstance(data, list) else data.get("blocks", [])
    if not blocks:
        print("[AUDIT] No blocks found.")
        return

    batch_size = 50
    audited_blocks = []
    
    model_keys = get_task_model_keys("reading") # Reuse reading keys

    print(f"[AUDIT] Accel-Reviewing {len(blocks)} blocks in batches of {batch_size}...")

    for i in range(0, len(blocks), batch_size):
        batch = blocks[i : i + batch_size]
        payload = {"blocks": batch}
        
        messages = [
            {"role": "system", "content": REVIEW_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        ]

        try:
            result = azure_chat_with_fallback(
                messages=messages,
                max_tokens=8000, # Large output needed
                timeout=120,
                model_keys=model_keys,
                response_json_schema={"type": "object", "properties": {"blocks": {"type": "array"}}},
                response_mime_type="application/json"
            )
            
            content = result.get("content")
            
            # Parse response
            # It might be in data["blocks"] or list directly depending on model flexibility, prompt asks for {"blocks":...}
            batch_result = []
            if isinstance(content, dict) and "blocks" in content:
                batch_result = content["blocks"]
            elif isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and "blocks" in parsed:
                        batch_result = parsed["blocks"]
                    elif isinstance(parsed, list):
                        batch_result = parsed
                except:
                    pass
            
            if len(batch_result) == len(batch):
                audited_blocks.extend(batch_result)
                print(f"[AUDIT] Batch {i//batch_size + 1}/{len(blocks)//batch_size + 1} Reviewed.")
            else:
                print(f"[AUDIT_WARN] Batch size mismatch or failure. Keeping original for batch {i}.")
                audited_blocks.extend(batch) # Fallback to original

        except Exception as e:
            print(f"[AUDIT_ERROR] Batch {i} failed: {e}. Keeping original.")
            audited_blocks.extend(batch)

    # Save back
    # Verify we didn't lose anything
    if len(audited_blocks) != len(blocks):
        print("[AUDIT_FATAL] Block count mismatch after audit. Aborting save.")
        return

    # Check specifically for Middle Dots one last time locally to be 200% sure
    fixed_count = 0
    for b in audited_blocks:
        if "・" in b.get("b_text", ""):
            b["b_text"] = b["b_text"].replace("・", "")
            fixed_count += 1
    
    if fixed_count > 0:
        print(f"[AUDIT_FINAL] Removed residual dots from {fixed_count} blocks.")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(audited_blocks, f, indent=2, ensure_ascii=False)
    
    print(f"[AUDIT_SUCCESS] Audited file saved: {json_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python audit_b_text.py <path_to_srt_blocks.json>")
        sys.exit(1)
    
    audit_b_text(sys.argv[1])
