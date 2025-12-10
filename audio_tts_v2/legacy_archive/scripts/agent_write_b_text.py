
import json
import re
import sys
import os
from pathlib import Path

# Add project root to sys.path to import tts modules
sys.path.append("audio_tts_v2")
from tts import llm_adapter

def apply_latin_fixes(txt: str) -> str:
    # --- 1. Long/Specific Terms (ORDER MATTERS: Superstrings first) ---
    txt = txt.replace("ALH84001", "エーエルエイチハチヨンゼロゼロイチ")
    txt = txt.replace("IBM", "アイビーエム")
    txt = txt.replace("vs", "バーサス")
    txt = txt.replace("VS", "バーサス")
    txt = txt.replace("NSAM", "エヌエスエーエム") # Must be before NSA
    txt = txt.replace("NSA", "エヌエスエー")
    txt = txt.replace("NASA", "ナサ")
    txt = txt.replace("DNA", "ディーエヌエー")
    txt = txt.replace("SNS", "エスエヌエス")
    txt = txt.replace("WHO", "ダブリューエイチオー")
    txt = txt.replace("CIA", "シーアイエー") # Must be before A
    txt = txt.replace("FBI", "エフビーアイ")
    txt = txt.replace("KGB", "ケージービー")
    txt = txt.replace("MI6", "エムアイシックス")
    txt = txt.replace("HSCA", "エイチエスシーエー")
    txt = txt.replace("JFK", "ジェイエフケイ") # Must be before F
    txt = txt.replace("TLP", "ティーエルピー")
    txt = txt.replace("RaaS", "ラース")
    txt = txt.replace("Besa", "ベサ")
    txt = txt.replace("Mafia", "マフィア")
    txt = txt.replace("Yura", "ユラ")
    txt = txt.replace("Assassination", "アサシネーション")
    txt = txt.replace("Politics", "ポリティクス")
    txt = txt.replace("Red Room", "レッドルーム")
    txt = txt.replace("Deep Web", "ディープウェブ")
    txt = txt.replace("Dark Web", "ダークウェブ")
    txt = txt.replace("The Onion Router", "オニオンルーター")
    txt = txt.replace("onion", "オニオン")
    txt = txt.replace("SecureDrop", "セキュアドロップ")
    txt = txt.replace("Keyscore", "キースコア")
    txt = txt.replace("Stinks", "スティンクス")
    txt = txt.replace("Tor", "トーア")
    txt = txt.replace("Tails", "テイルズ")
    txt = txt.replace("Google", "グーグル")
    txt = txt.replace("Yahoo", "ヤフー")
    txt = txt.replace("Facebook", "フェイスブック")
    txt = txt.replace("BBC", "ビービーシー")
    txt = txt.replace("Amazon", "アマゾン")
    txt = txt.replace("LSD", "エルエスディー")
    txt = txt.replace("Fullz", "フルズ")
    txt = txt.replace("YouTuber", "ユーチューバー")
    txt = txt.replace("plateau", "プラトー")
    txt = txt.replace("OS", "オーエス")
    txt = txt.replace("fMRI", "エフエムアールアイ") # Before MRI
    txt = txt.replace("MRI", "エムアールアイ")
    txt = txt.replace("RAS", "アールエーエス")
    txt = txt.replace("santosha", "サントーシャ")
    txt = txt.replace("Lunacy", "ルナシー") # Before Luna
    txt = txt.replace("Luna", "ルナ")
    txt = txt.replace("Cicada", "シケイダ")
    txt = txt.replace("3301", "サンサンゼロイチ")
    txt = txt.replace("Liber Primus", "リベルプリムス")
    txt = txt.replace("D.C.", "ディーシー")
    txt = txt.replace("D. C.", "ディーシー")
    txt = txt.replace("S.B.", "エスビー")
    txt = txt.replace("SF", "エスエフ")
    txt = txt.replace("UFO", "ユーフォー")
    txt = txt.replace("NIT", "エヌアイティー")
    txt = txt.replace("IPアドレス", "アイピーアドレス")
    txt = txt.replace("IP", "アイピー")
    txt = txt.replace("CG", "シージー")
    txt = txt.replace("GCHQ", "ジーシーエイチキュー")
    txt = txt.replace("Exit Node", "イグジットノード")
    txt = txt.replace("Exit", "イグジット")
    txt = txt.replace("Node", "ノード")
    txt = txt.replace("OK", "オッケー")
    txt = txt.replace("NG", "エヌジー")
    txt = txt.replace("PTSD", "ピーティーエスディー")
    txt = txt.replace("EMDR", "イーエムディーアール")
    txt = txt.replace("AI", "エーアイ")
    txt = txt.replace("CE", "シーイー")
    txt = txt.replace("FRB", "エフアールビー")
    txt = txt.replace("DRE", "ディーアールイー")
    txt = txt.replace("L.", "エル") 
    txt = txt.replace("E.", "イー")

    # --- 2. Context/Particle Replacements ---
    txt = txt.replace("Aさん", "エイさん")
    txt = txt.replace("Bさん", "ビーさん")
    txt = txt.replace("Cさん", "シーさん")
    txt = txt.replace("Xさん", "エックスさん")
    txt = txt.replace("Yさん", "ワイさん")
    txt = txt.replace("Space X", "スペースエックス")
    txt = txt.replace("X", "エックス") 
    txt = txt.replace("&", "アンド")
    txt = txt.replace("・", "") # User Feedback: Middle dots cause unwanted pauses. Remove them.
    txt = txt.replace("+", "プラス") 
    txt = txt.replace("F・", "エフ・")
    txt = txt.replace("E・", "イー・") 
    txt = txt.replace("L・", "エル・")
    txt = txt.replace("C・", "シー・")
    txt = txt.replace("D&M", "ディーアンドエム")
    txt = txt.replace("Alternative 3", "オルタナティブスリー")
    txt = txt.replace("Secret Space Program", "シークレットスペースプログラム")
    txt = txt.replace("NO", "ノー")
    txt = txt.replace("（e）", "（イー）")
    
    # --- Artifact Patch ---
    txt = txt.replace("エヌエスエーM", "エヌエスエーエム")
    txt = txt.replace("Aの", "エイの")
    txt = txt.replace("Bは", "ビーは")
    txt = txt.replace("Bを", "ビーを")
    txt = txt.replace("Aと", "エイと")
    txt = txt.replace("Bと", "ビーと")
    txt = txt.replace("Aを", "エイを")
    txt = txt.replace("Bの", "ビーの")
    txt = txt.replace("Aは", "エイは")
    txt = txt.replace("Aが", "エイが")
    txt = txt.replace("Bなら", "ビーなら")
    txt = txt.replace("Cなら", "シーなら")
    txt = txt.replace("Dの", "ディーの")
    txt = txt.replace("*** End Patch", "")
    txt = txt.replace("Hister", "ヒスター")
    txt = txt.replace("Hitler", "ヒトラー")
    txt = txt.replace("Mongolois", "モンゴロイス")
    txt = txt.replace("COVID-19", "コビッドナインティーン")
    txt = txt.replace("Shape-from-Shading", "シェイプフロムシェーディング")
    txt = txt.replace("Earth", "アース")
    txt = txt.replace("vs", "ブイエス")

    # --- 3. Single Char Fallbacks (SCORCHED EARTH) ---
    txt = txt.replace("A", "エイ").replace("B", "ビー").replace("C", "シー").replace("D", "ディー").replace("E", "イー")
    txt = txt.replace("F", "エフ").replace("G", "ジー").replace("H", "エイチ").replace("I", "アイ").replace("J", "ジェイ")
    txt = txt.replace("K", "ケイ").replace("L", "エル").replace("M", "エム").replace("N", "エヌ").replace("O", "オー")
    txt = txt.replace("P", "ピー").replace("Q", "キュー").replace("R", "アール").replace("S", "エス").replace("T", "ティー")
    txt = txt.replace("U", "ユー").replace("V", "ブイ").replace("W", "ダブリュー").replace("X", "エックス").replace("Y", "ワイ").replace("Z", "ゼット")
    # Lowercase
    txt = txt.replace("a", "エー").replace("b", "ビー").replace("c", "シー").replace("d", "ディー").replace("e", "イー")
    txt = txt.replace("f", "エフ").replace("g", "ジー").replace("h", "エイチ").replace("i", "アイ").replace("j", "ジェイ")
    txt = txt.replace("k", "ケイ").replace("l", "エル").replace("m", "エム").replace("n", "エヌ").replace("o", "オー")
    txt = txt.replace("p", "ピー").replace("q", "キュー").replace("r", "アール").replace("s", "エス").replace("t", "ティー")
    txt = txt.replace("u", "ユー").replace("v", "ブイ").replace("w", "ダブリュー").replace("x", "エックス").replace("y", "ワイ").replace("z", "ゼット")
    
    return txt

