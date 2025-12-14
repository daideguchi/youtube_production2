#!/usr/bin/env python3
"""
Sync finished audio_tts_v2 artifacts (srt/wav) into commentary input for auto-draft.

- Scans workspaces/audio/final/<CH>/<video>/ for .srt/.wav
- Copies missing files to workspaces/video/input/<CH>_<PresetName>/
- Never overwrites existing files
- Maintains progress/audio_sync_status.json with checked flag preserved

Usage:
    python3 tools/sync_audio_inputs.py [--dry-run]
"""
import json
import shutil
from pathlib import Path
import argparse
import hashlib

from factory_common.paths import repo_root, audio_artifacts_root, video_input_root, video_pkg_root

BASE = repo_root()  # repo root
ART_ROOT = audio_artifacts_root() / "final"
INPUT_ROOT = video_input_root()
PRESET_PATH = video_pkg_root() / "config" / "channel_presets.json"
MANIFEST = video_pkg_root() / "progress" / "audio_sync_status.json"


def load_channel_names():
    names = {}
    if PRESET_PATH.exists():
        data = json.loads(PRESET_PATH.read_text())
        for k, v in (data.get("channels") or {}).items():
            nm = v.get("name") or k
            safe = nm.replace("/", "_").replace(" ", "_")
            names[k] = safe
    return names


def sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest():
    if not MANIFEST.exists():
        return {}
    try:
        data = json.loads(MANIFEST.read_text())
    except Exception:
        return {}
    # manifest as dict keyed by relpath
    if isinstance(data, list):
        # legacy list -> convert
        out = {}
        for item in data:
            if isinstance(item, str):
                out[item] = {"path": item, "type": "unknown", "checked": False}
        return out
    if isinstance(data, dict):
        # legacy {"copied":[...], "skipped_existing":[...]} -> flatten
        if "copied" in data or "skipped_existing" in data:
            out = {}
            for key in ("copied", "skipped_existing"):
                for item in data.get(key, []):
                    out[item] = {"path": item, "type": "unknown", "checked": False}
            return out
        return data
    return {}


def save_manifest(manifest: dict):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    channel_names = load_channel_names()
    manifest = load_manifest()

    copied = []
    skipped = []

    for ch_dir in sorted(ART_ROOT.glob("*")):
        if not ch_dir.is_dir():
            continue
        ch = ch_dir.name
        safe_name = channel_names.get(ch, ch)
        target_dir = INPUT_ROOT / f"{ch}_{safe_name}"
        # files directly under channel dir (e.g., CHTEST-001.srt)
        root_srts = sorted(ch_dir.glob("*.srt"))
        root_wavs = sorted(ch_dir.glob("*.wav"))
        for f in root_srts + root_wavs:
            target = target_dir / f.name
            rel = str(target.relative_to(BASE))
            entry = manifest.get(rel, {"path": rel, "type": f.suffix.lstrip("."), "checked": False})
            entry["type"] = f.suffix.lstrip(".")
            entry["src"] = str(f.relative_to(BASE))
            entry["size"] = f.stat().st_size
            entry["hash"] = sha1(f) if not args.dry_run else ""
            if target.exists():
                skipped.append(rel)
            else:
                if not args.dry_run:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target)
                copied.append(rel)
            manifest[rel] = entry
        for video_dir in sorted(ch_dir.glob("*")):
            if not video_dir.is_dir():
                continue
            srts = sorted(video_dir.glob("*.srt"))
            wavs = sorted(video_dir.glob("*.wav"))
            for f in srts + wavs:
                target = target_dir / f.name
                rel = str(target.relative_to(BASE))
                entry = manifest.get(rel, {"path": rel, "type": f.suffix.lstrip("."), "checked": False})
                entry["type"] = f.suffix.lstrip(".")
                entry["src"] = str(f.relative_to(BASE))
                entry["size"] = f.stat().st_size
                entry["hash"] = sha1(f) if not args.dry_run else ""

                if target.exists():
                    skipped.append(rel)
                else:
                    if not args.dry_run:
                        target_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, target)
                    copied.append(rel)
                manifest[rel] = entry

    save_manifest(manifest)

    print(f"copied: {len(copied)} | skipped(existing): {len(skipped)} | manifest: {MANIFEST}")
    if copied:
        print("copied samples:", copied[:5])
    if skipped:
        print("skipped samples:", skipped[:5])


if __name__ == "__main__":
    main()
