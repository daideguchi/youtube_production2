#!/usr/bin/env python3
"""
archive_episode_asset_pack.py — Episode Asset Pack を1コマンドで書庫化する

SSOT:
  - ssot/ops/OPS_VIDEO_ASSET_PACK.md
  - ssot/ops/OPS_GH_RELEASES_ARCHIVE.md

Flow:
  1) run_dir -> Episode Asset Pack（番号固定）: video_assets_pack.py export
  2) Episode Asset Pack -> 1ファイル（.tgz）へ束ねる
  3) (optional) GitHub Releases 書庫へ push: release_archive.py push
  4) (optional) 外部SSDへ退避: YTM_OFFLOAD_ROOT

Safety:
  - default は dry-run（何も書かない）
  - `--run` 指定時のみ書き込み/アップロード/退避を実行する
  - coordination locks を尊重し、lock 対象は停止する
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402
from factory_common import paths as repo_paths  # noqa: E402


class EpisodeAssetPackArchiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class Plan:
    channel: str
    video: str
    video_id: str
    run_dir: str
    pack_dir: Path
    bundle_path: Path
    offload_dest: Optional[Path]
    export_cmd: list[str]
    push_cmd: Optional[list[str]]


def _norm_channel(raw: str) -> str:
    s = str(raw or "").strip().upper()
    m = re.fullmatch(r"CH(\d{1,3})", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    if s.startswith("CH"):
        return s
    raise EpisodeAssetPackArchiveError(f"invalid channel: {raw!r}")


def _norm_video(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        raise EpisodeAssetPackArchiveError(f"invalid video: {raw!r}")
    try:
        return f"{int(s):03d}"
    except Exception:
        if len(s) == 3 and s.isdigit():
            return s
        raise EpisodeAssetPackArchiveError(f"invalid video: {raw!r}")


def _resolve_path(raw: str) -> Path:
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        p = repo_paths.repo_root() / p
    return p


def _default_bundle_path(channel: str, video: str) -> Path:
    name = f"episode_asset_pack__{channel}-{video}.tgz"
    return Path(tempfile.gettempdir()) / name


def _require_no_blocking_lock(path: Path, *, when: str) -> None:
    locks = default_active_locks_for_mutation()
    blocking = find_blocking_lock(path, locks)
    if not blocking:
        return
    scopes = ",".join(blocking.scopes)
    raise EpisodeAssetPackArchiveError(
        f"blocked by coordination lock ({when}): lock_id={blocking.lock_id} created_by={blocking.created_by} mode={blocking.mode} scopes={scopes}"
    )


def _run_passthrough(cmd: list[str], *, cwd: Optional[Path] = None) -> None:
    p = subprocess.run(cmd, cwd=str(cwd or repo_paths.repo_root()))
    if p.returncode != 0:
        raise EpisodeAssetPackArchiveError(f"command failed (exit={p.returncode}): {' '.join(cmd)}")

def _git_tracked_paths_under(path: Path) -> list[str]:
    """
    Best-effort: return git-tracked paths under `path` (repo-relative).
    Used to prevent accidental deletion of committed asset packs.
    """
    try:
        root = repo_paths.repo_root()
        rel = path.resolve().relative_to(root).as_posix()
    except Exception:
        return []

    if shutil.which("git") is None:
        return []

    try:
        cp = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--", rel],
            check=False,
            capture_output=True,
            text=True,
        )
        if cp.returncode != 0:
            return []
        return [line.strip() for line in (cp.stdout or "").splitlines() if line.strip()]
    except Exception:
        return []


def _build_plan(args: argparse.Namespace) -> Plan:
    channel = _norm_channel(args.channel)
    video = _norm_video(args.video)
    video_id = f"{channel}-{video}"

    pack_dir = repo_paths.video_episode_assets_dir(channel, video)
    bundle_path = _resolve_path(args.bundle_path) if args.bundle_path else _default_bundle_path(channel, video)

    run_dir = str(args.run_dir or "").strip()

    export_cmd = [
        sys.executable,
        str(repo_paths.repo_root() / "scripts" / "ops" / "video_assets_pack.py"),
        "export",
        "--channel",
        channel,
        "--video",
        video,
    ]
    if run_dir:
        export_cmd += ["--run", run_dir]
    if bool(args.include_audio):
        export_cmd += ["--include-audio"]
    if bool(args.overwrite_pack):
        export_cmd += ["--overwrite"]
    if bool(args.run):
        export_cmd += ["--write"]

    push_cmd: Optional[list[str]] = None
    if bool(args.push):
        note = str(args.note or "").strip() or f"episode asset pack (images) {video_id}"
        tags = str(args.tags or "").strip() or f"type:episode_asset_pack,channel:{channel},video:{video}"
        push_cmd = [
            sys.executable,
            str(repo_paths.repo_root() / "scripts" / "ops" / "release_archive.py"),
            "push",
            str(bundle_path),
            "--note",
            note,
            "--tags",
            tags,
        ]
        if str(args.repo or "").strip():
            push_cmd += ["--repo", str(args.repo).strip()]
        if str(args.archive_dir or "").strip():
            push_cmd += ["--archive-dir", str(args.archive_dir).strip()]

    offload_dest: Optional[Path] = None
    if bool(args.offload):
        base = str(args.offload_root or "").strip()
        root = Path(base).expanduser().resolve() if base else repo_paths.offload_root()
        if root is None:
            raise EpisodeAssetPackArchiveError("offload requested but YTM_OFFLOAD_ROOT (or --offload-root) is not set")
        offload_dest = root / "episode_asset_pack" / channel / bundle_path.name

    return Plan(
        channel=channel,
        video=video,
        video_id=video_id,
        run_dir=run_dir,
        pack_dir=pack_dir,
        bundle_path=bundle_path,
        offload_dest=offload_dest,
        export_cmd=export_cmd,
        push_cmd=push_cmd,
    )


def _write_bundle(*, pack_dir: Path, bundle_path: Path) -> None:
    if not pack_dir.exists() or not pack_dir.is_dir():
        raise EpisodeAssetPackArchiveError(f"asset pack dir not found: {pack_dir}")

    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = bundle_path.with_suffix(bundle_path.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    # Bundle root is "{CHxx}/{NNN}/..." to allow extracting anywhere.
    ch = pack_dir.parent.name
    nnn = pack_dir.name
    arcname = f"{ch}/{nnn}"

    with tarfile.open(tmp_path, mode="w:gz") as tf:
        tf.add(pack_dir, arcname=arcname)
    tmp_path.replace(bundle_path)


def _offload_bundle(*, src: Path, dest: Path, move: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise EpisodeAssetPackArchiveError(f"offload destination already exists: {dest}")
    if move:
        try:
            src.rename(dest)
            return
        except OSError:
            shutil.move(str(src), str(dest))
        return
    shutil.copy2(src, dest)


def _delete_path(path: Path) -> None:
    # Safety: never follow symlinks (unlink the link itself).
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Archive episode asset pack as a single bundle (tgz) and optionally push/offload it.")
    p.add_argument("--channel", required=True, help="Channel code (e.g., CH01)")
    p.add_argument("--video", required=True, help="Video number (e.g., 220)")
    p.add_argument("--run-dir", default="", help="Optional explicit workspaces/video/runs/<run_id> path for export.")
    p.add_argument("--include-audio", action="store_true", help="Also export workspaces/audio/final wav/srt into pack.")
    p.add_argument("--overwrite-pack", action="store_true", help="Overwrite existing files in asset pack on export.")
    p.add_argument("--bundle-path", default="", help="Bundle output path (default: /tmp/episode_asset_pack__CHxx-NNN.tgz).")

    p.add_argument("--push", action="store_true", help="Also push bundle to GitHub Releases archive (release_archive.py).")
    p.add_argument("--repo", default="", help="GitHub repo (OWNER/REPO). If omitted, release_archive uses ARCHIVE_REPO env or git origin.")
    p.add_argument("--archive-dir", default="", help="Optional archive base dir for manifest/index (default: repo/gh_releases_archive).")
    p.add_argument("--note", default="", help="Manifest note for release archive (optional).")
    p.add_argument("--tags", default="", help="Manifest tags for release archive (optional, comma-separated).")

    p.add_argument("--offload", action="store_true", help="Also offload bundle to external storage (YTM_OFFLOAD_ROOT).")
    p.add_argument("--offload-root", default="", help="Override offload root (default: env YTM_OFFLOAD_ROOT).")
    p.add_argument(
        "--offload-mode",
        choices=["move", "copy"],
        default="move",
        help="Offload mode (default: move).",
    )

    p.add_argument("--skip-export", action="store_true", help="Skip export step and bundle existing pack_dir.")
    p.add_argument(
        "--delete-pack-dir",
        action="store_true",
        help="Delete local pack_dir after bundling/push/offload (capacity).",
    )
    p.add_argument(
        "--force-delete-pack-dir",
        action="store_true",
        help="Allow deleting pack_dir even if it contains git-tracked files (will leave repo dirty).",
    )
    p.add_argument(
        "--delete-local-bundle",
        action="store_true",
        help="Delete local bundle file after push/offload (capacity). Requires --push or --offload.",
    )
    p.add_argument("--run", action="store_true", help="Execute (default: dry-run).")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    bootstrap(load_env=True)
    args = build_parser().parse_args(argv)

    try:
        plan = _build_plan(args)
    except EpisodeAssetPackArchiveError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    mode = "RUN" if bool(args.run) else "DRY"
    print(f"[archive_episode_asset_pack] mode={mode} episode={plan.video_id}")
    print(f"- pack_dir    : {plan.pack_dir}")
    print(f"- bundle_path : {plan.bundle_path}")
    if plan.offload_dest:
        print(f"- offload_dest: {plan.offload_dest} (mode={args.offload_mode})")
    if plan.push_cmd:
        print(f"- release_push: {' '.join(plan.push_cmd)}")

    if bool(args.skip_export):
        print("- export      : (skipped)")
    else:
        print(f"- export      : {' '.join(plan.export_cmd)}")

    if not bool(args.run):
        return 0

    if bool(args.delete_local_bundle) and not (bool(args.push) or bool(args.offload)):
        print("[ERROR] --delete-local-bundle requires --push or --offload", file=sys.stderr)
        return 2

    # Safety: respect locks for the pack dir (export writes here).
    _require_no_blocking_lock(plan.pack_dir, when="export/pack_dir")

    if not bool(args.skip_export):
        _run_passthrough(plan.export_cmd, cwd=repo_paths.repo_root())

    # Bundle writes a new file. Respect locks when output is inside the repo.
    _require_no_blocking_lock(plan.bundle_path, when="bundle/output")

    _write_bundle(pack_dir=plan.pack_dir, bundle_path=plan.bundle_path)
    print(f"[OK] bundle: {plan.bundle_path}")

    if plan.push_cmd:
        _run_passthrough(plan.push_cmd, cwd=repo_paths.repo_root())

    if plan.offload_dest:
        _offload_bundle(src=plan.bundle_path, dest=plan.offload_dest, move=(args.offload_mode == "move"))
        print(f"[OK] offload: {plan.offload_dest}")

    if bool(args.delete_pack_dir):
        tracked = _git_tracked_paths_under(plan.pack_dir)
        if tracked and not bool(args.force_delete_pack_dir):
            sample = "\n".join([f"  - {p}" for p in tracked[:12]])
            more = f"\n  ... ({len(tracked)-12} more)" if len(tracked) > 12 else ""
            raise EpisodeAssetPackArchiveError(
                "refusing to delete pack_dir containing git-tracked files.\n"
                "If this is intentional (local capacity), re-run with --force-delete-pack-dir.\n"
                f"tracked_paths:\n{sample}{more}"
            )
        _require_no_blocking_lock(plan.pack_dir, when="delete/pack_dir")
        _delete_path(plan.pack_dir)
        print(f"[OK] delete_pack_dir: {plan.pack_dir}")

    if bool(args.delete_local_bundle) and plan.bundle_path.exists():
        _delete_path(plan.bundle_path)
        print(f"[OK] delete_local_bundle: {plan.bundle_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
