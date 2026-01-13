from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from audio_tts.tts.routing import load_routing_config, resolve_eleven_model, resolve_eleven_voice
from backend.app.normalize import normalize_channel_code, normalize_video_number
from factory_common.paths import (
    audio_final_dir,
    audio_pkg_root,
    repo_root as ssot_repo_root,
    script_data_root as ssot_script_data_root,
)

router = APIRouter(prefix="/api/audio-tts", tags=["audio-tts"])

REPO_ROOT = ssot_repo_root()
DATA_ROOT = ssot_script_data_root()


@router.get("/health")
def audio_tts_health():
    try:
        cfg = load_routing_config()
    except Exception as exc:
        return {"status": "error", "detail": f"routing_config_load_failed: {exc}"}

    result = {
        "status": "ok",
        "engine_default": getattr(cfg, "engine_default", None),
        "engine_override_env": os.getenv("ENGINE_DEFAULT_OVERRIDE"),
        "voicevox": {
            "url": getattr(cfg, "voicevox_url", None),
            "speaker_env": getattr(cfg, "voicevox_speaker_env", None),
            "ok": False,
            "detail": None,
        },
        "azure_openai": {
            "api_key_present": bool(os.getenv("AZURE_OPENAI_API_KEY")),
            "endpoint": os.getenv("AZURE_OPENAI_ENDPOINT"),
            "deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        },
        "elevenlabs": {
            "api_key_present": bool(os.getenv(getattr(cfg, "eleven_api_key_env", ""))),
            "voice_id": resolve_eleven_voice("", cfg=cfg) if getattr(cfg, "eleven_voice_id", None) else None,
            "model_id": resolve_eleven_model(cfg) if cfg else None,
        },
    }
    # Voicevox ping (best effort)
    try:
        if getattr(cfg, "voicevox_url", None):
            resp = requests.get(f"{cfg.voicevox_url}/speakers", timeout=2)
            resp.raise_for_status()
            result["voicevox"]["ok"] = True
    except Exception as exc:  # pragma: no cover - best effort check
        result["voicevox"]["detail"] = str(exc)
    return result


def _clear_redo_flags(
    channel: str,
    video: str,
    *,
    redo_script: Optional[bool] = None,
    redo_audio: Optional[bool] = None,
) -> None:
    """
    ベストエフォートでリテイクフラグを更新する（API内部利用）。
    音声成功時は redo_audio=False、台本保存時は redo_script=False などに利用。

    NOTE:
    - status.json の読み書きは backend.main の共通実装に寄せる。
    - 循環importを避けるため、runtime import を使用する。
    """
    try:
        channel_code = normalize_channel_code(channel)
        video_number = normalize_video_number(video)
        from backend import main as backend_main  # runtime import

        status = backend_main.load_status(channel_code, video_number)
        meta = status.setdefault("metadata", {})
        if redo_script is not None:
            meta["redo_script"] = bool(redo_script)
        if redo_audio is not None:
            meta["redo_audio"] = bool(redo_audio)
        status["metadata"] = meta
        status["updated_at"] = backend_main.current_timestamp()
        backend_main.save_status(channel_code, video_number, status)
    except Exception:
        # ベストエフォートなので握りつぶす
        pass


def _resolve_script_pipeline_input_path(channel: str, video: str) -> Path:
    """
    旧式の解決（後方互換）。呼び出し元は _resolve_final_tts_input_path を優先すること。
    """
    base = DATA_ROOT / channel / video
    candidates = [
        base / "audio_prep" / "script_sanitized.txt",
        base / "content" / "assembled.md",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise HTTPException(status_code=404, detail=f"script_pipeline input not found: {channel}-{video}")


def _resolve_final_tts_input_path(channel: str, video: str) -> Path:
    """
    標準の音声生成で必ず参照する「AテキストSoT」を解決する。
    優先度:
    1) content/assembled_human.md
    2) content/assembled.md

    重要: 旧運用の `script_sanitized.txt` / `script_audio_human.txt` 等へ暗黙フォールバックしない。
    （入力不足は 404 で止め、事故を防ぐ）
    見つからない場合は 404 を返す。
    """
    base = DATA_ROOT / channel / video
    candidates = [
        base / "content" / "assembled_human.md",
        base / "content" / "assembled.md",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise HTTPException(status_code=404, detail=f"final tts input not found: {channel}-{video}")


@router.post("/run-from-script")
def api_audio_tts_run_from_script(
    channel: str = Body(..., embed=True),
    video: str = Body(..., embed=True),
    engine_override: Optional[str] = Body(None),
    reading_source: Optional[str] = Body(None),
):
    channel_code = normalize_channel_code(channel)
    video_no = normalize_video_number(video)
    input_path = _resolve_final_tts_input_path(channel_code, video_no)
    payload = TtsRequest(
        channel=channel_code,
        video=video_no,
        input_path=str(input_path),
        engine_override=engine_override,
        reading_source=reading_source,
    )
    return _run_audio_tts(payload)


# === audio_tts integration (simple CLI bridge) ===
class TtsRequest(BaseModel):
    channel: str
    video: str
    input_path: str
    engine_override: Optional[str] = Field(None, description="voicevox|voicepeak|elevenlabs を強制する場合")
    reading_source: Optional[str] = Field(None, description="voicepeak用読み取得ソース")
    voicepeak_narrator: Optional[str] = None
    voicepeak_speed: Optional[int] = None
    voicepeak_pitch: Optional[int] = None
    voicepeak_emotion: Optional[str] = None


def _run_audio_tts(req: TtsRequest) -> Dict[str, Any]:
    repo_root = REPO_ROOT  # Use constant defined at top
    pkg_root = audio_pkg_root()
    script = pkg_root / "scripts" / "run_tts.py"
    if not script.exists():
        raise HTTPException(status_code=500, detail="run_tts.py not found")
    input_path = Path(req.input_path)
    if not input_path.is_absolute():
        input_path = (repo_root / input_path).resolve()
    if not input_path.exists():
        raise HTTPException(status_code=400, detail=f"input_path not found: {input_path}")
    env = os.environ.copy()
    # Ensure imports resolve in subprocess even when started outside repo root.
    pythonpath_prefix = f"{repo_root}{os.pathsep}{repo_root / 'packages'}"
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{pythonpath_prefix}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else pythonpath_prefix
    )
    cmd = [
        sys.executable,
        str(script),
        "--channel",
        req.channel,
        "--video",
        req.video,
        "--input",
        str(input_path),
    ]

    # NOTE: Do NOT force --out-wav/--log here.
    # run_tts must write intermediates under workspaces/scripts/**/audio_prep/
    # (including audio_prep/script_sanitized.txt) and then sync to workspaces/audio/final/.
    final_dir = audio_final_dir(req.channel, req.video)
    final_wav_path = final_dir / f"{req.channel}-{req.video}.wav"
    final_srt_path = final_dir / f"{req.channel}-{req.video}.srt"
    final_log_path = final_dir / "log.json"
    if req.engine_override:
        cmd.extend(["--engine-override", req.engine_override])
    if req.reading_source:
        cmd.extend(["--reading-source", req.reading_source])
    if req.voicepeak_narrator:
        cmd.extend(["--voicepeak-narrator", req.voicepeak_narrator])
    if req.voicepeak_speed is not None:
        cmd.extend(["--voicepeak-speed", str(req.voicepeak_speed)])
    if req.voicepeak_pitch is not None:
        cmd.extend(["--voicepeak-pitch", str(req.voicepeak_pitch)])
    if req.voicepeak_emotion:
        cmd.extend(["--voicepeak-emotion", req.voicepeak_emotion])
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, env=env, check=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"audio_tts failed: {e.stderr or e.stdout or e}")
    stdout = completed.stdout.strip()
    if not final_wav_path.exists():
        raise HTTPException(status_code=500, detail=f"audio_tts did not create wav: {stdout}")
    wav_file_path = str(final_wav_path.resolve())
    srt_file_path = str(final_srt_path.resolve()) if final_srt_path.exists() else None
    log_file_path = str(final_log_path.resolve()) if final_log_path.exists() else None

    audio_url = f"/api/channels/{req.channel}/videos/{req.video}/audio"
    srt_url = f"/api/channels/{req.channel}/videos/{req.video}/srt" if final_srt_path.exists() else None
    log_url = f"/api/channels/{req.channel}/videos/{req.video}/log" if final_log_path.exists() else None
    engine = req.engine_override
    if not engine:
        m = re.search(r"Engine=([a-zA-Z0-9_]+)", stdout)
        if m:
            engine = m.group(1).lower()
    llm_meta = None

    # リテイク(音声)は成功時に自動で解除（ベストエフォート）
    _clear_redo_flags(req.channel, req.video, redo_audio=False)
    # 音声が成功しても台本リテイクが残っている場合は明示的に残す（redo_scriptは触らない）

    # 生成後の残骸（巨大chunk等）は削除して散らかりを抑える（最終成果物は削除しない）
    cleanup: Dict[str, Any] = {}
    try:
        prep_dir = DATA_ROOT / req.channel / req.video / "audio_prep"
        chunks_dir = prep_dir / "chunks"
        if chunks_dir.is_dir():
            shutil.rmtree(chunks_dir)
            cleanup["audio_prep_chunks_removed"] = str(chunks_dir)
    except Exception as exc:  # pragma: no cover - best effort
        cleanup["audio_prep_chunks_error"] = str(exc)

    try:
        prep_dir = DATA_ROOT / req.channel / req.video / "audio_prep"
        prep_wav = prep_dir / f"{req.channel}-{req.video}.wav"
        prep_srt = prep_dir / f"{req.channel}-{req.video}.srt"
        if prep_wav.exists() and final_wav_path.exists():
            prep_wav.unlink()
            cleanup["audio_prep_wav_removed"] = str(prep_wav)
        if prep_srt.exists() and final_srt_path.exists():
            prep_srt.unlink()
            cleanup["audio_prep_srt_removed"] = str(prep_srt)
    except Exception as exc:  # pragma: no cover - best effort
        cleanup["audio_prep_binaries_error"] = str(exc)

    keep_chunks_env = (os.getenv("YTM_TTS_KEEP_CHUNKS") or "").strip().lower()
    keep_chunks = keep_chunks_env in {"1", "true", "yes", "y", "on"}
    if not keep_chunks:
        try:
            final_chunks_dir = final_dir / "chunks"
            if final_chunks_dir.is_dir():
                shutil.rmtree(final_chunks_dir)
                cleanup["final_chunks_removed"] = str(final_chunks_dir)
        except Exception as exc:  # pragma: no cover - best effort
            cleanup["final_chunks_error"] = str(exc)

    return {
        "engine": engine,
        # Backward-compatible keys (front-end expects URL-ish strings, not absolute file paths)
        "wav_path": audio_url,
        "srt_path": srt_url,
        "log": log_url,
        "stdout": stdout,
        "final_wav": audio_url,
        "final_srt": srt_url,
        "llm_meta": llm_meta,
        # Debug-only extras (not part of response_model)
        "wav_file_path": wav_file_path,
        "srt_file_path": srt_file_path,
        "log_file_path": log_file_path,
        "cleanup": cleanup or None,
    }


@router.post("/run")
def api_audio_tts_run(payload: TtsRequest):
    channel_code = normalize_channel_code(payload.channel)
    video_no = normalize_video_number(payload.video)
    resolved = _resolve_final_tts_input_path(channel_code, video_no)

    provided = Path(payload.input_path)
    repo_root = REPO_ROOT  # Use constant defined at top
    if not provided.is_absolute():
        provided = (repo_root / provided).resolve()

    if provided.resolve() != resolved.resolve():
        raise HTTPException(
            status_code=400,
            detail=f"input_path must be final script: {resolved} (provided: {provided})",
        )

    fixed = payload.copy()
    fixed.channel = channel_code
    fixed.video = video_no
    fixed.input_path = str(resolved)
    return _run_audio_tts(fixed)


class TtsBatchItem(BaseModel):
    channel: str
    video: str
    input_path: str
    engine_override: Optional[str] = None
    reading_source: Optional[str] = None
    voicepeak_narrator: Optional[str] = None
    voicepeak_speed: Optional[int] = None
    voicepeak_pitch: Optional[int] = None
    voicepeak_emotion: Optional[str] = None


class TtsBatchResponse(BaseModel):
    results: List[Dict[str, Any]]
    success_count: int
    failure_count: int


@router.post("/run-batch", response_model=TtsBatchResponse)
def api_audio_tts_run_batch(payload: List[TtsBatchItem]):
    results: List[Dict[str, Any]] = []
    success = 0
    failure = 0
    for item in payload:
        try:
            channel_code = normalize_channel_code(item.channel)
            video_no = normalize_video_number(item.video)
            resolved = _resolve_final_tts_input_path(channel_code, video_no)
            provided = Path(item.input_path)
            repo_root = REPO_ROOT  # Use constant defined at top
            if not provided.is_absolute():
                provided = (repo_root / provided).resolve()
            if provided.resolve() != resolved.resolve():
                raise HTTPException(
                    status_code=400,
                    detail=f"input_path must be final script: {resolved} (provided: {provided})",
                )
            res = _run_audio_tts(
                TtsRequest(
                    channel=channel_code,
                    video=video_no,
                    input_path=str(resolved),
                    engine_override=item.engine_override,
                    reading_source=item.reading_source,
                    voicepeak_narrator=item.voicepeak_narrator,
                    voicepeak_speed=item.voicepeak_speed,
                    voicepeak_pitch=item.voicepeak_pitch,
                    voicepeak_emotion=item.voicepeak_emotion,
                )
            )
            results.append({"channel": item.channel, "video": item.video, "ok": True, **res})
            success += 1
        except HTTPException as exc:
            results.append(
                {
                    "channel": item.channel,
                    "video": item.video,
                    "ok": False,
                    "error": exc.detail,
                    "status_code": exc.status_code,
                }
            )
            failure += 1
        except Exception as exc:  # pragma: no cover - best effort
            results.append(
                {
                    "channel": item.channel,
                    "video": item.video,
                    "ok": False,
                    "error": str(exc),
                }
            )
            failure += 1
    return TtsBatchResponse(results=results, success_count=success, failure_count=failure)
