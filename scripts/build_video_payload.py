#!/usr/bin/env python3
"""Generate unified video payload JSON for CapCut/Remotion engines."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factory_common.paths import audio_final_dir, video_runs_root


def _load_json(path: Path, required: bool = True) -> Dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_audio_path(channel: str, video: str) -> Dict[str, str]:
    ch = str(channel).upper()
    no = str(video).zfill(3)
    base = audio_final_dir(ch, no)
    wav = base / f"{ch}-{no}.wav"
    srt = base / f"{ch}-{no}.srt"
    return {"audio": str(wav.resolve()), "srt": str(srt.resolve())}


def build_payload(project_id: str) -> Dict[str, Any]:
    project_dir = video_runs_root() / project_id
    if not project_dir.exists():
        raise FileNotFoundError(project_dir)

    episode_info = _load_json(project_dir / "episode_info.json")
    belt_config = _load_json(project_dir / "belt_config.json")
    chapters = _load_json(project_dir / "chapters.json")
    image_cues_path = project_dir / "image_cues.json"
    image_cues = _load_json(image_cues_path, required=False)

    channel, video = episode_info.get("video_id", "").split("-", 1)
    media_paths = _resolve_audio_path(channel, video)

    images: List[Dict[str, Any]] = []
    images_dir = project_dir / "images"
    if images_dir.exists():
        for image_path in sorted(images_dir.iterdir()):
            if image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            images.append({"path": str(image_path.resolve())})

    payload = {
        "project": {
            "id": project_id,
            "channel": channel,
            "video_number": video,
            "title": episode_info.get("title"),
            "duration_sec": belt_config.get("total_duration") or chapters.get("total_duration"),
        },
        "sources": {
            "episode_info": str((project_dir / "episode_info.json").resolve()),
            "belt_config": str((project_dir / "belt_config.json").resolve()),
            "chapters": str((project_dir / "chapters.json").resolve()),
            "image_cues": str(image_cues_path.resolve()) if image_cues else None,
        },
        "media": {
            "audio": media_paths["audio"],
            "srt": media_paths["srt"],
            "images": images,
        },
        "belts": belt_config,
        "chapters": chapters.get("chapters", []),
        "image_cues": image_cues if image_cues else [],
        "generated_at": episode_info.get("generated_at"),
    }
    return payload


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Build unified video payload JSON.")
    parser.add_argument("--project-id", required=True, help="e.g. CH06-015")
    parser.add_argument("--output", help="Save payload to file")
    args = parser.parse_args(argv)

    payload = build_payload(args.project_id)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Payload written to {out_path}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