def write_b_text_with_llm(files):
    api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] API Key not found!")
        sys.exit(1)
        
    model = "gpt-5-mini" # Using lightweight model for reading generation
    
    for f_path_str in files:
        f_path = Path(f_path_str)
        if not f_path.exists():
            print(f"[SKIP] {f_path} not found")
            continue
            
        m = re.match(r"temp_srt_blocks_(CH[0-9]+)_([0-9]+)\.json", f_path.name)
        if not m:
            continue
            
        channel, video = m.groups()
        print(f"[AGENT] Processing {channel}-{video} with LLM...")
        
        data = json.loads(f_path.read_text(encoding="utf-8"))
        
        # 1. Call LLM to generate base readings (Kanji->Kana, Context)
        try:
            readings = llm_adapter.generate_reading_for_blocks(data, model, api_key)
        except Exception as e:
            print(f"[ERROR] LLM Failed for {channel}-{video}: {e}")
            sys.exit(1)
            
        # 2. Integrate LLM readings + Apply Agent Latin Fixes
        latin_warnings = 0
        converted_count = 0
        
        # Ensure 1:1 match
        if len(readings) != len(data):
            print(f"[ERROR] Mismatch: {len(data)} blocks vs {len(readings)} readings. Fallback to raw text for unmatched.")
        
        for i, blk in enumerate(data):
            # Base reading from LLM or Raw if index overflow (rare)
            base_reading = readings[i] if i < len(readings) else blk.get("text", "")
            
            # Apply Agent Rules (Latin Fixes)
            final_reading = apply_latin_fixes(base_reading)
            
            # Warn if Latin persists
            if re.search(r"[a-zA-Z]", final_reading):
                print(f"[WARN] Latin remaining in {channel}-{video} Block {i}: {final_reading}")
                latin_warnings += 1
                
            blk["b_text"] = final_reading
            converted_count += 1
            
        final_dir = Path(f"audio_tts_v2/artifacts/final/{channel}/{video}")
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / "srt_blocks.json"
        
        final_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[AGENT] Wrote {converted_count} readings (LLM+Agent) to {final_path} (Warnings: {latin_warnings})")

if __name__ == "__main__":
    write_b_text_with_llm(sys.argv[1:])
