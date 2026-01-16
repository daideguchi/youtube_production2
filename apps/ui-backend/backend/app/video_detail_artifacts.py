from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from backend.app.path_utils import safe_relative_path
from factory_common.paths import audio_final_dir


def _summarize_video_detail_artifacts(
    channel_code: str,
    video_number: str,
    *,
    base_dir: Path,
    content_dir: Path,
    audio_prep_dir: Path,
    assembled_path: Path,
    assembled_human_path: Path,
    b_text_with_pauses_path: Path,
    audio_path: Optional[Path],
    srt_path: Optional[Path],
) -> Dict[str, Any]:
    def _iso_mtime(mtime: float) -> str:
        return datetime.fromtimestamp(mtime, timezone.utc).isoformat().replace("+00:00", "Z")

    def _count_dir_children(path: Path, *, max_items: int = 10_000) -> Optional[int]:
        if not path.exists() or not path.is_dir():
            return None
        try:
            count = 0
            for _ in path.iterdir():
                count += 1
                if count >= max_items:
                    break
            return count
        except OSError:
            return None

    def _entry(
        *,
        key: str,
        label: str,
        path: Path,
        kind: Literal["file", "dir"] = "file",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        exists = False
        size_bytes = None
        modified_time = None
        try:
            exists = path.exists()
        except OSError:
            exists = False
        if exists:
            try:
                stat = path.stat()
                size_bytes = stat.st_size
                modified_time = _iso_mtime(stat.st_mtime)
            except OSError:
                pass
        return {
            "key": key,
            "label": label,
            "path": safe_relative_path(path) or str(path),
            "kind": kind,
            "exists": exists,
            "size_bytes": size_bytes,
            "modified_time": modified_time,
            "meta": meta,
        }

    project_dir_label = safe_relative_path(base_dir) or str(base_dir)

    items: List[Dict[str, Any]] = []
    items.append(_entry(key="status", label="status.json", path=base_dir / "status.json"))

    items.append(
        _entry(
            key="content_dir",
            label="content/",
            path=content_dir,
            kind="dir",
            meta={"count": _count_dir_children(content_dir)},
        )
    )
    items.append(_entry(key="assembled_human", label="assembled_human.md", path=assembled_human_path))
    items.append(_entry(key="assembled", label="assembled.md", path=assembled_path))

    items.append(
        _entry(
            key="audio_prep_dir",
            label="audio_prep/",
            path=audio_prep_dir,
            kind="dir",
            meta={"count": _count_dir_children(audio_prep_dir)},
        )
    )
    b_label = (
        "TTS入力スナップショット (a_text.txt)"
        if b_text_with_pauses_path.name == "a_text.txt"
        else "b_text_with_pauses.txt"
    )
    items.append(_entry(key="b_text_with_pauses", label=b_label, path=b_text_with_pauses_path))
    items.append(_entry(key="audio_prep_log", label="audio_prep/log.json", path=audio_prep_dir / "log.json"))

    final_dir = audio_final_dir(channel_code, video_number)
    items.append(
        _entry(
            key="audio_final_dir",
            label="audio_tts final/",
            path=final_dir,
            kind="dir",
            meta={"count": _count_dir_children(final_dir)},
        )
    )
    expected_wav = final_dir / f"{channel_code}-{video_number}.wav"
    expected_srt = final_dir / f"{channel_code}-{video_number}.srt"
    items.append(_entry(key="final_wav", label="final wav", path=audio_path or expected_wav))
    items.append(_entry(key="final_srt", label="final srt", path=srt_path or expected_srt))
    items.append(_entry(key="final_log", label="final log.json", path=final_dir / "log.json"))
    items.append(_entry(key="final_a_text", label="final a_text.txt", path=final_dir / "a_text.txt"))

    return {"project_dir": project_dir_label, "items": items}

