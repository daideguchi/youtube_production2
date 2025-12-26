#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


# Make repo-root imports work even when executed from another CWD.
_REPO_ROOT = _discover_repo_root(Path(__file__).resolve())
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_PACKAGES_ROOT = _REPO_ROOT / "packages"
if _PACKAGES_ROOT.exists() and str(_PACKAGES_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_ROOT))


from factory_common.paths import audio_final_dir, script_data_root, status_path, video_root, video_runs_root, workspace_root  # noqa: E402
from factory_common.timeline_manifest import parse_episode_id  # noqa: E402


EPISODE_MANIFEST_SCHEMA = "ytm.episode_ssot_manifest.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _norm_channel(value: str) -> str:
    ch = (value or "").strip().upper()
    if not ch:
        raise SystemExit("channel is required (e.g. CH02)")
    return ch


def _norm_video(value: str) -> str:
    token = (value or "").strip()
    if not token:
        raise SystemExit("video is required (e.g. 014)")
    digits = "".join(ch for ch in token if ch.isdigit())
    if not digits:
        raise SystemExit(f"invalid video: {value}")
    return f"{int(digits):03d}"


def _parse_videos(values: Optional[Iterable[str]]) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for raw in values:
        if raw is None:
            continue
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            out.append(_norm_video(part))
    return sorted(set(out))


def _sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_a_paths(channel: str, video: str) -> tuple[Path, Path]:
    base = video_root(channel, video) / "content"
    return base / "assembled_human.md", base / "assembled.md"


def _canonical_a_path(human: Path, assembled: Path) -> Path:
    return human if human.exists() else assembled


def _require_existing_file(path: Path, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"{label} missing: {path}")


@dataclass(frozen=True)
class RunCandidate:
    run_id: str
    run_dir: Path
    has_timeline_manifest: bool
    has_capcut_draft: bool
    has_image_cues: bool
    has_images_dir: bool
    has_belt_config: bool
    mtime: float
    capcut_draft_target: Optional[str]
    draft_info_mtime: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "has_timeline_manifest": self.has_timeline_manifest,
            "has_capcut_draft": self.has_capcut_draft,
            "has_image_cues": self.has_image_cues,
            "has_images_dir": self.has_images_dir,
            "has_belt_config": self.has_belt_config,
            "mtime": self.mtime,
            "capcut_draft_target": self.capcut_draft_target,
            "draft_info_mtime": self.draft_info_mtime,
        }


def _safe_readlink(path: Path) -> Optional[str]:
    try:
        if not path.is_symlink():
            return None
        return os.readlink(path)
    except OSError:
        return None


def _iter_run_candidates(channel: str, video: str, *, include_hidden: bool = False) -> list[RunCandidate]:
    ch = _norm_channel(channel)
    vid = _norm_video(video)
    root = video_runs_root()
    out: list[RunCandidate] = []

    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        if not include_hidden and run_dir.name.startswith(("_", ".")):
            continue

        ep = None
        tm = run_dir / "timeline_manifest.json"
        if tm.exists():
            try:
                data = _load_json(tm)
                ep_raw = data.get("episode") or {}
                ep_id = str(ep_raw.get("id") or "")
                ep = parse_episode_id(ep_id) or parse_episode_id(f"{ep_raw.get('channel','')}-{ep_raw.get('video','')}")
            except Exception:
                ep = None
        if ep is None:
            ep = parse_episode_id(run_dir.name)

        if not ep or ep.channel != ch or ep.video != vid:
            continue

        capcut_link = run_dir / "capcut_draft"
        capcut_target = _safe_readlink(capcut_link)
        draft_info = run_dir / "capcut_draft_info.json"
        out.append(
            RunCandidate(
                run_id=run_dir.name,
                run_dir=run_dir,
                has_timeline_manifest=tm.exists(),
                has_capcut_draft=capcut_link.exists(),
                has_image_cues=(run_dir / "image_cues.json").exists(),
                has_images_dir=(run_dir / "images").exists(),
                has_belt_config=(run_dir / "belt_config.json").exists(),
                mtime=run_dir.stat().st_mtime,
                capcut_draft_target=capcut_target,
                draft_info_mtime=draft_info.stat().st_mtime if draft_info.exists() else 0.0,
            )
        )

    # Prefer candidates that actually produced a draft; then newest.
    out.sort(key=lambda c: (c.has_capcut_draft, c.draft_info_mtime, c.mtime, c.run_id), reverse=True)
    return out


