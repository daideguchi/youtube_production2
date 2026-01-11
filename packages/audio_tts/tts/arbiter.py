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

    parts: List[str] = []
    i = 0
    n = len(tokens)
    while i < n:
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
        parts.append(words.get(surface, surface) if words else surface)
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
