import sys
import json
import re
import MeCab
import unicodedata

# -------------------------------------------------------------------------
# AGENT RULES (Copied from agent_write_b_text.py)
# -------------------------------------------------------------------------
def apply_latin_fixes(text: str) -> str:
    """
    Apply specific replacement rules for Latin characters and symbols.
    """
    # 1. Normalized to NFKC to handle full-width/half-width consistency
    text = unicodedata.normalize('NFKC', text)
    
    # 2. Specific replacer dict (The "Agent Knowledge")
    replacements = {
        "NASA": "ナサ",
        "IBM": "アイビーエム",
        "SNS": "エスエヌエス",
        "AI": "エーアイ",
        "CEO": "シーイーオー",
        "NG": "エヌジー",
        "OK": "オーケー",
        "VS": "ブイエス",
        "vs": "ブイエス",
        "Q&A": "キューアンドエー",
        "GW": "ゴールデンウィーク",
        "USB": "ユーエスビー",
        "PC": "ピーシー",
        "OB": "オービー",
        "D": "ディー",  # Basic single letters if isolated (risky, but useful)
        # Add more as needed based on "100 point" feedback
    }
    
    # 3. Apply Replacements (Case insensitive for keys if needed, but dict is explicit)
    for k, v in replacements.items():
        # Use regex with word boundaries if possible, or just strict replacement
        # Simple replace for now, as these are usually distinct
        text = text.replace(k, v)
        text = text.replace(k.lower(), v) # Apply lower case too just in case

    # 4. Symbol Fixes
    text = text.replace("%", "パーセント")
    text = text.replace("&", "アンド")
    text = text.replace("+", "プラス")
    text = text.replace("=", "イコール")
    
    # 5. Fallback for remaining single Latin letters (A-Z) -> Katakana
    # This is a "Safety Net"
    latin_map = {
        "A": "エー", "B": "ビー", "C": "シー", "D": "ディー", "E": "イー",
        "F": "エフ", "G": "ジー", "H": "エイチ", "I": "アイ", "J": "ジェー",
        "K": "ケー", "L": "エル", "M": "エム", "N": "エヌ", "O": "オー",
        "P": "ピー", "Q": "キュー", "R": "アール", "S": "エス", "T": "ティー",
        "U": "ユー", "V": "ブイ", "W": "ダブリュー", "X": "エックス", "Y": "ワイ", "Z": "ゼット"
    }
    for char, read in latin_map.items():
        text = text.replace(char, read)
        text = text.replace(char.lower(), read)
        
    return text

def clean_reading(text):
    """
    Clean up the MeCab reading output (remove spaces, unknowns, etc.)
    """
    # MeCab output might key "ハシ" (Grave) vs "ハシ" (Bridge). We just want text.
    # Convert half-width kana to full-width if any? MeCab usually outputs full-width.
    return text.replace(" ", "").replace("　", "")

# -------------------------------------------------------------------------
# MeCab Logic
# -------------------------------------------------------------------------
def generate_reading_mecab(text, tagger):
    """
    Generate Katakana reading using MeCab (IPADIC).
    """
    node = tagger.parseToNode(text)
    reading = ""
    
    while node:
        if node.surface: # Skip BOS/EOS
            features = node.feature.split(",")
            # IPADIC: [pos, pos1, pos2, pos3, ctype, cform, original, reading, pron]
            # reading is at index 7. pronunciation is at index 8.
            # We prefer reading (Katakana) or Pronunciation?
            # Reading is usually safer for straightforward conversion.
            
            if len(features) > 7 and features[7] != "*":
                reading += features[7]
            else:
                # If unknown or symbol
                reading += node.surface
                
        node = node.next
        
    return reading

def main():
    import ipadic # Import here to avoid dependency if not installed (but we installed it)
    input_file = sys.argv[1]
    
    # Initialize MeCab with IPADIC
    try:
        tagger = MeCab.Tagger(ipadic.MECAB_ARGS)
    except Exception as e:
        print(f"Error initializing MeCab with ipadic: {e}")
        sys.exit(1)

    with open(input_file, 'r', encoding='utf-8') as f:
        blocks = json.load(f)
        
    output_blocks = []
    
    for block in blocks:
        raw = block.get("raw_text", block.get("text", ""))
        
        # 1. Generate Base Reading
        base_reading = generate_reading_mecab(raw, tagger)
        
        # 2. Apply Agent Rules
        final_reading = apply_latin_fixes(base_reading)
        
        # 3. Final Formatting
        final_reading = clean_reading(final_reading)
        
        new_block = {
            "index": block["index"],
            "text": block["text"],
            "raw_text": raw,
            "b_text": final_reading
        }
        output_blocks.append(new_block)
    
    filename = input_file.split("/")[-1]
    parts = filename.replace("temp_srt_blocks_", "").replace(".json", "").split("_")
    if len(parts) == 2:
        channel, video_id = parts[0], parts[1]
        output_path = f"audio_tts_v2/artifacts/final/{channel}/{video_id}/srt_blocks.json"
        
        import os
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_blocks, f, ensure_ascii=False, indent=2)
            
        print(f"[SUCCESS] Wrote {len(output_blocks)} readings to {output_path}")
    else:
        print("[ERROR] Could not deduce output path from filename.")

if __name__ == "__main__":
    main()
