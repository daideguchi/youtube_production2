#!/usr/bin/env python3
"""
音声アセット整合性のCLIレポート。
必須ファイル (b_text_with_pauses.txt / CHxx-xxx.wav / CHxx-xxx.srt / log.json / pause_map.json / engine_metadata.json)
の有無と音声・SRTの長さ差分をチェックします。
"""
from __future__ import annotations

import argparse
import json
import re
import wave
from pathlib import Path

from factory_common.paths import script_data_root
from typing import Iterable, List, Dict, Any

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = script_data_root()


def iter_video_dirs() -> Iterable[tuple[str, str, Path]]:
    if not DATA_ROOT.exists():
        return []
    for ch_dir in sorted(DATA_ROOT.iterdir()):
        if not ch_dir.is_dir():
            continue
        ch = ch_dir.name.upper()
        if not ch.startswith("CH"):
            continue
        for vid_dir in sorted(ch_dir.iterdir()):
            if not vid_dir.is_dir():
                continue
            vid = vid_dir.name
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
        ap_dir = vid_dir / "audio_prep"
        b = ap_dir / "b_text_with_pauses.txt"
        wav = ap_dir / f"{ch}-{vid}.wav"
        srt = ap_dir / f"{ch}-{vid}.srt"
        log = ap_dir / "log.json"
        pm = ap_dir / "pause_map.json"
        em = ap_dir / "engine_metadata.json"

        missing: List[str] = []
        for p, name in [(b, "b_text_with_pauses.txt"), (wav, wav.name), (srt, srt.name), (log, log.name), (pm, pm.name), (em, em.name)]:
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
                    "b_text": str(b) if b.exists() else None,
                    "wav": str(wav) if wav.exists() else None,
                    "srt": str(srt) if srt.exists() else None,
                    "log": str(log) if log.exists() else None,
                    "pause_map": str(pm) if pm.exists() else None,
                    "engine_metadata": str(em) if em.exists() else None,
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
