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
VOICEPEAK_DICT_PATH = audio_pkg_root() / "data" / "voicepeak" / "dic.json"
VOICEPEAK_SETTINGS_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Dreamtonics"
    / "Voicepeak"
    / "settings"
)
VOICEPEAK_LOCAL_DICT_PATH = VOICEPEAK_SETTINGS_DIR / "dic.json"
VOICEPEAK_LOCAL_USER_CSV_PATH = VOICEPEAK_SETTINGS_DIR / "user.csv"
LLM_LOG_PATH = logs_root() / "tts_llm_usage.log"

# Surfaces that should be kept even if they match MeCab/trivial diff.
FORCE_GLOBAL_SURFACES = {"同じ道"}

_VOICEPEAK_COMMA_DROP_PARTICLES_DEFAULT = {"は", "が", "に", "で", "も", "へ", "を"}


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
        surface = t.get("surface") or ""
        pos = str(t.get("pos") or "")
        reading_mecab = t.get("reading_mecab") or ""
        # MeCab readings for kana-only tokens can be dictionary-form (e.g., 「い」->「イル」),
        # which is NOT what we want for spoken TTS baselines. Prefer the surface when it is
        # already kana-only; otherwise use MeCab's reading when available.
        if isinstance(surface, str) and re.fullmatch(r"[\u3040-\u309f\u30a0-\u30ffー]+", surface):
            reading = surface
        else:
            reading = reading_mecab or surface or ""

        # Normalize particle orthography to spoken kana for comparison baselines.
        # - は/へ/を are written as-is but pronounced わ/え/お when used as function words.
        # - MeCab may also keep them inside combined tokens (e.g., 「では」 as 接続詞 -> 「デハ」).
        if pos in {"助詞", "助動詞", "接続詞"} and isinstance(surface, str) and surface:
            if surface.endswith(("は", "ハ")) and reading.endswith(("は", "ハ")):
                reading = reading[:-1] + ("わ" if reading.endswith("は") else "ワ")
            elif surface.endswith(("へ", "ヘ")) and reading.endswith(("へ", "ヘ")):
                reading = reading[:-1] + ("え" if reading.endswith("へ") else "エ")
            elif surface.endswith(("を", "ヲ")) and reading.endswith(("を", "ヲ")):
                reading = reading[:-1] + ("お" if reading.endswith("を") else "オ")

        readings.append(reading)
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


