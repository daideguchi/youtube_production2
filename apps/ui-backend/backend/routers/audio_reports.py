from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.app.normalize import normalize_channel_code, normalize_video_number
from factory_common.paths import audio_final_dir, script_data_root as ssot_script_data_root

router = APIRouter(prefix="/api/audio", tags=["audio"])

DATA_ROOT = ssot_script_data_root()


class AudioIntegrityItem(BaseModel):
    channel: str
    video: str
    missing: List[str]
    audio_path: Optional[str] = None
    srt_path: Optional[str] = None
    b_text_path: Optional[str] = None
    audio_duration: Optional[float] = None
    srt_duration: Optional[float] = None
    duration_diff: Optional[float] = None


class VoicevoxKanaDiff(BaseModel):
    engine_kana: str = ""
    llm_kana: str = ""
    diff: List[Any] = Field(default_factory=list)


class AudioAnalysisResponse(BaseModel):
    channel: str
    video: str
    b_text_with_pauses: Optional[str] = None
    pause_map: Optional[List[Any]] = None
    voicevox_kana: Optional[str] = None
    voicevox_kana_corrected: Optional[str] = None
    voicevox_kana_diff: Optional[VoicevoxKanaDiff] = None
    voicevox_kana_llm_ref: Optional[Any] = None
    voicevox_accent_phrases: Optional[Any] = None
    warnings: List[str] = Field(default_factory=list)


def _infer_srt_duration_seconds(path: Path) -> Optional[float]:
    if not path.exists() or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    # Find last timestamp in HH:MM:SS,mmm
    import re

    matches = list(re.finditer(r"(\\d{2}):(\\d{2}):(\\d{2}),(\\d{3})", text))
    if not matches:
        return None
    hh, mm, ss, ms = matches[-1].groups()
    try:
        h = int(hh)
        m = int(mm)
        s = int(ss)
        ms_val = int(ms)
    except ValueError:
        return None
    return h * 3600 + m * 60 + s + ms_val / 1000.0


def _iter_video_dirs() -> Iterable[tuple[str, str, Path]]:
    if not DATA_ROOT.exists():
        return []
    for channel_dir in sorted(DATA_ROOT.iterdir()):
        if not channel_dir.is_dir():
            continue
        channel = channel_dir.name.upper()
        if not channel.startswith("CH"):
            continue
        for video_dir in sorted(channel_dir.iterdir()):
            if not video_dir.is_dir():
                continue
            video = video_dir.name
            yield channel, video, video_dir


