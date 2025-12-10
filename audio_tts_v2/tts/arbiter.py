from typing import List, Dict, Optional, Any
import re
import json
import hashlib
import time
from pathlib import Path
from .strict_structure import AudioSegment
from .mecab_tokenizer import tokenize_with_mecab
from .voicevox_api import VoicevoxClient
from factory_common.llm_router import get_router

KB_PATH = Path(__file__).resolve().parents[1] / "data" / "global_knowledge_base.json"

class WordDictionary:
    """単語単位の読み辞書"""
    def __init__(self, path: Path):
        self.path = path
        self.words: Dict[str, str] = self._load()

    def _load(self) -> Dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data.get("words", {})
        except Exception:
            return {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 2,
            "updated_at": time.time(),
            "words": self.words
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, word: str) -> Optional[str]:
        return self.words.get(word)

    def set(self, word: str, reading: str):
        self.words[word] = reading

    def apply_to_text(self, text: str) -> str:
        """辞書にある単語を自動で置換する（トークン単位 + 文字列マッチングの両方で検出）"""
        if not self.words:
            return text
        
        result = text
        # まず文字列マッチングで置換（長い単語から順に処理）
        sorted_words = sorted(self.words.keys(), key=len, reverse=True)
        for word in sorted_words:
            if word in result:
                result = result.replace(word, self.words[word])
        
        # さらにトークン単位でも確認（MeCabの分割結果を考慮）
        tokens = tokenize_with_mecab(text)
        patched_tokens = []
        
        for t in tokens:
            surface = t["surface"]
            if surface in self.words:
                patched_tokens.append(self.words[surface])
            else:
                patched_tokens.append(surface)
        
        token_result = "".join(patched_tokens)
        
        # 文字列マッチングの結果を優先（より確実）
        return result if result != text else token_result

# Normalize Kana
def normalize_kana_for_comparison(text: str) -> str:
    text = text.replace("'", "").replace("/", "").replace("_", "")
    text = text.replace("、", "").replace("。", "").replace("！", "").replace("？", "")
    text = text.replace(" ", "").replace("　", "")
    text = text.translate(str.maketrans(
        {chr(h): chr(h + 0x60) for h in range(ord("ぁ"), ord("ゔ") + 1)}
    ))
    text = re.sub(r"\s+", "", text)
    # 文字列置換によるヒューリスティックな正規化（オウ->オオ等）は行わない。
    # 判定はすべてLLMに委譲する。
    return text

def get_mecab_reading(text: str) -> str:
    tokens = tokenize_with_mecab(text)
    readings = []
    for t in tokens:
        r = t.get("reading_mecab") or t.get("surface") or ""
        readings.append(r)
    return "".join(readings)

def apply_patches(original_text: str, corrections: List[Dict[str, str]]) -> str:
    if not corrections:
        return original_text
        
    tokens = tokenize_with_mecab(original_text)
    patched_tokens = []
    
    correction_map = {c["word"]: c["reading"] for c in corrections}
    
    for t in tokens:
        surface = t["surface"]
        if surface in correction_map:
            patched_tokens.append(correction_map[surface])
        else:
            patched_tokens.append(surface)
            
    return "".join(patched_tokens)

