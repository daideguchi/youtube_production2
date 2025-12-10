"""
中間生成物と古いログをクリーンアップする簡易スクリプト
※実行前にバックアップ推奨
"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "script_pipeline" / "data"
LOG_DIR = DATA_ROOT / "_state" / "logs"
KEEP_DAYS = 14


def _is_old(path: Path, days: int) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return mtime < datetime.now() - timedelta(days=days)
    except Exception:
        return False


def clean_logs() -> None:
    if LOG_DIR.exists():
        for p in LOG_DIR.glob("*.log"):
            if _is_old(p, KEEP_DAYS):
                try:
                    p.unlink()
                except Exception:
                    pass


def clean_intermediate() -> None:
    # audio_prep, logs配下などを削除対象に（outputやcontentは残す）
    for channel_dir in DATA_ROOT.iterdir():
        if not channel_dir.is_dir() or channel_dir.name.startswith("_"):
            continue
        for video_dir in channel_dir.iterdir():
            if not video_dir.is_dir():
                continue
            # audio_prep と logs を削除
            for sub in ["audio_prep", "logs"]:
                target = video_dir / sub
                if target.exists():
                    try:
                        shutil.rmtree(target)
                    except Exception:
                        pass


def main() -> None:
    clean_logs()
    clean_intermediate()


if __name__ == "__main__":
    main()
