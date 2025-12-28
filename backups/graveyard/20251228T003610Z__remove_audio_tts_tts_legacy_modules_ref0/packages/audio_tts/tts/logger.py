from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def save_tts_log(
    *,
    out_path: Path,
    channel: str,
    video_no: str,
    script_id: str,
    engine: str,
    a_text: str,
    b_text: str,
    tokens: List[Dict[str, Any]],
    kana_engine: Dict[str, Any],
    annotations: Dict[str, Any],
    b_text_build_log: List[Dict[str, Any]],
    audio_meta: Dict[str, Any],
    engine_metadata: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
    qa_issues: Optional[List[Dict[str, Any]]] = None,
    srt_entries: Optional[List[Dict[str, Any]]] = None,
) -> None:
    payload = {
        "channel": channel,
        "video_no": video_no,
        "script_id": script_id,
        "engine": engine,
        "a_text": a_text,
        "b_text": b_text,
        "tokens": tokens,
        "kana_engine": kana_engine,
        "annotations": annotations,
        "b_text_build_log": b_text_build_log,
        "audio": audio_meta,
        "engine_metadata": engine_metadata or {},
        "meta": meta or {},
    }
    if qa_issues is not None:
        payload["qa_issues"] = qa_issues
    if srt_entries is not None:
        payload["srt_entries"] = srt_entries
    out_path = Path(out_path)
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 付随ファイルも同ディレクトリに出力して閲覧しやすくする
    extras = {
        "a_text.txt": a_text,
        "b_text.txt": b_text,
        "b_text_build_log.json": b_text_build_log,
        "tokens.json": tokens,
        "kana_engine.json": kana_engine,
        "annotations.json": annotations,
        "audio_meta.json": audio_meta,
        "engine_metadata.json": engine_metadata or {},
    }
    if qa_issues is not None:
        extras["qa_issues.json"] = qa_issues
    if srt_entries is not None:
        extras["srt_entries.json"] = srt_entries

    for name, content in extras.items():
        path = out_dir / name
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
