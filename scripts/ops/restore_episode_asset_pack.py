#!/usr/bin/env python3
"""
restore_episode_asset_pack.py — Restore an Episode Asset Pack from GitHub Releases archive.

SSOT:
  - ssot/ops/OPS_GH_RELEASES_ARCHIVE.md
  - ssot/ops/OPS_VIDEO_ASSET_PACK.md

Flow:
  1) Resolve archive_id:
     - explicit: --archive-id A-YYYY-MM-DD-####, or
     - lookup: (type:episode_asset_pack, channel:CHxx, video:NNN) in manifest.jsonl
  2) Download + verify bundle via release_archive.py pull
  3) Extract tgz into extract_dir (safe)
  4) (optional) Write into workspaces/video/assets/episodes/{CH}/{NNN} (explicit flag)

Safety:
  - Default is dry-run (prints plan only).
  - --run is required for download/extract/write.
  - Respects coordination locks (requires LLM_AGENT_NAME to ignore own locks safely).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402
from factory_common import paths as repo_paths  # noqa: E402


class EpisodeAssetPackRestoreError(RuntimeError):
    pass


_ARCHIVE_ID_RE = re.compile(r"^A-\d{4}-\d{2}-\d{2}-\d{4}$")


@dataclass(frozen=True)
class ManifestEntry:
    archive_id: str
    created_at: str
    repo: str
    release_tag: str
    original_name: str
    tags: tuple[str, ...]

    @staticmethod
    def from_json(obj: dict[str, Any]) -> "ManifestEntry | None":
        archive_id = str(obj.get("archive_id") or "").strip()
        if not archive_id:
            return None
        created_at = str(obj.get("created_at") or "").strip()
        repo = str(obj.get("repo") or "").strip()
        release_tag = str(obj.get("release_tag") or "").strip()
        original = obj.get("original") if isinstance(obj.get("original"), dict) else {}
        original_name = str((original or {}).get("name") or "").strip()
        tags_raw = obj.get("tags") if isinstance(obj.get("tags"), list) else []
        tags = tuple([str(t).strip() for t in tags_raw if str(t).strip()])
        return ManifestEntry(
            archive_id=archive_id,
            created_at=created_at,
            repo=repo,
            release_tag=release_tag,
            original_name=original_name,
            tags=tags,
        )


def _parse_dt(value: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        v = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _norm_channel(raw: str) -> str:
    s = str(raw or "").strip().upper()
    m = re.fullmatch(r"CH(\d{1,3})", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    if s.startswith("CH") and len(s) >= 3:
        return s
    raise EpisodeAssetPackRestoreError(f"invalid --channel: {raw!r}")


def _norm_video(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        raise EpisodeAssetPackRestoreError("invalid --video: empty")
    try:
        return f"{int(token):03d}"
    except Exception:
        if token.isdigit() and len(token) == 3:
            return token
    raise EpisodeAssetPackRestoreError(f"invalid --video: {raw!r}")


def _resolve_archive_dir(raw: str) -> Path:
    s = str(raw or "").strip()
    if s:
        p = Path(s).expanduser()
        if not p.is_absolute():
            p = repo_paths.repo_root() / p
        return p
    return repo_paths.repo_root() / "gh_releases_archive"


def _manifest_path(archive_dir: Path) -> Path:
    return archive_dir / "manifest" / "manifest.jsonl"


def _iter_manifest_entries(path: Path) -> list[ManifestEntry]:
    if not path.exists():
        return []
    out: list[ManifestEntry] = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            ent = ManifestEntry.from_json(obj)
            if ent:
                out.append(ent)
    return out


def _pick_latest_episode_asset_pack(
    *,
    archive_dir: Path,
    channel: str,
    video: str,
) -> ManifestEntry:
    mp = _manifest_path(archive_dir)
    entries = _iter_manifest_entries(mp)
    required = {
        "type:episode_asset_pack",
        f"channel:{channel}",
        f"video:{video}",
    }
    matched = [e for e in entries if required.issubset(set(e.tags))]
    if not matched:
        raise EpisodeAssetPackRestoreError(
            f"manifest has no matching entry: tags={sorted(required)} path={mp.relative_to(repo_paths.repo_root())}"
        )
    matched.sort(key=lambda e: _parse_dt(e.created_at), reverse=True)
    return matched[0]


def _require_no_blocking_lock(path: Path, *, when: str) -> None:
    locks = default_active_locks_for_mutation()
    blocking = find_blocking_lock(path, locks)
    if blocking:
        scopes = ",".join(blocking.scopes)
        raise EpisodeAssetPackRestoreError(
            f"blocked by coordination lock ({when}): lock_id={blocking.lock_id} created_by={blocking.created_by} mode={blocking.mode} scopes={scopes}"
        )


def _run_passthrough(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    p = subprocess.run(cmd, cwd=str(repo_paths.repo_root()), env=env)
    if p.returncode != 0:
        raise EpisodeAssetPackRestoreError(f"command failed (exit={p.returncode}): {' '.join(cmd)}")


def _safe_extract_tgz(*, tgz_path: Path, extract_dir: Path) -> None:
    extract_dir.mkdir(parents=True, exist_ok=True)
    base = extract_dir.resolve()
    with tarfile.open(tgz_path, mode="r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            name = str(m.name or "").lstrip("/")
            if not name:
                continue
            if name.startswith("../") or "/../" in name:
                raise EpisodeAssetPackRestoreError(f"unsafe tar member path: {m.name!r}")
            dest = (extract_dir / name).resolve()
            try:
                dest.relative_to(base)
            except Exception:
                raise EpisodeAssetPackRestoreError(f"unsafe tar member path: {m.name!r}")
        # Pin tarfile behavior across Python versions (3.14 changes the default filter).
        try:
            tf.extractall(path=extract_dir, filter="data")  # py>=3.12
        except TypeError:
            tf.extractall(path=extract_dir)


def _copy_tree_atomic(*, src: Path, dest: Path, overwrite: bool) -> None:
    if dest.exists():
        if not overwrite:
            raise EpisodeAssetPackRestoreError(f"dest already exists (use --overwrite-pack): {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp_restore")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp, dirs_exist_ok=False)
    if dest.exists():
        shutil.rmtree(dest)
    tmp.replace(dest)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Restore episode asset pack from GitHub Releases archive.")
    ap.add_argument("--archive-id", default="", help="Explicit archive id (A-YYYY-MM-DD-####).")
    ap.add_argument("--channel", default="", help="Channel code (CHxx). Used for lookup when --archive-id is omitted.")
    ap.add_argument("--video", default="", help="Video number (NNN). Used for lookup when --archive-id is omitted.")
    ap.add_argument("--archive-dir", default="", help="Archive base dir (default: repo/gh_releases_archive).")
    ap.add_argument("--repo", default="", help="GitHub repo (OWNER/REPO). Default: manifest.repo / ARCHIVE_REPO / git origin.")
    ap.add_argument("--outdir", default="/tmp/ytm_restore", help="Directory for downloaded bundle (default: /tmp/ytm_restore).")
    ap.add_argument("--extract-dir", default="", help="Directory to extract into (default: <outdir>/unpacked/<archive_id>).")
    ap.add_argument(
        "--write-pack",
        action="store_true",
        help="Copy extracted pack into workspaces/video/assets/episodes/{CH}/{NNN} (git-tracked).",
    )
    ap.add_argument("--overwrite-pack", action="store_true", help="Allow overwriting existing pack_dir on --write-pack.")
    ap.add_argument("--run", action="store_true", help="Execute (default: dry-run).")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    bootstrap(load_env=True)
    args = build_parser().parse_args(argv)

    archive_dir = _resolve_archive_dir(args.archive_dir)
    mp = _manifest_path(archive_dir)

    archive_id = str(args.archive_id or "").strip()
    entry: ManifestEntry | None = None

    if archive_id:
        if not _ARCHIVE_ID_RE.match(archive_id):
            raise EpisodeAssetPackRestoreError(f"invalid --archive-id: {archive_id!r}")
        # Best-effort: load entry for metadata display.
        for e in _iter_manifest_entries(mp):
            if e.archive_id == archive_id:
                entry = e
                break
    else:
        ch = _norm_channel(args.channel)
        vid = _norm_video(args.video)
        entry = _pick_latest_episode_asset_pack(archive_dir=archive_dir, channel=ch, video=vid)
        archive_id = entry.archive_id

    if not entry:
        # fallback when manifest is missing the entry: still allow pull by id
        entry = ManifestEntry(archive_id=archive_id, created_at="", repo="", release_tag="", original_name="", tags=tuple())

    outdir = Path(str(args.outdir or "")).expanduser()
    if not outdir.is_absolute():
        outdir = repo_paths.repo_root() / outdir
    outdir = outdir.resolve()

    extract_dir = Path(str(args.extract_dir or "")).expanduser() if str(args.extract_dir or "").strip() else None
    if extract_dir:
        if not extract_dir.is_absolute():
            extract_dir = outdir / extract_dir
        extract_dir = extract_dir.resolve()
    else:
        extract_dir = (outdir / "unpacked" / archive_id).resolve()

    repo_override = str(args.repo or "").strip()
    pull_cmd = [
        sys.executable,
        str(repo_paths.repo_root() / "scripts" / "ops" / "release_archive.py"),
        "pull",
        archive_id,
        "--archive-dir",
        str(archive_dir),
        "--outdir",
        str(outdir),
    ]
    if repo_override:
        pull_cmd += ["--repo", repo_override]

    expected_name = str(entry.original_name or "").strip()
    if expected_name:
        expected_path = outdir / expected_name
    else:
        expected_path = outdir / f"{archive_id}.bin"

    mode = "RUN" if bool(args.run) else "DRY"
    print(f"[restore_episode_asset_pack] mode={mode} archive_id={archive_id}")
    if entry.tags:
        print(f"- tags       : {', '.join(entry.tags)}")
    if expected_name:
        print(f"- bundle_name: {expected_name}")
    print(f"- outdir     : {outdir}")
    print(f"- extract_dir: {extract_dir}")
    print(f"- pull       : {' '.join(pull_cmd)}")
    if bool(args.write_pack):
        print("- write_pack : ON")
        print(f"- overwrite  : {bool(args.overwrite_pack)}")
    else:
        print("- write_pack : OFF")

    if not bool(args.run):
        return 0

    _require_no_blocking_lock(outdir, when="download/outdir")
    _require_no_blocking_lock(extract_dir, when="extract/extract_dir")

    env = dict(os.environ)
    env["LLM_AGENT_NAME"] = str(os.getenv("LLM_AGENT_NAME") or os.getenv("AGENT_NAME") or "").strip()
    _run_passthrough(pull_cmd, env=env)

    if not expected_path.exists():
        # best-effort: fallback to "newest tgz in outdir"
        tgz = sorted(outdir.glob("*.tgz"), key=lambda p: p.stat().st_mtime, reverse=True)
        if tgz:
            expected_path = tgz[0]
    if not expected_path.exists():
        raise EpisodeAssetPackRestoreError(f"downloaded bundle not found in outdir: {outdir}")

    if not expected_path.name.endswith((".tgz", ".tar.gz")):
        raise EpisodeAssetPackRestoreError(f"not a tgz bundle: {expected_path.name}")

    _safe_extract_tgz(tgz_path=expected_path, extract_dir=extract_dir)
    print(f"[OK] extracted: {extract_dir}")

    if bool(args.write_pack):
        # Expect the tgz to contain "{CHxx}/{NNN}/..."
        ch = _norm_channel(args.channel) if str(args.channel or "").strip() else ""
        vid = _norm_video(args.video) if str(args.video or "").strip() else ""
        if not (ch and vid) and entry.tags:
            for t in entry.tags:
                s = str(t or "").strip()
                if s.startswith("channel:") and not ch:
                    ch = _norm_channel(s.split(":", 1)[1])
                if s.startswith("video:") and not vid:
                    vid = _norm_video(s.split(":", 1)[1])
        if not (ch and vid):
            m = re.search(r"(CH\\d{2})-(\\d{3})", expected_path.name.upper())
            if m:
                ch = _norm_channel(m.group(1))
                vid = _norm_video(m.group(2))
        if not (ch and vid):
            raise EpisodeAssetPackRestoreError("missing --channel/--video for --write-pack (cannot infer from manifest or filename)")

        src_pack = extract_dir / ch / vid
        if not src_pack.exists():
            raise EpisodeAssetPackRestoreError(f"expected pack dir not found after extract: {src_pack}")
        dest_pack = repo_paths.video_episode_assets_dir(ch, vid)

        _require_no_blocking_lock(dest_pack, when="write/pack_dir")
        _copy_tree_atomic(src=src_pack, dest=dest_pack, overwrite=bool(args.overwrite_pack))
        print(f"[OK] pack_dir: {dest_pack}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EpisodeAssetPackRestoreError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2)
