#!/usr/bin/env python3
"""
research_genre_index.py — workspaces/research をジャンル軸で“迷わず参照できる”状態にする索引生成

狙い:
  - workspaces/research はジャンル別に雑多な資料が集まりやすい
  - チャンネルのベンチマーク（SoT: channel_info.json -> benchmarks.script_samples）から
    「どのジャンルがどのCHで参照されているか」を逆引きできる索引を作る
  - 既存ファイルは移動しない（参照切れ事故を防ぐ）。INDEX.md を追加して“整理”する。

出力（--apply の場合）:
  - workspaces/research/INDEX.md
  - workspaces/research/<genre>/INDEX.md

運用:
  - 更新したい時に実行:
      python3 scripts/ops/research_genre_index.py --apply
  - 手動メモは INDEX.md の <!-- MANUAL START/END --> ブロック内に書く（自動生成で保持される）
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from _bootstrap import bootstrap

MANUAL_START = "<!-- MANUAL START -->"
MANUAL_END = "<!-- MANUAL END -->"
ROOT_GENRE = "(root)"
DEFAULT_MANUAL_PLACEHOLDER = "（ここは手動メモ。自動生成で保持されます）"
INBOX_DIR = "INBOX"

ROOT_MANUAL_TEMPLATE = """## 運用メモ（手動）
- 追加する資料は原則ジャンル配下へ（例: `ブッダ系/`）。
- チャンネル側の参照SoT: `packages/script_pipeline/channels/CHxx-*/channel_info.json` の `benchmarks`。
- INDEX 更新: `python3 scripts/ops/research_genre_index.py --apply`
"""

GENRE_MANUAL_TEMPLATE = """## 勝ちパターン（要約）
- （ここに「強フック→何→どう締める」などを1〜5行で）

## NG/注意（地雷）
- （誇大、断定、尺、口調、禁則など）

## 企画ネタ（ストック）
- （タイトル案、フック案）

