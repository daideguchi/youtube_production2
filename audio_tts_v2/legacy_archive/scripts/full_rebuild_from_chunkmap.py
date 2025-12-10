"""
chunk_map.json を唯一のソースとして、全行を Voicevox で再合成し、
各行をSRT時間に合わせてパディング/トリムした上で連結する。
総尺が期待値とズレた場合はFAILで終了し、既存WAVは上書きしない。

使い方例:
  python audio_tts_v2/scripts/full_rebuild_from_chunkmap.py \
    --channel CH02 --video 001 \
    --chunk-map audio_tts_v2/artifacts/final/CH02/001/chunk_map.json \
    --out-wav audio_tts_v2/artifacts/final/CH02/001/CH02-001.wav \
    --voicevox-speaker 13
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import List
import requests


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Full rebuild WAV from chunk_map (Voicevox). Fails if duration mismatch.")
    p.add_argument("--channel", required=True)
    p.add_argument("--video", required=True)
    p.add_argument("--chunk-map", required=True, type=Path)
    p.add_argument("--out-wav", required=True, type=Path)
    p.add_argument("--voicevox-speaker", required=False, default=None)
    p.add_argument("--voicevox-url", required=False, default="http://127.0.0.1:50021")
    p.add_argument("--allow-drift-ms", type=float, default=50.0, help="max total duration drift (ms)")
    return p.parse_args()


def ts_ms(ts: str) -> int:
    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(",")
    return (int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + int(ms)


def probe(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True,
        text=True,
    )
    return float(r.stdout.strip()) if r.stdout.strip() else 0.0


def synth(text: str, speaker: str, url: str) -> Path:
    q = requests.post(f"{url}/audio_query", params={"text": text, "speaker": speaker}).json()
    wav = requests.post(f"{url}/synthesis", params={"speaker": speaker}, data=json.dumps(q)).content
    tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
    tmp.write_bytes(wav)
    return tmp


def pad_or_trim(src: Path, target: float, compress_tol: float = 0.02) -> Path:
    """
    - If shorter: pad with silence at tail
    - If longer by more than compress_tol: time-compress with atempo to fit (no cut)
    - Otherwise: keep as-is
    """
    dur = probe(src)
    out = Path(tempfile.mkstemp(suffix=".wav")[1])
    if dur < target - 1e-3:
        pad = target - dur
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-f",
                "lavfi",
                "-t",
                f"{pad}",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-filter_complex",
                "[0:a][1:a]concat=n=2:v=0:a=1[a]",
                "-map",
                "[a]",
                str(out),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    elif dur > target + compress_tol:
        rate = target / dur
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-filter_complex",
                f"[0:a]atempo={rate}[a]",
                "-map",
                "[a]",
                str(out),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        out.write_bytes(src.read_bytes())
    # 仕上げチェック: まだ長い場合は末尾をトリム（音声を極力保持するため compress→trimの順）
    final_dur = probe(out)
    if final_dur > target + 0.05:
        trimmed = Path(tempfile.mkstemp(suffix=".wav")[1])
        subprocess.run(["ffmpeg", "-y", "-i", str(out), "-t", f"{target}", str(trimmed)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = trimmed
    return out


def concat_wavs(wavs: List[Path]) -> Path:
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        for w in wavs:
            f.write(f"file '{w}'\n")
        list_path = f.name
    out = Path(tempfile.mkstemp(suffix=".wav")[1])
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", str(out)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def main():
    args = parse_args()
    chunk_map = json.load(open(args.chunk_map, "r", encoding="utf-8"))
    speaker = args.voicevox_speaker or "13"

    wavs = []
    total_ms = 0
    for e in chunk_map:
        dur_ms = ts_ms(e["end"]) - ts_ms(e["start"])
        total_ms += dur_ms
        raw = synth(e["text"], speaker, args.voicevox_url)
        adj = pad_or_trim(raw, dur_ms / 1000.0)
        wavs.append(adj)

    concat = concat_wavs(wavs)

    # 全体もターゲット長にフィットさせる（わずかなズレを atempo で吸収、短ければサイレンス）
    target = total_ms / 1000.0
    concat = pad_or_trim(concat, target, compress_tol=1.0)

    final_dur = probe(concat)
    drift_ms = abs(final_dur - target) * 1000
    if drift_ms > args.allow_drift_ms:
        raise SystemExit(f"[FAIL] duration mismatch final={final_dur:.3f}s target={target:.3f}s drift_ms={drift_ms:.1f}")

    # replace
    args.out_wav.parent.mkdir(parents=True, exist_ok=True)
    concat.replace(args.out_wav)
    print(f"[OK] rebuilt {args.out_wav} duration={final_dur:.3f}s drift_ms={drift_ms:.1f}")


if __name__ == "__main__":
    main()