def _load_audio_analysis(channel: str, video: str) -> AudioAnalysisResponse:
    channel_code = normalize_channel_code(channel)
    video_no = normalize_video_number(video)
    base_dir = DATA_ROOT / channel_code / video_no
    warnings: List[str] = []

    audio_prep = base_dir / "audio_prep"
    final_dir = audio_final_dir(channel_code, video_no)

    from backend.main import resolve_text_file

    # --- B text (TTS input snapshot) -----------------------------------------
    # Preferred: final/a_text.txt (what was actually synthesized).
    # Fallbacks are best-effort for in-progress / legacy episodes.
    final_a_text_path = final_dir / "a_text.txt"
    prep_sanitized_path = audio_prep / "script_sanitized.txt"
    legacy_final_b_path = final_dir / "b_text_with_pauses.txt"
    legacy_prep_b_path = audio_prep / "b_text_with_pauses.txt"

    b_text_source: Optional[str] = None
    b_text: Optional[str] = None
    if final_a_text_path.exists():
        b_text = resolve_text_file(final_a_text_path)
        b_text_source = "final/a_text.txt"
    elif prep_sanitized_path.exists():
        b_text = resolve_text_file(prep_sanitized_path)
        b_text_source = "audio_prep/script_sanitized.txt"
        warnings.append("final/a_text.txt missing; using audio_prep/script_sanitized.txt")
    elif legacy_final_b_path.exists():
        b_text = resolve_text_file(legacy_final_b_path)
        b_text_source = "final/b_text_with_pauses.txt (legacy)"
        warnings.append("final/a_text.txt missing; using legacy final/b_text_with_pauses.txt")
    elif legacy_prep_b_path.exists():
        b_text = resolve_text_file(legacy_prep_b_path)
        b_text_source = "audio_prep/b_text_with_pauses.txt (legacy)"
        warnings.append("final/a_text.txt missing; using legacy audio_prep/b_text_with_pauses.txt")
    else:
        # Last resort: show A-text preview (not synthesized yet).
        assembled_human_path = base_dir / "content" / "assembled_human.md"
        assembled_path = base_dir / "content" / "assembled.md"
        a_path = assembled_human_path if assembled_human_path.exists() else assembled_path
        if a_path.exists():
            b_text = resolve_text_file(a_path)
            b_text_source = "A-text preview"
            warnings.append("TTS input snapshot missing; showing A-text preview (not yet synthesized)")
        else:
            warnings.append("TTS input text missing (final/a_text.txt, audio_prep/script_sanitized.txt, assembled.md)")

    if b_text_source and b_text_source != "final/a_text.txt":
        warnings.append(f"b_text_source: {b_text_source}")
    if not b_text:
        warnings.append("b_text is empty")

    # --- pause_map (best-effort legacy/aux) ----------------------------------
    pause_map_path = audio_prep / "pause_map.json"

    raw_pause_map: Any = None
    pause_map: Optional[List[Dict[str, Any]]] = None
    if pause_map_path.exists():
        try:
            raw_pause_map = json.loads(pause_map_path.read_text(encoding="utf-8"))
        except Exception:
            warnings.append("pause_map.json parse failed")
    else:
        warnings.append("pause_map.json missing")

    if isinstance(raw_pause_map, dict):
        pauses = raw_pause_map.get("pauses")
        if isinstance(pauses, list):
            pause_map = []
            for idx, val in enumerate(pauses, start=1):
                try:
                    pause_val = float(val)
                except (TypeError, ValueError):
                    warnings.append(f"pause_map: invalid pause value at {idx}")
                    continue
                pause_map.append({"section": idx, "pause_sec": pause_val})
    elif isinstance(raw_pause_map, list):
        pause_map = []
        for idx, entry in enumerate(raw_pause_map, start=1):
            if isinstance(entry, dict):
                try:
                    pause_val = float(entry.get("pause_sec") or entry.get("pause") or entry.get("value") or 0.0)
                except (TypeError, ValueError):
                    warnings.append(f"pause_map: invalid pause value at {idx}")
                    continue
                try:
                    section_idx = int(
                        entry.get("section")
                        or entry.get("section_index")
                        or entry.get("index")
                        or entry.get("section_idx")
                        or idx
                    )
                except (TypeError, ValueError):
                    section_idx = idx
                pause_map.append({"section": section_idx, "pause_sec": pause_val})
            else:
                try:
                    pause_val = float(entry)
                except (TypeError, ValueError):
                    warnings.append(f"pause_map: invalid pause value at {idx}")
                    continue
                pause_map.append({"section": idx, "pause_sec": pause_val})
    elif raw_pause_map is not None:
        warnings.append("pause_map.json unexpected format (expected list or {pauses:[]})")

    if pause_map is not None and len(pause_map) == 0:
        warnings.append("pause_map.json has 0 entries")

    engine_meta: dict = {}
    strict_log_path = final_dir / "log.json"
    legacy_log_path = audio_prep / "log.json"
    engine_metadata_path = audio_prep / "engine_metadata.json"
    legacy_engine_metadata_path = final_dir / "engine_metadata.json"

    derived_voicevox_kana: Optional[str] = None
    derived_voicevox_kana_corrected: Optional[str] = None

    # Strict pipeline log: segments[*].voicevox / segments[*].reading
    if strict_log_path.exists():
        try:
            strict_data = json.loads(strict_log_path.read_text(encoding="utf-8"))
            if isinstance(strict_data, dict) and isinstance(strict_data.get("segments"), list):
                segments = strict_data.get("segments") or []
                kana_lines: List[str] = []
                reading_lines: List[str] = []
                for seg in segments:
                    if not isinstance(seg, dict):
                        continue
                    vv = seg.get("voicevox")
                    rd = seg.get("reading")
                    if isinstance(vv, str) and vv.strip():
                        kana_lines.append(vv.strip())
                    if isinstance(rd, str) and rd.strip():
                        reading_lines.append(rd.strip())
                if kana_lines:
                    derived_voicevox_kana = "\n".join(kana_lines)
                if reading_lines:
                    derived_voicevox_kana_corrected = "\n".join(reading_lines)
        except Exception:
            warnings.append("final/log.json parse failed")

    # Legacy engine metadata (engine_metadata.json or legacy log.json)
    for candidate in (legacy_engine_metadata_path, engine_metadata_path, strict_log_path, legacy_log_path):
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if "engine_metadata" in data and isinstance(data["engine_metadata"], dict):
                        engine_meta = data["engine_metadata"]
                    else:
                        engine_meta = data
                    break
            except Exception:
                warnings.append(f"{candidate.name} parse failed")

    kana_diff = None
    raw_diff = engine_meta.get("voicevox_kana_diff") if isinstance(engine_meta, dict) else None
    if isinstance(raw_diff, dict):
        kana_diff = VoicevoxKanaDiff(
            engine_kana=str(raw_diff.get("engine_kana") or ""),
            llm_kana=str(raw_diff.get("llm_kana") or ""),
            diff=raw_diff.get("diff") or [],
        )

    return AudioAnalysisResponse(
        channel=channel_code,
        video=video_no,
        b_text_with_pauses=b_text,
        pause_map=pause_map,
        voicevox_kana=(
            derived_voicevox_kana
            or (engine_meta.get("voicevox_kana") if isinstance(engine_meta, dict) else None)
        ),
        voicevox_kana_corrected=(
            derived_voicevox_kana_corrected
            or (engine_meta.get("voicevox_kana_corrected") if isinstance(engine_meta, dict) else None)
        ),
        voicevox_kana_diff=kana_diff,
        voicevox_kana_llm_ref=engine_meta.get("voicevox_kana_llm_ref") if isinstance(engine_meta, dict) else None,
        voicevox_accent_phrases=engine_meta.get("voicevox_accent_phrases") if isinstance(engine_meta, dict) else None,
        warnings=warnings,
    )