## 未整理メモ（inbox）
- （雑多な気づきをここに投げて、後で整理）
"""

INBOX_MANUAL_TEMPLATE = """## 使い方（手動）
- 迷った資料は一旦ここ（INBOX）へ置く。
- 仕分けが決まったら該当ジャンルへ移動し、必要なら `channel_info.json:benchmarks.script_samples` に紐付ける。
- 「参照するチャンネルが決まった資料」は INBOX から外す（ここには未整理だけ残す）。
"""

FOLDER_MANUAL_TEMPLATE = """## メモ（手動）
- （このフォルダの目的・運用ルールを1〜5行で）
"""


@dataclass(frozen=True)
class ChannelRef:
    channel_code: str
    channel_name: str
    sample_path: str
    label: str
    note: Optional[str]
    exists: bool


@dataclass(frozen=True)
class FsEntry:
    rel_path: str
    kind: str  # FILE | DIR
    size_bytes: Optional[int]
    modified_utc: Optional[str]
    children_count: Optional[int]


@dataclass(frozen=True)
class CompetitorRef:
    channel_code: str
    channel_name: str
    handle: Optional[str]
    name: Optional[str]
    url: Optional[str]
    note: Optional[str]


def _compare_channel_code(code: str) -> Tuple[int, str]:
    digits = "".join(ch for ch in code if ch.isdigit())
    if digits:
        try:
            return int(digits), code
        except Exception:
            return 10_000, code
    return 10_000, code


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_channel_info_paths(*, script_pkg: Path) -> List[Path]:
    base = script_pkg / "channels"
    if not base.exists():
        return []
    out: List[Path] = []
    for fp in sorted(base.glob("CH*-*/channel_info.json")):
        if fp.is_file():
            out.append(fp)
    return out


def _extract_manual_block(existing: str) -> str:
    if not existing:
        return ""
    start = existing.find(MANUAL_START)
    end = existing.find(MANUAL_END)
    if start == -1 or end == -1 or end <= start:
        return ""
    inner = existing[start + len(MANUAL_START) : end]
    return inner.strip("\n")


def _render_manual_block(existing: str, template: str) -> str:
    preserved = _extract_manual_block(existing).strip("\n")
    if not preserved.strip() or preserved.strip() == DEFAULT_MANUAL_PLACEHOLDER:
        body = template.strip("\n")
    else:
        body = preserved.strip("\n")
    return "\n".join([MANUAL_START, body, MANUAL_END])


def _safe_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _collect_genres(*, base: Path) -> List[str]:
    out: List[str] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            continue
        if child.name == INBOX_DIR:
            continue
        out.append(child.name)
    return out


def _resolve_genre_from_rel(rel: str) -> str:
    normalized = rel.strip().lstrip("/")
    if "/" not in normalized:
        return ROOT_GENRE
    return normalized.split("/", 1)[0]


def _safe_norm_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_normalize_handle(value: Any) -> Optional[str]:
    handle = _safe_norm_str(value)
    if not handle:
        return None
    h = handle.strip()
    if not h:
        return None
    return h if h.startswith("@") else f"@{h}"


def _collect_channel_refs(*, research_base: Path, script_pkg: Path) -> tuple[Dict[str, List[ChannelRef]], Dict[str, List[CompetitorRef]]]:
    """
    Returns:
      genre -> list[ChannelRef]
    """
    refs: Dict[str, List[ChannelRef]] = {}
    competitors: Dict[str, List[CompetitorRef]] = {}
    for info_path in _iter_channel_info_paths(script_pkg=script_pkg):
        try:
            payload = _read_json(info_path)
        except Exception:
            continue
        channel_code = str(payload.get("channel_id") or payload.get("channel_code") or "").strip()
        if not channel_code:
            continue
        channel_name = str(payload.get("name") or "").strip() or channel_code
        benchmarks = payload.get("benchmarks") if isinstance(payload.get("benchmarks"), dict) else {}
        samples = benchmarks.get("script_samples") if isinstance(benchmarks, dict) else []
        if not isinstance(samples, list):
            continue
        channel_genres: set[str] = set()
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            base = str(sample.get("base") or "").strip()
            if base != "research":
                continue
            rel = str(sample.get("path") or "").strip()
            if not rel:
                continue
            genre = _resolve_genre_from_rel(rel)
            channel_genres.add(genre)
            label = str(sample.get("label") or "").strip() or rel
            note = str(sample.get("note") or "").strip() or None
            exists = (research_base / rel).exists()
            refs.setdefault(genre, []).append(
                ChannelRef(
                    channel_code=channel_code,
                    channel_name=channel_name,
                    sample_path=rel,
                    label=label,
                    note=note,
                    exists=exists,
                )
            )
        if not channel_genres:
            channel_genres.add(ROOT_GENRE)
        bench_channels = benchmarks.get("channels") if isinstance(benchmarks, dict) else []
        if isinstance(bench_channels, list):
            for item in bench_channels:
                if not isinstance(item, dict):
                    continue
                handle = _safe_normalize_handle(item.get("handle"))
                name = _safe_norm_str(item.get("name"))
                url = _safe_norm_str(item.get("url"))
                note = _safe_norm_str(item.get("note"))
                if not (handle or url or name or note):
                    continue
                for genre in sorted(channel_genres):
                    competitors.setdefault(genre, []).append(
                        CompetitorRef(
                            channel_code=channel_code,
                            channel_name=channel_name,
                            handle=handle,
                            name=name,
                            url=url,
                            note=note,
                        )
                    )
    for genre, items in refs.items():
        refs[genre] = sorted(items, key=lambda x: (_compare_channel_code(x.channel_code), x.sample_path))
    for genre, items in competitors.items():
        competitors[genre] = sorted(items, key=lambda x: (_compare_channel_code(x.channel_code), x.handle or x.url or x.name or ""))
    return refs, competitors


def _stat_entry(path: Path, *, base: Path) -> FsEntry:
    rel = path.relative_to(base).as_posix()
    if path.is_dir():
        try:
            children = list(path.iterdir())
            count = len(children)
        except Exception:
            count = None
        return FsEntry(rel_path=rel, kind="DIR", size_bytes=None, modified_utc=None, children_count=count)
    try:
        st = path.stat()
        modified = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        return FsEntry(
            rel_path=rel,
            kind="FILE",
            size_bytes=st.st_size,
            modified_utc=modified,
            children_count=None,
        )
    except Exception:
        return FsEntry(rel_path=rel, kind="FILE", size_bytes=None, modified_utc=None, children_count=None)


def _collect_fs_entries(*, directory: Path, base: Path) -> List[FsEntry]:
    if not directory.exists() or not directory.is_dir():
        return []
    entries: List[FsEntry] = []
    for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith("."):
            continue
        if child.name == "INDEX.md":
            continue
        entries.append(_stat_entry(child, base=base))
    return entries


def _render_fs_table(entries: List[FsEntry], *, refs_by_path: Optional[Dict[str, int]] = None) -> List[str]:
    lines: List[str] = []
    lines.append("| kind | path | refs | size | modified | items |")
    lines.append("| --- | --- | ---: | ---: | --- | ---: |")
    for e in entries:
        refs = str((refs_by_path or {}).get(e.rel_path, 0))
        size = f"{e.size_bytes:,}" if isinstance(e.size_bytes, int) else "—"
        modified = e.modified_utc or "—"
        items = f"{e.children_count:,}" if isinstance(e.children_count, int) else "—"
        lines.append(f"| {e.kind} | `{e.rel_path}` | {refs} | {size} | {modified} | {items} |")
    return lines


def _render_competitors(competitors: List[CompetitorRef]) -> List[str]:
    if not competitors:
        return ["- （未登録）"]

    def _key(item: CompetitorRef) -> str:
        if item.handle:
            return f"h:{item.handle.lower()}"
        if item.url:
            return f"u:{item.url}"
        if item.name:
            return f"n:{item.name}"
        return "unknown"

    grouped: Dict[str, List[CompetitorRef]] = {}
    for item in competitors:
        grouped.setdefault(_key(item), []).append(item)

    def _display(item: CompetitorRef) -> str:
        head = item.handle or item.name or item.url or "—"
        name = item.name or None
        url = item.url or None
        parts = [head]
        if name and name != head:
            parts.append(name)
        if url and url != head:
            parts.append(url)
        return " / ".join(parts)

    rows: List[Tuple[int, str, str]] = []
    for key, items in grouped.items():
        chs = sorted({x.channel_code for x in items}, key=_compare_channel_code)
        note = next((x.note for x in items if x.note and x.note.strip()), None)
        note_snip = None
        if note:
            note_clean = " ".join(note.split())
            note_snip = (note_clean[:140] + "…") if len(note_clean) > 140 else note_clean
        display = _display(items[0])
        lines = [f"- `{display}`（参照CH: {', '.join(chs)}）"]
        if note_snip:
            lines.append(f"  - {note_snip}")
        rows.append((len(chs), display, "\n".join(lines)))

    rows.sort(key=lambda x: (-x[0], x[1]))
    out: List[str] = []
    for _, _, block in rows:
        out.extend(block.splitlines())
    return out


def _render_shared_samples(refs: List[ChannelRef]) -> List[str]:
    if not refs:
        return ["- （なし）"]
    grouped: Dict[str, List[ChannelRef]] = {}
    for ref in refs:
        grouped.setdefault(ref.sample_path, []).append(ref)
    rows: List[Tuple[int, str, str]] = []
    for path, items in grouped.items():
        chs = sorted({x.channel_code for x in items}, key=_compare_channel_code)
        exists = all(x.exists for x in items)
        status = "OK" if exists else "MISSING"
        rows.append((len(chs), path, f"- `{path}`（{status} / 参照CH: {', '.join(chs)}）"))
    rows.sort(key=lambda x: (-x[0], x[1]))
    return [row[2] for row in rows]


def _render_root_index(
    *,
    existing_text: str,
    genres: List[str],
    channel_refs: Dict[str, List[ChannelRef]],
    inbox_entries: Optional[List[FsEntry]],
    root_entries: List[FsEntry],
) -> str:
    lines: List[str] = []
    lines.append("# workspaces/research — ジャンル索引（自動生成）")
    lines.append("")
    lines.append("ジャンル軸（現状の構造）を維持したまま、迷わず参照できる `INDEX.md` を生成します。")
    lines.append("")
    lines.append(_render_manual_block(existing_text, ROOT_MANUAL_TEMPLATE))
    lines.append("")
    if inbox_entries is not None:
        lines.append("## 未整理（INBOX）")
        lines.append("")
        lines.append(f"- `{INBOX_DIR}/INDEX.md`（{len(inbox_entries)} 件）")
        lines.append("  - 迷った資料は一旦ここ → ジャンルへ仕分け → `benchmarks.script_samples` に紐付け")
        lines.append("")
    lines.append("## ジャンル一覧")
    lines.append("")
    if not genres:
        lines.append("- （ジャンルディレクトリがありません）")
    else:
        for genre in genres:
            refs = channel_refs.get(genre, [])
            chs = sorted({r.channel_code for r in refs}, key=_compare_channel_code)
            ch_label = ", ".join(chs) if chs else "—"
            lines.append(f"- **{genre}**（参照CH: {len(chs)}） → `{genre}/INDEX.md`")
            lines.append(f"  - 参照CH: {ch_label}")
    lines.append("")
    lines.append("## ルート直下（ジャンル外）")
    lines.append("")
    root_refs = channel_refs.get(ROOT_GENRE, [])
    if root_refs:
        chs = sorted({r.channel_code for r in root_refs}, key=_compare_channel_code)
        lines.append(f"- 参照CH: {', '.join(chs)}")
        for ref in root_refs:
            status = "OK" if ref.exists else "MISSING"
            note = f" — {ref.note}" if ref.note else ""
            lines.append(f"  - `{ref.sample_path}`（{status} / {ref.label}）{note}")
        lines.append("")
    if not root_entries:
        lines.append("- （なし）")
    else:
        refs_by_path = {}
        for r in root_refs:
            refs_by_path[r.sample_path] = refs_by_path.get(r.sample_path, 0) + 1
        lines.extend(_render_fs_table(root_entries, refs_by_path=refs_by_path))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_generated_by: `python3 scripts/ops/research_genre_index.py --apply`_")
    lines.append("")
    return "\n".join(lines)


def _render_genre_index(
    *,
    existing_text: str,
    genre: str,
    refs: List[ChannelRef],
    competitors: List[CompetitorRef],
    fs_entries: List[FsEntry],
) -> str:
    lines: List[str] = []
    lines.append(f"# {genre} — ベンチマーク/参考台本 索引（自動生成）")
    lines.append("")
    lines.append(_render_manual_block(existing_text, GENRE_MANUAL_TEMPLATE))
    lines.append("")
    lines.append("## 競合チャンネル（benchmarks.channels / ジャンル集約）")
    lines.append("")
    lines.extend(_render_competitors(competitors))
    lines.append("")
    lines.append("## 共有サンプル（script_samples / path → CH）")
    lines.append("")
    lines.extend(_render_shared_samples(refs))
    lines.append("")
    lines.append("## チャンネル別参照（script_samples / CH → path）")
    lines.append("")
    if not refs:
        lines.append("- （このジャンルへの参照はまだありません）")
    else:
        by_channel: Dict[str, List[ChannelRef]] = {}
        for ref in refs:
            by_channel.setdefault(ref.channel_code, []).append(ref)
        for channel_code in sorted(by_channel.keys(), key=_compare_channel_code):
            items = by_channel[channel_code]
            channel_name = items[0].channel_name if items else channel_code
            lines.append(f"- **{channel_code}** {channel_name}")
            for item in items:
                status = "OK" if item.exists else "MISSING"
                note = f" — {item.note}" if item.note else ""
                lines.append(f"  - `{item.sample_path}`（{status} / {item.label}）{note}")
    lines.append("")
    lines.append("## このジャンルのファイル（直下）")
    lines.append("")
    if not fs_entries:
        lines.append("- （空です）")
    else:
        refs_by_path: Dict[str, int] = {}
        for r in refs:
            refs_by_path[r.sample_path] = refs_by_path.get(r.sample_path, 0) + 1
        lines.extend(_render_fs_table(fs_entries, refs_by_path=refs_by_path))
        unref = [e for e in fs_entries if e.kind == "FILE" and refs_by_path.get(e.rel_path, 0) == 0]
        if unref:
            lines.append("")
            lines.append("## 未参照ファイル（refs=0）")
            lines.append("")
            for e in unref:
                lines.append(f"- `{e.rel_path}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_generated_by: `python3 scripts/ops/research_genre_index.py --apply`_")
    lines.append("")
    return "\n".join(lines)


def _render_folder_index(
    *,
    existing_text: str,
    title: str,
    manual_template: str,
    fs_entries: List[FsEntry],
) -> str:
    lines: List[str] = []
    lines.append(f"# {title} — 索引（自動生成）")
    lines.append("")
    lines.append(_render_manual_block(existing_text, manual_template))
    lines.append("")
    lines.append("## このフォルダのファイル（直下）")
    lines.append("")
    if not fs_entries:
        lines.append("- （空です）")
    else:
        lines.extend(_render_fs_table(fs_entries, refs_by_path=None))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_generated_by: `python3 scripts/ops/research_genre_index.py --apply`_")
    lines.append("")
    return "\n".join(lines)


def _write_if_changed(path: Path, content: str) -> bool:
    prev = _safe_text(path)
    if prev == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate genre-based INDEX.md for workspaces/research.")
    ap.add_argument("--apply", action="store_true", help="Write INDEX.md files (default: dry-run).")
    ap.add_argument("--genre", type=str, default=None, help="Only generate a single genre directory (exact name).")
    args = ap.parse_args()

    bootstrap(load_env=False)
    from factory_common.paths import research_root, script_pkg_root

    base = research_root()
    if not base.exists():
        raise SystemExit("workspaces/research not found")

    script_pkg = script_pkg_root()
    genres = _collect_genres(base=base)
    channel_refs, competitors = _collect_channel_refs(research_base=base, script_pkg=script_pkg)

    inbox_dir = base / INBOX_DIR
    inbox_entries: Optional[List[FsEntry]] = None
    if inbox_dir.exists() and inbox_dir.is_dir():
        inbox_entries = _collect_fs_entries(directory=inbox_dir, base=base)

    # Root-level entries (files/dirs not considered genres)
    root_entries: List[FsEntry] = []
    for child in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith(".") or child.name == "_local":
            continue
        if child.name == "INDEX.md":
            continue
        if child.is_dir() and child.name == INBOX_DIR:
            continue
        if child.is_dir() and child.name in genres:
            continue
        root_entries.append(_stat_entry(child, base=base))

    changed: List[str] = []

    root_index_path = base / "INDEX.md"
    root_existing = _safe_text(root_index_path)
    root_md = _render_root_index(
        existing_text=root_existing,
        genres=genres,
        channel_refs=channel_refs,
        inbox_entries=inbox_entries,
        root_entries=root_entries,
    )
    if args.apply:
        if _write_if_changed(root_index_path, root_md):
            changed.append(str(root_index_path.relative_to(base.parent)))

    if inbox_entries is not None:
        inbox_index_path = inbox_dir / "INDEX.md"
        inbox_existing = _safe_text(inbox_index_path)
        inbox_md = _render_folder_index(
            existing_text=inbox_existing,
            title="INBOX（未整理）",
            manual_template=INBOX_MANUAL_TEMPLATE,
            fs_entries=inbox_entries,
        )
        if args.apply:
            if _write_if_changed(inbox_index_path, inbox_md):
                changed.append(str(inbox_index_path.relative_to(base.parent)))

    target_genres = [args.genre] if args.genre else genres
    for genre in target_genres:
        if genre not in genres:
            if args.genre:
                raise SystemExit(f"unknown genre: {genre}")
            continue
        genre_dir = base / genre
        index_path = genre_dir / "INDEX.md"
        existing = _safe_text(index_path)
        fs_entries = _collect_fs_entries(directory=genre_dir, base=base)
        md = _render_genre_index(
            existing_text=existing,
            genre=genre,
            refs=channel_refs.get(genre, []),
            competitors=competitors.get(genre, []),
            fs_entries=fs_entries,
        )
        if args.apply:
            if _write_if_changed(index_path, md):
                changed.append(str(index_path.relative_to(base.parent)))

        for child in sorted(genre_dir.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            if child.name.startswith(".") or child.name.startswith("_"):
                continue
            if not child.name.endswith("_docs"):
                continue
            nested_entries = _collect_fs_entries(directory=child, base=base)
            nested_index_path = child / "INDEX.md"
            nested_existing = _safe_text(nested_index_path)
            nested_md = _render_folder_index(
                existing_text=nested_existing,
                title=f"{genre}/{child.name}",
                manual_template=FOLDER_MANUAL_TEMPLATE,
                fs_entries=nested_entries,
            )
            if args.apply:
                if _write_if_changed(nested_index_path, nested_md):
                    changed.append(str(nested_index_path.relative_to(base.parent)))

    if not args.apply:
        print("[dry-run] will write:")
        print(f"- {root_index_path}")
        if inbox_dir.exists() and inbox_dir.is_dir():
            print(f"- {inbox_dir / 'INDEX.md'}")
        for genre in target_genres:
            if genre not in genres:
                continue
            print(f"- {base / genre / 'INDEX.md'}")
            genre_dir = base / genre
            for child in sorted(genre_dir.iterdir(), key=lambda p: p.name.lower()):
                if not child.is_dir():
                    continue
                if child.name.startswith(".") or child.name.startswith("_"):
                    continue
                if not child.name.endswith("_docs"):
                    continue
                print(f"- {child / 'INDEX.md'}")
        print("")
        print("Run with --apply to write files.")
        return 0

    if changed:
        print("updated:")
        for p in changed:
            print(f"- {p}")
    else:
        print("no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
