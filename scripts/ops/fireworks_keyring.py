#!/usr/bin/env python3
from __future__ import annotations

"""
fireworks_keyring.py — Fireworks（台本/本文）APIキーのキーローテ用ストアを管理する

目的:
- repo 内（workspaces/_scratch など）に秘密鍵を置かずに、運用者が「キーを追加するだけ」で
  ローテーションできるようにする。

既定:
- キーファイル: ~/.ytm/secrets/fireworks_script_keys.txt
  - ルート変更: YTM_SECRETS_ROOT または --path

ファイル形式:
- 1行1キー（推奨）
- コメント: 先頭 '#'
- ENV 風: FIREWORKS_SCRIPT=...（右辺だけ抽出）

安全:
- このツールはキーをフルで表示しない（--show-masked でもマスク表示）。
"""

import argparse
import os
from pathlib import Path
from typing import List

from factory_common.paths import secrets_root, workspace_root


def _dedupe_keep_order(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for it in items:
        s = str(it or "").strip()
        if not s or s in seen:
            continue
        out.append(s)
        seen.add(s)
    return out


def _parse_keys(text: str) -> List[str]:
    keys: List[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            _left, right = line.split("=", 1)
            line = right.strip()
        # Stored as ASCII tokens (no spaces).
        if " " in line or "\t" in line:
            continue
        if not all(ord(ch) < 128 for ch in line):
            continue
        if len(line) < 16:
            continue
        keys.append(line)
    return _dedupe_keep_order(keys)


def _read_keys(path: Path) -> List[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    return _parse_keys(text)


def _write_keys(path: Path, keys: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(keys) + ("\n" if keys else "")
    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _default_keyring_path() -> Path:
    return secrets_root() / "fireworks_script_keys.txt"


def _legacy_memo_path() -> Path:
    return workspace_root() / "_scratch" / "fireworks_apiメモ"


def _mask(key: str) -> str:
    k = str(key or "")
    if len(k) <= 8:
        return "*" * len(k)
    return f"{k[:4]}…{k[-4:]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        type=str,
        default="",
        help="keyring file path (default: ~/.ytm/secrets/fireworks_script_keys.txt)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("path", help="print keyring path (no keys)")

    sub.add_parser("init", help="create keyring file if missing (no keys)")

    p_add = sub.add_parser("add", help="add one key (no full key printed)")
    p_add.add_argument("--key", type=str, default="", help="key value (or read from stdin)")

    p_list = sub.add_parser("list", help="list key count (optionally masked)")
    p_list.add_argument("--show-masked", action="store_true", help="print masked keys (prefix…suffix)")

    p_mig = sub.add_parser("migrate-from-scratch", help="one-time import from legacy memo into keyring")
    p_mig.add_argument("--src", type=str, default="", help="legacy source path (default: workspaces/_scratch/fireworks_apiメモ)")

    args = parser.parse_args()
    path = Path(args.path).expanduser().resolve() if str(args.path).strip() else _default_keyring_path()

    if args.cmd == "path":
        print(str(path))
        return

    if args.cmd == "init":
        if not path.exists():
            _write_keys(path, [])
        print(f"ok: {path} (exists={path.exists()})")
        return

    if args.cmd == "add":
        key = str(getattr(args, "key", "") or "").strip()
        if not key:
            try:
                key = (os.sys.stdin.read() or "").strip()
            except Exception:
                key = ""
        parsed = _parse_keys(key)
        if len(parsed) != 1:
            raise SystemExit("invalid key: expected a single ASCII token (no spaces)")
        key = parsed[0]

        existing = _read_keys(path)
        merged = _dedupe_keep_order([*existing, key])
        if merged != existing:
            _write_keys(path, merged)
        print(f"ok: added (total={len(merged)})")
        return

    if args.cmd == "list":
        keys = _read_keys(path)
        print(f"count={len(keys)} path={path}")
        if getattr(args, "show_masked", False):
            for k in keys:
                print(_mask(k))
        return

    if args.cmd == "migrate-from-scratch":
        src = (
            Path(str(getattr(args, "src", "") or "").strip()).expanduser().resolve()
            if str(getattr(args, "src", "") or "").strip()
            else _legacy_memo_path()
        )
        if not src.exists():
            raise SystemExit(f"legacy memo not found: {src}")
        src_keys = _read_keys(src)
        dst_keys = _read_keys(path)
        merged = _dedupe_keep_order([*dst_keys, *src_keys])
        if merged != dst_keys:
            _write_keys(path, merged)
        print(f"ok: imported {len(src_keys)} (total={len(merged)}) from {src}")
        return

    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    main()