def _index_run_candidates(*, include_hidden: bool = False) -> dict[tuple[str, str], list[RunCandidate]]:
    root = video_runs_root()
    out: dict[tuple[str, str], list[RunCandidate]] = {}
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        if not include_hidden and run_dir.name.startswith(("_", ".")):
            continue

        ch = ""
        vid = ""

        tm = run_dir / "timeline_manifest.json"
        ep = None
        if tm.exists():
            try:
                data = _load_json(tm)
                ep_raw = data.get("episode") or {}
                ep_id = str(ep_raw.get("id") or "")
                ep = parse_episode_id(ep_id) or parse_episode_id(f"{ep_raw.get('channel','')}-{ep_raw.get('video','')}")
            except Exception:
                ep = None
        if ep is None:
            ep = parse_episode_id(run_dir.name)

        if not ep:
            continue
        ch = ep.channel
        vid = ep.video

        capcut_link = run_dir / "capcut_draft"
        capcut_target = _safe_readlink(capcut_link)
        draft_info = run_dir / "capcut_draft_info.json"
        cand = RunCandidate(
            run_id=run_dir.name,
            run_dir=run_dir,
            has_timeline_manifest=tm.exists(),
            has_capcut_draft=capcut_link.exists(),
            has_image_cues=(run_dir / "image_cues.json").exists(),
            has_images_dir=(run_dir / "images").exists(),
            has_belt_config=(run_dir / "belt_config.json").exists(),
            mtime=run_dir.stat().st_mtime,
            capcut_draft_target=capcut_target,
            draft_info_mtime=draft_info.stat().st_mtime if draft_info.exists() else 0.0,
        )
        out.setdefault((ch, vid), []).append(cand)

    for k, v in out.items():
        v.sort(key=lambda c: (c.has_capcut_draft, c.draft_info_mtime, c.mtime, c.run_id), reverse=True)
    return out


def _auto_pick_run(candidates: list[RunCandidate]) -> Optional[str]:
    if not candidates:
        return None
    with_draft = [c for c in candidates if c.has_capcut_draft]
    if len(with_draft) == 1:
        return with_draft[0].run_id
    if len(with_draft) >= 2:
        # Prefer the latest draft_info, then mtime.
        best = max(with_draft, key=lambda c: (c.draft_info_mtime, c.mtime, c.run_id))
        return best.run_id

    with_cues = [c for c in candidates if c.has_image_cues]
    if len(with_cues) == 1:
        return with_cues[0].run_id

    if len(candidates) == 1:
        return candidates[0].run_id

    return None


def _update_status_metadata(channel: str, video: str, *, updates: dict[str, Any]) -> Path:
    p = status_path(channel, video)
    _require_existing_file(p, "status.json")
    data = _load_json(p)
    meta = data.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
        data["metadata"] = meta
    for k, v in updates.items():
        meta[k] = v
    _save_json(p, data)
    return p


