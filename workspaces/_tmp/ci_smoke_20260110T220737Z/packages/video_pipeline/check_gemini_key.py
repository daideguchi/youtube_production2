#!/usr/bin/env python3
"""
読み込まれている Gemini API キーを確認するスクリプト。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


# Allow running as a script (`python3 packages/video_pipeline/check_gemini_key.py`)
# even when repo root is not on sys.path.
_repo_root = _discover_repo_root(Path(__file__).resolve())
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from _bootstrap import bootstrap  # noqa: E402

repo_root = bootstrap(load_env=True)

print("=" * 60)
print("Gemini APIキーの確認")
print("=" * 60)

# 1. 環境変数から
env_key = os.environ.get("GEMINI_API_KEY")
print(f"\n1. 環境変数 (os.environ):")
if env_key:
    print(f"   ✅ 設定されています: {env_key[:20]}...{env_key[-10:]}")
else:
    print("   ❌ 設定されていません")

# 2. config.pyから読み込まれるキー
print(f"\n2. config.py経由で読み込まれるキー:")
try:
    from video_pipeline.src.core.config import config

    config_key = config.GEMINI_API_KEY
    print(f"   ✅ 読み込み成功: {config_key[:20]}...{config_key[-10:]}")
except Exception as e:
    print(f"   ❌ 読み込みエラー: {e}")
    config_key = None

# 3. .envファイルを確認（プロジェクトとホーム）
print(f"\n3. .envファイルを確認:")
env_files = [
    repo_root / ".env",
    Path.home() / ".env",
]

for env_file in env_files:
    if env_file.exists():
        print(f"\n   {env_file}:")
        try:
            content = env_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                if "GEMINI_API_KEY" in line and not line.strip().startswith("#"):
                    print(f"     {line[:80]}")
        except Exception as e:
            print(f"     ❌ 読み込みエラー: {e}")
    else:
        print(f"   {env_file}: 存在しません")

# 4. 実際に使用されるキーの比較
print(f"\n" + "=" * 60)
print("実際に使用されるキー:")
print("=" * 60)
if config_key:
    print(f"config.GEMINI_API_KEY: {config_key}")
    print(f"   長さ: {len(config_key)} 文字")
    if env_key and env_key != config_key:
        print(f"\n⚠️  警告: 環境変数とconfig.pyで読み込まれるキーが異なります！")
        print(f"   環境変数: {env_key[:20]}...{env_key[-10:]}")
        print(f"   config.py: {config_key[:20]}...{config_key[-10:]}")
    elif env_key == config_key:
        print(f"   ✅ 環境変数とconfig.pyのキーは一致しています")
else:
    print("❌ キーを読み込めませんでした")
