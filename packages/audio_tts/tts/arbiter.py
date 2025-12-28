from typing import List, Dict, Optional, Any
import os
import re
import json
import hashlib
import time
from pathlib import Path
from .strict_structure import AudioSegment
from .mecab_tokenizer import tokenize_with_mecab
from .voicevox_api import VoicevoxClient
from .reading_dict import (
    ReadingEntry,
    is_banned_surface,
    export_words_for_word_dict,
    load_channel_reading_dict,
    merge_channel_readings,
    normalize_reading_kana,
    is_safe_reading,
)
from .risk_utils import is_trivial_diff
from . import auditor
from .reading_structs import KanaPatch
from .text_normalizer import normalize_text_for_tts

from factory_common.paths import audio_pkg_root, logs_root, video_root

KB_PATH = audio_pkg_root() / "data" / "global_knowledge_base.json"
LEARNING_DICT_PATH = audio_pkg_root() / "configs" / "learning_dict.json"
LLM_LOG_PATH = logs_root() / "tts_llm_usage.log"

# Surfaces that should be kept even if they match MeCab/trivial diff.
FORCE_GLOBAL_SURFACES = {"同じ道"}


def _log_llm_meta(task: str, meta: dict):
    if not meta:
        return
    try:
        LLM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {"task": task, **meta}
        with LLM_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_learning_dict() -> Dict[str, str]:
    """Load global learning dictionary (ignores banned surfaces)."""
    if not LEARNING_DICT_PATH.exists():
        return {}
    try:
        data = json.loads(LEARNING_DICT_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        cleaned: Dict[str, str] = {}
        for surface, reading in data.items():
            key = str(surface).strip()
            if is_banned_surface(key):
                continue
            if not isinstance(reading, str):
                continue
            normalized = normalize_reading_kana(reading)
            if not is_safe_reading(normalized):
                continue
            if normalized == key:
                continue
            cleaned[key] = normalized
        return cleaned
    except Exception:
        return {}

class WordDictionary:
    """単語単位の読み辞書"""
    def __init__(self, path: Path):
        self.path = path
        self.words: Dict[str, str] = self._load()

    def _load(self) -> Dict[str, str]:
        base: Dict[str, str] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                loaded = data.get("words", {}) or {}
                if isinstance(loaded, dict):
                    for word, reading in loaded.items():
                        surface = str(word).strip()
                        if is_banned_surface(surface):
                            continue
                        if not isinstance(reading, str):
                            continue
                        normalized = normalize_reading_kana(reading)
                        if not is_safe_reading(normalized):
                            continue
                        if normalized == surface:
                            continue
                        if surface not in FORCE_GLOBAL_SURFACES:
                            mecab_kana = normalize_reading_kana(get_mecab_reading(surface))
                            if mecab_kana and (
                                mecab_kana == normalized or is_trivial_diff(mecab_kana, normalized)
                            ):
                                continue
                        base[surface] = normalized
            except Exception:
                base = {}

        # Merge global learning dict
        try:
            learning = _load_learning_dict()
            base.update(learning)
        except Exception:
            pass

        return base

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
        if is_banned_surface(word):
            return
        self.words[word] = reading

    def apply_to_text(self, text: str) -> str:
        """辞書にある単語を自動で置換する（トークン単位 + 文字列マッチングの両方で検出）"""
        if not self.words:
            return text
        
        result = text
        # まず文字列マッチングで置換（長い単語から順に処理）
        sorted_words = sorted(self.words.keys(), key=len, reverse=True)
        for word in sorted_words:
            if is_banned_surface(word):
                continue
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
    speaker_id: int,
    channel: Optional[str] = None,
    video: Optional[str] = None,
    skip_tts_reading: bool = False,
) -> Dict[int, List[KanaPatch]]:
    """Strict reading resolver that delegates to auditor (surface-aggregated, max 2 LLM calls).

    Returns patches_by_block for use in synthesis.
    """
    if engine != "voicevox":
        for seg in segments:
            seg.reading = seg.text
        return {}

    if not voicevox_client:
        raise ValueError("Voicevox client required for Strict Mode")

    # 1. 辞書ロード（グローバル + チャンネル固有 + ローカル）
    kb = WordDictionary(KB_PATH)
    channel_dict = load_channel_reading_dict(channel) if channel else {}
    if channel_dict:
        kb.words.update(export_words_for_word_dict(channel_dict))
    # 動画ローカル辞書（audio_prep/local_reading_dict.json）があればマージ
    local_overrides: Dict[int, Dict[int, str]] = {}
    if channel and video:
        video_dir = video_root(channel, video)
        local_dict_path = (
            video_dir / "audio_prep" / "local_reading_dict.json"
        )
        if local_dict_path.exists():
            try:
                local_dict = json.loads(local_dict_path.read_text(encoding="utf-8"))
                for k, v in local_dict.items():
                    if not is_banned_surface(k):
                        kb.words[k] = v
                print(f"[ARBITER] Loaded local_reading_dict.json ({len(local_dict)} entries)")
            except Exception as e:
                print(f"[WARN] Failed to load local_reading_dict.json: {e}")
        # 位置指定オーバーライド（section_id/token_index 単位）
        local_tok_path = (
            video_dir / "audio_prep" / "local_token_overrides.json"
        )
        if local_tok_path.exists():
            try:
                data = json.loads(local_tok_path.read_text(encoding="utf-8"))
                for item in data:
                    sid = int(item.get("section_id", -1))
                    tidx = int(item.get("token_index", -1))
                    reading = item.get("reading") or ""
                    if sid < 0 or tidx < 0 or not reading:
                        continue
                    local_overrides.setdefault(sid, {})[tidx] = reading
                print(f"[ARBITER] Loaded local_token_overrides.json ({len(local_overrides)} sections)")
            except Exception as e:
                print(f"[WARN] Failed to load local_token_overrides.json: {e}")
    # 2. 初期化
    for seg in segments:
        seg.text_for_check = seg.text
        seg.reading = seg.text
        seg.arbiter_verdict = "pending_auditor"

    print(f"[ARBITER] auditing {len(segments)} segments (auditor path)...")
    blocks: List[Dict[str, Any]] = []

    # 3. 各セグメントを blocks に積む（auditor が surface 集約＆2コール上限で処理）
    for i, seg in enumerate(segments):
        target_text = seg.text  # オリジナルのテキスト

        # 3.1 辞書適用＋位置オーバーライドを使ってテキストを再構成する
        tokens = tokenize_with_mecab(target_text)
        patched_parts: List[str] = []
        override_map = local_overrides.get(i) or {}
        for idx_tok, tok in enumerate(tokens):
            if idx_tok in override_map:
                patched_parts.append(override_map[idx_tok])
                continue
            surface = tok.get("surface", "")
            if surface in kb.words:
                patched_parts.append(kb.words[surface])
            else:
                patched_parts.append(surface)
        patched_text = normalize_text_for_tts("".join(patched_parts))

        # 3.2 Voicevox audio_query は辞書/override適用後のテキストで実行
        try:
            query = voicevox_client.audio_query(patched_text, speaker_id)
            vv_kana = query.get("kana", "")
            seg.voicevox_reading = vv_kana
        except Exception as e:
            print(f"[ERROR] Voicevox query failed: {e}")
            raise RuntimeError(f"Voicevox query failed for segment {i}") from e

        # 3.3 MeCab読み（辞書/override適用後のテキストで取得）
        expected_reading = get_mecab_reading(patched_text)
        seg.mecab_reading = expected_reading
        # Synth側で使う読みも辞書適用後で上書き
        seg.reading = patched_text

        blocks.append(
            {
                "index": i,
                "text": target_text,
                "b_text": patched_text,
                "mecab_kana": expected_reading,
                "voicevox_kana": vv_kana,
                "accent_phrases": query.get("accent_phrases") or [],
                "audit_needed": True,
            }
        )

    if skip_tts_reading:
        print("[ARBITER] skip_tts_reading=True -> dictionaries/overrides applied; auditor/LLM skipped.")
        for seg in segments:
            seg.arbiter_verdict = "dict_only_skip_llm"
        return {}

    # 4. auditor に委譲（surface集約＋最大2コール/40件）
    try:
        _, patches_by_block, _, _, _ = auditor.audit_blocks(
            blocks,
            channel=channel,
            video=video,
            channel_dict=channel_dict,
            hazard_dict=None,
            max_ruby_calls=2,
            max_ruby_terms=40,
            enable_vocab=False,
        )
    except Exception as e:
        print(f"[ERROR] auditor failed: {e}")
        raise RuntimeError("auditor failed") from e

    print("[ARBITER] auditor finished (surface aggregation path).")
    return patches_by_block