def _ensure_symlink(dest: Path, target: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink():
            dest.unlink()
        else:
            raise SystemExit(f"Refusing to overwrite non-symlink: {dest}")
    dest.symlink_to(target)


def _build_episode_manifest(channel: str, video: str, *, include_hidden_runs: bool = False) -> dict[str, Any]:
    ch = _norm_channel(channel)
    vid = _norm_video(video)
    episode_id = f"{ch}-{vid}"

    human_a, assembled_a = _resolve_a_paths(ch, vid)
    a_path = _canonical_a_path(human_a, assembled_a)

    audio_dir = audio_final_dir(ch, vid)
    wav = audio_dir / f"{episode_id}.wav"
    srt = audio_dir / f"{episode_id}.srt"
    a_snapshot = audio_dir / "a_text.txt"
    b_text = audio_dir / "b_text.txt"
    b_text_with_pauses = audio_dir / "b_text_with_pauses.txt"

    status_p = status_path(ch, vid)
    status_exists = status_p.exists()
    status = _load_json(status_p) if status_exists else {}
    meta = status.get("metadata") if isinstance(status.get("metadata"), dict) else {}
    selected_run = meta.get("video_run_id") if isinstance(meta, dict) else None

    run_candidates = _iter_run_candidates(ch, vid, include_hidden=include_hidden_runs)
    selected_run_dir = video_runs_root() / str(selected_run) if selected_run else None

    warnings: list[str] = []
    if not a_path.exists():
        warnings.append(f"A text missing: {a_path}")
    if not status_exists:
        warnings.append(f"status.json missing: {status_p}")
    if not audio_dir.exists():
        warnings.append(f"audio final missing: {audio_dir}")
    else:
        if not wav.exists():
            warnings.append(f"audio wav missing: {wav}")
        if not srt.exists():
            warnings.append(f"audio srt missing: {srt}")

    a_sha1 = None
    if a_path.exists():
        a_sha1 = _sha1_text(_read_text(a_path))

    a_snapshot_sha1 = None
    a_matches_snapshot = None
    if a_snapshot.exists():
        a_snapshot_sha1 = _sha1_text(_read_text(a_snapshot))
        if a_sha1:
            a_matches_snapshot = a_sha1 == a_snapshot_sha1

    if selected_run and selected_run_dir and not selected_run_dir.exists():
        warnings.append(f"selected video_run_id not found under runs/: {selected_run}")

    manifest: dict[str, Any] = {
        "schema": EPISODE_MANIFEST_SCHEMA,
        "generated_at": _utc_now_iso(),
        "episode": {"id": episode_id, "channel": ch, "video": vid},
        "sot": {
            "script": {
                "a_text": {
                    "path": str(a_path),
                    "sha1": a_sha1,
                    "preferred": str(human_a),
                    "fallback": str(assembled_a),
                },
            },
            "audio": {
                "dir": str(audio_dir),
                "wav": str(wav),
                "srt": str(srt),
                "a_text_snapshot": {"path": str(a_snapshot), "sha1": a_snapshot_sha1, "matches_a_text": a_matches_snapshot},
                "b_text": {"path": str(b_text), "exists": b_text.exists()},
                "b_text_with_pauses": {"path": str(b_text_with_pauses), "exists": b_text_with_pauses.exists()},
            },
            "video": {
                "video_run_id": selected_run,
                "run_dir": str(selected_run_dir) if selected_run_dir else None,
                "capcut_draft": str((selected_run_dir / "capcut_draft")) if selected_run_dir else None,
                "candidates": [c.as_dict() for c in run_candidates],
            },
        },
        "warnings": warnings,
    }
    return manifest


def _iter_selected_videos(channel: str) -> list[str]:
    """
    Return video numbers where status.json exists and metadata.video_run_id is set.
    """
    ch = _norm_channel(channel)
    root = script_data_root() / ch
    if not root.exists():
        return []
    out: list[str] = []
    for vid_dir in sorted(root.iterdir()):
        if not vid_dir.is_dir() or not vid_dir.name.isdigit():
            continue
        p = vid_dir / "status.json"
        if not p.exists():
            continue
        try:
            data = _load_json(p)
        except Exception:
            continue
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        if meta and meta.get("video_run_id"):
            out.append(vid_dir.name.zfill(3))
    return sorted(set(out))


def _move_dir(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise SystemExit(f"archive destination already exists: {dest}")
    try:
        src.rename(dest)
        return
    except OSError:
        import shutil

        shutil.move(str(src), str(dest))


def cmd_show(args: argparse.Namespace) -> int:
    manifest = _build_episode_manifest(args.channel, args.video, include_hidden_runs=args.include_hidden_runs)
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0 if not manifest.get("warnings") else 2

    ep = manifest["episode"]["id"]
    print(f"Episode: {ep}")
    a = manifest["sot"]["script"]["a_text"]
    print(f"A text : {a['path']}")
    audio = manifest["sot"]["audio"]
    print(f"Audio  : {audio['wav']}")
    print(f"SRT    : {audio['srt']}")
    print(f"Run    : {manifest['sot']['video']['video_run_id'] or '(unset)'}")
    if manifest.get("warnings"):
        print("\nWarnings:")
        for w in manifest["warnings"]:
            print(f"- {w}")
        return 2
    return 0


def cmd_confirm_a(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    vid = _norm_video(args.video)
    human_a, assembled_a = _resolve_a_paths(ch, vid)
    if not human_a.exists():
        _require_existing_file(assembled_a, "assembled.md")
        _write_text(human_a, _read_text(assembled_a))
        print(f"[CREATE] {human_a} (copied from assembled.md)")

    if args.sync_mirror:
        if assembled_a.exists():
            human_text = _read_text(human_a)
            assembled_text = _read_text(assembled_a)
            if human_text != assembled_text:
                backup = assembled_a.with_suffix(f".md.bak.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
                _write_text(backup, assembled_text)
                _write_text(assembled_a, human_text)
                print(f"[SYNC] assembled_human.md -> assembled.md (backup: {backup.name})")
        else:
            _write_text(assembled_a, _read_text(human_a))
            print(f"[SYNC] assembled_human.md -> assembled.md (created mirror)")

    a_sha1 = _sha1_text(_read_text(human_a))
    status_p = _update_status_metadata(
        ch,
        vid,
        updates={
            "a_text_path": str(human_a),
            "a_text_sha1": a_sha1,
            "a_text_confirmed_at": _utc_now_iso(),
        },
    )
    print(f"[OK] A text confirmed: {ch}-{vid} sha1={a_sha1} (status: {status_p})")
    return 0


def cmd_auto_select_run(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    videos = _parse_videos(args.videos) or [_norm_video(args.video)]
    failures: list[str] = []

    for vid in videos:
        p = status_path(ch, vid)
        if not p.exists():
            failures.append(f"{ch}-{vid}: status.json missing ({p})")
            continue
        candidates = _iter_run_candidates(ch, vid, include_hidden=args.include_hidden_runs)
        picked = _auto_pick_run(candidates)
        if not picked:
            failures.append(f"{ch}-{vid}: ambiguous (candidates={len(candidates)})")
            continue
        status_p = _update_status_metadata(ch, vid, updates={"video_run_id": picked, "video_run_selected_at": _utc_now_iso()})
        print(f"[OK] {ch}-{vid}: video_run_id={picked} (status: {status_p})")

    return 0 if not failures else 2


def cmd_set_run(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    vid = _norm_video(args.video)
    run_id = (args.run_id or "").strip()
    if not run_id:
        raise SystemExit("--run-id is required")
    run_dir = video_runs_root() / run_id
    if not run_dir.exists():
        raise SystemExit(f"run not found: {run_dir}")

    candidates = _iter_run_candidates(ch, vid, include_hidden=True)
    if not args.force and run_id not in {c.run_id for c in candidates}:
        raise SystemExit(f"run_id does not match episode {ch}-{vid}. Use --force to override.")

    status_p = _update_status_metadata(ch, vid, updates={"video_run_id": run_id, "video_run_selected_at": _utc_now_iso()})
    print(f"[OK] {ch}-{vid}: video_run_id={run_id} (status: {status_p})")
    return 0


def cmd_archive_runs(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    videos = _parse_videos(args.videos)
    if args.all_selected:
        videos = sorted(set(videos) | set(_iter_selected_videos(ch)))
    if args.video:
        videos = sorted(set(videos) | {_norm_video(args.video)})
    if not videos:
        raise SystemExit("No videos specified. Use --video/--videos or --all-selected.")

    mode = args.mode
    if mode not in ("dry-run", "run"):
        raise SystemExit(f"invalid mode: {mode}")

    archive_root = (
        Path(args.archive_root).expanduser().resolve()
        if args.archive_root
        else (workspace_root() / "video" / "_archive" / _utc_now_compact() / ch)
    )
    runs_archive = archive_root / "runs"

    # Build run index once (fast even with many episodes).
    run_index = _index_run_candidates(include_hidden=args.include_hidden_runs)

    moved: list[dict[str, Any]] = []
    skipped: list[str] = []
    warnings: list[str] = []

    for vid in videos:
        status_p = status_path(ch, vid)
        if not status_p.exists():
            skipped.append(f"{ch}-{vid}: status.json missing")
            continue
        status = _load_json(status_p)
        meta = status.get("metadata") if isinstance(status.get("metadata"), dict) else {}
        selected = (meta or {}).get("video_run_id") if isinstance(meta, dict) else None
        if not selected:
            skipped.append(f"{ch}-{vid}: metadata.video_run_id missing")
            continue

        candidates = run_index.get((ch, vid), [])
        if not candidates:
            warnings.append(f"{ch}-{vid}: no run candidates found in runs/ (selected={selected})")
            continue

        selected_dir = video_runs_root() / str(selected)
        if not selected_dir.exists():
            warnings.append(f"{ch}-{vid}: selected run dir missing: {selected_dir}")
            continue

        for cand in candidates:
            if cand.run_id == selected:
                continue
            src = cand.run_dir
            if not src.exists():
                continue
            dest = runs_archive / src.name
            record = {
                "episode": f"{ch}-{vid}",
                "selected_run_id": selected,
                "archived_run_id": src.name,
                "src": str(src),
                "dest": str(dest),
                "has_capcut_draft": cand.has_capcut_draft,
                "capcut_draft_target": cand.capcut_draft_target,
            }
            moved.append(record)
            if mode == "run":
                _move_dir(src, dest)

    report = {
        "schema": "ytm.video_run_archive_report.v1",
        "generated_at": _utc_now_iso(),
        "mode": mode,
        "channel": ch,
        "videos": videos,
        "archive_root": str(archive_root),
        "moved_count": len(moved),
        "skipped": skipped,
        "warnings": warnings,
        "moved": moved,
    }
    if mode == "run":
        _save_json(archive_root / "archive_report.json", report)
    else:
        # In dry-run, write a report next to stdout for review, but under logs/ (safe).
        log_dir = workspace_root() / "logs" / "regression"
        log_dir.mkdir(parents=True, exist_ok=True)
        _save_json(log_dir / f"archive_video_runs_dryrun_{ch}_{_utc_now_compact()}.json", report)

    print(f"[ARCHIVE] mode={mode} channel={ch} videos={len(videos)} moved={len(moved)}")
    if skipped:
        print(f"[ARCHIVE] skipped={len(skipped)} (e.g. {skipped[0]})")
    if warnings:
        print(f"[ARCHIVE] warnings={len(warnings)} (e.g. {warnings[0]})")
    if mode == "run":
        print(f"[ARCHIVE] report: {archive_root / 'archive_report.json'}")
    return 0 if not warnings else 2


def cmd_materialize(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    vid = _norm_video(args.video)
    episode_id = f"{ch}-{vid}"

    manifest = _build_episode_manifest(ch, vid, include_hidden_runs=args.include_hidden_runs)
    out_dir = workspace_root() / "episodes" / ch / vid
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest first (even if incomplete) for debugging.
    manifest_path = out_dir / "episode_manifest.json"
    _save_json(manifest_path, manifest)

    human_a, assembled_a = _resolve_a_paths(ch, vid)
    a_path = _canonical_a_path(human_a, assembled_a)
    audio_dir = audio_final_dir(ch, vid)
    wav = audio_dir / f"{episode_id}.wav"
    srt = audio_dir / f"{episode_id}.srt"

    meta = manifest.get("sot", {}).get("video", {})
    selected_run = meta.get("video_run_id")
    run_dir = (video_runs_root() / str(selected_run)) if selected_run else None

    links: list[tuple[str, Path]] = []
    if a_path.exists():
        links.append(("A_text.md", a_path))
    if audio_dir.exists():
        links.append(("audio_final", audio_dir))
    if wav.exists():
        links.append(("audio.wav", wav))
    if srt.exists():
        links.append(("audio.srt", srt))
    if run_dir and run_dir.exists():
        links.append(("run", run_dir))
        capcut = run_dir / "capcut_draft"
        if capcut.exists():
            links.append(("capcut_draft", capcut))

    desired = {name: target for name, target in links}
    managed_names = {"A_text.md", "audio_final", "audio.wav", "audio.srt", "run", "capcut_draft"}

    # Ensure desired symlinks, and remove stale/broken ones (noise reduction).
    for name in sorted(managed_names):
        dest = out_dir / name
        target = desired.get(name)
        if target is None:
            if dest.is_symlink():
                dest.unlink()
            continue
        _ensure_symlink(dest, target)

    print(f"[OK] materialized: {out_dir} (manifest: {manifest_path})")
    if manifest.get("warnings"):
        return 2
    return 0


def cmd_ensure(args: argparse.Namespace) -> int:
    # Best-effort: confirm A, select run, materialize. (Audio generation is out-of-scope here.)
    rc = 0
    if args.confirm_a:
        rc = max(rc, cmd_confirm_a(args))
    rc = max(rc, cmd_auto_select_run(args))
    rc = max(rc, cmd_materialize(args))
    return rc


def main() -> int:
    p = argparse.ArgumentParser(description="Episode SSOT resolver / 1:1 artifact guard")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("show", help="Show resolved SSOT paths for an episode")
    sp.add_argument("--channel", required=True)
    sp.add_argument("--video", required=True)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--include-hidden-runs", action="store_true", help="Include runs starting with _ or .")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("confirm-a", help="Ensure assembled_human exists and record a_text_sha1 in status.json")
    sp.add_argument("--channel", required=True)
    sp.add_argument("--video", required=True)
    sp.add_argument(
        "--sync-mirror",
        action="store_true",
        help="If assembled_human != assembled, overwrite assembled.md to match (with .bak timestamp backup).",
    )
    sp.set_defaults(func=cmd_confirm_a)

    sp = sub.add_parser("auto-select-run", help="Auto-pick a run and write metadata.video_run_id in status.json")
    sp.add_argument("--channel", required=True)
    sp.add_argument("--video", help="Single video (fallback when --videos omitted)")
    sp.add_argument("--videos", action="append", help="Comma-separated list of videos (repeatable)")
    sp.add_argument("--include-hidden-runs", action="store_true", help="Include runs starting with _ or .")
    sp.set_defaults(func=cmd_auto_select_run)

    sp = sub.add_parser("set-run", help="Manually set metadata.video_run_id in status.json")
    sp.add_argument("--channel", required=True)
    sp.add_argument("--video", required=True)
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--force", action="store_true", help="Skip episode/run_id validation")
    sp.set_defaults(func=cmd_set_run)

    sp = sub.add_parser("archive-runs", help="Archive unselected run dirs for episodes with video_run_id set")
    sp.add_argument("--channel", required=True)
    sp.add_argument("--video", help="Single video (optional)")
    sp.add_argument("--videos", action="append", help="Comma-separated list of videos (repeatable)")
    sp.add_argument("--all-selected", action="store_true", help="Use all videos where metadata.video_run_id is already set")
    sp.add_argument("--mode", choices=["dry-run", "run"], default="dry-run")
    sp.add_argument("--archive-root", help="Override archive root (default: workspaces/video/_archive/<timestamp>)")
    sp.add_argument("--include-hidden-runs", action="store_true", help="Include runs starting with _ or .")
    sp.set_defaults(func=cmd_archive_runs)

    sp = sub.add_parser("materialize", help="Create workspaces/episodes/<CH>/<NNN>/ with symlinks + manifest")
    sp.add_argument("--channel", required=True)
    sp.add_argument("--video", required=True)
    sp.add_argument("--include-hidden-runs", action="store_true", help="Include runs starting with _ or .")
    sp.set_defaults(func=cmd_materialize)

    sp = sub.add_parser("ensure", help="confirm-a + auto-select-run + materialize (best-effort)")
    sp.add_argument("--channel", required=True)
    sp.add_argument("--video", required=True)
    sp.add_argument("--videos", action="append", help="Optional batch videos for selection (comma-separated)")
    sp.add_argument("--include-hidden-runs", action="store_true", help="Include runs starting with _ or .")
    sp.add_argument("--confirm-a", action="store_true", help="Create/confirm assembled_human + record sha1")
    sp.add_argument("--sync-mirror", action="store_true")
    sp.set_defaults(func=cmd_ensure)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