@router.get("/integrity", response_model=List[AudioIntegrityItem])
def audio_integrity_report():
    from backend.main import get_audio_duration_seconds, safe_relative_path

    items: List[AudioIntegrityItem] = []
    for channel, video, _video_dir in _iter_video_dirs():
        final_dir = audio_final_dir(channel, video)
        a_text_path = final_dir / "a_text.txt"
        log_path = final_dir / "log.json"

        audio_candidates = [
            final_dir / f"{channel}-{video}.wav",
            final_dir / f"{channel}-{video}.flac",
            final_dir / f"{channel}-{video}.mp3",
            final_dir / f"{channel}-{video}.m4a",
        ]
        audio_path = next((p for p in audio_candidates if p.exists()), None)
        srt_path = final_dir / f"{channel}-{video}.srt"
        missing: List[str] = []
        if not a_text_path.exists():
            missing.append("a_text.txt")
        if not log_path.exists():
            missing.append("log.json")
        if audio_path is None:
            missing.append(f"{channel}-{video}.(wav|flac|mp3|m4a)")
        if not srt_path.exists():
            missing.append(f"{channel}-{video}.srt")
        audio_duration = get_audio_duration_seconds(audio_path) if audio_path else None
        srt_duration = _infer_srt_duration_seconds(srt_path) if srt_path.exists() else None
        duration_diff = None
        if audio_duration is not None and srt_duration is not None:
            duration_diff = abs(audio_duration - srt_duration)
            if duration_diff < 0.01:
                duration_diff = 0.0
        items.append(
            AudioIntegrityItem(
                channel=channel,
                video=video,
                missing=missing,
                audio_path=safe_relative_path(audio_path) if audio_path else None,
                srt_path=safe_relative_path(srt_path) if srt_path.exists() else None,
                b_text_path=safe_relative_path(a_text_path) if a_text_path.exists() else None,
                audio_duration=audio_duration,
                srt_duration=srt_duration,
                duration_diff=duration_diff,
            )
        )
    return items


@router.get("/analysis/{channel}/{video}", response_model=AudioAnalysisResponse)
def audio_analysis(channel: str, video: str):
    return _load_audio_analysis(channel, video)

