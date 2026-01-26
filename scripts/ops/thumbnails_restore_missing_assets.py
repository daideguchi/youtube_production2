#!/usr/bin/env python3
"""
thumbnails_restore_missing_assets.py — restore missing thumbnail asset files by copying from known sources.

Use-case:
- During incidents, non-selected thumbnail variants may disappear from:
    workspaces/thumbnails/assets/{CH}/{NNN}/*.png
- We must NOT "pretend it never happened" by deleting variant references, and we must NOT overwrite.

This tool:
- Reads a scan report (schema: ytm.ops.thumbnails.scan_missing_assets.v1).
- For each missing image_path, tries source roots in order and copies the file into the local workspace.
- Never overwrites existing destination files unless explicitly allowed.
- Writes a JSON report under workspaces/logs/ops/thumbnails_restore/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from _bootstrap import bootstrap

bootstrap(load_env=True)

from factory_common import paths as fpaths  # noqa: E402


REPORT_SCHEMA = "ytm.ops.thumbnails.restore_missing_assets.v1"
EXPECTED_SCAN_SCHEMA = "ytm.ops.thumbnails.scan_missing_assets.v1"
DOCS_PREVIEW_EXTS = ("jpg", "jpeg", "png", "webp")


def _now_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _copy_atomic(src: Path, dest: Path) -> None:
    _ensure_dir(dest.parent)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
    except Exception:
        pass
    shutil.copyfile(src, tmp)
    os.replace(str(tmp), str(dest))


def _convert_to_png_atomic(src: Path, dest: Path) -> None:
    _ensure_dir(dest.parent)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
    except Exception:
        pass
    from PIL import Image  # noqa: WPS433

    with Image.open(src) as im:
        im.save(str(tmp), format="PNG", optimize=True)
    os.replace(str(tmp), str(dest))


def _logs_dir() -> Path:
    return fpaths.logs_root() / "ops" / "thumbnails_restore"


def _default_scan_path() -> Optional[Path]:
    d = _logs_dir()
    if not d.exists():
        return None
    scans = sorted(d.glob("scan_missing_thumbnails_assets__ALL__*.json"))
    return scans[-1] if scans else None


def _default_docs_previews_root() -> Path:
    return fpaths.repo_root() / "docs" / "media" / "thumbs"


def _find_docs_preview(*, docs_root: Path, channel: str, video: str) -> Optional[Path]:
    ch = str(channel).upper()
    vid = str(video).zfill(3)
    for ext in DOCS_PREVIEW_EXTS:
        cand = docs_root / ch / f"{vid}.{ext}"
        try:
            if cand.exists() and cand.is_file():
                return cand
        except Exception:
            continue
    return None


@dataclass(frozen=True)
class MissingItem:
    channel: str
    video: str
    variant_id: str
    image_path: str  # relative under thumbnails/assets

    @property
    def dest_path(self) -> Path:
        return fpaths.thumbnails_root() / "assets" / self.image_path


def _iter_missing(scan_doc: Dict[str, Any]) -> Iterable[MissingItem]:
    report = scan_doc.get("report")
    if not isinstance(report, dict):
        return
    for ch, entry in report.items():
        if not isinstance(entry, dict):
            continue
        for it in entry.get("missing", []) or []:
            if not isinstance(it, dict):
                continue
            image_path = str(it.get("image_path") or "").strip().lstrip("/")
            if not image_path:
                continue
            yield MissingItem(
                channel=str(ch),
                video=str(it.get("video") or ""),
                variant_id=str(it.get("variant_id") or ""),
                image_path=image_path.replace("\\", "/"),
            )


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Restore missing thumbnails assets by copying from sources.")
    ap.add_argument(
        "--scan",
        default="",
        help="Path to scan report JSON (default: latest scan_missing_thumbnails_assets__ALL__*.json).",
    )
    ap.add_argument(
        "--source-root",
        action="append",
        default=[],
        help="Source root that contains thumbnails/assets/{image_path}. Can be specified multiple times (priority order).",
    )
    ap.add_argument(
        "--use-vault",
        action="store_true",
        help="Also try <vault_workspaces_root>/thumbnails/assets as a source (if configured).",
    )
    ap.add_argument(
        "--use-docs-previews",
        action="store_true",
        help="If not found in source roots, try docs/media/thumbs/<CHxx>/<NNN>.<ext> and convert to PNG (no overwrite).",
    )
    ap.add_argument(
        "--docs-previews-root",
        default="",
        help="Override docs previews root (default: <repo_root>/docs/media/thumbs).",
    )
    ap.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="Allow overwriting existing destination files (NOT recommended; default: skip).",
    )
    ap.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="Ignore specific image_path (can be repeated). Example: CH06/191/1.png",
    )
    ap.add_argument("--dry-run", action="store_true", help="Report only (default: dry-run unless --run).")
    ap.add_argument("--run", action="store_true", help="Apply copy operations.")
    args = ap.parse_args()

    scan_path = Path(args.scan).expanduser() if str(args.scan).strip() else (_default_scan_path() or None)
    if scan_path is None:
        raise SystemExit("scan report not found (pass --scan explicitly)")
    if not scan_path.exists():
        raise SystemExit(f"scan report not found: {scan_path}")

    scan_doc = _load_json(scan_path)
    schema = str(scan_doc.get("schema") or "").strip()
    if schema != EXPECTED_SCAN_SCHEMA:
        raise SystemExit(f"unexpected scan schema: {schema} (expected: {EXPECTED_SCAN_SCHEMA})")

    sources: List[Path] = []
    for raw in args.source_root:
        if not raw:
            continue
        sources.append(Path(raw).expanduser())

    if args.use_vault:
        vault = fpaths.vault_workspaces_root()
        if vault is not None:
            sources.append(vault / "thumbnails" / "assets")

    docs_previews_root: Optional[Path] = None
    if args.use_docs_previews:
        docs_previews_root = (
            Path(args.docs_previews_root).expanduser()
            if str(args.docs_previews_root).strip()
            else _default_docs_previews_root()
        )

    ignore_set = {str(p).strip().lstrip("/").replace("\\", "/") for p in (args.ignore or []) if str(p).strip()}

    run = bool(args.run) and not bool(args.dry_run)
    stamp = _now_stamp()
    out_report = _logs_dir() / f"restore_missing_thumbnails_assets__{stamp}.json"
    _ensure_dir(out_report.parent)

    results: List[Dict[str, Any]] = []
    stats = {
        "missing_total": 0,
        "restored": 0,
        "restored_from_sources": 0,
        "restored_from_docs_previews": 0,
        "skipped_exists": 0,
        "not_found": 0,
        "errors": 0,
    }

    for item in _iter_missing(scan_doc):
        if item.image_path in ignore_set:
            continue
        stats["missing_total"] += 1

        dest = item.dest_path
        try:
            if dest.exists() and not args.allow_overwrite:
                stats["skipped_exists"] += 1
                results.append(
                    {
                        "image_path": item.image_path,
                        "status": "skipped_exists",
                        "dest": str(dest),
                    }
                )
                continue
        except Exception as exc:
            stats["errors"] += 1
            results.append({"image_path": item.image_path, "status": "error", "error": str(exc)})
            continue

        src_hit: Optional[Path] = None
        for root in sources:
            cand = root / item.image_path
            try:
                if cand.exists() and cand.is_file():
                    src_hit = cand
                    break
            except Exception:
                continue

        if src_hit is None:
            docs_hit = (
                _find_docs_preview(docs_root=docs_previews_root, channel=item.channel, video=item.video)
                if docs_previews_root is not None
                else None
            )
            if docs_hit is None:
                stats["not_found"] += 1
                results.append({"image_path": item.image_path, "status": "not_found"})
                continue

            try:
                if run:
                    _convert_to_png_atomic(docs_hit, dest)
                entry = {
                    "image_path": item.image_path,
                    "status": "restored_from_docs_preview" if run else "would_restore_from_docs_preview",
                    "src_kind": "docs_preview",
                    "src": str(docs_hit),
                    "dest": str(dest),
                    "src_sha256": _sha256(docs_hit),
                }
                if run:
                    entry["dest_sha256"] = _sha256(dest)
                    entry["dest_size_bytes"] = int(dest.stat().st_size)
                results.append(entry)
                stats["restored"] += 1
                stats["restored_from_docs_previews"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                results.append({"image_path": item.image_path, "status": "error", "src": str(docs_hit), "error": str(exc)})
            continue

        try:
            if run:
                _copy_atomic(src_hit, dest)
            entry = {
                "image_path": item.image_path,
                "status": "restored" if run else "would_restore",
                "src_kind": "source_root",
                "src": str(src_hit),
                "dest": str(dest),
                "src_sha256": _sha256(src_hit),
            }
            if run:
                entry["dest_sha256"] = _sha256(dest)
                entry["dest_size_bytes"] = int(dest.stat().st_size)
            results.append(entry)
            stats["restored"] += 1
            stats["restored_from_sources"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            results.append({"image_path": item.image_path, "status": "error", "src": str(src_hit), "error": str(exc)})

    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "run" if run else "dry_run",
        "scan_path": str(scan_path),
        "sources": [str(p) for p in sources],
        "use_docs_previews": bool(docs_previews_root is not None),
        "docs_previews_root": str(docs_previews_root) if docs_previews_root is not None else None,
        "stats": stats,
        "results": results,
    }
    out_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[restore_missing_assets] wrote {out_report}")
    print(f"[restore_missing_assets] stats: {stats}")
    return 0 if stats["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
