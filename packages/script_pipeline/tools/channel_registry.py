#!/usr/bin/env python3
"""
Channel registry/scaffold tool.

Goal:
- Add a new internal channel (CHxx) with a YouTube handle as the only identifier.
- Create the minimal file/dir set so UI + agents do not have to guess where to edit.

Usage (CLI):
  python3 -m script_pipeline.tools.channel_registry create \\
    --channel CH17 \\
    --name "ブッダの◯◯" \\
    --youtube-handle "@buddha-f001" \\
    --description "..." \\
    --chapter-count 8 \\
    --target-chars-min 6500 \\
    --target-chars-max 8500
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from factory_common.paths import planning_root, repo_root, script_data_root, script_pkg_root
from factory_common.youtube_handle import (
    YouTubeHandleResolutionError,
    normalize_youtube_handle,
    resolve_youtube_channel_id_from_handle,
)

_CHANNEL_CODE_RE = re.compile(r"^CH\d+$", re.IGNORECASE)


@dataclass(frozen=True)
class ChannelScaffoldResult:
    channel_code: str
    channel_dir: Path
    channel_info_path: Path
    script_prompt_path: Path
    planning_csv_path: Path
    persona_path: Path
    sources_path: Path
    channels_info_path: Path


def _now_utc_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_channel_code(value: str) -> str:
    code = (value or "").strip().upper()
    if not _CHANNEL_CODE_RE.match(code):
        raise ValueError(f"invalid channel code: {value!r} (expected: CH##...)")
    return code


def _sanitize_dir_token(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("channel name is empty")
    # Prevent path traversal / invalid names across platforms.
    text = text.replace("/", "／").replace("\\", "＼").replace("\0", "")
    return text.strip()


def _script_pipeline_root_prefer_symlink() -> Path:
    root = repo_root()
    symlink = root / "script_pipeline"
    return symlink if symlink.exists() else script_pkg_root()


def channels_root() -> Path:
    return _script_pipeline_root_prefer_symlink() / "channels"


def channels_info_path() -> Path:
    return channels_root() / "channels_info.json"


def sources_yaml_path() -> Path:
    return repo_root() / "configs" / "sources.yaml"


def _read_csv_headers(path: Path) -> List[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            return next(reader)
        except StopIteration:
            return []


def _default_planning_headers() -> List[str]:
    candidates = [
        planning_root() / "channels" / "CH12.csv",
        planning_root() / "templates" / "CH01_planning_template.csv",
        planning_root() / "channels" / "CH01.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            headers = _read_csv_headers(candidate)
            if headers:
                return headers
    # Minimal fallback (must include "No." because sync_all_scripts sorts by it).
    return ["No.", "チャンネル", "動画番号", "動画ID", "台本番号", "タイトル", "進捗", "台本パス", "更新日時"]


def _write_text(path: Path, content: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.replace("\r\n", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    path.write_text(normalized, encoding="utf-8")


def _write_json(path: Path, payload: Dict[str, Any], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_channel_dir(channel_code: str) -> Optional[Path]:
    code = _normalize_channel_code(channel_code)
    root = channels_root()
    if not root.exists():
        return None
    matches = sorted((p for p in root.iterdir() if p.is_dir() and p.name.upper().startswith(f"{code}-")))
    return matches[0] if matches else None


def _iter_channel_info_payloads() -> Iterable[Tuple[str, Dict[str, Any]]]:
    root = channels_root()
    if not root.exists():
        return []
    out: List[Tuple[str, Dict[str, Any]]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        info_path = entry / "channel_info.json"
        if not info_path.exists():
            continue
        try:
            payload = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        channel_id = str(payload.get("channel_id") or "").strip().upper() or entry.name.split("-", 1)[0].upper()
        out.append((channel_id, payload))
    return out


def ensure_unique_youtube_handle(channel_code: str, youtube_handle: str) -> None:
    code = _normalize_channel_code(channel_code)
    target = normalize_youtube_handle(youtube_handle).lower()
    conflicts: List[str] = []
    for other_code, payload in _iter_channel_info_payloads():
        if other_code.upper() == code:
            continue
        youtube_info = payload.get("youtube") or {}
        other = youtube_info.get("handle") or youtube_info.get("custom_url") or ""
        if not other:
            continue
        try:
            other_key = normalize_youtube_handle(str(other)).lower()
        except Exception:
            continue
        if other_key == target:
            conflicts.append(other_code.upper())
    if conflicts:
        conflicts_s = ", ".join(sorted(set(conflicts)))
        raise ValueError(f"YouTube handle is already used by: {conflicts_s} ({target})")


def rebuild_channels_info() -> Path:
    """
    Rebuild channels_info.json from per-channel channel_info.json (quota-free).

    Mirrors UI backend behavior (sorted directory iteration + json dump).
    """

    entries: List[Dict[str, Any]] = []
    root = channels_root()
    for entry in sorted(root.iterdir()) if root.exists() else []:
        if not entry.is_dir():
            continue
        info_path = entry / "channel_info.json"
        if not info_path.exists():
            continue
        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        entries.append(data)
    out_path = channels_info_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def _render_sources_channel_block(
    channel_code: str,
    *,
    planning_csv: str,
    persona: str,
    channel_prompt: str,
    chapter_count: Optional[int],
    target_chars_min: Optional[int],
    target_chars_max: Optional[int],
) -> List[str]:
    lines = [
        f"  {channel_code}:\n",
        f"    planning_csv: {planning_csv}\n",
        f"    persona: {persona}\n",
        f"    channel_prompt: {channel_prompt}\n",
    ]
    if chapter_count is not None:
        lines.append(f"    chapter_count: {int(chapter_count)}\n")
    if target_chars_min is not None:
        lines.append(f"    target_chars_min: {int(target_chars_min)}\n")
    if target_chars_max is not None:
        lines.append(f"    target_chars_max: {int(target_chars_max)}\n")
    return lines


def _find_channels_section_bounds(lines: List[str]) -> Tuple[int, int]:
    channels_idx = -1
    for i, line in enumerate(lines):
        if line.rstrip("\n") == "channels:":
            channels_idx = i
            break
    if channels_idx < 0:
        raise ValueError("configs/sources.yaml is missing top-level 'channels:' section")

    end = len(lines)
    for i in range(channels_idx + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Next top-level mapping key starts with no indentation.
        if line[:1] not in (" ", "\t") and re.match(r"^[A-Za-z0-9_]+:\s*$", stripped):
            end = i
            break
    return channels_idx, end


def add_sources_channel_entry(
    channel_code: str,
    *,
    planning_csv: str,
    persona: str,
    channel_prompt: str,
    chapter_count: Optional[int],
    target_chars_min: Optional[int],
    target_chars_max: Optional[int],
) -> Path:
    """
    Append a new channels.CHxx entry to configs/sources.yaml without rewriting the whole YAML.
    (Preserves comments and existing formatting.)
    """

    code = _normalize_channel_code(channel_code)
    path = sources_yaml_path()
    if not path.exists():
        raise FileNotFoundError(f"sources.yaml not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    channels_idx, end_idx = _find_channels_section_bounds(lines)
    entry_pat = re.compile(rf"^  {re.escape(code)}:\s*$")
    for line in lines[channels_idx + 1 : end_idx]:
        if entry_pat.match(line.rstrip("\n")):
            raise ValueError(f"configs/sources.yaml already has channels.{code}")

    block = _render_sources_channel_block(
        code,
        planning_csv=planning_csv,
        persona=persona,
        channel_prompt=channel_prompt,
        chapter_count=chapter_count,
        target_chars_min=target_chars_min,
        target_chars_max=target_chars_max,
    )

    insert_at = end_idx
    if insert_at > 0 and lines[insert_at - 1].strip():
        block = ["\n", *block]

    updated = [*lines[:insert_at], *block, *lines[insert_at:]]
    path.write_text("".join(updated), encoding="utf-8")
    return path


def create_channel_scaffold(
    *,
    channel: str,
    name: str,
    youtube_handle: str,
    description: Optional[str],
    chapter_count: Optional[int],
    target_chars_min: Optional[int],
    target_chars_max: Optional[int],
    overwrite: bool = False,
) -> ChannelScaffoldResult:
    code = _normalize_channel_code(channel)
    display_name = _sanitize_dir_token(name)

    if find_channel_dir(code):
        raise FileExistsError(f"channel directory already exists for: {code}")

    ensure_unique_youtube_handle(code, youtube_handle)
    resolved = resolve_youtube_channel_id_from_handle(youtube_handle)

    channel_dir_name = f"{code}-{display_name}"
    channel_dir = channels_root() / channel_dir_name
    channel_info = channel_dir / "channel_info.json"
    script_prompt_path = channel_dir / "script_prompt.txt"

    # SoT locations (workspaces) + compat (progress)
    ws_planning_csv = planning_root() / "channels" / f"{code}.csv"
    ws_persona = planning_root() / "personas" / f"{code}_PERSONA.md"
    legacy_planning_csv = repo_root() / "progress" / "channels" / f"{code}.csv"
    legacy_persona = repo_root() / "progress" / "personas" / f"{code}_PERSONA.md"

    # UI channel list source (workspaces/scripts)
    data_dir = script_data_root() / code
    data_dir.mkdir(parents=True, exist_ok=True)

    # planning CSV (header-only)
    headers = _default_planning_headers()
    csv_content = ",".join(headers) + "\n"
    _write_text(ws_planning_csv, csv_content, overwrite=overwrite)
    # If progress/ is a symlink to workspaces/planning, skip the duplicate write.
    try:
        legacy_same = legacy_planning_csv.resolve() == ws_planning_csv.resolve()
    except Exception:
        legacy_same = False
    if legacy_planning_csv.parent.exists() and not legacy_same:
        _write_text(legacy_planning_csv, csv_content, overwrite=overwrite)

    # persona stub
    persona_summary = f"{display_name} の視聴者ペルソナ（要約）をここに 1 行で書く。"
    persona_body = (
        f"# {code}_PERSONA\n"
        f"> {persona_summary}\n\n"
        f"## チャンネル\n"
        f"- コード: {code}\n"
        f"- 名称: {display_name}\n\n"
        f"## トーン\n"
        f"- TODO\n\n"
        f"## 禁止事項\n"
        f"- TODO\n\n"
        f"## 更新\n"
        f"- {datetime.now().strftime('%Y-%m-%d')} created by channel_registry\n"
    )
    _write_text(ws_persona, persona_body, overwrite=overwrite)
    try:
        persona_same = legacy_persona.resolve() == ws_persona.resolve()
    except Exception:
        persona_same = False
    if legacy_persona.parent.exists() and not persona_same:
        _write_text(legacy_persona, persona_body, overwrite=overwrite)

    # script_prompt stub (store content in channel_info.json for UI consumers)
    chars_min = int(target_chars_min) if target_chars_min is not None else None
    chars_max = int(target_chars_max) if target_chars_max is not None else None
    goal = "TODO"
    if chars_min and chars_max:
        goal = f"{chars_min:,}〜{chars_max:,}字"
    elif chars_max:
        goal = f"{chars_max:,}字目安"
    prompt_body = (
        f"# {code} 台本プロンプト（{display_name}）\n\n"
        f"役割: あなたは「{display_name}」の語り手。\n\n"
        f"ゴール: {goal} の一人語り台本。結論→理由→具体例→今日できる手順で腹落ちさせ、最後は安心で終える。\n\n"
        f"構成: TODO（固定アウトラインがある場合はここに記載）\n\n"
        f"禁止: 煽り・説教・断定過多・宗教勧誘。ポーズは `---` のみ許可。\n\n"
        f"出力ルール: 台本本文のみ（タイトル・メタ情報・制作裏は禁止）。\n"
    )
    _write_text(script_prompt_path, prompt_body, overwrite=overwrite)

    # channel_info.json
    now = _now_utc_z()
    payload: Dict[str, Any] = {
        "channel_id": code,
        "name": display_name,
        "description": (description or "").strip() or None,
        "persona_path": str(legacy_persona.relative_to(repo_root())) if legacy_persona.exists() else str(ws_persona.relative_to(repo_root())),
        "template_path": f"script_pipeline/channels/{channel_dir_name}/script_prompt.txt",
        "branding": {
            "avatar_url": resolved.avatar_url or "",
            "banner_url": "",
            "title": resolved.title or "",
            "subscriber_count": 0,
            "view_count": 0,
            "video_count": 0,
            "custom_url": resolved.handle,
            "handle": resolved.handle,
            "url": resolved.url,
            "updated_at": now,
        },
        "youtube": {
            "channel_id": resolved.channel_id,
            "title": resolved.title or "",
            "custom_url": resolved.handle,
            "handle": resolved.handle,
            "url": resolved.url,
            "source": resolved.channel_id,
            "view_count": 0,
            "subscriber_count": 0,
            "video_count": 0,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        },
        "script_prompt": prompt_body.strip(),
    }
    channel_dir.mkdir(parents=True, exist_ok=True)
    _write_json(channel_info, payload, overwrite=overwrite)

    # configs/sources.yaml entry (primary SoT)
    sources_path = add_sources_channel_entry(
        code,
        planning_csv=str(ws_planning_csv.relative_to(repo_root())),
        persona=str(ws_persona.relative_to(repo_root())),
        channel_prompt=str((script_pkg_root() / "channels" / channel_dir_name / "script_prompt.txt").relative_to(repo_root())),
        chapter_count=chapter_count,
        target_chars_min=target_chars_min,
        target_chars_max=target_chars_max,
    )

    catalog_path = rebuild_channels_info()

    return ChannelScaffoldResult(
        channel_code=code,
        channel_dir=channel_dir,
        channel_info_path=channel_info,
        script_prompt_path=script_prompt_path,
        planning_csv_path=ws_planning_csv,
        persona_path=ws_persona,
        sources_path=sources_path,
        channels_info_path=catalog_path,
    )


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register/scaffold a new channel from a YouTube handle.")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create CHxx scaffold (dirs + channel_info + sources.yaml entry).")
    create.add_argument("--channel", required=True, help="Channel code (e.g., CH17).")
    create.add_argument("--name", required=True, help="Internal channel display name (dir suffix).")
    create.add_argument("--youtube-handle", required=True, help="YouTube handle (e.g., @buddha-a001).")
    create.add_argument("--description", default=None, help="Optional channel description.")
    create.add_argument("--chapter-count", type=int, default=None, help="Optional chapter_count (sources.yaml).")
    create.add_argument("--target-chars-min", type=int, default=None, help="Optional target_chars_min (sources.yaml).")
    create.add_argument("--target-chars-max", type=int, default=None, help="Optional target_chars_max (sources.yaml).")
    create.add_argument("--overwrite", action="store_true", help="Overwrite existing files (danger).")

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = _parse_args(argv)
    if args.command == "create":
        try:
            result = create_channel_scaffold(
                channel=args.channel,
                name=args.name,
                youtube_handle=args.youtube_handle,
                description=args.description,
                chapter_count=args.chapter_count,
                target_chars_min=args.target_chars_min,
                target_chars_max=args.target_chars_max,
                overwrite=bool(args.overwrite),
            )
        except YouTubeHandleResolutionError as exc:
            raise SystemExit(f"handle resolution failed: {exc}") from exc
        except Exception as exc:
            raise SystemExit(str(exc)) from exc

        print(
            json.dumps(
                {
                    "channel_code": result.channel_code,
                    "channel_dir": str(result.channel_dir),
                    "channel_info": str(result.channel_info_path),
                    "script_prompt": str(result.script_prompt_path),
                    "planning_csv": str(result.planning_csv_path),
                    "persona": str(result.persona_path),
                    "sources": str(result.sources_path),
                    "channels_info": str(result.channels_info_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return


if __name__ == "__main__":
    main()
