"""
部分差し替え専用スクリプト（LLMなし）。

入力SRTの指定範囲だけVoicevoxで合成し、既存WAVの同時間帯を置換する。
SRTは時刻を変えずそのまま。run_ttsを通さないので全体再生成リスクを排除。
"""

from __future__ import annotations

import argparse
import subprocess
import json
import tempfile
from pathlib import Path
from typing import List, Dict, Tuple

VOICEVOX_URL = "http://127.0.0.1:50021"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Patch specific SRT indices with new TTS and splice into WAV (Voicevox, no LLM)")
    p.add_argument("--channel", required=True)
    p.add_argument("--video", required=True)
    p.add_argument("--srt", required=True, type=Path, help="Path to existing SRT")
    p.add_argument("--wav", required=True, type=Path, help="Path to existing WAV")
    p.add_argument("--start-index", required=True, type=int, help="Start SRT index (inclusive)")
    p.add_argument("--end-index", required=True, type=int, help="End SRT index (inclusive)")
    p.add_argument("--chunk-map", required=False, type=Path, help="Optional chunk map (SRT index/ctime mapping)")
    p.add_argument("--voicevox-speaker", required=False, default=None, help="Voicevox speaker id (uses AOYAMA_SPEAKER_ID env if missing)")
    return p.parse_args()


def load_srt(path: Path) -> list[dict]:
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        blocks = f.read().split("\n\n")
    for blk in blocks:
        lines = [l for l in blk.splitlines() if l.strip()]
        if len(lines) >= 3:
            idx = int(lines[0])
            start, end = lines[1].split(" --> ")
            text = "\n".join(lines[2:])
            entries.append({"index": idx, "start": start, "end": end, "text": text})
    return entries


def srt_ts_to_ms(ts: str) -> int:
    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(",")
    return (int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + int(ms)


def synth_voicevox(text: str, speaker: str | None) -> Path:
    import requests
    # 音声合成: audio_query -> synthesis
    q = requests.post(f"{VOICEVOX_URL}/audio_query", params={"text": text, "speaker": speaker}).json()
    wav = requests.post(f"{VOICEVOX_URL}/synthesis", params={"speaker": speaker}, data=json.dumps(q)).content
    out = Path("/tmp/patch_segment.wav")
    out.write_bytes(wav)
    return out


def _probe_duration_sec(path: Path) -> float:
    import subprocess
    cmd=["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)]
    res=subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def _pad_or_trim(src: Path, target_sec: float) -> Path:
    """
    Ensure src matches target_sec by trimming or padding with silence (src first, then silence).
    """
    import subprocess
    dur = _probe_duration_sec(src)
    out = Path(f"/tmp/patched_{src.stem}_{target_sec:.3f}.wav")
    if dur > target_sec + 1e-3:
        subprocess.run(["ffmpeg", "-y", "-i", str(src), "-t", f"{target_sec}", str(out)], check=True)
    elif dur < target_sec - 1e-3:
        pad = target_sec - dur
        subprocess.run([
            "ffmpeg","-y",
            "-i", str(src),
            "-f","lavfi","-t", f"{pad}","-i","anullsrc=r=24000:cl=mono",
            "-filter_complex","[0:a][1:a]concat=n=2:v=0:a=1[a]",
            "-map","[a]", str(out)
        ], check=True)
    else:
        out.write_bytes(src.read_bytes())
    return out


def _concat_wavs(wavs: List[Path]) -> Path:
    """
    ffmpeg concat for multiple mono wavs (already padded/trimmed).
    """
    if len(wavs) == 1:
        out = Path("/tmp/concat_single.wav")
        out.write_bytes(wavs[0].read_bytes())
        return out
    # use concat demuxer
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        for w in wavs:
            f.write(f"file '{w}'\n")
        list_path = f.name
    out = Path("/tmp/concat_block.wav")
    subprocess.run([
        "ffmpeg","-y","-f","concat","-safe","0","-i",list_path,"-c","copy",str(out)
    ], check=True)
    return out


def main():
    args = parse_args()
    speaker = args.voicevox_speaker or (Path(".env").read_text().split("AOYAMA_SPEAKER_ID=")[-1].splitlines()[0])
    entries = load_srt(args.srt)
    chunk_times: Dict[int, Tuple[int, int]] = {}
    if args.chunk_map and args.chunk_map.exists():
        try:
            cm = json.load(open(args.chunk_map, 'r', encoding='utf-8'))
            for e in cm:
                try:
                    idx = int(e["index"])
                    chunk_times[idx] = (srt_ts_to_ms(e["start"]), srt_ts_to_ms(e["end"]))
                except Exception:
                    continue
        except Exception:
            print("[WARN] chunk_map is invalid; continuing without it")

    # 対象区間の開始/終了時刻（ms）: chunk_map優先
    target = [e for e in entries if args.start_index <= e["index"] <= args.end_index]
    if not target:
        raise SystemExit("no target srt entries")
    # 使用する時間は chunk_map があればそれ、なければ SRT
    def idx_start_end(idx: int, default_start: str, default_end: str) -> Tuple[int, int]:
        if idx in chunk_times:
            return chunk_times[idx]
        return srt_ts_to_ms(default_start), srt_ts_to_ms(default_end)

    start_ms, _ = idx_start_end(target[0]["index"], target[0]["start"], target[0]["end"])
    _, end_ms = idx_start_end(target[-1]["index"], target[-1]["start"], target[-1]["end"])

    # 合成（対象行ごとに作り、SRT区間の長さに合わせてパディング/トリム）
    block_wavs: list[Path] = []
    for e in target:
        s_ms, e_ms = idx_start_end(e["index"], e["start"], e["end"])
        dur_sec = (e_ms - s_ms) / 1000.0
        raw = synth_voicevox(e["text"], speaker)
        adj = _pad_or_trim(raw, dur_sec)
        block_wavs.append(adj)
    seg_wav = _concat_wavs(block_wavs)
    # 全体長をターゲット区間と一致させる
    target_total = (end_ms - start_ms) / 1000.0
    seg_wav = _pad_or_trim(seg_wav, target_total)
    final_dur = _probe_duration_sec(seg_wav)
    drift = abs(final_dur - target_total) * 1000
    if drift > 50:
        raise SystemExit(f"[FAIL] rebuilt segment drift {drift:.1f}ms (target {target_total:.3f}s, got {final_dur:.3f}s)")

    patched = args.wav.with_suffix(".patched.wav")
    # ffmpeg で時間帯差し替え: 前半|新セグメント|後半 を concat
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(args.wav),
        "-i", str(seg_wav),
        "-filter_complex",
        f"[0:a]atrim=0:{start_ms/1000},asetpts=PTS-STARTPTS[a0];"
        f"[1:a]asetpts=PTS-STARTPTS[a1];"
        f"[0:a]atrim={end_ms/1000},asetpts=PTS-STARTPTS[a2];"
        f"[a0][a1][a2]concat=n=3:v=0:a=1[a]",
        "-map", "[a]",
        str(patched)
    ], check=True)

    patched.rename(args.wav)
    print(f"patched: {args.wav} (kept srt as-is)")


if __name__ == "__main__":
    main()
