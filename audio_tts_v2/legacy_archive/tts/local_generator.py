import unicodedata
import MeCab
import ipadic

def get_tagger():
    """Returns a MeCab Tagger instance."""
    return MeCab.Tagger(ipadic.MECAB_ARGS)

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

def clean_reading(text: str) -> str:
    """
    Clean up the MeCab reading output (remove spaces, unknowns, etc.)
    """
    return text.replace(" ", "").replace("　", "")

def generate_reading_mecab(text: str, tagger) -> str:
    """
    Generate Katakana reading using MeCab (IPADIC).
    """
    # Ensure text is not empty or None
    if not text:
        return ""
    
    # ParseToNode is safer for feature extraction
    try:
        node = tagger.parseToNode(text)
    except RuntimeError:
        # Fallback if text is too weird? catch all
        return text

    reading = ""
    
    while node:
        if node.surface: # Skip BOS/EOS
            features = node.feature.split(",")
            # IPADIC: [pos, pos1, pos2, pos3, ctype, cform, original, reading, pron]
            # reading is at index 7.
            if len(features) > 7 and features[7] != "*":
                reading += features[7]
            else:
                # If unknown or symbol, use surface
                reading += node.surface
                
        node = node.next
        
    return reading

def generate_draft_readings(blocks: list[dict]) -> list[str]:
    """
    Generates draft readings for a list of blocks.
    New Logic (User Request): 
    - Keep Original Kanji/Text if possible. 
    - Only fix explicit problematic Latin/Symbols (Rule-based).
    - Do NOT convert everything to MeCab Katakana.
    """
    readings = []
    for block in blocks:
        raw = str(block.get("raw_text") or block.get("text", ""))
        
        # 1. Agent Rules (Latin Fixes, Symbol Fixes)
        draft = apply_latin_fixes(raw)
        
        # 2. Middle Dot Removal (Explicit Rule)
        draft = draft.replace("・", "")
        
        readings.append(draft)
        
    return readings

def generate_reference_kana(blocks: list[dict]) -> list[str]:
    """
    Generates 'Reference A' (MeCab-based Standard Reading) for consensus checking.
    Returns a list of purely Katakana strings.
    """
    tagger = get_tagger()
    readings = []
    
    # Kana converter for fallback
    import re
    hira_re = re.compile(r"[ぁ-ゖ]")
    def to_katakana(s: str) -> str:
        return "".join([chr(ord(c) + 0x60) if hira_re.match(c) else c for c in s])

    for block in blocks:
        # Use raw text for reference to avoid circular logic, but cleaned is better?
        # Use 'b_text' if available (which is draft), or 'text'.
        # Actually checking the *original* text is better for independency.
        # But we want to know how the *current draft* reads? No, we want Ground Truth of original.
        # Let's use 'text' (cleaned display text) or 'raw_text'.
        txt = str(block.get("text", ""))
        
        # We need pure Katakana for comparison
        raw_reading = generate_reading_mecab(txt, tagger)
        
        # Normalize
        # 1. Convert Hiragana to Katakana (just in case)
        # 2. Remove non-reading chars (like punctuation if MeCab left them)
        normalized = to_katakana(raw_reading)
        
        readings.append(normalized)
        
    return readings
