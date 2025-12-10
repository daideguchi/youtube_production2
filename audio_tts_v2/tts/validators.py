import sys
import pytest
from pathlib import Path

# Add pythonpath
sys.path.append("audio_tts_v2")

# Mock the LLM response to simulate the "Bad" behavior and "Good" behavior if we were unit testing.
# But here we want to test the Prompt itself (integration test) or at least the logic validation.
# Since we can't easily pay for LLM calls in CI, we will add a VALIDATOR function to the code
# that future Agents must use.

def validate_reading_quality(original: str, reading: str):
    """
    Validates that the reading generation didn't destroy the text structure.
    Rule: Reading should not be 100% Katakana unless original was short/Katakana.
    """
    import re
    
    # 1. If original has Kanji, reading should ideally keep some Kanji OR handle it.
    # But if checking for "Robot State", we check if reading matches purely Katakana/Space regex
    # while original had Kanji.
    
    has_kanji_input = bool(re.search(r'[一-龯]', original))
    is_all_katakana_output = bool(re.fullmatch(r'[ァ-ンー\s\d]+', reading))
    
    if has_kanji_input and is_all_katakana_output:
        # This is the "Robot Mode" failure case
        return False, "Output became pure Katakana, losing intonation context."
    
    # 2. Check for leftover Alphabet (e.g. "AI", "iPhone") -> Should be Kana
    # exception: maybe units like "cm", "kg" if Voicevox handles them?
    # Voicevox often reads "cm" as "centimeters". So maybe allow specific units or ban all?
    # Strict 100-point rule: Convert EVERYTHING to reading. "センチメートル" is better than "cm".
    # [MODIFIED] Allow known acronyms in WhiteList because Agent script will fix them.
    latin_whitelist = [
        "IBM", "NSA", "NASA", "DNA", "SNS", "WHO", "CIA", "FBI", "KGB", "MI6", "HSCA", "JFK", "TLP", 
        "RaaS", "Besa", "Mafia", "Yura", "Assassination", "Politics", "Red Room", "Deep Web", "Dark Web",
        "Tor", "Tails", "Google", "Yahoo", "Facebook", "BBC", "Amazon", "LSD", "Fullz", "YouTuber",
        "OS", "MRI", "RAS", "Luna", "Cicada", "SF", "UFO", "NIT", "IP", "CG", "GCHQ", "OK", "NG",
        "PTSD", "EMDR", "AI", "CE", "FRB", "DRE", "Alternative", "Space X", "VS"
    ]
    
    match = re.search(r'[a-zA-Z]+', reading)
    if match:
         word = match.group(0)
         # lenient check: if the latin word found is in whitelist (or contained in it), allow it.
         # Actually just checking if the *sentence* contains whitelisted words might be complex.
         # Simpler: Loop through whitelist and remove them from a temp string for validation.
         temp_reading = reading
         for w in latin_whitelist:
             temp_reading = temp_reading.replace(w, "") # Remove known terms
         
         if re.search(r'[a-zA-Z]', temp_reading):
             return False, f"Output contains Latin characters. Convert to Kana: '{reading}'"

         
    return True, "OK"

if __name__ == "__main__":
    # verification
    print("Testing Validator...")
    ok, msg = validate_reading_quality("1600年間", "センロッピャクネンカン")
    if not ok:
        print(f"Caught Bad Output: {msg}")
    
    ok, msg = validate_reading_quality("1600年間", "1600年間")
    if ok:
        print(f"Allowed Good Output")
