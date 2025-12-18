#!/usr/bin/env python3
"""
音声アセット整合性のCLIレポート（final SoT 基準）。

必須ファイル:
- a_text.txt
- audio_manifest.json
- CHxx-xxx.wav
- CHxx-xxx.srt
- log.json

おまけ:
- WAV と SRT の終端時刻差分（大きい場合は desync 疑い）
"""
from __future__ import annotations

import argparse
import json
import re
import wave
from pathlib import Path

from typing import Iterable, List, Dict, Any

from _bootstrap import bootstrap

bootstrap()

from factory_common.paths import audio_artifacts_root

FINAL_ROOT = audio_artifacts_root() / "final"


def iter_video_dirs() -> Iterable[tuple[str, str, Path]]:
    if not FINAL_ROOT.exists():
        return []
    for ch_dir in sorted(FINAL_ROOT.iterdir()):
        if not ch_dir.is_dir():
            continue
        ch = ch_dir.name.upper()
        if not ch.startswith("CH"):
            continue
        for vid_dir in sorted(ch_dir.iterdir()):
            if not vid_dir.is_dir():
                continue
            vid = vid_dir.name
            if not vid.isdigit():
                continue
            yield ch, vid, vid_dir


def srt_duration_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    matches = list(re.finditer(r"(\\d{2}):(\\d{2}):(\\d{2}),(\\d{3})", text))
    if not matches:
        return None
    hh, mm, ss, ms = matches[-1].groups()
    try:
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
    except ValueError:
        return None


def wav_duration_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate)
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="JSONで結果を出力する（標準出力）")
    ap.add_argument("--max", type=int, default=50, help="標準出力に出す件数（JSON時は無視）")
    args = ap.parse_args()

    rows: List[Dict[str, Any]] = []
    for ch, vid, vid_dir in iter_video_dirs():
        a_text = vid_dir / "a_text.txt"
        manifest = vid_dir / "audio_manifest.json"
        wav = vid_dir / f"{ch}-{vid}.wav"
        srt = vid_dir / f"{ch}-{vid}.srt"
        log = vid_dir / "log.json"

        missing: List[str] = []
        for p, name in [
            (a_text, "a_text.txt"),
            (manifest, "audio_manifest.json"),
            (wav, wav.name),
            (srt, srt.name),
            (log, "log.json"),
        ]:
            if not p.exists():
                missing.append(name)

        wav_dur = wav_duration_seconds(wav)
        srt_dur = srt_duration_seconds(srt)
        diff = None
        if wav_dur is not None and srt_dur is not None:
            diff = abs(wav_dur - srt_dur)

        rows.append(
            {
                "channel": ch,
                "video": vid,
                "missing": missing,
                "audio_duration": wav_dur,
                "srt_duration": srt_dur,
                "duration_diff": diff,
                "paths": {
                    "a_text": str(a_text) if a_text.exists() else None,
                    "audio_manifest": str(manifest) if manifest.exists() else None,
                    "wav": str(wav) if wav.exists() else None,
                    "srt": str(srt) if srt.exists() else None,
                    "log": str(log) if log.exists() else None,
                },
            }
        )

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    missing_rows = [r for r in rows if r["missing"]]
    mismatch_rows = [r for r in rows if r["duration_diff"] is not None and r["duration_diff"] > 1]
    print(f"total={len(rows)} missing={len(missing_rows)} diff>1s={len(mismatch_rows)}")
    print("---- missing (up to --max) ----")
    for r in missing_rows[: args.max]:
        print(r["channel"], r["video"], r["missing"])
    print("---- duration diff>1s (up to --max) ----")
    for r in mismatch_rows[: args.max]:
        print(r["channel"], r["video"], f"diff={r['duration_diff']:.2f}s")


if __name__ == "__main__":
    main()
