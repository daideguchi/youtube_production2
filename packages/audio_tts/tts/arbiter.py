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
FORCE_GLOBAL_SURFACES = {"同じ道", "微調整", "一種", "憂い", "善き"}

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
    if os.environ.get("YTM_ENABLE_LEARNING_DICT", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return {}
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
            # Learning dict is best-effort; curated KB must win.
            for surface, reading in learning.items():
                base.setdefault(surface, reading)
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
        # NOTE: MeCab may keep them inside combined tokens too (e.g., 「本当は」「時には」 -> 「...ハ」).
        if isinstance(surface, str) and surface:
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

    if counter == "本":
        special = {
            1: "イッポン",
            3: "サンボン",
            6: "ロッポン",
            8: "ハッポン",
            10: "ジュッポン",
            20: "ニジュッポン",
            30: "サンジュッポン",
            40: "ヨンジュッポン",
            50: "ゴジュッポン",
            60: "ロクジュッポン",
            70: "ナナジュッポン",
            80: "ハチジュッポン",
            90: "キュウジュッポン",
        }
        return special.get(n) or (_jp_number_kana(n) + "ホン")

    if counter == "冊":
        # Book/notebook counter: 1冊=イッサツ, 8冊=ハッサツ, 10冊=ジュッサツ ...
        special = {
            1: "イッサツ",
            8: "ハッサツ",
            10: "ジュッサツ",
            20: "ニジュッサツ",
            30: "サンジュッサツ",
            40: "ヨンジュッサツ",
            50: "ゴジュッサツ",
            60: "ロクジュッサツ",
            70: "ナナジュッサツ",
            80: "ハチジュッサツ",
            90: "キュウジュッサツ",
        }
        return special.get(n) or (_jp_number_kana(n) + "サツ")

    if counter == "着":
        # Clothing counter: 1着=イッチャク, 8着=ハッチャク, 10着=ジュッチャク ...
        special = {1: "イッチャク", 8: "ハッチャク", 10: "ジュッチャク"}
        return special.get(n) or (_jp_number_kana(n) + "チャク")

    if counter == "通":
        # Mail/message counter: 1通=イッツウ, 8通=ハッツウ, 10通=ジュッツウ ...
        special = {
            1: "イッツウ",
            8: "ハッツウ",
            10: "ジュッツウ",
            20: "ニジュッツウ",
            30: "サンジュッツウ",
            40: "ヨンジュッツウ",
            50: "ゴジュッツウ",
            60: "ロクジュッツウ",
            70: "ナナジュッツウ",
            80: "ハチジュッツウ",
            90: "キュウジュッツウ",
        }
        return special.get(n) or (_jp_number_kana(n) + "ツウ")

    if counter == "件":
        # 1件=イッケン, 8件=ハッケン, 10件=ジュッケン ...
        special = {
            1: "イッケン",
            3: "サンケン",
            6: "ロッケン",
            8: "ハッケン",
            10: "ジュッケン",
            20: "ニジュッケン",
            30: "サンジュッケン",
            40: "ヨンジュッケン",
            50: "ゴジュッケン",
            60: "ロクジュッケン",
            70: "ナナジュッケン",
            80: "ハチジュッケン",
            90: "キュウジュッケン",
        }
        return special.get(n) or (_jp_number_kana(n) + "ケン")

    if counter == "軒":
        # House counter: 1軒=イッケン ... (same phonetics as 件)
        return _jp_number_with_counter_kana(n, "件")

    if counter == "発":
        # 1発=イッパツ, 6発=ロッパツ, 8発=ハッパツ, 10発=ジュッパツ ...
        special = {1: "イッパツ", 6: "ロッパツ", 8: "ハッパツ", 10: "ジュッパツ"}
        return special.get(n) or (_jp_number_kana(n) + "ハツ")

    if counter == "章":
        # 1章=イッショウ, 6章=ロッショウ, 8章=ハッショウ, 10章=ジュッショウ ...
        special = {1: "イッショウ", 6: "ロッショウ", 8: "ハッショウ", 10: "ジュッショウ"}
        return special.get(n) or (_jp_number_kana(n) + "ショウ")

    if counter == "枠":
        # 1枠=ヒトワク, 2枠=フタワク
        special = {1: "ヒトワク", 2: "フタワク"}
        return special.get(n) or (_jp_number_kana(n) + "ワク")

    if counter == "曲":
        # Song/music counter: 1曲=イッキョク, 8曲=ハッキョク, 10曲=ジュッキョク ...
        special = {
            1: "イッキョク",
            6: "ロッキョク",
            8: "ハッキョク",
            10: "ジュッキョク",
            20: "ニジュッキョク",
            30: "サンジュッキョク",
            40: "ヨンジュッキョク",
            50: "ゴジュッキョク",
            60: "ロクジュッキョク",
            70: "ナナジュッキョク",
            80: "ハチジュッキョク",
            90: "キュウジュッキョク",
        }
        return special.get(n) or (_jp_number_kana(n) + "キョク")

    if counter == "杯":
        special = {
            1: "イッパイ",
            3: "サンバイ",
            6: "ロッパイ",
            8: "ハッパイ",
            10: "ジュッパイ",
            20: "ニジュッパイ",
            30: "サンジュッパイ",
            40: "ヨンジュッパイ",
            50: "ゴジュッパイ",
            60: "ロクジュッパイ",
            70: "ナナジュッパイ",
            80: "ハチジュッパイ",
            90: "キュウジュッパイ",
        }
        return special.get(n) or (_jp_number_kana(n) + "ハイ")

    if counter == "口":
        special = {1: "ヒトクチ"}
        return special.get(n) or (_jp_number_kana(n) + "クチ")

    if counter == "匹":
        # Animal counter: 1匹=イッピキ, 3匹=サンビキ, 8匹=ハッピキ, 10匹=ジュッピキ ...
        special = {1: "イッピキ", 3: "サンビキ", 6: "ロッピキ", 8: "ハッピキ", 10: "ジュッピキ"}
        return special.get(n) or (_jp_number_kana(n) + "ヒキ")

    if counter == "粒":
        # 1粒=ヒトツブ, 10粒=ジュッツブ ...
        special = {1: "ヒトツブ", 10: "ジュッツブ"}
        return special.get(n) or (_jp_number_kana(n) + "ツブ")

    if counter == "握り":
        # 1握り=ヒトニギリ
        special = {1: "ヒトニギリ"}
        return special.get(n) or (_jp_number_kana(n) + "ニギリ")

    if counter == "首":
        # Poem counter: 1首=イッシュ, 8首=ハッシュ, 10首=ジュッシュ ...
        special = {1: "イッシュ", 8: "ハッシュ", 10: "ジュッシュ"}
        return special.get(n) or (_jp_number_kana(n) + "シュ")

    if counter == "画":
        # Stroke counter: 1画=イッカク, 8画=ハッカク, 10画=ジュッカク ...
        special = {1: "イッカク", 8: "ハッカク", 10: "ジュッカク"}
        return special.get(n) or (_jp_number_kana(n) + "カク")

    if counter == "滴":
        # Drop counter: 1滴=イッテキ, 8滴=ハッテキ, 10滴=ジュッテキ ...
        special = {1: "イッテキ", 6: "ロッテキ", 8: "ハッテキ", 10: "ジュッテキ"}
        return special.get(n) or (_jp_number_kana(n) + "テキ")

    if counter == "歩":
        # Step counter: 1歩=イッポ, 3歩=サンポ, 10歩=ジュッポ ...
        special = {
            1: "イッポ",
            3: "サンポ",
            6: "ロッポ",
            8: "ハッポ",
            10: "ジュッポ",
            20: "ニジュッポ",
            30: "サンジュッポ",
            40: "ヨンジュッポ",
            50: "ゴジュッポ",
            60: "ロクジュッポ",
            70: "ナナジュッポ",
            80: "ハチジュッポ",
            90: "キュウジュッポ",
        }
        return special.get(n) or (_jp_number_kana(n) + "ホ")

    if counter == "行":
        return _jp_number_kana(n) + "ギョウ"

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

    if counter == "拍":
        # Beat counter: 1拍=イッパク, 3拍=サンパク, 10拍=ジュッパク ...
        special = {
            1: "イッパク",
            3: "サンパク",
            6: "ロッパク",
            8: "ハッパク",
            10: "ジュッパク",
            20: "ニジュッパク",
            30: "サンジュッパク",
            40: "ヨンジュッパク",
            50: "ゴジュッパク",
            60: "ロクジュッパク",
            70: "ナナジュッパク",
            80: "ハチジュッパク",
            90: "キュウジュッパク",
        }
        return special.get(n) or (_jp_number_kana(n) + "ハク")

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

    if counter == "晩":
        # Common: 一晩=ヒトバン
        special = {1: "ヒトバン"}
        return special.get(n) or (_jp_number_kana(n) + "バン")

    if counter == "週間":
        # 1週間=イッシュウカン, 8週間=ハッシュウカン, 10週間=ジュッシュウカン ...
        special = {
            1: "イッシュウカン",
            8: "ハッシュウカン",
            10: "ジュッシュウカン",
            20: "ニジュッシュウカン",
            30: "サンジュッシュウカン",
            40: "ヨンジュッシュウカン",
            50: "ゴジュッシュウカン",
            60: "ロクジュッシュウカン",
            70: "ナナジュッシュウカン",
            80: "ハチジュッシュウカン",
            90: "キュウジュッシュウカン",
        }
        return special.get(n) or (_jp_number_kana(n) + "シュウカン")

    if counter in {"ヶ月", "か月"}:
        special = {1: "イッカゲツ", 6: "ロッカゲツ", 8: "ハッカゲツ", 10: "ジュッカゲツ"}
        return special.get(n) or (_jp_number_kana(n) + "カゲツ")

    if counter == "年":
        if n % 10 == 4:
            # 4年=ヨネン (not ゼロヨネン), 14年=ジュウヨネン, 24年=ニジュウヨネン ...
            prefix = _jp_number_kana(n - 4) if n > 4 else ""
            return (prefix + "ヨネン") if prefix else "ヨネン"
        return _jp_number_kana(n) + "ネン"

    if counter == "兆":
        # 1兆=イッチョウ, 60兆=ロクジュッチョウ
        special = {1: "イッチョウ", 8: "ハッチョウ", 10: "ジュッチョウ"}
        if n in special:
            return special[n]
        base = _jp_number_kana(n)
        if base.endswith("ジュウ"):
            base = base[:-2] + "ジュッ"
        elif base.endswith("イチ"):
            base = base[:-2] + "イッ"
        elif base.endswith("ハチ"):
            base = base[:-2] + "ハッ"
        return base + "チョウ"

    if counter == "世紀":
        # Century counter: 1世紀=イッセイキ, 18世紀=ジュウハッセイキ ...
        special = {
            1: "イッセイキ",
            6: "ロッセイキ",
            8: "ハッセイキ",
            10: "ジュッセイキ",
            20: "ニジュッセイキ",
            30: "サンジュッセイキ",
            40: "ヨンジュッセイキ",
            50: "ゴジュッセイキ",
            60: "ロクジュッセイキ",
            70: "ナナジュッセイキ",
            80: "ハチジュッセイキ",
            90: "キュウジュッセイキ",
        }
        if n in special:
            return special[n]
        base = _jp_number_kana(n)
        if base.endswith("イチ"):
            base = base[:-2] + "イッ"
        elif base.endswith("ロク"):
            base = base[:-2] + "ロッ"
        elif base.endswith("ハチ"):
            base = base[:-2] + "ハッ"
        return base + "セイキ"

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

    if counter in {"か所", "ヶ所", "箇所"}:
        special = {
            1: "イッカショ",
            6: "ロッカショ",
            8: "ハッカショ",
            10: "ジュッカショ",
            20: "ニジュッカショ",
            30: "サンジュッカショ",
            40: "ヨンジュッカショ",
            50: "ゴジュッカショ",
            60: "ロクジュッカショ",
            70: "ナナジュッカショ",
            80: "ハチジュッカショ",
            90: "キュウジュッカショ",
        }
        return special.get(n) or (_jp_number_kana(n) + "カショ")

    if counter == "ページ":
        # 10ページ=ジュッページ, 20ページ=ニジュッページ ...
        special = {
            10: "ジュッページ",
            20: "ニジュッページ",
            30: "サンジュッページ",
            40: "ヨンジュッページ",
            50: "ゴジュッページ",
            60: "ロクジュッページ",
            70: "ナナジュッページ",
            80: "ハチジュッページ",
            90: "キュウジュッページ",
        }
        return special.get(n) or (_jp_number_kana(n) + "ページ")

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
        if unit == "兆":
            return (_jp_number_with_counter_kana(n, "兆") + suffix, end)
        return (_jp_number_kana(n) + unit_kana + suffix, end)

    # Simple counter: 94 年 / 9 歳 / 98 パーセント / 10 分間 ...
    if i + 1 < n_tokens:
        counter = str(tokens[i + 1].get("surface") or "")
        if counter:
            if counter == "行":
                next_surface = str(tokens[i + 2].get("surface") or "") if (i + 2) < n_tokens else ""
                next_base = str(tokens[i + 2].get("base") or "") if (i + 2) < n_tokens else ""
                next_pos = str(tokens[i + 2].get("pos") or "") if (i + 2) < n_tokens else ""
                if (
                    next_surface in {"目", "だけ", "ずつ", "で", "の", "が", "は", "を", "に", "も", "多い", "少ない"}
                    or next_base in {"書く", "記す", "添える", "多い", "少ない", "ある"}
                    or next_pos in {"助詞", "助動詞"}
                ):
                    return (_jp_number_with_counter_kana(n, counter), i + 1)
            if counter in {
                "年",
                "歳",
                "人",
                "回",
                "個",
                "本",
                "冊",
                "着",
                "通",
                "件",
                "軒",
                "匹",
                "発",
                "杯",
                "口",
                "粒",
                "握り",
                "首",
                "歩",
                "つ",
                "分",
                "分間",
                "秒",
                "時間",
                "時",
                "日",
                "晩",
                "週間",
                "ヶ月",
                "か月",
                "世紀",
                "兆",
                "円",
                "点",
                "章",
                "画",
                "枠",
                "か所",
                "ヶ所",
                "箇所",
                "拍",
                "ページ",
                "曲",
                "滴",
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

    # 「十分」は「じゅっぷん（10分）」と「じゅうぶん（十分）」が揺れる。
    # MeCab が「十(数)+分(接尾)」に分割するケースでも、文末/コピュラ直前は「十分(じゅうぶん)」を優先する。
    if counter == "分" and n_val == 10:
        if (j + 1) >= n_tokens:
            return ("ジュウブン", j)
        next_tok = tokens[j + 1]
        next_surface = str(next_tok.get("surface") or "")
        next_pos = str(next_tok.get("pos") or "")
        if next_pos == "silence_tag" or next_surface in {"", "、", "。", "！", "?", "？", "」", "』", ")", "）"}:
            return ("ジュウブン", j)
        if next_surface in {"だ", "です", "だった", "でした"}:
            return ("ジュウブン", j)

    # Simple counters
    if counter == "行":
        next_surface = str(tokens[j + 1].get("surface") or "") if (j + 1) < n_tokens else ""
        next_base = str(tokens[j + 1].get("base") or "") if (j + 1) < n_tokens else ""
        next_pos = str(tokens[j + 1].get("pos") or "") if (j + 1) < n_tokens else ""
        if (
            next_surface in {"目", "だけ", "ずつ", "で", "の", "が", "は", "を", "に", "も", "多い", "少ない"}
            or next_base in {"書く", "記す", "添える", "多い", "少ない", "ある"}
            or next_pos in {"助詞", "助動詞"}
        ):
            return (_jp_number_with_counter_kana(n_val, counter), j)

    if counter in {
        "年",
        "歳",
        "人",
        "回",
        "個",
        "本",
        "冊",
        "着",
        "通",
        "件",
        "軒",
        "匹",
        "発",
        "杯",
        "口",
        "粒",
        "握り",
        "首",
        "歩",
        "つ",
        "分",
        "分間",
        "秒",
        "時間",
        "時",
        "日",
        "晩",
        "週間",
        "ヶ月",
        "か月",
        "世紀",
        "兆",
        "円",
        "点",
        "章",
        "画",
        "枠",
        "か所",
        "ヶ所",
        "箇所",
        "拍",
        "ページ",
        "曲",
        "割",
        "滴",
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

    def span_ok(start: int, end: int) -> bool:
        if end >= len(tokens):
            return False
        if has_override_in_range(start, end):
            return False
        for k in range(start, end + 1):
            if k in skip_indices:
                return False
        return True

    # Deterministic multi-token reading patches (B-text only).
    # Keep this conservative: prefer multi-token surfaces (avoids 1-char dict bans).
    B_PATCH_SPAN_3: dict[tuple[str, str, str], str] = {
        ("二", "択以", "外"): "ニタクイガイ",
        ("大", "ら", "か"): "おおらか",
        ("軽", "ん", "じ"): "かろんじ",
        ("刻", "一", "刻"): "こくいっこく",
        ("会", "いたく", "ない"): "あいたくない",
        ("趙", "州", "は"): "じょうしゅうわ",
        ("疲れ", "果て", "、"): "つかれはてて",
        ("艶", "や", "か"): "つややか",
        ("癒", "や", "す"): "いやす",
        ("何", "げ", "ない"): "なにげない",
        ("強", "張っ", "た"): "こわばった",
        ("労", "わっ", "て"): "いたわって",
        ("何", "十", "本"): "なんじゅっぽん",
    }
    B_PATCH_SPAN_2: dict[tuple[str, str], str] = {
        ("事実", "返し"): "じじつがえし",
        ("押し", "付け"): "おしつけ",
        ("趙", "州"): "じょうしゅう",
        ("禅", "寺"): "ぜんでら",
        ("荘", "子"): "そうし",
        ("善", "友"): "ぜんゆう",
        ("加", "齢"): "かれい",
        ("茶", "葉"): "ちゃば",
        ("容れ", "物"): "いれもの",
        ("汚そ", "う"): "よごそう",
        ("植え", "替え"): "うえかえ",
        ("関わり", "方"): "かかわりかた",
        ("刻", "一刻"): "こくいっこく",
        ("際", "断"): "さいだん",
        ("解こ", "う"): "とこう",
        ("抜き", "去り"): "ぬきさり",
        ("微", "笑み"): "ほほえみ",
        ("加", "虐心"): "かぎゃくしん",
        ("活", "かせる"): "いかせる",
        ("癒", "やせ"): "いやせ",
        ("癒", "やす"): "いやす",
        ("緩", "すぎる"): "ゆるすぎる",
        ("いつも", "通り"): "いつもどおり",
        ("怒ろ", "う"): "おころう",
        ("引き", "方"): "ひきかた",
        ("仏", "様"): "ほとけさま",
        ("突き立て", "、"): "つきたてて",
        ("脱ぎ捨て", "、"): "ぬぎすてて",
        ("楽", "さ"): "らくさ",
        ("締", "まり"): "しまり",
        ("粗", "探し"): "あらさがし",
        ("消し", "去り"): "けしさり",
        ("助け", "よう"): "たすけよう",
        ("助けよ", "う"): "たすけよう",
        ("立ち", "止まれ"): "たちどまれ",
        ("強", "張り"): "こわばり",
        ("頑", "な"): "かたくな",
        ("ささくれ", "立ち"): "ささくれだち",
        ("他", "人事"): "たにんごと",
        ("一", "線"): "いっせん",
        ("一", "等地"): "いっとうち",
        ("一", "客"): "いっきゃく",
        ("一", "坪"): "ひとつぼ",
        ("一", "皿"): "ひとさら",
        ("何", "本"): "なんぼん",
    }

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

        surface = str(tokens[i].get("surface") or "")
        next_surface = str(tokens[i + 1].get("surface") or "") if (i + 1) < n else ""
        next_next_surface = str(tokens[i + 2].get("surface") or "") if (i + 2) < n else ""

        # Placeholder: ○○ / 〇〇 -> マルマル (B-text only)
        if surface in {"○", "〇"} and next_surface == surface:
            j = i
            while j < n and str(tokens[j].get("surface") or "") == surface and (j - i) < 8:
                j += 1
            parts.append("マル" * (j - i))
            i = j
            continue

        key3 = (surface, next_surface, next_next_surface)
        if key3 in B_PATCH_SPAN_3 and span_ok(i, i + 2):
            parts.append(B_PATCH_SPAN_3[key3])
            i += 3
            continue
        key2 = (surface, next_surface)
        if key2 in B_PATCH_SPAN_2 and span_ok(i, i + 1):
            parts.append(B_PATCH_SPAN_2[key2])
            i += 2
            continue

        # Deterministic reading: 他の -> ホカの
        # Avoid suffix-tokenization accidents like: ホカノ人/国/道 -> ジン/コク/ドウ
        if surface == "他" and next_surface == "の":
            if not has_override_in_range(i, i + 1) and (i + 1) not in skip_indices:
                parts.append("ホカの")
                i += 2
                continue
        # Deterministic reading: 他から -> ホカから
        if surface == "他" and next_surface == "から":
            if not has_override_in_range(i, i + 1) and (i + 1) not in skip_indices:
                parts.append("ホカから")
                i += 2
                continue
        # Deterministic reading: 他に -> ホカに
        if surface == "他" and next_surface == "に":
            if not has_override_in_range(i, i + 1) and (i + 1) not in skip_indices:
                parts.append("ホカに")
                i += 2
                continue
        # Deterministic reading: 他でも -> ホカでも
        if surface == "他" and next_surface == "で" and next_next_surface == "も":
            if (
                not has_override_in_range(i, i + 2)
                and (i + 1) not in skip_indices
                and (i + 2) not in skip_indices
            ):
                parts.append("ホカでも")
                i += 3
                continue
        # Deterministic reading: 何でも -> なんでも
        if surface == "何" and next_surface == "で" and next_next_surface == "も":
            if (
                not has_override_in_range(i, i + 2)
                and (i + 1) not in skip_indices
                and (i + 2) not in skip_indices
            ):
                parts.append("なんでも")
                i += 3
                continue

        # Deterministic voicing: 数十X -> 数ジュッX (VOICEVOX uses ジュッ before some counters)
        if surface == "十":
            prev_surface_local = str(tokens[i - 1].get("surface") or "") if i > 0 else ""
            if prev_surface_local == "数" and next_surface in {
                "件",
                "回",
                "本",
                "冊",
                "通",
                "発",
                "章",
                "曲",
                "杯",
                "口",
                "歩",
                "分",
                "分間",
                "ページ",
                "個",
                "センチ",
                "キロ",
            }:
                parts.append("ジュッ")
                i += 1
                continue

        # Deterministic numeric normalization (B-text only)
        # NOTE: Run before dictionary matching so counters like「一晩」等が学習辞書に潰されない。
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
                tok_surface = str(tok.get("surface") or "")
                if not tok_surface:
                    break
                cand += tok_surface
                if (j - i + 1) >= 2 and not has_override_in_range(i, j):
                    repl = words.get(cand)
                    if repl:
                        best_repl = repl
                        best_j = j

        if best_repl is not None and best_j is not None:
            parts.append(best_repl)
            i = best_j + 1
            continue

        # Single-token dictionary replacement
        if words:
            direct = words.get(surface)
            if direct:
                parts.append(direct)
                i += 1
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
            # Default: "十分(じゅうぶん)" を明示（VOICEVOX が「ジュップン」側に寄る事故を防ぐ）
            parts.append("ジュウブン")
            i += 1
            continue

        # Deterministic 1-char Kanji fixes (only when token is standalone + MeCab reading indicates on-yomi)
        # Avoid global dict for 1-char surfaces (banned); keep it in code with conservative guards.
        reading_mecab = str(tokens[i].get("reading_mecab") or "")
        pos = str(tokens[i].get("pos") or "")
        base = str(tokens[i].get("base") or "")
        subpos = str(tokens[i].get("subpos") or "")
        prev_surface = str(tokens[i - 1].get("surface") or "") if i > 0 else ""
        next_surface = str(tokens[i + 1].get("surface") or "") if (i + 1) < n else ""
        next_base = str(tokens[i + 1].get("base") or "") if (i + 1) < n else ""
        next_next_surface = str(tokens[i + 2].get("surface") or "") if (i + 2) < n else ""
        next_next_base = str(tokens[i + 2].get("base") or "") if (i + 2) < n else ""

        # - 重要度/緊急度: VOICEVOX が「タビ」側へ寄ることがあるため、B側で「ど」を確定。
        if (
            surface in {"重要", "緊急"}
            and next_surface == "度"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append(surface + "ど")
            i += 2
            continue

        # - 自然光: VOICEVOX が「シゼンヒカリ」側へ寄るため、B側で「自然コウ」を確定。
        if (
            surface == "自然"
            and next_surface == "光"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("自然コウ")
            i += 2
            continue

        # - 封筒: 直前語によって「ブウトウ」側へ濁ることがあるため、B側でカタカナを確定。
        if surface == "封筒" and reading_mecab == "フウトウ":
            parts.append("フウトウ")
            i += 1
            continue

        # - ○（箇条書き記号）: TTSでは読まず、B側から除去（VOICEVOXが「マル」と読んで誤差になるのを防ぐ）。
        if surface == "○":
            i += 1
            continue

        # - 例え話: B側で「たとえばなし」に寄せ、自然な連濁（バナシ）を確定。
        if (
            surface == "例え"
            and next_surface == "話"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("たとえばなし")
            i += 2
            continue

        # - こんな/そんな/あんな話: VOICEVOX が「バナシ」側へ濁ることがあるため、「はなし」を確定。
        if surface == "話" and reading_mecab == "ハナシ" and prev_surface in {"こんな", "そんな", "あんな"} and span_ok(i, i):
            parts.append("はなし")
            i += 1
            continue

        # - 未払い: VOICEVOX が「ミバライ」側へ寄るため、「みはらい」を確定。
        if surface == "未払い" and reading_mecab == "ミハライ":
            parts.append("みはらい")
            i += 1
            continue

        # - 離れ: VOICEVOX が「バナレ」側へ濁ることがあるため、「はなれ」を確定。
        if surface == "離れ" and base == "離れる" and reading_mecab == "ハナレ" and span_ok(i, i):
            parts.append("はなれ")
            i += 1
            continue

        # - 主: VOICEVOX が「オモ」側へ誤読することがあるため、MeCab が「アルジ」のときだけ「あるじ」を確定。
        if surface == "主" and reading_mecab == "アルジ" and span_ok(i, i):
            parts.append("あるじ")
            i += 1
            continue

        # - 加齢: MeCab が「か弱い」と混同することがあるため、「かれい」を確定。
        if surface == "加齢" and reading_mecab == "カヨワイ" and span_ok(i, i):
            parts.append("かれい")
            i += 1
            continue

        # - 捨（仏教用語）: MeCab が読めないことがあるため、引用文脈（捨と）では「シャ」を確定。
        if surface == "捨" and next_surface == "と" and span_ok(i, i):
            parts.append("シャ")
            i += 1
            continue

        # - というのも: VOICEVOX が「トユウ」側へ寄るため、B側で「とゆうのも」に寄せる。
        if surface == "というのも" and reading_mecab == "トイウノモ" and span_ok(i, i):
            parts.append("とゆうのも")
            i += 1
            continue

        # - お話しできれば: VOICEVOX が「オハナシシデキレバ」になるため、「おはなし」に寄せる。
        if surface == "お話し" and next_base == "できる" and span_ok(i, i):
            parts.append("おはなし")
            i += 1
            continue

        # - 親自身: MeCab が「シンジシン」側へ寄るため、B側で「おやじしん」に寄せる。
        if surface == "親" and next_surface == "自身" and span_ok(i, i + 1):
            parts.append("おやじしん")
            i += 2
            continue

        # - 数分間: VOICEVOX が「スウフンケン」側へ寄るため、B側で「数分カン」に寄せる。
        if surface == "数" and next_surface == "分間" and span_ok(i, i + 1):
            parts.append("数分カン")
            i += 2
            continue

        # - 洗面所: VOICEVOX が「センメンジョ」側で読むため、B側でカタカナを確定。
        if surface == "洗面" and next_surface == "所" and span_ok(i, i + 1):
            parts.append("センメンジョ")
            i += 2
            continue

        # - 妬ま(妬む): VOICEVOX が「ソネマ」側へ寄るため、B側で「ねたま」を確定。
        if base == "妬む" and surface == "妬ま" and reading_mecab == "ネタマ" and span_ok(i, i):
            parts.append("ねたま")
            i += 1
            continue
        # - 妬ん(妬む): VOICEVOX が「ソネン」側へ寄るため、B側で「ねたん」を確定。
        if base == "妬む" and surface == "妬ん" and reading_mecab == "ネタン" and span_ok(i, i):
            parts.append("ねたん")
            i += 1
            continue

        # - 細やか: VOICEVOX が「ササヤカ」側へ寄るため、B側で「こまやか」を確定。
        if surface == "細やか" and reading_mecab == "コマヤカ" and span_ok(i, i):
            parts.append("こまやか")
            i += 1
            continue

        # - 質や形: 「質や形」が「シチヤガタ」側へ寄るのを避けるため、B側で「しつ」を確定。
        if (
            surface == "質"
            and reading_mecab == "シツ"
            and next_surface == "や"
            and next_next_surface == "形"
            and span_ok(i, i)
        ):
            parts.append("しつ")
            i += 1
            continue

        # - について触れ(触れる): VOICEVOX が「ブレ」側へ寄るため、B側で「ふれ」を確定。
        if (
            prev_surface == "について"
            and base == "触れる"
            and surface == "触れ"
            and reading_mecab == "フレ"
            and span_ok(i, i)
        ):
            parts.append("ふれ")
            i += 1
            continue

        # - 何物: MeCab が「ナニブツ」側へ寄るため、「なにもの」を確定。
        if (
            surface == "何"
            and next_surface == "物"
            and str(tokens[i + 1].get("reading_mecab") or "") == "ブツ"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("なにもの")
            i += 2
            continue

        # - 何一つ: VOICEVOX が「ナンヒトツ」側へ寄るため、「なにひとつ」を確定。
        if surface == "何一つ" and span_ok(i, i):
            parts.append("なにひとつ")
            i += 1
            continue

        # - 逸話: VOICEVOX が「イチバナシ」側へ寄るため、「いつわ」を確定。
        if surface == "逸話" and reading_mecab == "イツワ" and span_ok(i, i):
            parts.append("いつわ")
            i += 1
            continue

        # - 刻々: VOICEVOX は「コクコク」側へ寄るため、MeCab が「コッコク」のときは「こくこく」を確定。
        if surface == "刻々" and reading_mecab == "コッコク" and span_ok(i, i):
            parts.append("こくこく")
            i += 1
            continue

        # - 空: VOICEVOX が「ク/クウ」側へ寄ることがあるため、MeCab が「ソラ」のときは「そら」を確定。
        if surface == "空" and reading_mecab == "ソラ" and span_ok(i, i):
            parts.append("そら")
            i += 1
            continue

        # - 蔓: VOICEVOX が「ズル」側へ濁ることがあるため、「つる」を確定。
        if surface == "蔓" and reading_mecab == "ツル" and span_ok(i, i):
            parts.append("つる")
            i += 1
            continue

        # - 大海原: 接頭辞「大」が「ダイ」側へ寄るため、「おお」に寄せる（大+海原）。
        if surface == "大" and next_surface == "海原" and reading_mecab == "ダイ" and span_ok(i, i):
            parts.append("おお")
            i += 1
            continue

        # - 纏っ(纏う): VOICEVOX が「マツワ..」側へ寄るため、「まとっ」を確定。
        if base == "纏う" and surface == "纏っ" and reading_mecab == "マトッ" and span_ok(i, i):
            parts.append("まとっ")
            i += 1
            continue

        # - 差し出そうとして: 「差し出る」側に誤解析されることがあるため、「さしだ」に寄せる。
        if (
            base == "差し出る"
            and surface == "差し出"
            and next_surface == "そう"
            and reading_mecab == "サシデ"
            and span_ok(i, i)
        ):
            parts.append("さしだ")
            i += 1
            continue

        # - 水拭き: 「水+拭き(フキ)」を「みずぶき」に寄せる。
        if prev_surface == "水" and surface == "拭き" and base == "拭く" and reading_mecab == "フキ" and span_ok(i, i):
            parts.append("ぶき")
            i += 1
            continue

        # - 排水溝: 「排水+溝(ミゾ)」を「はいすいこう」に寄せる。
        if surface == "排水" and next_surface == "溝" and span_ok(i, i + 1):
            parts.append("はいすいこう")
            i += 2
            continue

        # - 金継ぎ: 「金+継ぎ(キム/ツギ)」を「きんつぎ」に寄せる。
        if surface == "金" and next_surface == "継ぎ" and span_ok(i, i + 1):
            parts.append("きんつぎ")
            i += 2
            continue

        # - 木槌: 「木+槌(ツチ)」を「きづち」に寄せる。
        if surface == "木" and next_surface == "槌" and span_ok(i, i + 1):
            parts.append("きづち")
            i += 2
            continue

        # - 被告人: 「被告+人(ジン)」を「ひこくにん」に寄せる。
        if surface == "被告" and next_surface == "人" and span_ok(i, i + 1):
            parts.append("ひこくにん")
            i += 2
            continue

        # - 僧侶たる者: 「たる+者(シャ)」を「もの」に寄せる。
        if surface == "者" and prev_surface == "たる" and reading_mecab == "シャ" and span_ok(i, i):
            parts.append("もの")
            i += 1
            continue

        # - ビニール傘: 「傘」を「がさ」に寄せる。
        if surface == "傘" and prev_surface == "ビニール" and reading_mecab == "カサ" and span_ok(i, i):
            parts.append("がさ")
            i += 1
            continue

        # - 褒め(褒める): VOICEVOX が濁ることがあるため、「ほめ」を確定。
        if base == "褒める" and surface == "褒め" and reading_mecab == "ホメ" and span_ok(i, i):
            parts.append("ほめ")
            i += 1
            continue

        # - 血眼: 「ちまなこ」を確定。
        if surface == "血眼" and reading_mecab == "チメ" and span_ok(i, i):
            parts.append("ちまなこ")
            i += 1
            continue

        # - 荒ぶる: 「荒ぶ+る」を「あらぶる」に寄せる。
        if surface == "荒ぶ" and next_surface == "る" and reading_mecab == "スサブ" and span_ok(i, i + 1):
            parts.append("あらぶる")
            i += 2
            continue

        # - 屈し*: VOICEVOX が「コゴメ」側へ寄るため、「くっし」を確定。
        if surface == "屈し" and reading_mecab == "クッシ" and span_ok(i, i):
            parts.append("くっし")
            i += 1
            continue

        # - 慧鶴: 「慧+鶴(トシ/ツル)」を「えかく」に寄せる。
        if surface == "慧" and next_surface == "鶴" and span_ok(i, i + 1):
            parts.append("えかく")
            i += 2
            continue

        # - 令和: 「令+和(リョウ/ワ)」を「れいわ」に寄せる。
        if surface == "令" and next_surface == "和" and reading_mecab == "リョウ" and span_ok(i, i + 1):
            parts.append("れいわ")
            i += 2
            continue

        # - こはく: 「こ+はく」分割で「は」=助詞扱いになるのを避け、「コハク」を確定。
        if surface == "こ" and next_surface == "はく" and span_ok(i, i + 1):
            parts.append("コハク")
            i += 2
            continue

        # - 逃げ去る: 「去り」が濁るのを避け、「さり」を確定。
        if surface == "去り" and base == "去る" and reading_mecab == "サリ" and span_ok(i, i):
            parts.append("さり")
            i += 1
            continue

        # - 取っ(取る): VOICEVOX が「ドッ」側へ寄ることがあるため、「とっ」を確定。
        if surface == "取っ" and base == "取る" and reading_mecab == "トッ" and span_ok(i, i):
            parts.append("とっ")
            i += 1
            continue

        # - 歩き出せる: 「歩き+出せる」が「で」側に寄るため、「あるきだせる」を確定。
        if surface == "歩き" and base == "歩く" and next_surface == "出せる" and span_ok(i, i + 1):
            parts.append("あるきだせる")
            i += 2
            continue

        # - 引き剥がす(剥がす): 「ヘガ..」側を避け、「はが..」へ寄せる。
        if base == "剥がす" and surface.startswith("剥が") and reading_mecab.startswith("ヘガ") and span_ok(i, i):
            parts.append("はが" + surface.replace("剥が", ""))
            i += 1
            continue

        # - 一領一鉢: 読みを「いちりょういっぱつ」に固定。
        if (
            surface == "一"
            and next_surface == "領"
            and next_next_surface == "一"
            and str(tokens[i + 3].get("surface") or "") == "鉢"
            and span_ok(i, i + 3)
        ):
            parts.append("いちりょういっぱつ")
            i += 4
            continue

        # - 一区切りつけ*: 「一+区+切りつけ」を「ひとくぎりつけ」に寄せる。
        if (
            surface == "一"
            and next_surface == "区"
            and next_next_base == "切りつける"
            and str(tokens[i + 2].get("surface") or "").startswith("切りつけ")
            and span_ok(i, i + 2)
        ):
            parts.append("ひとくぎりつけ")
            i += 3
            continue

        # - 今日という日の: 「日」が「ニチ」側へ寄るため、「ひ」に寄せる。
        if (
            surface == "日"
            and reading_mecab == "ニチ"
            and prev_surface == "という"
            and next_surface == "の"
            and i > 1
            and str(tokens[i - 2].get("surface") or "") == "今日"
        ):
            parts.append("ひ")
            i += 1
            continue

        # - 愛おし*: 「愛おしむ/愛おしく…」が「あい…」側へ崩れるのを避ける（愛+おし…）。
        if surface == "愛" and reading_mecab == "アイ" and next_surface.startswith("おし"):
            parts.append("いと")
            i += 1
            continue
        # - 愛おしめる: 「愛 + お + しめる」分割時の誤読を避ける。
        if (
            surface == "愛"
            and reading_mecab == "アイ"
            and next_surface == "お"
            and next_next_base == "しめる"
            and span_ok(i, i + 2)
        ):
            parts.append("いとお")
            i += 2
            continue

        # - 癒やし: 「癒(未知)+やし」分割を避け、「いやし」を確定。
        if surface == "癒" and next_surface == "やし" and span_ok(i, i + 1):
            parts.append("いやし")
            i += 2
            continue
        if surface == "癒" and next_surface == "や" and next_next_surface == "し" and span_ok(i, i + 2):
            parts.append("いやし")
            i += 3
            continue

        # - 唯一無二: VOICEVOX が「タダイチ」側へ寄るため、「ゆいいつむに」を確定。
        if surface == "唯一" and next_surface == "無二" and span_ok(i, i + 1):
            parts.append("ゆいいつむに")
            i += 2
            continue

        # - 仰いました: 「仰い(仰ぐ)+ます」→「おっしゃい」に寄せる。
        if base == "仰ぐ" and surface == "仰い" and reading_mecab == "アオイ" and next_base == "ます":
            parts.append("おっしゃい")
            i += 1
            continue

        # - 貼り(貼る): VOICEVOX が「バリ」側へ寄るため、「はり」を確定。
        if base == "貼る" and surface == "貼り" and reading_mecab == "ハリ":
            parts.append("はり")
            i += 1
            continue

        # - 病: VOICEVOX が「ヤメ」側へ寄るため、「やまい」を確定。
        if surface == "病" and reading_mecab == "ヤマイ":
            parts.append("やまい")
            i += 1
            continue


        # - 扁桃体: 未知語崩壊を避けるため「へんとうたい」を確定。
        if (
            surface == "扁桃"
            and next_surface == "体"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("へんとうたい")
            i += 2
            continue

        # - 大勢: MeCab が「タイセイ」側へ誤読するため、「おおぜい」を確定。
        if surface == "大勢" and reading_mecab == "タイセイ":
            parts.append("おおぜい")
            i += 1
            continue

        # - 尊く(尊い): VOICEVOX が「タットク」側へ寄るため、「とうとく」を確定。
        if surface == "尊く" and base == "尊い" and reading_mecab == "トウトク":
            parts.append("とうとく")
            i += 1
            continue

        # - 責め立て、: 文脈上「せめたてて」に寄せる（句読点は次トークンで維持）。
        if surface == "責め立て" and base == "責め立てる" and next_surface == "、":
            parts.append("せめたてて")
            i += 1
            continue

        # - 呪い(呪う): 「呪いながら」等で「マジナイ」側へ寄るため、「のろい」を確定。
        if surface == "呪い" and base == "呪う" and reading_mecab == "マジナイ":
            parts.append("のろい")
            i += 1
            continue

        # - 注ぎ込む: VOICEVOX が「ツギ..」側へ寄るため、「そそぎこ..」を確定。
        if base == "注ぎ込む" and surface.startswith("注ぎ込") and reading_mecab.startswith("ソソギコ"):
            parts.append("そそぎこ" + surface[len("注ぎ込") :])
            i += 1
            continue

        # - 白隠禅師/白隠さん: MeCab が「シロ/コモ」「シロ/カクサ」側へ分割しやすいため、B側で固有読みを確定。
        if (
            surface == "白"
            and next_surface == "隠"
            and next_next_surface == "禅師"
            and not has_override_in_range(i, i + 2)
            and (i + 1) not in skip_indices
            and (i + 2) not in skip_indices
        ):
            parts.append("ハクインゼンジ")
            i += 3
            continue
        if surface == "白" and next_surface == "隠" and span_ok(i, i + 1):
            parts.append("ハクイン")
            i += 2
            continue
        if (
            surface == "白"
            and next_surface == "隠さ"
            and next_next_surface == "ん"
            and not has_override_in_range(i, i + 2)
            and (i + 1) not in skip_indices
            and (i + 2) not in skip_indices
        ):
            parts.append("ハクインサン")
            i += 3
            continue

        # - 〜決め: 複合語では連濁で「ギメ」側へ寄りやすいため、名詞用法のみ「ぎめ」を確定。
        if surface == "決め" and pos == "名詞" and reading_mecab == "キメ":
            parts.append("ぎめ")
            i += 1
            continue

        # - 夜中: 「夜中に」で MeCab が「ヤチュウ」側へ寄るため、B側で「よなか」を確定。
        if surface == "夜中" and reading_mecab == "ヤチュウ":
            parts.append("よなか")
            i += 1
            continue

        # - 深けれ(深ける): MeCab が「フケレ」側に解析するため、B側で「ふかけれ」を確定。
        if surface == "深けれ" and base == "深ける" and reading_mecab == "フケレ":
            parts.append("ふかけれ")
            i += 1
            continue

        # - いつか箱: MeCab が「バコ」側へ寄るため、B側で「はこ」を確定。
        if surface == "箱" and prev_surface == "いつか" and reading_mecab == "バコ":
            parts.append("はこ")
            i += 1
            continue

        # - 潜る: VOICEVOX が「クグ..」側へ寄ることがあるため、B側で「もぐ..」へ寄せる。
        if base == "潜る" and surface == "潜る" and reading_mecab == "モグル":
            parts.append("もぐる")
            i += 1
            continue

        # - 怖れ(怖い+れ): 誤って「コワレ」側へ寄るため、B側で「おそれ」を確定。
        if (
            surface == "怖"
            and next_surface == "れ"
            and reading_mecab == "コワ"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("おそれ")
            i += 2
            continue

        # - 「はい」と言う/答える: 「は+いと」へ崩れるのを防ぐため、B側で「ハイト」を確定。
        if (
            surface == "は"
            and next_surface == "いと"
            and next_next_base in {"言う", "答える", "受け取る"}
            and reading_mecab == "ハ"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("ハイト")
            i += 2
            continue

        # - 一回分: 「一+回分」トークンで濁音/促音が揺れるため、B側で「イッカイブン」を確定。
        if surface == "一" and next_surface == "回分" and reading_mecab == "イチ" and not has_override_in_range(i, i + 1) and (i + 1) not in skip_indices:
            parts.append("イッカイブン")
            i += 2
            continue

        # - 労る/労わる: MeCab が「労+る/わる」へ崩れるため、B側で「いたわる」を確定。
        if surface == "労" and next_surface in {"わる", "る"} and reading_mecab == "ロウ" and not has_override_in_range(i, i + 1) and (i + 1) not in skip_indices:
            parts.append("いたわる")
            i += 2
            continue

        # - 擦り減る/擦り減らす: VOICEVOX が「こすり..」側へ寄ることがあるため、B側で読みを確定。
        if surface == "擦り" and next_surface in {"減り", "減る", "減らし", "減らす"} and reading_mecab == "コスリ" and not has_override_in_range(i, i + 1) and (i + 1) not in skip_indices:
            if next_surface == "減り":
                parts.append("すりへり")
            elif next_surface == "減る":
                parts.append("すりへる")
            elif next_surface == "減らし":
                parts.append("すりへらし")
            else:
                parts.append("すりへらす")
            i += 2
            continue

        # - 注げる: VOICEVOX が「ツゲル」側へ寄ることがあるため、B側で「そそげる」を確定。
        if surface == "注げる" and reading_mecab == "ソソゲル":
            parts.append("そそげる")
            i += 1
            continue

        # - 入り方: VOICEVOX が「イリガタ」側へ寄ることがあるため、フレーズで「はいりかた」を確定。
        if (
            surface == "入り"
            and base == "入る"
            and next_surface == "方"
            and reading_mecab == "ハイリ"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("はいりかた")
            i += 2
            continue

        # - 絡み: 一部文脈で濁るため、B側で「からみ」を確定（「政治絡み(ガラミ)」等は除外）。
        if surface == "絡み" and reading_mecab == "カラミ":
            parts.append("からみ")
            i += 1
            continue

        # - 光: VOICEVOX が「コウ」側へ寄ることがあるため、名詞用法(ヒカリ+助詞)は「ひかり」を確定。
        if surface == "光" and reading_mecab == "ヒカリ" and next_surface in {"は", "が", "を", "に", "で", "も", "だけ"}:
            parts.append("ひかり")
            i += 1
            continue

        # - 冷め(冷める): 直前語によって濁るため、B側で「さめ」を確定。
        if surface == "冷め" and base == "冷める" and reading_mecab == "サメ":
            parts.append("さめ")
            i += 1
            continue
        # - 冷え(冷える): VOICEVOX が「ビエ」側へ濁ることがあるため、B側で「ひえ」を確定。
        if surface == "冷え" and base == "冷える" and reading_mecab == "ヒエ":
            parts.append("ひえ")
            i += 1
            continue

        # - 留まっ(留まる): 「留まる」は「とどまる」側へ寄るため、B側で「とどまっ」を確定。
        if surface == "留まっ" and base == "留まる" and reading_mecab == "トマッ":
            parts.append("とどまっ")
            i += 1
            continue

        # - 出そう(出る+そう): VOICEVOX が「シュッソウ」側へ寄ることがあるため、B側で「で」を確定。
        if surface == "出" and base == "出る" and next_surface == "そう" and reading_mecab == "デ" and i not in skip_indices:
            parts.append("で")
            i += 1
            continue

        # - 時こう: 「時効(ジコウ)」側へ誤結合しないよう、「時(とき)+こう」を確定。
        if surface == "時" and next_surface == "こう" and reading_mecab == "トキ":
            parts.append("とき")
            i += 1
            continue

        # - 注ぎ込ん(注ぎ込む): VOICEVOX が「ツギ..」側へ寄るため、「そそぎこん」を確定。
        if base == "注ぎ込む" and surface == "注ぎ込ん" and reading_mecab == "ソソギコン":
            parts.append("そそぎこん")
            i += 1
            continue

        # - 長い間: MeCab が「ナガイマ」側へ誤結合するため、「あいだ」を確定。
        if surface == "間" and reading_mecab == "マ" and prev_surface == "長い":
            parts.append("あいだ")
            i += 1
            continue
        # - 何十年もの間: MeCab が「ものマ」側へ寄るため、「あいだ」を確定。
        if surface == "間" and reading_mecab == "マ" and prev_surface == "もの":
            parts.append("あいだ")
            i += 1
            continue

        # - コナトゥス: 小さい「ゥ」表記が比較で揺れるため、「コナトス」へ正規化。
        if surface == "コナトゥス":
            parts.append("コナトス")
            i += 1
            continue

        # - 戦い続け: VOICEVOX が「オノノイ」側へ寄るため、「たたかい」を確定。
        if surface == "戦い" and base == "戦う" and next_base == "続ける" and reading_mecab == "タタカイ":
            parts.append("たたかい")
            i += 1
            continue

        # - 家中: 「家中を」で MeCab が「カチュウ」側へ寄るため、「いえじゅう」を確定。
        if surface == "家中" and reading_mecab == "カチュウ":
            parts.append("いえじゅう")
            i += 1
            continue

        # - こんな風に: 「風(かぜ)」の誤読を避けるため、「こんな/そんな/あんな + 風 + に」は「ふう」に寄せる。
        if surface == "風" and next_surface == "に" and prev_surface in {"こんな", "そんな", "あんな"} and reading_mecab == "カゼ":
            parts.append("ふう")
            i += 1
            continue

        # - 手: VOICEVOX が「シュ」側へ寄ることがあるため、助詞前は「て」を確定。
        if surface == "手" and reading_mecab == "テ" and next_surface in {"を", "が", "は", "も", "に", "で", "へ", "から", "まで", "の", "と"}:
            parts.append("て")
            i += 1
            continue

        # - 手に入れ(る): 「手」が接尾扱いになり「シュ」側へ寄るため、B側で「て」を確定。
        if surface == "手" and next_surface == "に" and next_next_base == "入れる" and reading_mecab == "シュ":
            parts.append("て")
            i += 1
            continue

        # - つくり(時間を作り): 分+作り で「づくり」側へ寄るため、B側で「つくり」を確定。
        if surface == "作り" and reading_mecab == "ヅクリ" and prev_surface == "分":
            parts.append("つくり")
            i += 1
            continue

        # - 琴: 文脈上は「こと」が自然。
        if surface == "琴" and reading_mecab == "キン":
            parts.append("こと")
            i += 1
            continue

        # - 金: VOICEVOX が「キン」側へ寄ることがあるため、通常の金銭用法では「カネ」を確定。
        #   例: 金を手にする / 金がない / 帰る金もない
        if surface == "金" and next_surface in {"が", "を", "に", "へ", "で", "は", "も", "、", "。", "だ", "です", "だった", "でした"}:
            parts.append("カネ")
            i += 1
            continue

        # - 蓮: MeCab が「ハチス」側になるため、B側で「はす」を確定。
        if surface == "蓮" and reading_mecab == "ハチス":
            parts.append("ハス")
            i += 1
            continue

        # - 真正面: MeCab が「マッショウメン」側になるため、B側で「ましょうめん」を確定。
        if surface == "真正面" and reading_mecab == "マッショウメン":
            parts.append("ましょうめん")
            i += 1
            continue

        # - 暇: VOICEVOX の誤読(イトマ)を避けるため、B側で「ひま」を確定。
        if surface == "暇" and reading_mecab == "ヒマ":
            parts.append("ひま")
            i += 1
            continue

        # - 負け(負ける): VOICEVOX の誤読(フケ)を避けるため、B側で「まけ」を確定。
        if surface == "負け" and base in {"負ける", "負け"} and reading_mecab == "マケ":
            parts.append("まけ")
            i += 1
            continue

        # - 止まれる: VOICEVOX が「ヤマレル」側へ寄ることがあるため、B側で「とまれる」を確定。
        if surface == "止まれる" and reading_mecab == "トマレル":
            parts.append("とまれる")
            i += 1
            continue

        # - 止まれる: MeCab が「止む(ヤマ)+れる」へ崩れるため、B側で「とまれる」を確定。
        if surface == "止ま" and base == "止む" and next_surface == "れる" and reading_mecab == "ヤマ" and not has_override_in_range(i, i + 1) and (i + 1) not in skip_indices:
            parts.append("とまれる")
            i += 2
            continue

        # - 何でも(副詞): VOICEVOX が「ナニデモ」側へ寄ることがあるため、B側で「なんでも」を確定。
        if surface == "何でも" and reading_mecab == "ナンデモ":
            parts.append("なんでも")
            i += 1
            continue

        # - 察でき(る): 「察できます」は「さっし」が自然。
        if surface == "察" and reading_mecab == "サッ" and next_base == "できる":
            parts.append("さっし")
            i += 1
            continue

        # - 「はい」と受け入れる: 受容の「はい」を確定（「全てをはいと受け入れる」等）。
        if (
            surface == "はい"
            and reading_mecab == "ハイ"
            and prev_surface == "を"
            and next_surface == "と"
            and next_next_base == "受け入れる"
        ):
            parts.append("ハイ")
            i += 1
            continue

        # - 恩着せがましく言う: 「がましくいう」が「…クイイ」側へ寄るため、B側で「イウ」を確定。
        prev_prev_surface = str(tokens[i - 2].get("surface") or "") if i > 1 else ""
        if surface == "言う" and reading_mecab == "イウ" and prev_prev_surface == "がま" and prev_surface == "しく":
            parts.append("イウ")
            i += 1
            continue

        # - 「何好子…」: 引用内の「何」をVOI​​CEVOXに寄せず、B側で「なに」を確定。
        if surface == "何" and reading_mecab == "ナニ" and prev_surface == "「" and next_surface == "好子":
            parts.append("なに")
            i += 1
            continue

        # - 正しく: 文脈上は「ただしく」が自然。
        if surface == "正しく" and reading_mecab == "マサシク":
            parts.append("ただしく")
            i += 1
            continue

        # - 罰: 「罰(バチ)」の揺れを避け、「ばつ」を確定（罰当たり等の複合語は除外）。
        if surface == "罰" and reading_mecab == "バチ" and next_surface in {"を", "が", "は", "も", "に", "で", "の", "など", "や", "と", "。", "、"}:
            parts.append("ばつ")
            i += 1
            continue

        # - 何とか: VOICEVOX が「ナニトカ」側へ寄るため、B側で「なんとか」を確定。
        if surface == "何とか" and reading_mecab == "ナントカ":
            parts.append("なんとか")
            i += 1
            continue

        # - 淹れる/淹れた: MeCab が未知語分割するため、B側で「いれ..」を確定。
        if surface == "淹" and next_surface in {"れる", "れ"} and reading_mecab == "淹" and not has_override_in_range(i, i + 1) and (i + 1) not in skip_indices:
            parts.append("いれる" if next_surface == "れる" else "いれ")
            i += 2
            continue
        # VOICEVOX misreads some inflections when left in kanji; force kana in B-text.
        # - 見える: "見えれ(ば)" が「マミエ...」側に寄ることがある
        if surface == "見えれ" and base == "見える" and reading_mecab == "ミエレ":
            parts.append("ミエレ")
            i += 1
            continue
        # - 学ぶ: "学べ(ば)" が「マネ...」側に寄ることがある
        if surface == "学べ" and base == "学ぶ" and reading_mecab == "マナベ":
            parts.append("マナベ")
            i += 1
            continue
        # - 鈍る: "鈍ら(せる)" が「ナマラ...」側に寄ることがある
        if surface == "鈍ら" and base == "鈍る" and reading_mecab == "ニブラ":
            parts.append("ニブラ")
            i += 1
            continue
        # - 鈍る: 終止形も VOICEVOX が「ナマル」側へ寄るため、B側で「ニブル」を確定。
        if surface == "鈍る" and base == "鈍る" and reading_mecab == "ニブル":
            parts.append("ニブル")
            i += 1
            continue
        # - 来る: "来ら(れる)" が「キタラ」側に寄ることがある
        if surface == "来ら" and base == "来る" and reading_mecab == "キタラ":
            parts.append("コラ")
            i += 1
            continue
        # - 埋めよう(埋める): VOICEVOX が「ウズメヨウ」側に寄ることがあるため、B側で「ウメヨウ」を確定。
        #   Tokenization can be: 埋めよ + う OR 埋め + よう
        if (
            base == "埋める"
            and not has_override_in_range(i, i + 1)
            and i not in skip_indices
            and (i + 1) < n
            and (i + 1) not in skip_indices
            and (
                (surface == "埋めよ" and next_surface == "う")
                or (surface == "埋め" and next_surface == "よう")
            )
        ):
            parts.append("ウメヨウ")
            i += 2
            continue
        # - 埋まら(埋まる): VOICEVOX が「ウズマラ」側に寄ることがあるため、B側で「ウマラ」を確定。
        if surface == "埋まら" and base == "埋まる" and reading_mecab == "ウマラ":
            parts.append("ウマラ")
            i += 1
            continue
        # - 潜る: 「深く潜る」文脈は「モグ..」が自然だが、MeCab/VOICEVOXが「クグ..」側に寄ることがある
        if base == "潜る" and prev_surface == "深く" and reading_mecab.startswith("クグ"):
            parts.append("モグ" + reading_mecab[2:])
            i += 1
            continue
        if base == "潜る" and prev_surface == "深く" and reading_mecab.startswith("モグ"):
            parts.append(reading_mecab)
            i += 1
            continue
        # - 背負う: 「背負わない」等で VOICEVOX が「ショワ…」側に寄ることがあるため、B側で音を確定。
        if base == "背負う" and surface == "背負わ" and reading_mecab == "セオワ":
            parts.append("セオワ")
            i += 1
            continue
        # - 内なる: 「内(ウチ)+なる」文脈で VOICEVOX が「ナイ」側に寄ることがあるため、B側で確定。
        if surface == "内" and next_surface == "なる" and reading_mecab == "ウチ":
            parts.append("ウチ")
            i += 1
            continue
        # - 並べて: 一部の文脈で VOICEVOX が「ナベテ」側に寄ることがあるため、B側で確定。
        if base == "並べる" and surface == "並べ" and next_surface == "て" and reading_mecab == "ナラベ":
            parts.append("ナラベ")
            i += 1
            continue
        # - 同じ: VOICEVOX が「ドウジ」側へ寄ることがあるため、B側で「おなじ」に寄せる。
        if surface == "同じ" and reading_mecab == "オナジ":
            parts.append("おなじ")
            i += 1
            continue
        # - 振り返り: VOICEVOX が「フリガエリ」側へ寄ることがあるため、B側で「ふりかえり」に寄せる。
        if surface == "振り返り" and reading_mecab == "フリカエリ":
            parts.append("ふりかえり")
            i += 1
            continue
        # - 黄色/黄色い: VOICEVOX が「オウショク」側へ寄ることがあるため、B側で「きいろ」に寄せる。
        if surface == "黄色" and reading_mecab == "キイロ":
            parts.append("きいろ")
            i += 1
            continue
        if surface == "黄色い" and reading_mecab == "キイロイ":
            parts.append("きいろい")
            i += 1
            continue
        # - 恐怖: VOICEVOX が「キョオブ」側へ寄ることがあるため、B側で「きょうふ」に寄せる。
        if surface == "恐怖" and reading_mecab == "キョウフ":
            # NOTE: 直後が「は/が」などのとき、ひらがな先頭(き...)だとMeCabが誤結合しやすいのでカタカナを使う。
            parts.append("キョウフ")
            i += 1
            continue
        # - 不足: VOICEVOX が「ブソク」側へ寄ることがあるため、B側で「ふそく」に寄せる。
        if surface == "不足" and reading_mecab == "フソク":
            parts.append("ふそく")
            i += 1
            continue
        # - 扱い: VOICEVOX が崩れることがあるため、B側で「あつかい」に寄せる。
        if surface == "扱い" and reading_mecab == "アツカイ":
            parts.append("あつかい")
            i += 1
            continue
        # - 無力感: VOICEVOX が「ムリキカン」側へ寄ることがあるため、B側で「むりょく(感)」に寄せる。
        if surface == "無力" and next_surface == "感" and reading_mecab == "ムリョク":
            parts.append("むりょく")
            i += 1
            continue
        # - 通帳: VOICEVOX が誤読することがあるため、B側で「ツウチョウ」を確定。
        if surface == "通帳" and reading_mecab == "ツウチョウ":
            parts.append("ツウチョウ")
            i += 1
            continue
        # - 版: 「版(はん)」を MeCab が「バン」側に寄せるため、B側で「ハン」を確定。
        if surface == "版" and reading_mecab == "バン":
            parts.append("ハン")
            i += 1
            continue
        # - 緩く: VOICEVOX が「ナルク」側へ寄ることがあるため、B側で「ゆるく」を確定。
        if surface == "緩く" and base == "緩い" and reading_mecab == "ユルク":
            parts.append("ゆるく")
            i += 1
            continue
        # - 腹痛: MeCab が「ハライタ」側へ寄るが、VOICEVOX は「フクツウ」なので、B側で「ふくつう」を確定。
        if surface == "腹痛" and reading_mecab == "ハライタ":
            parts.append("ふくつう")
            i += 1
            continue
        # - 少し: VOICEVOX が「ショウシ」側へ寄ることがあるため、B側で「すこし」に寄せる。
        if surface == "少し" and reading_mecab == "スコシ":
            parts.append("すこし")
            i += 1
            continue
        # - 微差: VOICEVOX が誤読することがあるため、B側で「びさ」を確定。
        if (
            surface == "微"
            and next_surface == "差"
            and reading_mecab == "ビ"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("びさ")
            i += 2
            continue
        # - 一言: MeCab が「イチゲン」側へ寄ることがあるため、B側で「ひとこと」を確定。
        if surface == "一言" and reading_mecab in {"イチゲン", "ヒトコト"}:
            parts.append("ひとこと")
            i += 1
            continue
        # - 一択: MeCab が「一+択」に分割し「イチ+択」になりやすいので、B側で「いったく」を確定。
        if (
            surface == "一"
            and next_surface == "択"
            and reading_mecab == "イチ"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("いったく")
            i += 2
            continue
        # - 良しとし: VOICEVOX が「イシ」側へ寄ることがあるため、B側で「よし」に寄せる。
        if surface == "良し" and next_surface == "と" and reading_mecab == "ヨシ":
            parts.append("よし")
            i += 1
            continue
        # - 怠さ: MeCab が「オコタ+さ」になりやすいので、B側で「だるさ」を確定。
        if (
            surface == "怠"
            and next_surface == "さ"
            and base == "怠る"
            and reading_mecab == "オコタ"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("だるさ")
            i += 2
            continue
        # - 強張る: MeCab が「強(ツヨ)+張る(ハル)」に分割しがちなので、B側で「こわば..」へ寄せる。
        if (
            surface == "強"
            and base == "強い"
            and reading_mecab == "ツヨ"
            and next_surface in {"張る", "張り", "張っ", "張ら"}
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            if next_surface == "張る":
                parts.append("こわばる")
            elif next_surface == "張り":
                parts.append("こわばり")
            elif next_surface == "張っ":
                parts.append("こわばっ")
            elif next_surface == "張ら":
                parts.append("こわばら")
            else:
                parts.append("こわば")
            i += 2
            continue
        # - 台詞: MeCab が「ダイシ」側へ寄ることがあるため、B側で「せりふ」に寄せる。
        if surface == "台詞" and reading_mecab == "ダイシ":
            parts.append("せりふ")
            i += 1
            continue
        # - 剥がす: MeCab が「ヘガス」側へ寄ることがあるため、B側で「はがす」に寄せる。
        if surface == "剥がす" and base == "剥がす" and reading_mecab == "ヘガス":
            parts.append("はがす")
            i += 1
            continue
        # - 何分: 時間(なんぷん)として扱う（「何分で/何分以内/何分スクロール…」など）。
        if surface == "何分" and reading_mecab == "ナニブン":
            time_like_next = next_surface in {"で", "以内", "か", "まで", "後", "スクロール", "ぼんやり"}
            time_like_verb = False
            if not time_like_next:
                for k in range(i + 1, min(i + 6, n_tokens)):
                    if str(tokens[k].get("base") or "") == "使う":
                        time_like_verb = True
                        break
            if time_like_next or time_like_verb:
                parts.append("なんぷん")
                i += 1
                continue
        # - 何で: VOICEVOX が「ナニデ」側へ寄ることがあるため、B側で「なんで」を確定。
        if surface == "何で" and reading_mecab == "ナンデ":
            parts.append("なんで")
            i += 1
            continue
        # - 生: 「生の」はナマ、「生を」はセイ（人生/生命の「生」）に寄せる。
        if surface == "生" and reading_mecab == "ナマ" and next_surface == "の":
            parts.append("なま")
            i += 1
            continue
        if surface == "生" and reading_mecab == "ナマ" and next_surface == "を":
            parts.append("せい")
            i += 1
            continue
        # - 自然音(自然+音): MeCab は「オン」になりやすいが、文脈上は「おと」が自然なのでB側で寄せる。
        if surface == "音" and prev_surface == "自然" and reading_mecab == "オン":
            parts.append("おと")
            i += 1
            continue
        # - 外(そと): 接尾扱いで「ガイ」になることがあるため、格助詞前は「そと」に寄せる。
        if surface == "外" and reading_mecab == "ガイ" and next_surface in {"に", "へ", "で", "を"}:
            parts.append("そと")
            i += 1
            continue
        # - 保て: VOICEVOX が「タモテテ」側へ寄るため、B側で「たもて」を確定。
        if surface == "保て" and base == "保つ" and reading_mecab == "タモテ":
            parts.append("たもて")
            i += 1
            continue
        # - 出せ: VOICEVOX が「シュッセ」側へ寄ることがあるため、B側で「だせ」を確定。
        if surface == "出せ" and base == "出す" and reading_mecab == "ダセ":
            parts.append("だせ")
            i += 1
            continue
        # - 欲している: VOICEVOX は「ホッシ..」側になるため、B側も合わせる（欲し+て）。
        if surface == "欲し" and reading_mecab == "ホシ" and next_surface == "て":
            parts.append("ほっし")
            i += 1
            continue
        # - 疲れ: VOICEVOX が「ズカレ」側へ寄ることがあるため、B側で「つかれ」を確定。
        if surface == "疲れ" and base == "疲れる" and reading_mecab == "ツカレ":
            parts.append("つかれ")
            i += 1
            continue
        # - 行き先: 文脈上は「いきさき」が自然。
        if surface == "行き先" and reading_mecab == "ユキサキ":
            parts.append("いきさき")
            i += 1
            continue
        # - 手を放す: 「放す」が「ホカス」側に寄ることがあるため、B側で「ハナス」を確定。
        prev_prev_surface = str(tokens[i - 2].get("surface") or "") if i > 1 else ""
        prev_prev_pos = str(tokens[i - 2].get("pos") or "") if i > 1 else ""
        prev_prev_subpos = str(tokens[i - 2].get("subpos") or "") if i > 1 else ""
        # - 二段構え: VOICEVOX が「カマエ」側へ寄ることがあるため、数+段+構え のときは「がまえ」を確定。
        if (
            surface == "構え"
            and reading_mecab == "ガマエ"
            and prev_surface == "段"
            and prev_prev_pos == "名詞"
            and prev_prev_subpos == "数"
        ):
            parts.append("がまえ")
            i += 1
            continue
        # - 一度書き: VOICEVOX が「カキ」側へ寄ることがあるため、「度+書き(ガキ)」は「がき」を確定。
        if surface == "書き" and reading_mecab == "ガキ" and prev_surface == "度":
            parts.append("がき")
            i += 1
            continue
        # - 書き換わる/書き換え: VOICEVOX が「ガキ」側へ寄ることがあるため、「かき」を確定。
        if (
            surface == "書き"
            and base == "書く"
            and reading_mecab == "カキ"
            and (next_surface.startswith("換") or next_base.startswith("換"))
        ):
            parts.append("かき")
            i += 1
            continue
        # - 描き直す: VOICEVOX が「カキナオス」側へ寄ることがあるため、「えがき」を確定。
        if surface == "描き" and base == "描く" and reading_mecab == "エガキ" and next_base == "直す":
            parts.append("えがき")
            i += 1
            continue
        if base == "放す" and surface == "放す" and reading_mecab == "ホカス":
            parts.append("ハナス")
            i += 1
            continue
        # - 眠そう: VOICEVOX が「ネムラソウ」側へ寄ることがあるため、B側で「ねむそう」を確定。
        if (
            surface == "眠"
            and base == "眠い"
            and next_surface == "そう"
            and reading_mecab == "ネム"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("ねむそう")
            i += 2
            continue
        # - インスタ映え: VOICEVOX は「バエ」だが MeCab は「ハエ」になりやすいので、B側で「バエ」を確定。
        if surface == "映え" and prev_surface == "インスタ" and reading_mecab == "ハエ":
            parts.append("バエ")
            i += 1
            continue
        # - 何に: VOICEVOX が「ナンニ」側へ寄ることがあるため、B側で「なにに」を確定。
        #   NOTE: カタカナにすると直後の1字漢字が接尾扱いになりやすい（手→シュ等）ので、ひらがなを使う。
        if (
            surface == "何"
            and next_surface == "に"
            and reading_mecab == "ナニ"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("なにに")
            i += 2
            continue
        # - 川: 「いつか川」は接尾トークンになり「ガワ」化しがちなので、一般名詞として「カワ」へ寄せる。
        if surface == "川" and prev_surface == "いつか" and reading_mecab == "ガワ":
            parts.append("カワ")
            i += 1
            continue
        # - 時間帯: VOICEVOX が「ジカンオビ」側へ寄ることがあるため、「時間+帯」は「タイ」を確定。
        if surface == "帯" and prev_surface == "時間" and reading_mecab == "タイ":
            parts.append("タイ")
            i += 1
            continue
        # - 後で: VOICEVOX が「ゴデ」側へ寄ることがあるため、「あとで」を確定。
        if surface == "後で" and reading_mecab == "アトデ":
            parts.append("あとで")
            i += 1
            continue
        if (
            surface == "後"
            and next_surface == "で"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("あとで")
            i += 2
            continue
        # - 上で(うえで): 接尾(法律上で など)は除外し、「上(ウエ)+で」のときのみ「うえで」を確定。
        if (
            surface == "上"
            and next_surface == "で"
            and reading_mecab == "ウエ"
            and not has_override_in_range(i, i + 1)
            and (i + 1) not in skip_indices
        ):
            parts.append("うえで")
            i += 2
            continue
        # - 端(はし): VOICEVOX は「ハシ」になりやすいが MeCab は「ハジ」になりやすいので、「はし」を確定。
        if surface == "端" and reading_mecab in {"ハジ", "タン"} and next_surface in {"に", "へ", "を", "で", "から", "まで", "の", "、", "。", "」"}:
            parts.append("はし")
            i += 1
            continue
        # 1-char Kanji that VOICEVOX tends to choose the wrong kun/on reading for.
        if surface == "朝" and reading_mecab == "アサ":
            parts.append("アサ")
            i += 1
            continue
        # 「守り(名詞)」は「モリ」になりがちだが、語としては「マモリ」が自然な文脈が多い。
        if surface == "守り" and pos == "名詞" and reading_mecab == "モリ":
            parts.append("マモリ")
            i += 1
            continue
        # 「積み上がり/積み上がった」は「ズミ」側に寄ることがあるため、直後が「上が...」なら「ツミ」を確定。
        if surface == "積み" and base == "積む" and reading_mecab == "ツミ" and next_surface.startswith("上が"):
            parts.append("ツミ")
            i += 1
            continue
        # 「焦る」は「コゲる」側に寄ることがあるため、B側で「アセル」を確定。
        if base == "焦る" and reading_mecab == "アセル":
            parts.append("アセル")
            i += 1
            continue
        # 「責め」は「ゼメ」側に寄ることがあるため、B側で「セメ」を確定。
        if surface == "責め" and reading_mecab == "セメ":
            parts.append("セメ")
            i += 1
            continue
        # 「辛い」は文脈で「ツライ/カライ」が揺れるため、食べ物文脈だけ「カラ..」へ寄せる。
        if base == "辛い" and reading_mecab.startswith("ツラ"):
            spicy_next = next_surface in {"カレー", "ラーメン", "キムチ", "唐辛子", "スパイス", "辛口", "味", "ソース", "わさび", "からし"}
            parts.append(("カラ" if spicy_next else "ツラ") + reading_mecab[2:])
            i += 1
            continue
        # 「歪む」は MeCab が「イガ...」側に寄ることがあるため、B側で「ユガ...」へ確定。
        if base == "歪む" and reading_mecab.startswith("イガ"):
            parts.append("ユガ" + reading_mecab[2:])
            i += 1
            continue
        # 「罪悪感なく」は「罪悪感じなく」側に寄ることがあるため、フレーズで確定。
        if surface == "罪悪" and next_surface == "感" and (i + 2) < n:
            nn_surface = str(tokens[i + 2].get("surface") or "")
            if nn_surface == "なく" and not has_override_in_range(i, i + 2) and all(k not in skip_indices for k in (i + 1, i + 2)):
                parts.append("ザイアクカンナク")
                i += 3
                continue
        if surface == "灯" and reading_mecab == "アカリ":
            parts.append("アカリ")
            i += 1
            continue
        # 「声」は「セイ」側に寄ることがあるため、単独語+助詞のときは「コエ」を確定。
        if surface == "声" and next_surface in {"か", "が", "を", "に", "で", "は", "も", "の", "、", "。", "」"}:
            parts.append("コエ")
            i += 1
            continue
        # 「口」は文脈で濁って読まれがちなので、単独語+助詞のときは「クチ」を確定。
        if surface == "口" and next_surface in {"に", "を", "が", "は", "で", "も", "の", "、", "。"}:
            parts.append("クチ")
            i += 1
            continue
        # 「梁（はり）」は誤読しやすい単漢字のため、語として出るときは「ハリ」を確定。
        if surface == "梁" and next_surface in {"の", "が", "を", "に", "で", "は", "も", "、", "。"}:
            parts.append("ハリ")
            i += 1
            continue
        # 「称え(たたえ)」が「トナエ」側に寄ることがあるため、B側で確定。
        if base == "称える" and surface.startswith("称え") and reading_mecab == "トナエ":
            parts.append("タタエ")
            i += 1
            continue
        # 「圧」は MeCab が読めずに漢字のまま残ることがあるため、B側で音に寄せる。
        if surface == "圧" and reading_mecab == "圧":
            parts.append("アツ")
            i += 1
            continue
        # 「紙」は VOICEVOX が「シ」側に寄ることがあるため、B側で「カミ」を確定。
        if surface == "紙" and reading_mecab == "カミ" and next_surface in {"に", "を", "が", "は", "で", "も", "の", "、", "。"}:
            parts.append("カミ")
            i += 1
            continue
        # 「癖」は VOICEVOX が「ヘキ」側に寄ることがあるため、通常の用法では「クセ」を確定。
        if surface == "癖" and reading_mecab == "クセ" and next_surface in {"が", "を", "に", "は", "で", "も", "の", "、", "。"}:
            parts.append("クセ")
            i += 1
            continue
        # 「場」は単独だと VOICEVOX が「ジョウ」側に寄ることがあるため、B側で「バ」を確定。
        if surface == "場" and reading_mecab == "バ":
            parts.append("バ")
            i += 1
            continue
        # 「分(ブン)」は理由/差分の用法で VOICEVOX が「ワケ」側に寄ることがあるため、B側で「ブン」へ固定。
        if surface == "分" and reading_mecab == "ブン":
            parts.append("ブン")
            i += 1
            continue
        # 「端」は「はした」側に誤解析されることがあるため、位置の意味では「ハシ」に寄せる。
        if surface == "端" and reading_mecab == "ハシタ" and next_surface in {"に", "で", "へ", "から", "まで", "、", "。"}:
            parts.append("ハシ")
            i += 1
            continue
        # 「次」は文脈で「ジ」になり得るが、単独の「次は/次の」は「ツギ」が自然。
        if surface == "次" and reading_mecab == "ジ" and next_surface in {"は", "の", "に", "が", "を", "も", "で", "、", "。"}:
            parts.append("ツギ")
            i += 1
            continue
        # 「我を通す」は「ガ」が自然（MeCab は「ワガ」になりがち）。
        if surface == "我" and next_surface == "を" and (i + 2) < n:
            nn_base = str(tokens[i + 2].get("base") or "")
            nn_surface = str(tokens[i + 2].get("surface") or "")
            if nn_base == "通す" or nn_surface.startswith("通"):
                parts.append("ガ")
                i += 1
                continue
        # 「何と言う/何という」は話し言葉だと「ナン」が自然。
        if surface == "何" and next_surface in {"と", "という"}:
            parts.append("ナン")
            i += 1
            continue
        # 「力」は単独だと「リョク」になり得るが、多くの文脈では「チカラ」が自然。
        if surface == "力" and reading_mecab == "リョク" and next_surface in {"に", "が", "を", "も", "で", "、", "。"}:
            parts.append("チカラ")
            i += 1
            continue
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
        if surface == "一行" and reading_mecab == "イッコウ" and (next_base == "書く" or next_surface in {"ずつ", "づつ"}):
            parts.append("イチギョウ")
            i += 1
            continue
        # 「一手」は「イチテ/イッテ」が揺れるが、語としては「イッテ」が自然。
        if surface == "一手" and reading_mecab == "イチテ":
            parts.append("イッテ")
            i += 1
            continue
        # 「怒り」は VOICEVOX が「オコリ」側に誤読しやすいので、B側で確定する。
        # 例外: 「怒りっぽい」等は「オコリ」側が自然。
        if surface == "怒り" and reading_mecab == "イカリ":
            parts.append("オコリ" if next_surface.startswith("っぽ") else "イカリ")
            i += 1
            continue
        # 「今」は VOICEVOX が「コン」側へ寄ることがあるため、B側で「いま」を確定。
        if surface == "今" and reading_mecab == "イマ":
            parts.append("いま")
            i += 1
            continue
        # 「今」は文脈で MeCab が「コン」になり得るが、多くは「イマ」が自然。
        if surface == "今" and reading_mecab == "コン":
            # カタカナ直後の1文字名詞（例: イマ手）を MeCab が接尾扱いして壊すことがあるため、
            # 直後が単漢字の場合だけ読点で分離して安全にする。
            if len(next_surface) == 1 and re.search(r"[一-龯々〆ヵヶ]", next_surface):
                parts.append("イマ、")
            else:
                parts.append("イマ")
            i += 1
            continue
        # 「今日中（きょうじゅう）」は「キョウチュウ」になりがちなのでB側で確定。
        if surface == "中" and prev_surface == "今日":
            parts.append("ジュウ")
            i += 1
            continue
        # 「Xの中」は位置の意味では「ナカ」が自然（MeCab/VOICEVOXの揺れを抑える）。
        if surface == "中" and prev_surface in {"の", "ノ"} and next_surface in {
            "で",
            "に",
            "へ",
            "を",
            "から",
            "まで",
            "が",
            "は",
            "も",
            "、",
            "。",
        }:
            parts.append("ナカ")
            i += 1
            continue
        # 「いい人」は VOICEVOX が「イイジン」側に寄ることがあるため、語として確定。
        if surface == "人" and prev_surface == "いい" and reading_mecab == "ヒト" and next_surface in {"で", "に", "は", "が", "を", "も", "、", "。"}:
            parts.append("ヒト")
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
        if surface == "後" and reading_mecab in {"ノチ", "ゴ"}:
            # Duration: X(年/日/分/秒/時/時間/ヶ月)後 -> ゴ
            if prev_surface in {"年", "日", "分", "秒", "時", "時間", "ヶ月", "か月", "晩"}:
                parts.append("ゴ")
                i += 1
                continue
            # Demonstratives: この/その/あの後 -> アト
            if prev_surface in {"この", "その", "あの"}:
                parts.append("アト")
                i += 1
                continue
            # Adverbial: 後で/後でも -> アト
            if next_surface in {"で", "でも"}:
                parts.append("アト")
                i += 1
                continue
            if next_surface in {"", "、", "。", "に", "の", "は", "を", "が", "で", "でも", "も", "から"}:
                parts.append("アト")
                i += 1
                continue
        if surface == "間" and prev_surface == "ヶ月" and next_surface in {"", "、", "。", "に", "の", "は", "を", "が", "で", "も"}:
            parts.append("カン")
            i += 1
            continue
        if surface == "間" and reading_mecab == "マ" and next_surface in {"だけ", "だ", "です", "だった", "でした", "、", "。", "に", "の", "は", "を", "が", "で", "も", "から"}:
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
