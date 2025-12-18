import os
import glob
import re
import wave
import contextlib
import json
import sys
from pathlib import Path

from _bootstrap import bootstrap

REPO_ROOT = bootstrap()

from factory_common.paths import script_data_root

def get_wav_duration(wav_path):
    try:
        with contextlib.closing(wave.open(str(wav_path), 'r')) as f:
            frames = f.getnframes()
            rate = f.getframerate()
            return frames / float(rate)
    except Exception as e:
        print(f"[ERR] Failed to read WAV {wav_path}: {e}")
        return 0.0

def parse_srt_last_timestamp(srt_path):
    # Format: 00:00:05,123 --> 00:00:10,456
    last_end_sec = 0.0
    try:
        content = srt_path.read_text(encoding="utf-8")
        # Find all timestamps
        matches = re.findall(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", content)
        if not matches:
            return 0.0
        
        # Convert last match to seconds
        # Note: matches will return flattened tuples. We need to be careful.
        # Actually, simpler regex to just find "--> HH:MM:SS,mmm" pattern
        
        times = re.findall(r"--> (\d{2}):(\d{2}):(\d{2}),(\d{3})", content)
        if times:
            h, m, s, ms = times[-1]
            last_end_sec = int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000.0
            
    except Exception as e:
        print(f"[ERR] Failed to parse SRT {srt_path}: {e}")
    
    return last_end_sec

def verify_sync(channel=None):
    root = script_data_root()
    if channel:
        search_pattern = str(root / channel / "*" / "audio_prep")
    else:
        search_pattern = str(root / "CH*" / "*" / "audio_prep")
        
    prep_dirs = glob.glob(search_pattern)
    prep_dirs.sort()
    
    print(f"[VERIFY] Scanning {len(prep_dirs)} folders...")
    
    ok_count = 0
    fail_count = 0
    skip_count = 0
    
    for d in prep_dirs:
        dpath = Path(d)
        video_dir = dpath.parent
        channel_name = video_dir.parent.name
        video_name = video_dir.name
        
        wav_path = dpath / f"{channel_name}-{video_name}.wav"
        srt_path = dpath / f"{channel_name}-{video_name}.srt"
        
        if not wav_path.exists() or not srt_path.exists():
            print(f"[SKIP] {channel_name}-{video_name}: Missing WAV or SRT. Checked: {wav_path}")
            skip_count += 1
            continue
            
        wav_dur = get_wav_duration(wav_path)
        srt_dur = parse_srt_last_timestamp(srt_path)
        
        diff = abs(wav_dur - srt_dur)
        
        # Threshold: 1.0s (Voicevox sometimes adds silence at end, or SRT cuts off early?)
        # Usually SRT end should be very close to WAV end.
        threshold = 2.0 
        
        status = "OK"
        if diff > threshold:
            status = "FAIL"
            fail_count += 1
            print(f"[{status}] {channel_name}-{video_name}: WAV={wav_dur:.2f}s SRT={srt_dur:.2f}s Diff={diff:.2f}s")
        else:
            ok_count += 1
            # print(f"[{status}] {channel_name}-{video_name}: Diff={diff:.2f}s")

    print(f"\n[RESULT] Scanned {len(prep_dirs)} | OK: {ok_count} | FAIL: {fail_count} | SKIP: {skip_count}")

if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else None
    verify_sync(target)
