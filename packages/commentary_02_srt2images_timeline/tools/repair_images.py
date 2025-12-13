#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import os
from typing import List, Tuple

# Use project src
ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / 'src'))

from srt2images.nanobanana_client import _run_direct, _convert_to_16_9  # type: ignore
import subprocess
import shutil


def load_cues(run_dir: Path):
    data = json.loads((run_dir / 'image_cues.json').read_text(encoding='utf-8'))
    return data['cues']


def find_bad_images(images_dir: Path, total: int, size_threshold: int = 50_000) -> Tuple[List[int], List[int]]:
    """Return (small_or_missing, invalid_png) index lists (1-based)."""
    from PIL import Image
    small_or_missing = []
    invalid = []
    for i in range(1, total + 1):
        p = images_dir / f"{i:04d}.png"
        if not p.exists():
            small_or_missing.append(i)
            continue
        try:
            sz = p.stat().st_size
        except Exception:
            small_or_missing.append(i)
            continue
        if sz < size_threshold:
            small_or_missing.append(i)
            continue
        try:
            with Image.open(p) as im:
                im.verify()
        except Exception:
            invalid.append(i)
    return small_or_missing, invalid


def build_prompt(cue: dict) -> str:
    # Use the pipeline-generated prompt if available (preserves style/template)
    if cue.get('prompt'):
        return cue['prompt']

    # Fallback logic if 'prompt' key is missing
    summary = cue.get('summary', cue.get('text', ''))
    return f"""Create a 16:9 cinematic visual image for: {summary}

Setting: Japan. Gentle, heartwarming senior romance; soft pastel tones; natural light; calm and kind atmosphere.
Characters: The same elderly couple in every scene — Satoko (woman in her 70s, short gray hair, light beige cardigan, gentle smile) and Takahashi (man in his 70s, short hair, glasses, calm). Keep faces, hairstyle, clothing, and mood consistent across all images.
Style: Japanese aesthetic, cinematic lighting, professional composition
Resolution: 1920x1080
Requirements: NO TEXT OR WORDS anywhere in the image; pure visual storytelling; clear central subject; clean design; avoid busy backgrounds.
"""


def _run_cli(prompt: str, output_path: str, timeout_sec: int = 300) -> bool:
    exe = shutil.which('ddnanobanana') or shutil.which('nanobanana')
    if not exe:
        return False
    cmd = [exe, 'generate', prompt, '--output', output_path, '--no-show'] if 'ddnano' in exe else [exe, '--prompt', prompt, '--output', output_path]
    # Load API key from ~/nanobanana/config.json if present
    env = None
    try:
        cfg_path = Path.home() / 'nanobanana' / 'config.json'
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            key = cfg.get('api_key')
            if key:
                env = dict(**os.environ)
                env['GEMINI_API_KEY'] = env.get('GEMINI_API_KEY', key)
    except Exception:
        pass
    try:
        subprocess.run(cmd, check=True, timeout=timeout_sec, env=env)
        return True
    except Exception:
        return False


def regenerate(run_dir: Path, indices: List[int]):
    cues = load_cues(run_dir)
    images_dir = run_dir / 'images'
    images_dir.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    import time
    for i in indices:
        cue = cues[i - 1]
        out_path = images_dir / f"{i:04d}.png"
        prompt = build_prompt(cue)
        attempts = 0
        while attempts < 5:
            attempts += 1
            ok = _run_direct(
                prompt=prompt,
                output_path=str(out_path),
                width=1920,
                height=1080,
                config_path=str(Path.home() / 'nanobanana' / 'config.json'),
                timeout_sec=300,
                input_images=None,
            )
            if not ok:
                ok = _run_cli(prompt, str(out_path), timeout_sec=300)

            # If file looks like base64 text, decode it
            try:
                head = out_path.read_bytes()[:16]
                if head.startswith(b'iVBOR'):
                    import base64
                    b = base64.b64decode(out_path.read_bytes())
                    out_path.write_bytes(b)
            except Exception:
                pass

            # Verify
            valid = False
            try:
                if out_path.exists() and out_path.stat().st_size >= 50_000:
                    with Image.open(out_path) as im:
                        im.verify()
                    valid = True
            except Exception:
                valid = False

            if ok and valid:
                try:
                    _convert_to_16_9(str(out_path), 1920, 1080)
                except Exception:
                    pass
                print(f"✔ Regenerated {out_path.name}")
                break
            time.sleep(2)
        else:
            print(f"✖ Failed to regenerate {out_path.name}")


def detect_duplicates(images_dir: Path) -> List[int]:
    import hashlib
    from PIL import Image
    hashes = {}
    dups = []
    for p in sorted(images_dir.glob('*.png')):
        try:
            data = p.read_bytes()
            h = hashlib.sha1(data).hexdigest()
            if h in hashes:
                dups.append(int(p.stem))
            else:
                # also treat obviously tiny images as duplicates bucket
                hashes[h] = p.name
        except Exception:
            continue
    return sorted(dups)


def main():
    ap = argparse.ArgumentParser(description='Regenerate missing or invalid images with Japan-senior, no-text, gentle, consistent-characters constraints')
    ap.add_argument('--run', required=True, help='srt2images output run dir (contains image_cues.json and images/)')
    ap.add_argument('--threshold', type=int, default=50_000, help='Size threshold (bytes) for placeholder detection')
    ap.add_argument('--all', action='store_true', help='Force regenerate all indices')
    ap.add_argument('--dedupe', action='store_true', help='Regenerate duplicate images (byte-identical), keeping the first')
    args = ap.parse_args()

    run_dir = Path(args.run).resolve()
    cues = load_cues(run_dir)
    total = len(cues)
    images_dir = run_dir / 'images'
    small_or_missing, invalid = find_bad_images(images_dir, total, args.threshold)
    targets = set(small_or_missing + invalid)
    if args.dedupe:
        targets.update(detect_duplicates(images_dir))
    if args.all:
        targets = set(range(1, total + 1))
    targets = sorted(targets)
    print(f"Targets to regenerate: {len(targets)} / {total}")
    if not targets:
        print('Nothing to do.')
        return
    regenerate(run_dir, targets)


if __name__ == '__main__':
    main()
