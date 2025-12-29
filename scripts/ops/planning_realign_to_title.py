#!/usr/bin/env python3
"""
Realign Planning CSV "theme hint" columns to the title (title is absolute truth).

Why:
- Planning rows often get contaminated (e.g., content_summary / tags / key concept copied from another episode).
- When those fields disagree with the title, they mislead both humans and the script pipeline.
- This tool deterministically rewrites the commonly contaminated "theme hint" columns
  *only when misalignment is detected* (or when the summary is missing).

What it changes (when present in the CSV header):
- 内容
- 内容（企画要約）
- 悩みタグ_メイン / 悩みタグ_サブ
- ライフシーン
- キーコンセプト
- ベネフィット一言
- たとえ話イメージ
- 説明文_リード / 説明文_この動画でわかること

Detection (deterministic):
- If タイトル【...】 and 内容（企画要約）【...】 disagree (format-only variations are ignored), rewrite the theme-hint columns.
- If 内容（企画要約） is empty, fill it with a title-anchored summary.
- If the tags match but differ only by formatting (e.g., punctuation/spacing), it only normalizes the summary tag to the title tag.

It does NOT touch:
- タイトル（absolute SoT）
- 企画意図 / ターゲット層 / 具体的な内容（話の構成案） などの設計入力

Usage:
  python scripts/ops/planning_realign_to_title.py --channel CH07 --from 019 --to 030 --apply --write-latest
  python scripts/ops/planning_realign_to_title.py --csv workspaces/planning/channels/CH07.csv --from 019 --to 030 --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.paths import logs_root, planning_root, repo_root
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock


_TAG_RE = re.compile(r"^\s*【([^】]{1,40})】\s*")


THEME_COLUMNS: tuple[str, ...] = (
    "内容",
    "内容（企画要約）",
    "悩みタグ_メイン",
    "悩みタグ_サブ",
    "ライフシーン",
    "キーコンセプト",
    "ベネフィット一言",
    "たとえ話イメージ",
    "説明文_リード",
    "説明文_この動画でわかること",
)


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _normalize_channel(ch: str) -> str:
    s = (ch or "").strip().upper()
    if re.fullmatch(r"CH\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def _planning_csv_path(channel: str) -> Path:
    return planning_root() / "channels" / f"{channel}.csv"


def _video_number_from_row(row: dict[str, str]) -> str:
    raw = (row.get("動画番号") or row.get("No.") or "").strip()
    if raw:
        try:
            return f"{int(raw):03d}"
        except Exception:
            return raw
    for key in ("動画ID", "台本番号"):
        v = (row.get(key) or "").strip()
        m = re.search(r"\bCH\d{2}-(\d{3})\b", v)
        if m:
            return m.group(1)
    return "???"


def _parse_title_parts(title: str) -> tuple[str, str]:
    """
    Return (title_tag, title_body).
    - title_tag: inside leading 【...】 when present, else "".
    - title_body: title without the leading tag, else title itself.
    """
    s = (title or "").strip()
    m = _TAG_RE.match(s)
    if not m:
        return "", s
    tag = (m.group(1) or "").strip()
    body = _TAG_RE.sub("", s, count=1).strip()
    return tag, body or s


def _normalize_tag(tag: str) -> str:
    """Normalize tags for format-only variations (e.g., ニコラ・テスラ流 vs ニコラテスラ流)."""
    s = unicodedata.normalize("NFKC", str(tag or "")).strip()
    s = re.sub(r"[\s\u3000・･·、,\.／/\\\-‐‑‒–—―ー〜~]", "", s)
    return s


def _leading_tag(text: str) -> str:
    s = (text or "").strip()
    m = _TAG_RE.match(s)
    if not m:
        return ""
    return (m.group(1) or "").strip()


def _replace_leading_tag(text: str, new_tag: str) -> str:
    s = (text or "").strip()
    if not s:
        return s
    _old, body = _parse_title_parts(s)
    if not new_tag:
        return body.strip()
    return f"【{new_tag}】{body.strip()}"


def _derive_content_summary(title: str, title_tag: str, title_body: str) -> str:
    topic = title_body.strip() if title_body.strip() else (title or "").strip()
    if not topic:
        return ""
    if title_tag:
        return f"【{title_tag}】{topic} をテーマに、企画意図と構成案に沿ってわかりやすく解説する回。"
    return f"{topic} をテーマに、企画意図と構成案に沿ってわかりやすく解説する回。"


def _derive_safe_theme_values(*, title: str, title_tag: str, title_body: str) -> dict[str, str]:
    topic = title_body.strip() if title_body.strip() else (title or "").strip()
    key_concept = title_tag or topic
    return {
        "内容": "",
        "内容（企画要約）": _derive_content_summary(title, title_tag, title_body),
        "悩みタグ_メイン": title_tag or "",
        "悩みタグ_サブ": "",
        "ライフシーン": "",
        "キーコンセプト": key_concept,
        "ベネフィット一言": "明日ひとつだけ試せる一歩が分かる",
        "たとえ話イメージ": "",
        "説明文_リード": f"{topic}で迷うときに。考え方と、今日からできる一歩を整理します。" if topic else "",
        "説明文_この動画でわかること": f"・{topic}で苦しくなる理由\\n・心を守る考え方\\n・今日からできる一歩" if topic else "",
    }


@dataclass(frozen=True)
class Change:
    channel: str
    video: str
    row_index: int
    column: str
    before: str
    after: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "video": self.video,
            "row_index": self.row_index,
            "column": self.column,
            "before": self.before,
            "after": self.after,
        }


def _coerce_int_video(value: str) -> Optional[int]:
    s = (value or "").strip()
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def _in_range(video: str, start: int, end: int) -> bool:
    n = _coerce_int_video(video)
    if n is None:
        return False
    return start <= n <= end


def realign_rows(
    rows: list[dict[str, str]],
    headers: list[str],
    *,
    channel: str,
    from_video: int,
    to_video: int,
) -> list[Change]:
    changes: list[Change] = []
    present_cols = {h for h in headers if (h or "").strip()}
    targets = [c for c in THEME_COLUMNS if c in present_cols]
    if not targets:
        return changes

    for idx, row in enumerate(rows, start=1):
        video = _video_number_from_row(row)
        if not _in_range(video, from_video, to_video):
            continue

        title = (row.get("タイトル") or "").strip()
        if not title:
            continue

        title_tag, title_body = _parse_title_parts(title)
        summary = (row.get("内容（企画要約）") or "").strip()
        summary_tag = _leading_tag(summary)

        title_tag_norm = _normalize_tag(title_tag)
        summary_tag_norm = _normalize_tag(summary_tag)

        needs_full_realign = False
        needs_tag_only = False

        if title_tag and summary_tag:
            if title_tag_norm and title_tag_norm == summary_tag_norm:
                # Minor format-only variation (e.g., punctuation/spacing).
                if title_tag != summary_tag:
                    needs_tag_only = True
            else:
                needs_full_realign = True
        elif summary_tag and not title_tag:
            # Summary has a tag but title does not → likely contaminated.
            needs_full_realign = True
        elif "内容（企画要約）" in present_cols and not summary:
            # Missing summary: fill a safe, title-anchored version.
            needs_full_realign = True

        if not (needs_full_realign or needs_tag_only):
            continue

        if needs_tag_only:
            desired = {"内容（企画要約）": _replace_leading_tag(summary, title_tag)}
        else:
            desired = _derive_safe_theme_values(title=title, title_tag=title_tag, title_body=title_body)

        for col in targets:
            if col not in desired:
                continue
            new_val = desired.get(col, "")
            old_val = str(row.get(col) or "")
            if old_val == new_val:
                continue
            changes.append(
                Change(
                    channel=channel,
                    video=video,
                    row_index=idx,
                    column=col,
                    before=old_val,
                    after=new_val,
                )
            )
            row[col] = new_val

    return changes


def _write_report(channel: str, csv_path: Path, changes: list[Change], *, write_latest: bool) -> tuple[Path, Path]:
    out_dir = logs_root() / "regression" / "planning_realign_to_title"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now_compact()
    json_path = out_dir / f"planning_realign_to_title_{channel}__{ts}.json"
    md_path = out_dir / f"planning_realign_to_title_{channel}__{ts}.md"

    payload: dict[str, Any] = {
        "schema": "ytm.planning_realign_to_title.v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "csv_path": str(csv_path),
        "changed_rows": len({c.row_index for c in changes}),
        "changed_cells": len(changes),
        "changes": [c.as_dict() for c in changes],
        "ok": True,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = []
    lines.append(f"# planning_realign_to_title report: {channel}")
    lines.append("")
    lines.append(f"- generated_at: {payload['generated_at']}")
    lines.append(f"- csv_path: {payload['csv_path']}")
    lines.append(f"- changed_rows: {payload['changed_rows']}")
    lines.append(f"- changed_cells: {payload['changed_cells']}")
    lines.append("")
    if changes:
        lines.append("## Changes (first 60)")
        for c in changes[:60]:
            before = (c.before or "").replace("\n", "\\n").strip()
            after = (c.after or "").replace("\n", "\\n").strip()
            if len(before) > 90:
                before = before[:90] + "…"
            if len(after) > 90:
                after = after[:90] + "…"
            lines.append(f"- {c.channel}/{c.video} row={c.row_index} col={c.column}: '{before}' -> '{after}'")
    else:
        lines.append("## Changes")
        lines.append("- (none)")
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    if write_latest:
        (out_dir / f"planning_realign_to_title_{channel}__latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / f"planning_realign_to_title_{channel}__latest.md").write_text(
            "\n".join(lines).rstrip() + "\n", encoding="utf-8"
        )

    return json_path, md_path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="", help="Channel code like CH07")
    ap.add_argument("--csv", default="", help="Explicit CSV path (overrides --channel)")
    ap.add_argument("--from", dest="from_video", required=True, help="Start video number (e.g. 019)")
    ap.add_argument("--to", dest="to_video", required=True, help="End video number (e.g. 030)")
    ap.add_argument("--apply", action="store_true", help="Rewrite the CSV in-place")
    ap.add_argument("--write-latest", action="store_true", help="Also write *_latest.json/md (overwrite)")
    ap.add_argument("--ignore-locks", action="store_true", help="Ignore coordination locks (use with caution)")
    args = ap.parse_args(argv)

    channel = _normalize_channel(args.channel) if args.channel else ""
    csv_path = Path(args.csv).expanduser() if args.csv else None
    if csv_path is None:
        if not channel:
            raise SystemExit("Either --channel or --csv is required")
        csv_path = _planning_csv_path(channel)

    if not csv_path.is_absolute():
        csv_path = repo_root() / csv_path

    if not channel:
        channel = _normalize_channel(csv_path.stem)

    start = _coerce_int_video(str(args.from_video))
    end = _coerce_int_video(str(args.to_video))
    if start is None or end is None:
        raise SystemExit("--from/--to must be numeric")
    if start > end:
        start, end = end, start

    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    if args.apply and not args.ignore_locks:
        locks = default_active_locks_for_mutation()
        blocking = find_blocking_lock(csv_path, locks)
        if blocking is not None:
            raise SystemExit(
                f"blocked by active lock: {blocking.lock_id} mode={blocking.mode} scopes={blocking.scopes} created_by={blocking.created_by}"
            )

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)

    changes = realign_rows(rows, headers, channel=channel, from_video=start, to_video=end)
    report_json, report_md = _write_report(channel, csv_path, changes, write_latest=bool(args.write_latest))
    print(f"Wrote: {report_json}")
    print(f"Wrote: {report_md}")

    if args.apply and changes:
        out_dir = logs_root() / "regression" / "planning_realign_to_title"
        out_dir.mkdir(parents=True, exist_ok=True)
        backup_path = out_dir / f"backup_{channel}__{_utc_now_compact()}.csv"
        backup_path.write_text(csv_path.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
        print(f"Backup: {backup_path}")

        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        print(f"Applied: {csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