def _load_voicepeak_repo_dict_words() -> Dict[str, str]:
    """
    Load repo-managed Voicepeak user dictionary and return a safe subset as surface->pron map.

    NOTE:
    - This is for *text-level* replacement used by the strict pipeline (seg.reading).
    - We intentionally filter out banned/ambiguous surfaces (e.g. 1-char tokens like "何") so we
      don't force a reading that should remain context-dependent.
    - The full dictionary is still applied inside Voicepeak itself via `sync_voicepeak_user_dict`.
    """
    if not VOICEPEAK_DICT_PATH.exists():
        return {}
    try:
        payload = json.loads(VOICEPEAK_DICT_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return {}
    except Exception:
        return {}

    out: Dict[str, str] = {}
    for ent in payload:
        if not isinstance(ent, dict):
            continue
        surface = str(ent.get("sur") or "").strip()
        pron = str(ent.get("pron") or "").strip()
        if not surface or not pron:
            continue
        # Safety: keep strict pipeline conservative (avoid 1-char & context-dependent terms).
        if is_banned_surface(surface):
            continue
        normalized = normalize_reading_kana(pron)
        if not is_safe_reading(normalized):
            continue
        if normalized == surface:
            continue
        out[surface] = normalized
    return out


def _load_voicepeak_local_dict_words() -> Dict[str, str]:
    """
    Load the user's local Voicepeak dictionary (GUI-edited) and return a safe subset as surface->pron map.

    This ensures the STRICT pipeline respects the user's manually curated dict even when the repo
    dict is not updated yet.
    """
    if not VOICEPEAK_LOCAL_DICT_PATH.exists():
        return {}
    try:
        payload = json.loads(VOICEPEAK_LOCAL_DICT_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return {}
    except Exception:
        return {}

    out: Dict[str, str] = {}
    for ent in payload:
        if not isinstance(ent, dict):
            continue
        surface = str(ent.get("sur") or "").strip()
        pron = str(ent.get("pron") or "").strip()
        if not surface or not pron:
            continue
        if is_banned_surface(surface):
            continue
        normalized = normalize_reading_kana(pron)
        if not is_safe_reading(normalized):
            continue
        if normalized == surface:
            continue
        out[surface] = normalized
    return out


def _load_voicepeak_local_user_csv_words() -> Dict[str, str]:
    """
    Load the user's local Voicepeak user.csv (GUI-edited/exported) as surface->pron map.

    Voicepeak stores a large portion of user-added terms in user.csv/user.dic.
    For STRICT reading replacement we consume user.csv best-effort (safe subset only).
    """
    if not VOICEPEAK_LOCAL_USER_CSV_PATH.exists():
        return {}

    import csv

    out: Dict[str, str] = {}
    try:
        with VOICEPEAK_LOCAL_USER_CSV_PATH.open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                surface = str(row[0] or "").strip()
                pron = str(row[6] or "").strip() if len(row) > 6 else ""
                if not surface or not pron:
                    continue
                if is_banned_surface(surface):
                    continue
                normalized = normalize_reading_kana(pron)
                if not is_safe_reading(normalized):
                    continue
                if normalized == surface:
                    continue
                out[surface] = normalized
    except Exception:
        return {}

    return out


def _load_voicepeak_comma_policy(channel: Optional[str]) -> tuple[str, set[str]]:
    """
    Load Voicepeak comma smoothing policy from per-channel voice_config.json.

    We keep this inside arbiter so we don't need to thread config through every call-site.
    """
    if not channel:
        return ("", set(_VOICEPEAK_COMMA_DROP_PARTICLES_DEFAULT))

    try:
        from .routing import load_default_voice_config

        voice_cfg = load_default_voice_config(channel)
        engine_opts = (voice_cfg or {}).get("engine_options") if isinstance(voice_cfg, dict) else {}
        engine_opts = engine_opts if isinstance(engine_opts, dict) else {}
    except Exception:
        return ("", set(_VOICEPEAK_COMMA_DROP_PARTICLES_DEFAULT))

    policy = str(engine_opts.get("comma_policy") or "").strip().lower()
    raw_particles = engine_opts.get("comma_drop_particles")
    particles = set(_VOICEPEAK_COMMA_DROP_PARTICLES_DEFAULT)
    if isinstance(raw_particles, list):
        cleaned = {str(x).strip() for x in raw_particles if str(x).strip()}
        if cleaned:
            particles = cleaned

    return (policy, particles)


def _apply_voicepeak_comma_policy(text: str, policy: str, drop_particles: set[str]) -> str:
    """
    Reduce choppy pacing in Voicepeak by dropping some Japanese commas (読点).

    Policy:
      - 'particles' / 'drop_after_particles': remove '、' when it follows common particles
        like 'は/が/に/で/も/へ/を' (configurable).
    """
    if not text:
        return text
    if not policy:
        return text
    if policy not in {"particles", "particle", "drop_after_particles"}:
        return text
    if "、" not in text:
        return text

    tokens = tokenize_with_mecab(text)
    if not tokens:
        return text

    drop_char_positions: set[int] = set()
    for i, tok in enumerate(tokens):
        if str(tok.get("surface") or "") != "、":
            continue
        if i <= 0:
            continue
        prev_surface = str(tokens[i - 1].get("surface") or "")
        if prev_surface not in drop_particles:
            continue
        try:
            start = int(tok.get("char_start", -1))
        except Exception:
            start = -1
        if 0 <= start < len(text):
            drop_char_positions.add(start)

    if not drop_char_positions:
        return text

    out_chars: List[str] = []
    for idx, ch in enumerate(text):
        if idx in drop_char_positions and ch == "、":
            continue
        out_chars.append(ch)
    return "".join(out_chars)


def _apply_phrase_dict(text: str, words: Dict[str, str]) -> str:
    """Apply phrase-level replacements (longer surfaces first)."""
    if not text or not words:
        return text
    out = text
    for surface in sorted(words.keys(), key=len, reverse=True):
        if surface and surface in out:
            out = out.replace(surface, words[surface])
    return out


_ASCII_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\\-]*$")
_VOICEVOX_LETTER_KANA = {
    "A": "エー",
    "B": "ビー",
    "C": "スィー",  # VOICEVOX normalizes シー -> スィー, so prefer スィー for stable comparisons
    "D": "ディー",
    "E": "イー",
    "F": "エフ",
    "G": "ジー",
    "H": "エイチ",
    "I": "アイ",
    "J": "ジェイ",
    "K": "ケー",
    "L": "エル",
    "M": "エム",
    "N": "エヌ",
    "O": "オー",
    "P": "ピー",
    "Q": "キュー",
    "R": "アール",
    "S": "エス",
    "T": "ティー",
    "U": "ユー",
    "V": "ブイ",
    "W": "ダブリュー",
    "X": "エックス",
    "Y": "ワイ",
    "Z": "ゼット",
}
_ASCII_WORD_OVERRIDES = {
    # Minimal set: keep global behavior stable; channel dict can override richer vocabulary.
    "and": "アンド",
    "or": "オア",
    "it": "イット",
    "through": "スルー",
}


def _ascii_token_to_kana(surface: str) -> Optional[str]:
    """Best-effort ASCII token -> Katakana reading (B-text only)."""
    s = str(surface or "").strip()
    if not s or not _ASCII_TOKEN_RE.match(s):
        return None

    lower = s.lower()
    if lower in _ASCII_WORD_OVERRIDES:
        return _ASCII_WORD_OVERRIDES[lower]

    # Known all-caps words often used as loanwords
    if s == "LINE":
        return "ライン"
    if s == "OFF":
        return "オフ"

    buf: List[str] = []
    for ch in s:
        if "A" <= ch <= "Z" or "a" <= ch <= "z":
            kana = _VOICEVOX_LETTER_KANA.get(ch.upper())
            if kana:
                buf.append(kana)
            continue
        if "0" <= ch <= "9":
            buf.append(
                ["ゼロ", "イチ", "ニ", "サン", "ヨン", "ゴ", "ロク", "ナナ", "ハチ", "キュウ"][ord(ch) - ord("0")]
            )
            continue
        # ignore separators like '.' / '-'
    out = "".join(buf)
    return out or None


def _jp_number_kana_under_10000(n: int) -> str:
    digits = ["ゼロ", "イチ", "ニ", "サン", "ヨン", "ゴ", "ロク", "ナナ", "ハチ", "キュウ"]
    if n <= 0:
        return ""
    parts: List[str] = []
    thousands = (n // 1000) % 10
    hundreds = (n // 100) % 10
    tens = (n // 10) % 10
    ones = n % 10

    if thousands:
        if thousands == 1:
            parts.append("セン")
        elif thousands == 3:
            parts.append("サンゼン")
        elif thousands == 8:
            parts.append("ハッセン")
        else:
            parts.append(digits[thousands] + "セン")
    if hundreds:
        if hundreds == 1:
            parts.append("ヒャク")
        elif hundreds == 3:
            parts.append("サンビャク")
        elif hundreds == 6:
            parts.append("ロッピャク")
        elif hundreds == 8:
            parts.append("ハッピャク")
        else:
            parts.append(digits[hundreds] + "ヒャク")
    if tens:
        if tens == 1:
            parts.append("ジュウ")
        else:
            parts.append(digits[tens] + "ジュウ")
    if ones:
        parts.append(digits[ones])
    return "".join(parts)


def _jp_number_kana(n: int) -> str:
    """Arabic integer -> Katakana reading (no counters)."""
    if n == 0:
        return "ゼロ"
    if n < 0:
        return "マイナス" + _jp_number_kana(-n)

    units = [
        (10**12, "チョウ"),
        (10**8, "オク"),
        (10**4, "マン"),
        (1, ""),
    ]
    parts: List[str] = []
    remaining = n
    for base, unit in units:
        chunk = remaining // base
        remaining = remaining % base
        if chunk <= 0:
            continue
        chunk_read = _jp_number_kana_under_10000(int(chunk))
        if not chunk_read:
            continue
        parts.append(chunk_read + unit)
    return "".join(parts) or "ゼロ"


def _jp_number_with_counter_kana(n: int, counter: str) -> str:
    """Arabic integer + counter -> Katakana reading."""
    counter = str(counter or "")

    if counter == "つ":
        special = {
            1: "ヒトツ",
            2: "フタツ",
            3: "ミッツ",
            4: "ヨッツ",
            5: "イツツ",
            6: "ムッツ",
            7: "ナナツ",
            8: "ヤッツ",
            9: "ココノツ",
            10: "トオ",
        }
        return special.get(n) or (_jp_number_kana(n) + "ツ")

    if counter == "人":
        if n == 1:
            return "ヒトリ"
        if n == 2:
            return "フタリ"
        return _jp_number_kana(n) + "ニン"

    if counter == "回":
        special = {1: "イッカイ", 6: "ロッカイ", 8: "ハッカイ", 10: "ジュッカイ"}
        return special.get(n) or (_jp_number_kana(n) + "カイ")

    if counter == "個":
        special = {1: "イッコ", 6: "ロッコ", 8: "ハッコ", 10: "ジュッコ"}
        return special.get(n) or (_jp_number_kana(n) + "コ")

    if counter == "分":
        special = {
            1: "イップン",
            2: "ニフン",
            3: "サンプン",
            4: "ヨンプン",
            5: "ゴフン",
            6: "ロップン",
            7: "ナナフン",
            8: "ハップン",
            9: "キュウフン",
            10: "ジュップン",
            20: "ニジュップン",
            30: "サンジュップン",
            40: "ヨンジュップン",
            50: "ゴジュップン",
            60: "ロクジュップン",
            70: "ナナジュップン",
            80: "ハチジュップン",
            90: "キュウジュップン",
        }
        return special.get(n) or (_jp_number_kana(n) + "フン")

    if counter == "分間":
        # Mirror "分" assimilation (イップンカン / ジュップンカン etc).
        return _jp_number_with_counter_kana(n, "分") + "カン"

    if counter == "秒":
        return _jp_number_kana(n) + "ビョウ"

    if counter == "歳":
        special = {
            1: "イッサイ",
            8: "ハッサイ",
            10: "ジュッサイ",
            20: "ニジュッサイ",
            30: "サンジュッサイ",
            40: "ヨンジュッサイ",
            50: "ゴジュッサイ",
            60: "ロクジュッサイ",
            70: "ナナジュッサイ",
            80: "ハチジュッサイ",
            90: "キュウジュッサイ",
        }
        return special.get(n) or (_jp_number_kana(n) + "サイ")

    if counter == "日":
        special = {
            1: "イチニチ",
            2: "フツカ",
            3: "ミッカ",
            4: "ヨッカ",
            5: "イツカ",
            6: "ムイカ",
            7: "ナノカ",
            8: "ヨウカ",
            9: "ココノカ",
            10: "トオカ",
            20: "ハツカ",
        }
        return special.get(n) or (_jp_number_kana(n) + "ニチ")

    if counter == "ヶ月":
        special = {1: "イッカゲツ", 6: "ロッカゲツ", 8: "ハッカゲツ", 10: "ジュッカゲツ"}
        return special.get(n) or (_jp_number_kana(n) + "カゲツ")

    if counter == "年":
        if n % 10 == 4:
            prefix = _jp_number_kana(n - 4) if n >= 4 else ""
            return (prefix + "ヨネン") if prefix else "ヨネン"
        return _jp_number_kana(n) + "ネン"

    if counter == "時":
        special = {0: "レイジ", 4: "ヨジ", 7: "シチジ", 9: "クジ"}
        if n in special:
            return special[n]
        if n % 10 == 4:
            prefix = _jp_number_kana(n - 4) if n >= 4 else ""
            return (prefix + "ヨジ") if prefix else "ヨジ"
        return _jp_number_kana(n) + "ジ"

    if counter == "時間":
        if n % 10 == 4:
            prefix = _jp_number_kana(n - 4) if n >= 4 else ""
            return (prefix + "ヨジカン") if prefix else "ヨジカン"
        return _jp_number_kana(n) + "ジカン"

    if counter == "円":
        return _jp_number_kana(n) + "エン"

    if counter == "点":
        return _jp_number_kana(n) + "テン"

    if counter == "割":
        return _jp_number_kana(n) + "ワリ"

    if counter in {"%", "パーセント"}:
        return _jp_number_kana(n) + "パーセント"

    # Fallback
    return _jp_number_kana(n) + counter


def _try_numeric_replacement(tokens: List[Dict[str, object]], i: int) -> Optional[tuple[str, int]]:
    """Return (replacement, end_index) for numeric token sequences, else None."""
    try:
        surface = str(tokens[i].get("surface") or "")
    except Exception:
        return None
    if not surface or not surface.isdigit():
        return None
    n = int(surface)
    n_tokens = len(tokens)

    # Decimal: 0 . 5  -> レイテンゴ (best-effort)
    if i + 2 < n_tokens:
        mid = str(tokens[i + 1].get("surface") or "")
        right = str(tokens[i + 2].get("surface") or "")
        if mid in {".", "．"} and right.isdigit():
            left_read = "レイ" if n == 0 else _jp_number_kana(n)
            return (left_read + "テン" + _jp_number_kana(int(right)), i + 2)

    # Fraction / minute+particle: 100 分の 1 / 30 分の 余白
    # NOTE: When the RHS is not a number, treat "X分のY" as "X分の(Y)" (= X minutes of Y).
    if i + 1 < n_tokens and str(tokens[i + 1].get("surface") or "") == "分の":
        right = str(tokens[i + 2].get("surface") or "") if (i + 2) < n_tokens else ""
        if right.isdigit():
            return (_jp_number_kana(n) + "ブンノ" + _jp_number_kana(int(right)), i + 2)
        return (_jp_number_with_counter_kana(n, "分") + "ノ", i + 1)

    # Range: 2 から 3
    if i + 2 < n_tokens and str(tokens[i + 1].get("surface") or "") == "から":
        right = str(tokens[i + 2].get("surface") or "")
        if right.isdigit():
            return (_jp_number_kana(n) + "カラ" + _jp_number_kana(int(right)), i + 2)

    # Large units: 100 万 円 / 1600 億 ドル / 20 兆 円
    if i + 1 < n_tokens and str(tokens[i + 1].get("surface") or "") in {"万", "億", "兆"}:
        unit = str(tokens[i + 1].get("surface") or "")
        unit_kana = {"万": "マン", "億": "オク", "兆": "チョウ"}.get(unit, unit)
        end = i + 1
        suffix = ""
        if i + 2 < n_tokens:
            s2 = str(tokens[i + 2].get("surface") or "")
            if s2 in {"円", "ドル"}:
                suffix = "エン" if s2 == "円" else "ドル"
                end = i + 2
        return (_jp_number_kana(n) + unit_kana + suffix, end)

    # Simple counter: 94 年 / 9 歳 / 98 パーセント / 10 分間 ...
    if i + 1 < n_tokens:
        counter = str(tokens[i + 1].get("surface") or "")
        if counter:
            if counter in {
                "年",
                "歳",
                "人",
                "回",
                "個",
                "つ",
                "分",
                "分間",
                "秒",
                "時間",
                "時",
                "日",
                "ヶ月",
                "円",
                "点",
                "割",
                "%",
                "パーセント",
            }:
                return (_jp_number_with_counter_kana(n, counter), i + 1)

    # Bare number (fallback): still convert to avoid digits staying in B-text.
    return (_jp_number_kana(n), i)


_KANJI_NUM_DIGITS: dict[str, int] = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_KANJI_NUM_SMALL_UNITS: dict[str, int] = {"十": 10, "百": 100, "千": 1000}
_KANJI_NUM_BIG_UNITS: dict[str, int] = {"万": 10**4, "億": 10**8, "兆": 10**12}
_KANJI_NUM_CHARS = set(_KANJI_NUM_DIGITS) | set(_KANJI_NUM_SMALL_UNITS) | set(_KANJI_NUM_BIG_UNITS)


def _parse_kanji_number(seq: List[str]) -> Optional[int]:
    """Parse a Kanji number sequence (tokens) into an int (best-effort)."""
    if not seq:
        return None
    if any(ch not in _KANJI_NUM_CHARS for ch in seq):
        return None

    total = 0
    current = 0
    num = 0
    for ch in seq:
        if ch in _KANJI_NUM_DIGITS:
            num = _KANJI_NUM_DIGITS[ch]
            continue
        if ch in _KANJI_NUM_SMALL_UNITS:
            unit = _KANJI_NUM_SMALL_UNITS[ch]
            current += (num or 1) * unit
            num = 0
            continue
        if ch in _KANJI_NUM_BIG_UNITS:
            unit = _KANJI_NUM_BIG_UNITS[ch]
            current += num
            num = 0
            total += (current or 1) * unit
            current = 0
            continue
        return None
    return total + current + num


def _is_kanji_number_token(tok: Dict[str, object]) -> bool:
    surface = str(tok.get("surface") or "")
    subpos = str(tok.get("subpos") or "")
    if subpos != "数":
        return False
    if not surface:
        return False
    return all(ch in _KANJI_NUM_CHARS for ch in surface)


def _try_kanji_numeric_replacement(tokens: List[Dict[str, object]], i: int) -> Optional[tuple[str, int]]:
    """Return (replacement, end_index) for Kanji-number + counter sequences, else None."""
    n_tokens = len(tokens)
    if i < 0 or i >= n_tokens:
        return None
    # Avoid converting approximations like "数十万円" into a concrete value.
    prev_surface = str(tokens[i - 1].get("surface") or "") if i > 0 else ""
    if prev_surface == "数":
        return None
    if not _is_kanji_number_token(tokens[i]):
        return None

    seq: List[str] = []
    j = i
    while j < n_tokens and _is_kanji_number_token(tokens[j]) and len(seq) < 16:
        seq.append(str(tokens[j].get("surface") or ""))
        j += 1
    # Don't treat bare big-units like "万/億/兆" as "一万/一億/一兆".
    if len(seq) == 1 and seq[0] in _KANJI_NUM_BIG_UNITS:
        return None

    n_val = _parse_kanji_number(seq)
    if n_val is None:
        return None

    if j >= n_tokens:
        return None

    counter = str(tokens[j].get("surface") or "")
    if not counter:
        return None

    # Fraction / minute+particle: 三十 分の 一 / 三十 分の 余白
    if counter == "分の":
        # RHS numeric => true fraction
        if (j + 1) < n_tokens:
            rhs_surface = str(tokens[j + 1].get("surface") or "")
            if rhs_surface.isdigit():
                return (_jp_number_kana(n_val) + "ブンノ" + _jp_number_kana(int(rhs_surface)), j + 1)
            if _is_kanji_number_token(tokens[j + 1]):
                rhs_seq: List[str] = []
                k = j + 1
                while k < n_tokens and _is_kanji_number_token(tokens[k]) and len(rhs_seq) < 16:
                    rhs_seq.append(str(tokens[k].get("surface") or ""))
                    k += 1
                rhs_val = _parse_kanji_number(rhs_seq)
                if rhs_val is not None:
                    return (_jp_number_kana(n_val) + "ブンノ" + _jp_number_kana(rhs_val), k - 1)
        # Otherwise: treat as minutes + particle "の" (e.g., 三十分の余白)
        return (_jp_number_with_counter_kana(n_val, "分") + "ノ", j)

    # Simple counters
    if counter in {
        "年",
        "歳",
        "人",
        "回",
        "個",
        "つ",
        "分",
        "分間",
        "秒",
        "時間",
        "時",
        "日",
        "ヶ月",
        "円",
        "点",
        "割",
        "%",
        "パーセント",
    }:
        return (_jp_number_with_counter_kana(n_val, counter), j)

    return None

def _patch_tokens_with_words(
    tokens: List[Dict[str, object]],
    words: Dict[str, str],
    override_map: Dict[int, str],
) -> str:
    """
    Apply overrides + dictionary to tokenized text with "longest match" over token sequences.

    Why:
    - Token-level replacement first can break multi-token surfaces (e.g. "信長" -> "ノブナガ"
      prevents "信長公記" -> "シンチョオコオキ").
    - This function gives precedence to multi-token surfaces while still respecting per-token
      overrides (local_token_overrides.json).
    """
    if not tokens:
        return ""

    override_keys = set(override_map.keys())
    prefix = [0] * (len(tokens) + 1)
    for idx in range(len(tokens)):
        prefix[idx + 1] = prefix[idx] + (1 if idx in override_keys else 0)

    def has_override_in_range(start: int, end: int) -> bool:
        return (prefix[end + 1] - prefix[start]) > 0

    def _is_kana_like(s: str) -> bool:
        if not s:
            return False
        for ch in s:
            o = ord(ch)
            if 0x3040 <= o <= 0x309F:  # Hiragana
                continue
            if 0x30A0 <= o <= 0x30FF:  # Katakana (incl. long vowel mark)
                continue
            if ch in {"ー", "・"}:
                continue
            return False
        return True

    def _is_ascii_like(s: str) -> bool:
        if not s:
            return False
        for ch in s:
            if "0" <= ch <= "9":
                continue
            if "A" <= ch <= "Z" or "a" <= ch <= "z":
                continue
            if ch in {"_", "-", ".", "&", "+", "/"}:
                continue
            return False
        return True

    def _phrase_surface(start: int, end: int) -> str:
        return "".join(str(tokens[k].get("surface") or "") for k in range(start, end + 1))

    def _phrase_spoken_from_left(start: int, end: int) -> str:
        # Prefer dictionary (supports multi-token surfaces).
        surf = _phrase_surface(start, end)
        if words:
            repl = words.get(surf)
            if isinstance(repl, str) and repl:
                normalized = normalize_reading_kana(repl)
                if is_safe_reading(normalized):
                    return normalized
        # Fallback: MeCab reading for JP phrases (ASCII surfaces often fail safety checks).
        reading = "".join(str(tokens[k].get("reading_mecab") or "") for k in range(start, end + 1))
        normalized = normalize_reading_kana(reading)
        return normalized if is_safe_reading(normalized) else ""

    def _phrase_spoken_from_right(start: int, end: int) -> str:
        surf = _phrase_surface(start, end)
        if words:
            repl = words.get(surf)
            if isinstance(repl, str) and repl:
                normalized = normalize_reading_kana(repl)
                if is_safe_reading(normalized):
                    return normalized
        if _is_kana_like(surf):
            normalized = normalize_reading_kana(surf)
            return normalized if is_safe_reading(normalized) else ""
        return ""

    def _left_phrase_bounds(before_idx: int) -> Optional[tuple[int, int]]:
        if before_idx < 0:
            return None
        end = before_idx
        start = end
        while start - 1 >= 0:
            prev = tokens[start - 1]
            surface = str(prev.get("surface") or "")
            if not surface:
                break
            if surface in {"、", "。", "（", "）", "「", "」", "(", ")", "[", "]"}:
                break
            pos = str(prev.get("pos") or "")
            if pos in {"名詞", "接尾", "接頭詞"}:
                start -= 1
                continue
            break
        return (start, end) if start <= end else None

    def _compute_inline_annotation_skips() -> set[int]:
        """
        Drop inline reading hints from B-text only, e.g.:
        - 刈羽郡、かりわぐん
        - 大河内正敏、おおこうちまさとし
        - Apple（アップル）
        - 禅、Zen  (only when Zen is mapped to ゼン via dict)
        """
        skip: set[int] = set()
        n = len(tokens)

        def _mark_skip(start: int, end: int) -> None:
            if start < 0 or end < start:
                return
            if has_override_in_range(start, end):
                return
            for k in range(start, end + 1):
                skip.add(k)

        # Parentheses-based annotations: X（Y） where spoken(X)==spoken(Y)
        for i in range(n):
            if str(tokens[i].get("surface") or "") != "（":
                continue
            left_bounds = _left_phrase_bounds(i - 1)
            if not left_bounds:
                continue
            ls, le = left_bounds
            # Find closing paren.
            close = None
            for j in range(i + 1, min(i + 32, n)):
                if str(tokens[j].get("surface") or "") == "）":
                    close = j
                    break
            if close is None or close <= i + 1:
                continue
            left_spoken = _phrase_spoken_from_left(ls, le)
            if not left_spoken:
                continue
            right_spoken = _phrase_spoken_from_right(i + 1, close - 1)
            if not right_spoken:
                continue
            if normalize_reading_kana(left_spoken) == normalize_reading_kana(right_spoken):
                _mark_skip(i, close)

        # Comma-based annotations: X、Y where spoken(X)==spoken(Y)
        for i in range(n):
            if str(tokens[i].get("surface") or "") != "、":
                continue
            if i in skip:
                continue
            left_bounds = _left_phrase_bounds(i - 1)
            if not left_bounds:
                continue
            ls, le = left_bounds
            left_spoken = _phrase_spoken_from_left(ls, le)
            if not left_spoken:
                continue

            # 1) Kana reading chunk (often split into multiple tokens).
            cand = ""
            for j in range(i + 1, min(i + 32, n)):
                surface = str(tokens[j].get("surface") or "")
                if not surface:
                    break
                if surface in {"、", "。", "（", "）", "「", "」", "(", ")", "[", "]"}:
                    break
                if not _is_kana_like(surface):
                    break
                cand += surface
                cand_spoken = normalize_reading_kana(cand)
                if not left_spoken.startswith(cand_spoken):
                    break
                if cand_spoken == left_spoken:
                    _mark_skip(i, j)
                    break

            if i in skip:
                continue

            # 2) ASCII token(s) mapped by dict (e.g., 禅、Zen).
            for j in range(i + 1, min(i + 6, n)):
                surface = str(tokens[j].get("surface") or "")
                if not surface:
                    break
                if surface in {"、", "。", "（", "）", "「", "」", "(", ")", "[", "]"}:
                    break
                if not _is_ascii_like(surface):
                    break
                right_spoken = _phrase_spoken_from_right(i + 1, j)
                if right_spoken and normalize_reading_kana(right_spoken) == normalize_reading_kana(left_spoken):
                    _mark_skip(i, j)
                    break

        return skip

    skip_indices = _compute_inline_annotation_skips()

    parts: List[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        if i in skip_indices:
            i += 1
            continue
        if i in override_map:
            parts.append(str(override_map[i]))
            i += 1
            continue

        best_repl: Optional[str] = None
        best_j: Optional[int] = None
        if words:
            cand = ""
            for j in range(i, n):
                tok = tokens[j]
                if j in skip_indices:
                    break
                if tok.get("pos") == "silence_tag":
                    break
                surface = str(tok.get("surface") or "")
                if not surface:
                    break
                cand += surface
                if (j - i + 1) >= 2 and not has_override_in_range(i, j):
                    repl = words.get(cand)
                    if repl:
                        best_repl = repl
                        best_j = j

        if best_repl is not None and best_j is not None:
            parts.append(best_repl)
            i = best_j + 1
            continue

        surface = str(tokens[i].get("surface") or "")
        # Single-token dictionary replacement
        if words:
            direct = words.get(surface)
            if direct:
                parts.append(direct)
                i += 1
                continue

        # Deterministic numeric normalization (B-text only)
        numeric = _try_numeric_replacement(tokens, i)
        if numeric:
            repl, j = numeric
            if j >= i and not has_override_in_range(i, j) and all(k not in skip_indices for k in range(i, j + 1)):
                parts.append(repl)
                i = j + 1
                continue
        kanji_numeric = _try_kanji_numeric_replacement(tokens, i)
        if kanji_numeric:
            repl, j = kanji_numeric
            if j >= i and not has_override_in_range(i, j) and all(k not in skip_indices for k in range(i, j + 1)):
                parts.append(repl)
                i = j + 1
                continue

        # 「十分」は「十分だ(ジュウブン)」と「十分(ジュップン)」が文脈で揺れるため、
        # 時間用法が強い形だけ B 側で確定（Aは不変）。
        if surface == "十分":
            prev_surface = str(tokens[i - 1].get("surface") or "") if i > 0 else ""
            prev_prev_surface = str(tokens[i - 2].get("surface") or "") if i > 1 else ""
            next_surface = str(tokens[i + 1].get("surface") or "") if (i + 1) < n else ""
            # Examples:
            # - 時間に十分を加える / 十分後 / 十分間(は別tokenだが保険)
            if next_surface in {"を", "後", "間", "だけ", "ほど", "くらい"}:
                parts.append("ジュップン")
                i += 1
                continue
            # - 毎日の十分が続く（毎日の + 十分 + が）
            if prev_surface == "の" and prev_prev_surface in {"毎日", "毎朝", "毎晩"} and next_surface == "が":
                parts.append("ジュップン")
                i += 1
                continue

        # Deterministic 1-char Kanji fixes (only when token is standalone + MeCab reading indicates on-yomi)
        # Avoid global dict for 1-char surfaces (banned); keep it in code with conservative guards.
        reading_mecab = str(tokens[i].get("reading_mecab") or "")
        subpos = str(tokens[i].get("subpos") or "")
        prev_surface = str(tokens[i - 1].get("surface") or "") if i > 0 else ""
        next_surface = str(tokens[i + 1].get("surface") or "") if (i + 1) < n else ""
        next_base = str(tokens[i + 1].get("base") or "") if (i + 1) < n else ""
        if surface == "何" and ((i + 1) < n) and (next_surface.isdigit() or _is_kanji_number_token(tokens[i + 1])):
            parts.append("ナン")
            i += 1
            continue
        # "行った/行って" は文脈で「イッ(た)」「オコナッ(た)」が分かれるため、
        # MeCab が既に「オコナッ」を返している場合はそれを優先して VOICEVOX にも強制する。
        if surface == "行っ" and reading_mecab == "オコナッ":
            parts.append("オコナッ")
            i += 1
            continue
        # 「一行(イッコウ)」は「一行書く(イチギョウ)」文脈で誤読しやすいので、
        # 書く系の直後に限り「イチギョウ」に寄せる（「一行が進む」等は除外）。
        if surface == "一行" and reading_mecab == "イッコウ" and next_base == "書く":
            parts.append("イチギョウ")
            i += 1
            continue
        # 「心」は直前がカタカナ等だと MeCab が「シン」になりがち（例: ドウシテ心を…）。
        # 「好奇心」などの名詞+接尾は「シン」を維持し、それ以外は「ココロ」に寄せる。
        prev_pos = str(tokens[i - 1].get("pos") or "") if i > 0 else ""
        if surface == "心" and next_surface in {"を", "が", "に", "で", "は", "も", "の", "と", "という", "って", "、", "。"}:
            if subpos != "接尾" or prev_pos != "名詞":
                parts.append("ココロ")
                i += 1
                continue
        # 「体」は直前がカタカナ等だと MeCab が「タイ」になりがち（例: カレノ体は…）。
        # 接尾（身体能力など）は除外しつつ、語としての「体」は「カラダ」に寄せる。
        if surface == "体" and subpos != "接尾" and next_surface in {"が", "を", "に", "で", "は", "も", "、", "。"}:
            parts.append("カラダ")
            i += 1
            continue
        if (
            surface == "後"
            and prev_surface != "年"  # keep "10年後" -> ゴ
            and reading_mecab in {"ノチ", "ゴ"}
            and next_surface in {"", "、", "。", "に", "の", "は", "を", "が", "で", "も", "から"}
        ):
            parts.append("アト")
            i += 1
            continue
        if surface == "間" and prev_surface == "ヶ月" and next_surface in {"", "、", "。", "に", "の", "は", "を", "が", "で", "も"}:
            parts.append("カン")
            i += 1
            continue
        if surface == "間" and reading_mecab == "マ" and next_surface in {"だけ", "、", "。", "に", "は", "を", "が", "で", "も", "から"}:
            parts.append("アイダ")
            i += 1
            continue
        if surface == "水" and reading_mecab == "スイ":
            parts.append("ミズ")
            i += 1
            continue
        if surface == "土" and reading_mecab == "ド":
            parts.append("ツチ")
            i += 1
            continue
        if (
            surface == "君"
            and next_surface in {"に", "の", "は", "が", "を", "も", "へ", "と", "という", "って"}
        ):
            prev_pos = str(tokens[i - 1].get("pos") or "") if i > 0 else ""
            prev_subpos = str(tokens[i - 1].get("subpos") or "") if i > 0 else ""
            prev_is_name = prev_pos == "名詞" and prev_subpos in {"固有名詞", "人名"}
            if not prev_is_name:
                parts.append("キミ")
                i += 1
                continue
        if surface == "獣" and reading_mecab == "シシ":
            parts.append("ケモノ")
            i += 1
            continue
        if surface == "暇" and next_surface in {"が", "を", "に", "で", "は", "も", "など", "、", "。"}:
            parts.append("ヒマ")
            i += 1
            continue
        if surface == "隙" and reading_mecab == "ヒマ":
            parts.append("スキ")
            i += 1
            continue
        if surface == "芥" and reading_mecab == "ゴミ":
            parts.append("アクタ")
            i += 1
            continue
        if surface == "怒" and reading_mecab == "イカ" and next_surface == "」":
            parts.append("イカリ")
            i += 1
            continue
        if surface == "虚" and reading_mecab == "ウロ":
            parts.append("キョ")
            i += 1
            continue
        # 「鏡」は前後の文脈で MeCab の読みが揺れやすい（例: 心の鏡→カガミ / ココロノ鏡→キョウ）。
        # 1文字surfaceは辞書キーにできないため、語として単独で出た場合は決定的に「カガミ」へ寄せる。
        if surface == "鏡" and next_surface in {"が", "を", "に", "で", "は", "も", "、", "。", "だ", "です", "だった", "でした"}:
            parts.append("カガミ")
            i += 1
            continue
        if surface == "証" and reading_mecab == "アカシ" and next_surface in {"か", "かも", "が", "を", "に", "は", "も", "、", "。"}:
            parts.append("アカシ")
            i += 1
            continue

        # ASCII token fallback (for cases not covered by channel dict)
        ascii_kana = _ascii_token_to_kana(surface)
        if ascii_kana:
            parts.append(ascii_kana)
            i += 1
            continue

        parts.append(surface)
        i += 1

    return "".join(parts)


def resolve_readings_strict(
    segments: List[AudioSegment],
    engine: str,
    voicevox_client: Optional[VoicevoxClient],
    speaker_id: int,
    channel: Optional[str] = None,
    video: Optional[str] = None,
    skip_tts_reading: bool = False,
    segment_indices: Optional[List[int]] = None,
) -> Dict[int, List[KanaPatch]]:
    """Strict reading resolver that delegates to auditor (surface-aggregated, max 2 LLM calls).

    Returns patches_by_block for use in synthesis.
    """
    # Map `segments` (possibly a subset) back to global segment indices.
    # This keeps local_token_overrides / report indices / patch keys consistent
    # when callers pass only a subset of segments (e.g., partial regeneration).
    if segment_indices is not None:
        if len(segment_indices) != len(segments):
            raise ValueError(
                f"segment_indices length must match segments (got {len(segment_indices)} != {len(segments)})"
            )
        global_indices = [int(i) for i in segment_indices]
    else:
        global_indices = list(range(len(segments)))

    # 1. 辞書ロード（グローバル + チャンネル固有 + ローカル + Voicepeak SoT）
    kb = WordDictionary(KB_PATH)
    # IMPORTANT:
    # - VOICEVOX: Do NOT import Voicepeak local dictionaries (they are machine-local state and can
    #   silently change B-text, hurting reproducibility and causing ambiguous-surface accidents).
    # - VOICEPEAK: Import repo/local Voicepeak dicts for better stability, but keep them filtered
    #   by is_banned_surface/is_safe_reading.
    if engine == "voicepeak":
        try:
            kb.words.update(_load_voicepeak_repo_dict_words())
        except Exception:
            pass
        try:
            # Local Voicepeak dict overrides repo dict (user's manual corrections win).
            kb.words.update(_load_voicepeak_local_dict_words())
        except Exception:
            pass
        try:
            # Also respect the user's Voicepeak user.csv (often the main place GUI-edits land).
            kb.words.update(_load_voicepeak_local_user_csv_words())
        except Exception:
            pass
    channel_dict = load_channel_reading_dict(channel) if channel else {}
    if channel_dict:
        kb.words.update(export_words_for_word_dict(channel_dict))

    # 動画ローカル辞書（audio_prep/local_reading_dict.json）と token override（local_token_overrides.json）
    local_overrides: Dict[int, Dict[int, str]] = {}
    if channel and video:
        video_dir = video_root(channel, video)
        local_dict_path = video_dir / "audio_prep" / "local_reading_dict.json"
        if local_dict_path.exists():
            try:
                local_dict = json.loads(local_dict_path.read_text(encoding="utf-8"))
                added = 0
                if isinstance(local_dict, dict):
                    for k, v in local_dict.items():
                        surface = str(k or "").strip()
                        if is_banned_surface(surface):
                            continue
                        if not isinstance(v, str):
                            continue
                        normalized = normalize_reading_kana(v)
                        if not is_safe_reading(normalized):
                            continue
                        if not normalized or normalized == surface:
                            continue
                        kb.words[surface] = normalized
                        added += 1
                print(f"[ARBITER] Loaded local_reading_dict.json ({added} entries)")
            except Exception as e:
                print(f"[WARN] Failed to load local_reading_dict.json: {e}")

        local_tok_path = video_dir / "audio_prep" / "local_token_overrides.json"
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

    # VOICEVOX engine: voicevox_client is required for strict mode.
    if engine == "voicevox" and not voicevox_client:
        raise ValueError("Voicevox client required for Strict Mode")

    voicepeak_comma_policy, voicepeak_comma_drop_particles = ("", set(_VOICEPEAK_COMMA_DROP_PARTICLES_DEFAULT))
    if engine == "voicepeak":
        voicepeak_comma_policy, voicepeak_comma_drop_particles = _load_voicepeak_comma_policy(channel)

    # 2. 初期化
    for seg in segments:
        seg.text_for_check = seg.text
        seg.reading = seg.text
        seg.arbiter_verdict = "pending_auditor"

    blocks: List[Dict[str, Any]] = []
    if engine == "voicevox":
        print(f"[ARBITER] auditing {len(segments)} segments (auditor path)...")

    # 3. `seg.reading` を辞書/overrideで確定（voicevoxの場合は blocks も構築）
    for local_i, seg in enumerate(segments):
        block_id = global_indices[local_i]
        target_text = seg.text  # オリジナルのテキスト

        tokens = tokenize_with_mecab(target_text)
        override_map = local_overrides.get(block_id) or {}
        patched_text = _patch_tokens_with_words(tokens, kb.words, override_map)
        patched_text = normalize_text_for_tts(patched_text)
        if engine == "voicepeak":
            patched_text = _apply_voicepeak_comma_policy(
                patched_text,
                voicepeak_comma_policy,
                voicepeak_comma_drop_particles,
            )

        expected_reading = get_mecab_reading(patched_text)
        seg.mecab_reading = expected_reading
        seg.reading = patched_text

        if engine != "voicevox":
            continue

        # Voicevox audio_query for auditor / patches
        try:
            query = voicevox_client.audio_query(patched_text, speaker_id)  # type: ignore[union-attr]
            vv_kana = query.get("kana", "")
            seg.voicevox_reading = vv_kana
        except Exception as e:
            print(f"[ERROR] Voicevox query failed: {e}")
            raise RuntimeError(f"Voicevox query failed for segment {block_id}") from e

        blocks.append(
            {
                "index": block_id,
                "text": target_text,
                "b_text": patched_text,
                "mecab_kana": expected_reading,
                "voicevox_kana": vv_kana,
                "accent_phrases": query.get("accent_phrases") or [],
                "audit_needed": True,
            }
        )

    # Non-VOICEVOX engines: dictionary/overrides only.
    if engine != "voicevox":
        verdict = "dict_only_skip_llm" if skip_tts_reading else "dict_only_no_auditor"
        for seg in segments:
            seg.arbiter_verdict = verdict
        return {}

    # Global safety: always fail-fast on mismatches so we never silently ship misreads.

    def _collect_mismatches() -> List[Dict[str, object]]:
        out: List[Dict[str, object]] = []
        for b in blocks:
            mecab_kana = str(b.get("mecab_kana") or "")
            vv_kana = str(b.get("voicevox_kana") or "")
            if is_trivial_diff(mecab_kana, vv_kana):
                continue
            out.append(
                {
                    "index": b.get("index"),
                    "text": b.get("text"),
                    "b_text": b.get("b_text"),
                    "mecab_kana": mecab_kana,
                    "voicevox_kana": vv_kana,
                }
            )
        return out

    def _write_mismatch_report(tag: str, mismatches: List[Dict[str, object]]) -> Optional[Path]:
        if not channel or not video or not mismatches:
            return None
        try:
            out_dir = video_root(channel, video) / "audio_prep"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"reading_mismatches__{tag}.json"
            payload: Dict[str, object] = {
                "channel": channel,
                "video": video,
                "tag": tag,
                "generated_at": time.time(),
                "count": len(mismatches),
                "mismatches": mismatches,
            }
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return out_path
        except Exception:
            return None

    mismatches = _collect_mismatches()

    if skip_tts_reading:
        if mismatches:
            print(f"[ARBITER] mismatches detected (skip_tts_reading=True): {len(mismatches)}")
            report_path = _write_mismatch_report("skip_llm", mismatches)
            raise RuntimeError(
                "[ARBITER] reading mismatches detected (fail-fast). "
                f"Add dict/position patches and retry. Report: {report_path}"
            )
        verdict = "dict_only_skip_llm"
        for seg in segments:
            seg.arbiter_verdict = verdict
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
        print(f"[WARN] auditor failed ({e}); continuing without auditor patches.")
        if mismatches:
            report_path = _write_mismatch_report("auditor_failed", mismatches)
            raise RuntimeError(
                "[ARBITER] auditor failed and reading mismatches exist (fail-fast). "
                f"Resolve via dict/position patches and retry. Report: {report_path}"
            ) from e
        verdict = "dict_only_auditor_failed"
        for seg in segments:
            seg.arbiter_verdict = verdict
        return {}

    print("[ARBITER] auditor finished (surface aggregation path).")
    return patches_by_block