def resolve_readings_strict(
    segments: List[AudioSegment],
    engine: str,
    voicevox_client: Optional[VoicevoxClient],
    speaker_id: int
) -> None:
    
    if engine != "voicevox":
        for seg in segments:
            seg.reading = seg.text 
        return

    if not voicevox_client:
        raise ValueError("Voicevox client required for Strict Mode")

    # 1. 辞書ロード
    kb = WordDictionary(KB_PATH)
    
    # 初期化
    for seg in segments:
        seg.text_for_check = seg.text
        seg.reading = seg.text
        seg.arbiter_verdict = "pending"

    conflicts: List[Dict[str, Any]] = []
    print(f"[ARBITER] auditing {len(segments)} segments...")

    for i, seg in enumerate(segments):
        target_text = seg.text # オリジナルのテキスト
        
        # 1. Voicevoxのデフォルト読みを取得 (Reference B: Actual)
        try:
            query = voicevox_client.audio_query(target_text, speaker_id)
            vv_kana = query.get("kana", "")
            seg.voicevox_reading = vv_kana
        except Exception as e:
            print(f"[ERROR] Voicevox query failed: {e}")
            raise RuntimeError(f"Voicevox query failed for segment {i}") from e

        # 2. 比較対象の読みを取得 (Reference A: Expected)
        # 辞書適用を試みる
        patched_text = kb.apply_to_text(target_text)
        
        expected_reading = ""
        source_type = ""

        if patched_text != target_text:
            # 辞書に登録がある場合、その読みを正解候補とする
            expected_reading = get_mecab_reading(patched_text)
            source_type = "Dictionary"
        else:
            # 辞書になければ、MeCabの標準読みを正解候補とする
            mecab_raw = get_mecab_reading(target_text)
            expected_reading = mecab_raw
            source_type = "MeCab"
        
        seg.mecab_reading = expected_reading # ログ用

        # 3. 厳密な比較
        norm_vv = normalize_kana_for_comparison(vv_kana)
        norm_expected = normalize_kana_for_comparison(expected_reading)
        
        if norm_vv == norm_expected and norm_vv:
            # 完全一致なら、Voicevoxは正しく読めている。
            # 辞書にある単語だとしても、漢字のまま渡すことでアクセント推論を活かす。
            seg.reading = target_text
            seg.arbiter_verdict = f"match_{source_type.lower()}_kept_kanji"
        else:
            # 不一致（辞書指定と違う、またはMeCabと違う）
            
            # 【重要】辞書登録済みの単語でVoicevoxと不一致の場合、辞書を優先する
            if source_type == "Dictionary" and patched_text != target_text:
                # 辞書に登録されている単語は、Voicevoxの読みが違っても辞書を信頼
                # 明らかな誤読として、辞書適用済みテキストを使用
                seg.reading = patched_text
                seg.arbiter_verdict = "dictionary_priority_override"
                print(f"  [DICT] Seg {i}: 辞書優先で修正 - {target_text[:20]}... -> {patched_text[:20]}...")
                continue
            
            # 辞書登録がない場合、または一致しない場合はLLMに判断を委ねる
            # 「招待(ショウタイ)」vs「ショオタイ」のようなケースもここに来るが、LLMなら救える。
            conflicts.append({
                "id": i,
                "text": target_text,
                "expected": expected_reading, # 辞書/MeCabの読み
                "voicevox": vv_kana,          # Voicevoxの読み
                "source": source_type         # 何と食い違ったか
            })

    if not conflicts:
        print("[ARBITER] No conflicts.")
        return

    print(f"[ARBITER] Resolving {len(conflicts)} conflicts via LLM...")
    
    CHUNK_SIZE = 10
    router = get_router()
    
    for i in range(0, len(conflicts), CHUNK_SIZE):
        batch = conflicts[i:i+CHUNK_SIZE]
        
        prompt = f"""
あなたはプロのナレーターの読みを校正するAIです。
以下のテキストは、辞書指定(Dictionary)または標準辞書(MeCab)と、音声合成エンジン(Voicevox)の間で読みが食い違っています。

【最重要原則: Voicevoxの読みを優先せよ】
1. Voicevoxは漢字文脈からアクセントと読みを高精度に推論します。
2. 読み仮名をカタカナに開くとアクセント情報が失われ、一本調子になります。
3. **Voicevoxの読みが文脈的に自然であれば、辞書/MeCabと違っても修正しないでください（Correctionsを空にする）。**
4. **修正は「明確に誤読である」と確信できる場合のみ行ってください。**

【修正しない方が良い判断基準】
- Voicevoxの読みが文脈的に成立する
- 表記の違いだけで、発音として問題がない（長音、促音などの表記揺れ）
- 迷う場合は「修正しない」を選ぶ（Voicevoxを信頼）

■ 修正不要のケース (Corrections: []) - **これらは誤読ではありません**
1.  **長音の母音化**:
    *   辞書:「ショウタイ(招待)」 vs Voicevox:「ショオタイ」 -> **正解** (修正不要)
    *   辞書:「コウコウ(高校)」 vs Voicevox:「コオコオ」 -> **正解** (修正不要)
    *   辞書:「エイエン(永遠)」 vs Voicevox:「エエエン」 -> **正解** (修正不要)
2.  **助詞の発音**:
    *   「～へ」 -> 「エ」
    *   「言う」 -> 「イウ」/「ユウ」
3.  **無声化・微妙な揺れ**:
    *   「シテ」 vs 「シ_テ」 (無声化記号) -> **正解** (修正不要)

■ 修正が必要なケース (Corrections あり)
1.  **辞書指定との明白な乖離**:
    *   辞書:「コンニチ(今日)」 vs Voicevox:「キョウ」 -> 文脈が「本日は」なら修正必須。
2.  **同形異音の誤読**:
    *   「人気(ひとけ)」 vs Voicevox:「ニンキ」 -> **修正対象**
    *   「行って(おこなって)」 vs Voicevox:「イッテ」 -> **修正対象**
    *   「怒り(イカリ)」 vs Voicevox:「オコリ」 -> **修正対象** (「オコリ」は「起こり」等別の意味になるため不可)
    *   「その分(ソノブン)」 vs Voicevox:「ソノワケ」 -> **修正対象** (「分」は「ブン」と読む)
    *   「同じ道(オナジミチ)」 vs Voicevox:「ドオジミチ」 -> **修正対象** (「同じ」は「オナジ」と読む)
3.  **固有名詞の明白な誤読**:
    *   「大和(ヤマト)」 vs Voicevox:「ダイワ」 -> **修正対象**

出力はJSON形式のリストで返してください。
**迷ったら必ず修正なし（空リスト）を選んでください。** Voicevoxの読みを優先することが最優先事項です。
漢字のままVoicevoxに渡すことで、最高品質の音声が生成されます。

[
  {{
    "id": 0,
    "corrections": [
      {{ "word": "人気", "reading": "ヒトケ" }}
    ]
  }},
  {{
    "id": 1,
    "corrections": [] 
  }}
]

対象リスト:
{json.dumps(batch, ensure_ascii=False, indent=2)}
"""
        messages = [
            {"role": "system", "content": "You are a Japanese Reading Arbiter. Return strict JSON."},
            {"role": "user", "content": prompt}
        ]
        
        try:
            content = router.call(
                task="tts_reading",
                messages=messages,
                temperature=0.0,
                response_format="json_object"
            )
            
            response = []
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "content" in parsed:
                    # Handle case where LLM returns wrapper
                    response = parsed["content"]
                elif isinstance(parsed, list):
                    response = parsed
                elif isinstance(parsed, dict) and "corrections" in parsed:
                    # Single object? unlikely but possible
                     response = [parsed]
                elif isinstance(parsed, dict):
                    # Maybe {"results": [...]} or similar?
                    # Try to find a list value
                    for v in parsed.values():
                        if isinstance(v, list):
                            response = v
                            break
            except json.JSONDecodeError:
                print(f"[WARN] Failed to parse JSON from Arbiter LLM. Content: {content[:100]}...")
                response = []

            resp_map = {}
            if isinstance(response, list):
                for item in response:
                    if "id" in item:
                        resp_map[item["id"]] = item.get("corrections", [])
            
            for item in batch:
                idx = item["id"]
                seg = segments[idx]
                target_text = seg.text # オリジナル漢字

                if idx in resp_map:
                    corrections = resp_map[idx]
                    
                    if not corrections:
                        # LLM判定: Voicevoxの読みでOK（表記揺れ含む）
                        # 漢字のまま採用！
                        seg.reading = target_text
                        seg.arbiter_verdict = "llm_approved_voicevox_kanji"
                    else:
                        # LLM判定: 誤読あり。修正適用。
                        # ここで初めてカタカナに開かれる。
                        fixed_text = apply_patches(target_text, corrections)
                        seg.reading = fixed_text
                        seg.arbiter_verdict = "llm_fixed_misreading"
                        print(f"  [PATCH] Seg {idx}: {target_text[:15]}... -> {fixed_text[:15]}...")
                        
                        # 辞書学習（誤読パターンのみ登録）
                        for corr in corrections:
                            word = corr.get("word")
                            reading = corr.get("reading")
                            if word and reading:
                                print(f"  [LEARN] {word} -> {reading}")
                                kb.set(word, reading)
                else:
                    # Fallback
                    print(f"  [WARN] LLM no verdict for Seg {idx}. Keeping Kanji.")
                    seg.reading = target_text
                    seg.arbiter_verdict = "fallback_kanji"

        except Exception as e:
            print(f"[ERROR] Arbiter LLM failed for batch {i}: {e}")
            raise RuntimeError("Arbiter failed. Pipeline stopped.") from e
            
    kb.save()
    print(f"[ARBITER] Knowledge Base updated at {KB_PATH}")