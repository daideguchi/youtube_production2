#!/usr/bin/env python3
from __future__ import annotations

"""
release_archive.py — GitHub Releases を大容量アーカイブ置き場にする（manifest運用）

SSOT: `ssot/ops/OPS_GH_RELEASES_ARCHIVE.md`

Notes:
- 実体（mp4/wav/zip 等）は Releases assets へ退避し、repoには目録（manifest/index）のみ残す。
- 1ファイル < 2GiB 運用のため、必要なら chunk に分割して upload する。
- secrets は扱わない（gh auth に依存）。manifest に秘匿性が高い情報を入れない。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import repo_root  # noqa: E402

JST = timezone(timedelta(hours=9))

DEFAULT_CHUNK_SIZE_BYTES = 1_900_000_000  # < 2GiB
DEFAULT_LATEST_N = 200


class ReleaseArchiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedPaths:
    archive_dir: Path
    manifest_path: Path
    index_dir: Path
    latest_index_path: Path
    by_tag_dir: Path


def _root() -> Path:
    return repo_root()


def _resolve_archive_paths(archive_dir_raw: str | None) -> ResolvedPaths:
    if archive_dir_raw:
        archive_dir = Path(str(archive_dir_raw)).expanduser()
        if not archive_dir.is_absolute():
            archive_dir = _root() / archive_dir
    else:
        archive_dir = _root() / "gh_releases_archive"

    manifest_path = archive_dir / "manifest" / "manifest.jsonl"
    index_dir = archive_dir / "index"
    latest_index_path = index_dir / "latest.json"
    by_tag_dir = index_dir / "by_tag"
    return ResolvedPaths(
        archive_dir=archive_dir,
        manifest_path=manifest_path,
        index_dir=index_dir,
        latest_index_path=latest_index_path,
        by_tag_dir=by_tag_dir,
    )


def _now_jst() -> datetime:
    return datetime.now(JST)


def _today_jst_str() -> str:
    return _now_jst().strftime("%Y-%m-%d")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_gh() -> None:
    if shutil.which("gh") is None:
        raise ReleaseArchiveError("gh (GitHub CLI) not found. Install https://github.com/cli/cli")


def _run_capture(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd or _root()),
        check=False,
        capture_output=True,
        text=True,
    )


def _run_passthrough(cmd: list[str], *, cwd: Path | None = None) -> None:
    p = subprocess.run(cmd, cwd=str(cwd or _root()))
    if p.returncode != 0:
        raise ReleaseArchiveError(f"command failed (exit={p.returncode}): {' '.join(cmd)}")


def _parse_github_repo(remote_url: str) -> str | None:
    s = str(remote_url or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"git@github\.com:(?P<repo>[^/]+/[^/]+?)(?:\.git)?", s)
    if m:
        return m.group("repo")
    m = re.fullmatch(r"https?://github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?", s)
    if m:
        return m.group("repo")
    return None


def _infer_repo_from_git_origin() -> str | None:
    try:
        cp = _run_capture(["git", "remote", "get-url", "origin"], cwd=_root())
        if cp.returncode != 0:
            return None
        return _parse_github_repo(cp.stdout.strip())
    except Exception:
        return None


def _resolve_repo(repo_raw: str | None) -> str:
    if repo_raw:
        return str(repo_raw).strip()
    env_repo = str(os.environ.get("ARCHIVE_REPO") or "").strip()
    if env_repo:
        return env_repo
    inferred = _infer_repo_from_git_origin()
    if inferred:
        return inferred
    raise ReleaseArchiveError("set --repo or ARCHIVE_REPO (cannot infer from git remote origin)")


def _resolve_chunk_size_bytes(chunk_size_raw: int | None) -> int:
    if chunk_size_raw and int(chunk_size_raw) > 0:
        return int(chunk_size_raw)
    env_raw = str(os.environ.get("CHUNK_SIZE_BYTES") or "").strip()
    if env_raw.isdigit() and int(env_raw) > 0:
        return int(env_raw)
    return int(DEFAULT_CHUNK_SIZE_BYTES)


def _sanitize_stem(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(name))
    s = s.strip("_")
    return s or "file"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def _manifest_lock(paths: ResolvedPaths) -> Iterator[None]:
    lock_path = paths.manifest_path.parent / "manifest.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = lock_path.open("a", encoding="utf-8")
    try:
        try:
            import fcntl  # unix-only

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        yield
    finally:
        try:
            import fcntl  # unix-only

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            f.close()
        except Exception:
            pass


def _iter_manifest_entries(manifest_path: Path) -> Iterator[dict[str, Any]]:
    if not manifest_path.exists():
        return
    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _max_seq_for_date(manifest_path: Path, today: str) -> int:
    pat = re.compile(rf"^A-{re.escape(today)}-(\d{{4}})$")
    max_seq = 0
    for obj in _iter_manifest_entries(manifest_path):
        aid = str(obj.get("archive_id") or "")
        m = pat.match(aid)
        if not m:
            continue
        try:
            max_seq = max(max_seq, int(m.group(1)))
        except Exception:
            continue
    return max_seq


def _allocate_archive_id(manifest_path: Path, *, today: str) -> str:
    seq = _max_seq_for_date(manifest_path, today) + 1
    return f"A-{today}-{seq:04d}"


def _release_tag_for_today(today: str) -> str:
    return f"arch-{today}"


def _ensure_release_exists(*, repo: str, release_tag: str, dry_run: bool) -> None:
    if dry_run:
        return
    cp = _run_capture(["gh", "release", "view", release_tag, "-R", repo], cwd=_root())
    if cp.returncode == 0:
        return
    _run_passthrough(
        ["gh", "release", "create", release_tag, "-R", repo, "-t", release_tag, "-n", f"Daily archive bucket: {release_tag}"],
        cwd=_root(),
    )


def _sorted_asset_names(names: Iterable[str]) -> list[str]:
    def key(n: str) -> tuple[int, int, str]:
        if n.endswith("__full.bin"):
            return (0, 0, n)
        m = re.search(r"__part-(\d{4})\\.bin$", n)
        if m:
            return (1, int(m.group(1)), n)
        return (2, 0, n)

    return sorted([str(x) for x in names], key=key)


def _chunk_file(
    *,
    src_path: Path,
    out_dir: Path,
    asset_prefix: str,
    chunk_size_bytes: int,
) -> tuple[str, int, list[dict[str, Any]], list[Path]]:
    size_bytes = int(src_path.stat().st_size)
    orig_hasher = hashlib.sha256()
    chunk_meta: list[dict[str, Any]] = []
    chunk_paths: list[Path] = []

    buf_size = 1024 * 1024
    with src_path.open("rb") as rf:
        if size_bytes <= chunk_size_bytes:
            out_path = out_dir / f"{asset_prefix}__full.bin"
            chunk_hasher = hashlib.sha256()
            with out_path.open("wb") as wf:
                for buf in iter(lambda: rf.read(buf_size), b""):
                    orig_hasher.update(buf)
                    chunk_hasher.update(buf)
                    wf.write(buf)
            chunk_meta.append(
                {
                    "name": out_path.name,
                    "size_bytes": int(out_path.stat().st_size),
                    "sha256": chunk_hasher.hexdigest(),
                }
            )
            chunk_paths.append(out_path)
        else:
            part = 1
            while True:
                out_path = out_dir / f"{asset_prefix}__part-{part:04d}.bin"
                chunk_hasher = hashlib.sha256()
                remaining = int(chunk_size_bytes)
                wrote_any = False
                with out_path.open("wb") as wf:
                    while remaining > 0:
                        buf = rf.read(min(buf_size, remaining))
                        if not buf:
                            break
                        wrote_any = True
                        orig_hasher.update(buf)
                        chunk_hasher.update(buf)
                        wf.write(buf)
                        remaining -= len(buf)
                if not wrote_any:
                    out_path.unlink(missing_ok=True)
                    break
                chunk_meta.append(
                    {
                        "name": out_path.name,
                        "size_bytes": int(out_path.stat().st_size),
                        "sha256": chunk_hasher.hexdigest(),
                    }
                )
                chunk_paths.append(out_path)
                part += 1

    if not chunk_paths:
        raise ReleaseArchiveError("chunking produced no files")

    return orig_hasher.hexdigest(), size_bytes, chunk_meta, chunk_paths


def _upload_assets(*, repo: str, release_tag: str, chunk_paths: list[Path], dry_run: bool) -> None:
    if dry_run:
        return
    for p in chunk_paths:
        _run_passthrough(["gh", "release", "upload", release_tag, str(p), "-R", repo, "--clobber"], cwd=_root())


def _download_assets(*, repo: str, release_tag: str, asset_names: list[str], out_dir: Path, dry_run: bool) -> None:
    if dry_run:
        return
    for name in asset_names:
        _run_passthrough(["gh", "release", "download", release_tag, "-R", repo, "-p", name, "-D", str(out_dir)], cwd=_root())


def _slim_for_index(item: dict[str, Any]) -> dict[str, Any]:
    original = item.get("original") if isinstance(item.get("original"), dict) else {}
    return {
        "archive_id": item.get("archive_id"),
        "created_at": item.get("created_at"),
        "repo": item.get("repo"),
        "release_tag": item.get("release_tag"),
        "original_name": (original or {}).get("name"),
        "original_size_bytes": (original or {}).get("size_bytes"),
        "original_sha256": (original or {}).get("sha256"),
        "tags": item.get("tags") or [],
        "note": item.get("note") or "",
    }


def _tag_filename(tag: str) -> str:
    norm = _sanitize_stem(tag)
    return f"tag_{norm}.json"


def build_index(*, paths: ResolvedPaths, latest_n: int, dry_run: bool) -> None:
    items = list(_iter_manifest_entries(paths.manifest_path))
    items.sort(key=lambda x: str(x.get("created_at") or ""))
    latest = [_slim_for_index(x) for x in items[-int(latest_n) :]][::-1]

    by_tag: dict[str, list[dict[str, Any]]] = {}
    for x in items:
        s = _slim_for_index(x)
        for t in s.get("tags") or []:
            tag = str(t)
            by_tag.setdefault(tag, []).append(s)

    if dry_run:
        print(json.dumps({"latest_count": len(latest), "tag_count": len(by_tag)}, ensure_ascii=False, indent=2))
        return

    paths.latest_index_path.parent.mkdir(parents=True, exist_ok=True)
    paths.by_tag_dir.mkdir(parents=True, exist_ok=True)
    paths.latest_index_path.write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for tag, lst in by_tag.items():
        lst_sorted = sorted(lst, key=lambda x: str(x.get("created_at") or ""), reverse=True)
        out_path = paths.by_tag_dir / _tag_filename(tag)
        out_path.write_text(json.dumps(lst_sorted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_build_index(args: argparse.Namespace) -> int:
    paths = _resolve_archive_paths(args.archive_dir)
    latest_n = int(args.latest_n or DEFAULT_LATEST_N)
    build_index(paths=paths, latest_n=latest_n, dry_run=bool(args.dry_run))
    if not args.dry_run:
        print("OK:", str(paths.latest_index_path))
        print("OK:", str(paths.by_tag_dir))
    return 0


def _find_manifest_entry(manifest_path: Path, archive_id: str) -> dict[str, Any] | None:
    aid = str(archive_id).strip()
    if not aid:
        return None
    for obj in _iter_manifest_entries(manifest_path):
        if str(obj.get("archive_id") or "") == aid:
            return obj
    return None


def cmd_list(args: argparse.Namespace) -> int:
    paths = _resolve_archive_paths(args.archive_dir)
    query = str(args.query or "").strip().lower()
    tag = str(args.tag or "").strip()
    limit = int(args.limit or 50)

    items = list(_iter_manifest_entries(paths.manifest_path))
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)

    def match(x: dict[str, Any]) -> bool:
        if tag:
            tags = x.get("tags") or []
            if tag not in tags:
                return False
        if not query:
            return True
        original = x.get("original") if isinstance(x.get("original"), dict) else {}
        hay = " ".join(
            [
                str(x.get("archive_id") or ""),
                str((original or {}).get("name") or ""),
                str(x.get("note") or ""),
                ",".join([str(t) for t in (x.get("tags") or [])]),
            ]
        ).lower()
        return query in hay

    hits = [x for x in items if match(x)][:limit]
    print("archive_id\tcreated_at\tsize_bytes\toriginal_name\ttags\tnote")
    for x in hits:
        original = x.get("original") if isinstance(x.get("original"), dict) else {}
        print(
            "\t".join(
                [
                    str(x.get("archive_id") or ""),
                    str(x.get("created_at") or ""),
                    str((original or {}).get("size_bytes") or ""),
                    str((original or {}).get("name") or ""),
                    ",".join([str(t) for t in (x.get("tags") or [])]),
                    str(x.get("note") or "").replace("\t", " ").replace("\n", " ")[:120],
                ]
            )
        )
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    _require_gh()
    src_path = Path(str(args.file)).expanduser()
    if not src_path.exists() or not src_path.is_file():
        raise ReleaseArchiveError(f"file not found: {src_path}")

    paths = _resolve_archive_paths(args.archive_dir)
    repo = _resolve_repo(args.repo)
    today = _today_jst_str()
    release_tag = _release_tag_for_today(today)
    chunk_size_bytes = _resolve_chunk_size_bytes(args.chunk_size_bytes)
    note = str(args.note or "").strip()
    tags = [t.strip() for t in str(args.tags or "").split(",") if t.strip()]
    dry_run = bool(args.dry_run)

    paths.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    paths.index_dir.mkdir(parents=True, exist_ok=True)
    paths.by_tag_dir.mkdir(parents=True, exist_ok=True)

    with _manifest_lock(paths):
        archive_id = str(args.archive_id or "").strip() or _allocate_archive_id(paths.manifest_path, today=today)

    stem = _sanitize_stem(src_path.stem)
    asset_prefix = f"{archive_id}__{stem}"

    with tempfile.TemporaryDirectory(prefix="release_archive_") as td:
        out_dir = Path(td)
        original_sha256, size_bytes, chunks_meta, chunk_paths = _chunk_file(
            src_path=src_path,
            out_dir=out_dir,
            asset_prefix=asset_prefix,
            chunk_size_bytes=chunk_size_bytes,
        )

        if dry_run:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "repo": repo,
                        "archive_id": archive_id,
                        "release_tag": release_tag,
                        "original": {"name": src_path.name, "size_bytes": size_bytes, "sha256": original_sha256},
                        "chunk_count": len(chunks_meta),
                        "chunk_size_bytes": int(chunk_size_bytes),
                        "tags": tags,
                        "note": note,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        _ensure_release_exists(repo=repo, release_tag=release_tag, dry_run=False)
        _upload_assets(repo=repo, release_tag=release_tag, chunk_paths=chunk_paths, dry_run=False)

    entry: dict[str, Any] = {
        "archive_id": archive_id,
        "created_at": _utc_iso(),
        "repo": repo,
        "release_tag": release_tag,
        "original": {"name": src_path.name, "size_bytes": size_bytes, "sha256": original_sha256},
        "chunks": chunks_meta,
        "chunk_size_bytes": int(chunk_size_bytes),
        "tags": tags,
        "note": note,
    }

    with _manifest_lock(paths):
        paths.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if not paths.manifest_path.exists():
            paths.manifest_path.write_text("", encoding="utf-8")
        with paths.manifest_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if not bool(args.no_index):
        build_index(paths=paths, latest_n=int(args.latest_n or DEFAULT_LATEST_N), dry_run=False)

    print("OK: archive_id =", archive_id)
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    _require_gh()
    paths = _resolve_archive_paths(args.archive_dir)
    archive_id = str(args.archive_id).strip()
    entry = _find_manifest_entry(paths.manifest_path, archive_id)
    if entry is None:
        raise ReleaseArchiveError(f"archive_id not found: {archive_id}")

    repo = str(args.repo or "").strip() or str(entry.get("repo") or "").strip() or _resolve_repo(None)
    release_tag = str(entry.get("release_tag") or "").strip()
    if not release_tag:
        raise ReleaseArchiveError("manifest entry missing release_tag")

    original = entry.get("original") if isinstance(entry.get("original"), dict) else {}
    original_name = str((original or {}).get("name") or f"{archive_id}.bin")
    original_sha256 = str((original or {}).get("sha256") or "").strip()
    if not original_sha256:
        raise ReleaseArchiveError("manifest entry missing original.sha256")

    outdir = Path(str(args.outdir or "")).expanduser() if args.outdir else Path.cwd()
    if args.output_path:
        out_path = Path(str(args.output_path)).expanduser()
        if not out_path.is_absolute():
            out_path = outdir / out_path
    else:
        out_path = outdir / original_name
    out_path.parent.mkdir(parents=True, exist_ok=True)

    chunks = entry.get("chunks") if isinstance(entry.get("chunks"), list) else []
    asset_names = [str(x.get("name") or "") for x in chunks if isinstance(x, dict)]
    asset_names = [n for n in asset_names if n]
    if not asset_names:
        raise ReleaseArchiveError("manifest entry missing chunks[].name")
    asset_names = _sorted_asset_names(asset_names)

    if bool(args.dry_run):
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "archive_id": archive_id,
                    "repo": repo,
                    "release_tag": release_tag,
                    "assets": asset_names,
                    "output_path": str(out_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    with tempfile.TemporaryDirectory(prefix="release_archive_pull_") as td:
        tmp_dir = Path(td)
        _download_assets(repo=repo, release_tag=release_tag, asset_names=asset_names, out_dir=tmp_dir, dry_run=False)

        # verify chunk sha256
        for meta in chunks:
            if not isinstance(meta, dict):
                continue
            name = str(meta.get("name") or "")
            expected = str(meta.get("sha256") or "")
            if not name:
                continue
            p = tmp_dir / name
            if not p.exists():
                raise ReleaseArchiveError(f"download missing: {p}")
            if expected:
                got = _sha256_file(p)
                if got != expected:
                    raise ReleaseArchiveError(f"chunk sha256 mismatch: {name} expected={expected} got={got}")

        # concat
        h = hashlib.sha256()
        with out_path.open("wb") as wf:
            for name in asset_names:
                p = tmp_dir / name
                with p.open("rb") as rf:
                    for buf in iter(lambda: rf.read(1024 * 1024), b""):
                        h.update(buf)
                        wf.write(buf)
        got = h.hexdigest()
        if got != original_sha256:
            raise ReleaseArchiveError(f"original sha256 mismatch: expected={original_sha256} got={got}")

    print("OK:", str(out_path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="release_archive.py", description="Archive big files via GitHub Releases + manifest/index")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--archive-dir", default="", help="base dir for manifest/index (default: repo/gh_releases_archive)")

    sp = sub.add_parser("push", parents=[common], help="upload a local file to GitHub Releases and append manifest")
    sp.add_argument("file", help="local file path")
    sp.add_argument("--repo", default="", help="GitHub repo (OWNER/REPO). default: ARCHIVE_REPO env or git origin")
    sp.add_argument("--note", default="", help="free note stored in manifest")
    sp.add_argument("--tags", default="", help="comma-separated tags stored in manifest")
    sp.add_argument("--chunk-size-bytes", dest="chunk_size_bytes", type=int, default=0)
    sp.add_argument("--archive-id", default="", help="optional fixed archive id (default: auto A-YYYY-MM-DD-#### JST)")
    sp.add_argument("--no-index", action="store_true", help="skip index rebuild after push")
    sp.add_argument("--latest-n", type=int, default=DEFAULT_LATEST_N, help="latest N for index rebuild (default: 200)")
    sp.add_argument("--dry-run", action="store_true", help="compute chunks/hashes but do not upload or write manifest")
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("pull", parents=[common], help="restore a file by archive_id (download + verify + concat)")
    sp.add_argument("archive_id", help="e.g. A-2026-01-10-0007")
    sp.add_argument("--repo", default="", help="GitHub repo (OWNER/REPO). default: manifest.repo / ARCHIVE_REPO / git origin")
    sp.add_argument("--outdir", default="", help="output directory (default: cwd)")
    sp.add_argument("--output-path", default="", help="optional full output path (default: outdir/original.name)")
    sp.add_argument("--dry-run", action="store_true", help="print plan only (no download/write)")
    sp.set_defaults(func=cmd_pull)

    sp = sub.add_parser("build-index", parents=[common], help="rebuild index files from manifest")
    sp.add_argument("--latest-n", type=int, default=DEFAULT_LATEST_N)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_build_index)

    sp = sub.add_parser("list", parents=[common], help="list/search manifest entries (tsv)")
    sp.add_argument("--query", default="", help="substring match across id/name/tags/note")
    sp.add_argument("--tag", default="", help="filter by exact tag")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_list)

    return p


def main(argv: list[str]) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.func(args))
    except ReleaseArchiveError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[CANCEL] interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

