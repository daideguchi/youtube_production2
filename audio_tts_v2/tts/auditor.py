import json
from typing import List, Dict
from pathlib import Path
from factory_common.llm_router import get_router

REVIEW_PROMPT = """
You are a Lead Japanese B-Text Auditor.
There is a disagreement between the Dictionary (MeCab) and the AI Engine (Voicevox).
Your goal is to decide the correct reading based on strict standard Japanese context.

**Inputs per block:**
- `text`: ORIGINAL Display text (Context).
- `b_text`: Current Draft (Usually Kanji-mixed).
- `mecab_kana`: Standard Dictionary Reading (Reference A).
- `voicevox_kana`: Engine Predicted Reading (Reference B).

**Logic:**
1. **Compare Readings**:
   - `voicevox_kana` is what the engine WILL say if you don't fix it.
   - `mecab_kana` is the Standard Japanese Morphological Analysis.
   - **PRIORITY**: Trust `mecab_kana` (Standard) over `voicevox_kana` (AI Prediction) unless context explicitly demands a deviation.
   
2. **Critical Fixes (MUST FIX)**:
   - If `voicevox_kana` has wrong accent/intonation causing semantic drift (e.g. 辛い: Tsurai vs Karai) -> **MUST FIX**.
   - If `voicevox_kana` contains Latin/English chars -> **MUST FIX** (Convert to Kana).

3. **Output Rule**:
   - If `voicevox_kana` is CORRECT (matches standard reading OR appropriate context reading), return `b_text` AS IS (Keep Kanji).
   - If `voicevox_kana` is WRONG (misread), return the **Corrected Kana Reading** (not Kanji).

4. **Dictionary Learning**:
   - If you make a correction, add it to `learned_words`.

**Output JSON:**
{
  "blocks": [
    {"index": 0, "b_text": "..."} 
  ],
  "learned_words": {
    "Word": "Reading" 
  }
}
"""

def load_learning_dict():
    path = Path("audio_tts_v2/configs/learning_dict.json")
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            return {}
    return {}

def save_learning_dict(new_entries: Dict[str, str]):
    path = Path("audio_tts_v2/configs/learning_dict.json")
    current = load_learning_dict()
    current.update(new_entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2))

def audit_blocks(blocks: List[Dict]) -> List[Dict]:
    """
    Audits blocks using LLM + Twin-Engine Consensus.
    ONLY sends blocks to LLM if `audit_needed` is True.
    """
    from pathlib import Path
    if not blocks: return []

    # 1. Apply Learning Dictionary (Unconditional)
    learned = load_learning_dict()
    for b in blocks:
        txt = b.get("b_text", "")
        for k, v in learned.items():
            if k in txt:
                txt = txt.replace(k, v)
        b["b_text"] = txt

    # 2. Identify Candidates
    candidates = [b for b in blocks if b.get("audit_needed", True)]
    safe_blocks = [b for b in blocks if not b.get("audit_needed", True)]
    
    if not candidates:
        print("[AUDIT] All blocks achieved Consensus. No LLM Audit needed.")
        return blocks

    batch_size = 50
    audited_map = {} # index -> block
    new_learned_total = {}
    
    router = get_router()

    print(f"[AUDIT] Scanning {len(candidates)} SUSPECT blocks (Twin-Engine Divergence)...")

    for i in range(0, len(candidates), batch_size):
        batch = candidates[i : i + batch_size]
        payload = {"blocks": batch}
        
        messages = [
            {"role": "system", "content": REVIEW_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        ]

        try:
            content = router.call(
                task="tts_reading",
                messages=messages,
                max_tokens=8000, 
                timeout=120,
                response_format="json_object"
            )
            
            batch_result = []
            learned_words = {}
            
            # Simple JSON parsing
            try:
                parsed = json.loads(content)
                batch_result = parsed.get("blocks", [])
                learned_words = parsed.get("learned_words", {})
            except Exception as e:
                # Retry parsing (lenient)
                # (Simple text cleaning logic if needed, but Router ensures basic cleanup for some providers)
                pass
            
            if batch_result:
                for res_b in batch_result:
                    idx = res_b.get("index")
                    audited_map[idx] = res_b

                if learned_words:
                    new_learned_total.update(learned_words)
                print(f"[AUDIT] Batch {i//batch_size + 1} Reviewed. Found {len(learned_words)} new words.")
            else:
                print(f"[AUDIT_WARN] Empty result from LLM. Keeping originals.")

        except Exception as e:
            print(f"[AUDIT_ERROR] Batch failed: {e}. Keeping originals.")

    # 3. Merge Results
    final_blocks = []
    for b in blocks:
        idx = b.get("index")
        if idx in audited_map:
            # Update b_text from audit (preserve other metadata)
            new_b = audited_map[idx]
            b["b_text"] = new_b.get("b_text", b["b_text"])
        final_blocks.append(b)

    # 4. Save Learned Words
    if new_learned_total:
        print(f"[AUDIT] Saving {len(new_learned_total)} new learned words to dictionary.")
        save_learning_dict(new_learned_total)

    # Final Safety Check
    fixed_count = 0
    for b in final_blocks:
        if b.get("b_text") and "・" in b["b_text"]:
            b["b_text"] = b["b_text"].replace("・", "")
            fixed_count += 1
    
    if fixed_count > 0:
        print(f"[AUDIT_FINAL] Removed residual dots from {fixed_count} blocks.")
        
    return final_blocks