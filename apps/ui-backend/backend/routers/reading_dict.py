from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from audio_tts.tts.auditor import calc_kana_mismatch_score
from audio_tts.tts.mecab_tokenizer import tokenize_with_mecab
from audio_tts.tts.reading_dict import (
    ReadingEntry,
    is_banned_surface,
    is_safe_reading,
    load_channel_reading_dict,
    merge_channel_readings,
    normalize_reading_kana,
    save_channel_reading_dict,
)
from backend.app.channel_info_store import find_channel_directory, refresh_channel_info
from factory_common.paths import planning_root as ssot_planning_root
from factory_common.paths import script_data_root as ssot_script_data_root

router = APIRouter(prefix="/api/reading-dict", tags=["reading-dict"])

DATA_ROOT = ssot_script_data_root()
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"


def normalize_channel_code(channel: str) -> str:
    raw = channel.strip()
    if not raw or Path(raw).name != raw:
        raise HTTPException(status_code=400, detail="Invalid channel identifier")
    channel_code = raw.upper()
    if not re.match(r"^CH\\d+$", channel_code):
        raise HTTPException(status_code=400, detail="Invalid channel identifier")
    if (DATA_ROOT / channel_code).is_dir():
        return channel_code
    if (CHANNEL_PLANNING_DIR / f"{channel_code}.csv").is_file():
        return channel_code
    if find_channel_directory(channel_code) is not None:
        return channel_code
    # Fallback: allow channels known only via channels_info.json cache.
    if channel_code in refresh_channel_info():
        return channel_code
    raise HTTPException(status_code=404, detail=f"Channel {channel_code} not found")


class ChannelReadingUpsertRequest(BaseModel):
    surface: str = Field(..., description="表記（辞書キー）")
    reading_kana: str = Field(..., description="読み（カナ）")
    reading_hira: Optional[str] = Field(None, description="読み（ひらがな・任意）")
    voicevox_kana: Optional[str] = Field(None, description="Voicevox 元読み（任意）")
    accent_moras: Optional[List[str]] = Field(None, description="アクセントモーラ列（任意）")
    source: Optional[str] = Field("manual", description="登録元")


@router.get("/{channel}")
def get_channel_reading_dict_api(channel: str):
    channel_code = normalize_channel_code(channel)
    data = load_channel_reading_dict(channel_code)

    def _compute_mecab_kana(surface: str) -> str:
        try:
            tokens = tokenize_with_mecab(surface)
            parts: List[str] = []
            for tok in tokens:
                reading = tok.get("reading_mecab") or tok.get("surface") or ""
                parts.append(str(reading))
            return normalize_reading_kana("".join(parts))
        except Exception:
            return ""

    enriched: Dict[str, Dict[str, object]] = {}
    for surface, meta in data.items():
        meta_dict = dict(meta or {})
        mecab_kana = _compute_mecab_kana(surface)
        meta_dict["mecab_kana"] = mecab_kana
        voicevox_kana = meta_dict.get("voicevox_kana")
        if isinstance(voicevox_kana, str) and voicevox_kana:
            similarity, mora_diff, _ = calc_kana_mismatch_score(mecab_kana, voicevox_kana)
            meta_dict["similarity"] = similarity
            meta_dict["mora_diff"] = mora_diff
        enriched[surface] = meta_dict

    return enriched


@router.post("/{channel}")
def upsert_channel_reading_dict_api(channel: str, payload: ChannelReadingUpsertRequest):
    channel_code = normalize_channel_code(channel)
    surface = payload.surface.strip()
    reading_kana = payload.reading_kana.strip()
    reading_hira = (payload.reading_hira or "").strip() or reading_kana
    if is_banned_surface(surface):
        raise HTTPException(status_code=400, detail="短すぎる/曖昧な単語は辞書登録できません。")
    if not reading_kana:
        raise HTTPException(status_code=400, detail="読みを入力してください。")
    normalized_kana = normalize_reading_kana(reading_kana)
    normalized_hira = normalize_reading_kana(reading_hira)
    if not is_safe_reading(normalized_kana):
        raise HTTPException(status_code=400, detail="読みはカナで入力してください（漢字や説明文は不可）。")
    if normalized_kana == surface:
        raise HTTPException(status_code=400, detail="読みが表記と同じなので登録不要です。")
    entry = ReadingEntry(
        surface=surface,
        reading_hira=normalized_hira or normalized_kana,
        reading_kana=normalized_kana,
        voicevox_kana=(payload.voicevox_kana or "").strip() or None,
        accent_moras=payload.accent_moras,
        source=payload.source or "manual",
        last_updated="",
    )
    try:
        merged = merge_channel_readings(channel_code, {surface: entry})
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return merged


@router.delete("/{channel}/{surface}")
def delete_channel_reading_dict_entry_api(channel: str, surface: str):
    channel_code = normalize_channel_code(channel)
    key = surface.strip()
    current = load_channel_reading_dict(channel_code)
    if key not in current:
        raise HTTPException(status_code=404, detail="entry not found")
    current.pop(key, None)
    try:
        save_channel_reading_dict(channel_code, current)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"success": True}

