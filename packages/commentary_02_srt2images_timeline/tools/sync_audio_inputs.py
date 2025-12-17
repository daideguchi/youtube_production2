#!/usr/bin/env python3
"""
Sync finished audio_tts_v2 artifacts (srt/wav) into commentary input for auto-draft.

- Scans workspaces/audio/final/<CH>/<video>/ for .srt/.wav
- Copies missing files to workspaces/video/input/<CH>_<PresetName>/ (or symlinks wav when configured)
- Keeps workspaces/video/input as a **mirror** of audio final SoT:
  - Copy missing files
  - If a file exists but differs from final, archive+replace (default)
- Maintains workspaces/video/_state/audio_sync_status.json with checked flag preserved
  - legacy: packages/commentary_02_srt2images_timeline/progress/audio_sync_status.json

Usage:
    python -m commentary_02_srt2images_timeline.tools.sync_audio_inputs [--dry-run]
"""
import json
import os
import shutil
from pathlib import Path
import argparse
import hashlib
from datetime import datetime, timezone

from factory_common import locks as coordination_locks
from factory_common.paths import (
    repo_root,
    workspace_root,
    audio_artifacts_root,
    video_audio_sync_status_path,
    video_input_root,
    video_pkg_root,
)

BASE = repo_root()  # repo root
ART_ROOT = audio_artifacts_root() / "final"
INPUT_ROOT = video_input_root()
PRESET_PATH = video_pkg_root() / "config" / "channel_presets.json"
MANIFEST = video_audio_sync_status_path()


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _symlink_to(src: Path, dst: Path) -> None:
    """
    Create `dst` as a symlink to `src` (relative when possible).
    """
    try:
        rel = os.path.relpath(str(src), start=str(dst.parent))
        dst.symlink_to(rel)
    except Exception:
        dst.symlink_to(src)


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


def _archive_path_for(target: Path, *, channel: str, archive_root: Path) -> Path:
    """
    Build an archive destination path under:
      workspaces/video/_archive/<timestamp>/<CH>/video_input/<relative-from-video/input>
    """
    rel = target.relative_to(INPUT_ROOT)
    return archive_root / channel / "video_input" / rel


def _files_match(src: Path, dst: Path, *, hash_wav: bool) -> bool:
    """
    Fast equality check:
      - If size differs: mismatch
      - For .wav, default is size-only (hash_wav=False) to keep startup fast
      - Otherwise compare sha1
    """
    try:
        if src.stat().st_size != dst.stat().st_size:
            return False
    except Exception:
        return False
    if src.suffix.lower() == ".wav" and not hash_wav:
        return True
    try:
        return sha1(src) == sha1(dst)
    except Exception:
        return False


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
    ap.add_argument("--dry-run", action="store_true", help="Alias for --mode dry-run")
    ap.add_argument("--mode", choices=["dry-run", "run"], default="run")
    ap.add_argument(
        "--on-mismatch",
        choices=["skip", "replace", "archive-replace"],
        default="archive-replace",
        help="When video/input already exists but differs from audio/final",
    )
    ap.add_argument("--archive-root", help="Override archive root (default: workspaces/video/_archive/<timestamp>)")
    ap.add_argument("--hash-wav", action="store_true", help="Also compare sha1 for .wav (slower; default=size-only)")
    ap.add_argument(
        "--wav-policy",
        choices=["copy", "symlink", "skip"],
        default="copy",
        help="How to materialize .wav into workspaces/video/input (default: copy).",
    )
    ap.add_argument(
        "--wav-dedupe",
        action="store_true",
        help="When --wav-policy=symlink, replace existing matching wav copies with symlinks.",
    )
    ap.add_argument("--ignore-locks", action="store_true", help="Do not respect coordination locks (DANGEROUS).")
    ap.add_argument(
        "--orphan-policy",
        choices=["keep", "archive"],
        default="keep",
        help="Handle files present in workspaces/video/input but absent from workspaces/audio/final (keep or archive).",
    )
    args = ap.parse_args()

    mode = "dry-run" if args.dry_run else args.mode
    if mode not in ("dry-run", "run"):
        raise SystemExit(f"invalid mode: {mode}")

    channel_names = load_channel_names()
    manifest = load_manifest()
    active_locks = [] if args.ignore_locks else coordination_locks.default_active_locks_for_mutation()

    copied: list[str] = []
    skipped: list[str] = []
    updated: list[str] = []
    mismatched: list[str] = []
    archived: list[str] = []
    orphaned: list[str] = []
    skipped_locked: list[str] = []

    archive_root = (
        Path(args.archive_root).expanduser().resolve()
        if args.archive_root
        else (workspace_root() / "video" / "_archive" / _utc_now_compact())
    )

    def _is_locked(path: Path) -> bool:
        if not active_locks:
            return False
        lock = coordination_locks.find_blocking_lock(path, active_locks)
        if not lock:
            return False
        try:
            rel = str(path.resolve().relative_to(BASE))
        except Exception:
            rel = str(path)
        skipped_locked.append(f"{rel} (lock={lock.lock_id}, mode={lock.mode})")
        return True

    def _is_same_symlink_target(dst: Path, src: Path) -> bool:
        if not dst.is_symlink():
            return False
        try:
            return dst.resolve() == src.resolve()
        except Exception:
            return False

    def _materialize(src: Path, dst: Path) -> bool:
        """
        Return True when dst is created/updated, False when skipped.
        """
        if src.suffix.lower() == ".wav":
            if args.wav_policy == "skip":
                return False
            if args.wav_policy == "symlink":
                _symlink_to(src, dst)
                return True
        shutil.copy2(src, dst)
        return True

    for ch_dir in sorted(ART_ROOT.glob("*")):
        if not ch_dir.is_dir():
            continue
        ch = ch_dir.name
        safe_name = channel_names.get(ch, ch)
        target_dir = INPUT_ROOT / f"{ch}_{safe_name}"
        expected_names: set[str] = set()
        # files directly under channel dir (e.g., CHTEST-001.srt)
        root_srts = sorted(ch_dir.glob("*.srt"))
        root_wavs = sorted(ch_dir.glob("*.wav"))
        for f in root_srts + root_wavs:
            expected_names.add(f.name)
            target = target_dir / f.name
            rel = str(target.relative_to(BASE))
            entry = manifest.get(rel, {"path": rel, "type": f.suffix.lstrip("."), "checked": False})
            entry["type"] = f.suffix.lstrip(".")
            entry["src"] = str(f.relative_to(BASE))
            entry["size"] = f.stat().st_size
            # Store source hash for traceability (SRT always; WAV only when --hash-wav)
            if mode != "dry-run" and (f.suffix.lower() != ".wav" or args.hash_wav):
                entry["hash"] = sha1(f)
            else:
                entry["hash"] = entry.get("hash") or ""

            if target.exists():
                if (
                    f.suffix.lower() == ".wav"
                    and args.wav_policy == "symlink"
                    and args.wav_dedupe
                    and not _is_same_symlink_target(target, f)
                    and _files_match(f, target, hash_wav=args.hash_wav)
                ):
                    # Deduplicate: replace matching wav copies with a symlink.
                    did_update = (mode == "dry-run")
                    if mode == "run":
                        if _is_locked(f) or _is_locked(target):
                            did_update = False
                            skipped.append(rel)
                        else:
                            target_dir.mkdir(parents=True, exist_ok=True)
                            try:
                                target.unlink()
                            except Exception:
                                pass
                            _materialize(f, target)
                            did_update = True
                    if did_update:
                        updated.append(rel)
                        entry["checked"] = False
                        entry["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                elif _files_match(f, target, hash_wav=args.hash_wav):
                    skipped.append(rel)
                else:
                    mismatched.append(rel)
                    if args.on_mismatch == "skip":
                        skipped.append(rel)
                    else:
                        did_update = (mode == "dry-run")
                        if f.suffix.lower() == ".wav" and args.wav_policy == "skip":
                            did_update = False
                        elif mode == "run":
                            if _is_locked(f) or _is_locked(target):
                                did_update = False
                            else:
                                target_dir.mkdir(parents=True, exist_ok=True)
                                if args.on_mismatch == "archive-replace":
                                    dest = _archive_path_for(target, channel=ch, archive_root=archive_root)
                                    dest.parent.mkdir(parents=True, exist_ok=True)
                                    shutil.move(str(target), str(dest))
                                    archived.append(str(dest.relative_to(BASE)))
                                did_update = _materialize(f, target)
                        if did_update:
                            updated.append(rel)
                            # content changed; checked flag no longer valid
                            entry["checked"] = False
                            entry["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            else:
                if f.suffix.lower() == ".wav" and args.wav_policy == "skip":
                    skipped.append(rel)
                else:
                    if mode == "run":
                        if _is_locked(f) or _is_locked(target_dir):
                            skipped.append(rel)
                        else:
                            target_dir.mkdir(parents=True, exist_ok=True)
                            if _materialize(f, target):
                                copied.append(rel)
                            else:
                                skipped.append(rel)
                    else:
                        copied.append(rel)
            manifest[rel] = entry
        for video_dir in sorted(ch_dir.glob("*")):
            if not video_dir.is_dir():
                continue
            srts = sorted(video_dir.glob("*.srt"))
            wavs = sorted(video_dir.glob("*.wav"))
            for f in srts + wavs:
                expected_names.add(f.name)
                target = target_dir / f.name
                rel = str(target.relative_to(BASE))
                entry = manifest.get(rel, {"path": rel, "type": f.suffix.lstrip("."), "checked": False})
                entry["type"] = f.suffix.lstrip(".")
                entry["src"] = str(f.relative_to(BASE))
                entry["size"] = f.stat().st_size
                if mode != "dry-run" and (f.suffix.lower() != ".wav" or args.hash_wav):
                    entry["hash"] = sha1(f)
                else:
                    entry["hash"] = entry.get("hash") or ""

                if target.exists():
                    if (
                        f.suffix.lower() == ".wav"
                        and args.wav_policy == "symlink"
                        and args.wav_dedupe
                        and not _is_same_symlink_target(target, f)
                        and _files_match(f, target, hash_wav=args.hash_wav)
                    ):
                        did_update = (mode == "dry-run")
                        if mode == "run":
                            if _is_locked(f) or _is_locked(target):
                                did_update = False
                                skipped.append(rel)
                            else:
                                target_dir.mkdir(parents=True, exist_ok=True)
                                try:
                                    target.unlink()
                                except Exception:
                                    pass
                                _materialize(f, target)
                                did_update = True
                        if did_update:
                            updated.append(rel)
                            entry["checked"] = False
                            entry["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    elif _files_match(f, target, hash_wav=args.hash_wav):
                        skipped.append(rel)
                    else:
                        mismatched.append(rel)
                        if args.on_mismatch == "skip":
                            skipped.append(rel)
                        else:
                            did_update = (mode == "dry-run")
                            if f.suffix.lower() == ".wav" and args.wav_policy == "skip":
                                did_update = False
                            elif mode == "run":
                                if _is_locked(f) or _is_locked(target):
                                    did_update = False
                                else:
                                    target_dir.mkdir(parents=True, exist_ok=True)
                                    if args.on_mismatch == "archive-replace":
                                        dest = _archive_path_for(target, channel=ch, archive_root=archive_root)
                                        dest.parent.mkdir(parents=True, exist_ok=True)
                                        shutil.move(str(target), str(dest))
                                        archived.append(str(dest.relative_to(BASE)))
                                    did_update = _materialize(f, target)
                            if did_update:
                                updated.append(rel)
                                entry["checked"] = False
                                entry["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                else:
                    if f.suffix.lower() == ".wav" and args.wav_policy == "skip":
                        skipped.append(rel)
                    else:
                        if mode == "run":
                            if _is_locked(f) or _is_locked(target_dir):
                                skipped.append(rel)
                            else:
                                target_dir.mkdir(parents=True, exist_ok=True)
                                if _materialize(f, target):
                                    copied.append(rel)
                                else:
                                    skipped.append(rel)
                        else:
                            copied.append(rel)
                manifest[rel] = entry

        if args.orphan_policy != "keep" and target_dir.exists():
            current = sorted(target_dir.glob("*.srt")) + sorted(target_dir.glob("*.wav"))
            for f in current:
                if f.name in expected_names:
                    continue
                rel = str(f.relative_to(BASE))
                orphaned.append(rel)
                if args.orphan_policy == "archive" and mode == "run":
                    if _is_locked(f):
                        skipped.append(rel)
                        continue
                    dest = _archive_path_for(f, channel=ch, archive_root=archive_root)
                    dest.parent.mkdir(parents=True, exist_ok=True)

                    entry = manifest.get(rel, {"path": rel, "type": f.suffix.lstrip("."), "checked": False})
                    entry["type"] = f.suffix.lstrip(".")
                    entry["size"] = f.stat().st_size
                    if f.suffix.lower() != ".wav" or args.hash_wav:
                        entry["hash"] = sha1(f)
                    else:
                        entry["hash"] = entry.get("hash") or ""
                    entry["orphan"] = True
                    entry["checked"] = False
                    entry["archived_to"] = str(dest.relative_to(BASE))
                    entry["archived_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    manifest[rel] = entry

                    shutil.move(str(f), str(dest))
                    archived.append(str(dest.relative_to(BASE)))

    save_manifest(manifest)

    print(
        f"mode={mode} on_mismatch={args.on_mismatch} | copied: {len(copied)} | updated: {len(updated)} | skipped: {len(skipped)} | mismatched: {len(mismatched)}"
    )
    if archived:
        print(f"archived: {len(archived)} | archive_root: {archive_root}")
    if orphaned:
        print(f"orphans: {len(orphaned)} | orphan_policy: {args.orphan_policy}")
    if skipped_locked:
        print(f"skipped_locked: {len(skipped_locked)}")
    if copied:
        print("copied samples:", copied[:5])
    if updated:
        print("updated samples:", updated[:5])
    if skipped:
        print("skipped samples:", skipped[:5])
    if mismatched and args.on_mismatch == "skip":
        print("⚠️ mismatched (kept):", mismatched[:5])


if __name__ == "__main__":
    main()
