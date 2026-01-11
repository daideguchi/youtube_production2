from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from factory_common.paths import repo_root as ssot_repo_root
from factory_common.paths import thumbnails_root as ssot_thumbnails_root

PROJECT_ROOT = ssot_repo_root()


def find_thumbnails(
    channel_code: str,
    video_no: Optional[str] = None,
    title: Optional[str] = None,
    *,
    limit: int = 3,
) -> List[Dict[str, str]]:
    """
    workspaces/thumbnails/ 配下からチャンネルコード・動画番号に合致しそうなサムネをスコアで探す。
    スコア: channel一致 +3, video番号含む(+2) / 数字一致(+2)、タイトルワード一致(+1)。スコア同点は更新日時降順。

    NOTE:
    - channel_code / video_no は呼び出し側で正規化済みを想定（CHxx / 3桁）。
    - 返却形式は既存のUI互換（{path,url,name}）。
    """

    base = ssot_thumbnails_root()
    if not base.exists():
        return []

    channel_code = (channel_code or "").strip().upper()
    video_no = (video_no or "").strip() or None

    # Fast path: when channel+video is known, prefer stable outputs from the standard assets layout.
    # This keeps 2案(00_thumb_1/2) discoverable and avoids expensive full-tree scans.
    if video_no:
        asset_dir = base / "assets" / channel_code / video_no
        preferred_names = ("00_thumb_1.png", "00_thumb_2.png", "00_thumb.png")
        candidates = [asset_dir / name for name in preferred_names if (asset_dir / name).is_file()]
        if candidates:
            results: List[Dict[str, str]] = []
            for p in candidates[: max(0, int(limit))]:
                rel = p.relative_to(PROJECT_ROOT)
                results.append(
                    {
                        "path": str(rel),
                        "url": f"/thumbnails/assets/{channel_code}/{video_no}/{p.name}",
                        "name": p.name,
                    }
                )
            return results

    video_no_int = None
    if video_no and video_no.isdigit():
        try:
            video_no_int = int(video_no)
        except Exception:
            video_no_int = None

    title_tokens: List[str] = []
    if title:
        # 短い単語のみ加点対象
        title_tokens = [t.lower() for t in re.findall(r"[\\w一-龠ぁ-んァ-ヴー]+", title) if len(t) >= 2]

    matches: List[Tuple[int, float, Path]] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in [".png", ".jpg", ".jpeg", ".webp"]:
            continue
        score = 0
        lower = str(path).lower()
        if channel_code.lower() in lower:
            score += 3

        video_matched = False
        if video_no and video_no in lower:
            score += 2
            video_matched = True
        elif video_no_int is not None:
            nums = re.findall(r"(\\d{1,4})", lower)
            for n in nums:
                try:
                    if int(n) == video_no_int:
                        score += 2
                        video_matched = True
                        break
                except Exception:
                    continue

        if title_tokens:
            for tok in title_tokens:
                if tok and tok in lower:
                    score += 1
                    break

        if video_no and not video_matched:
            continue
        if score == 0 and channel_code.lower() not in lower:
            continue
        mtime = path.stat().st_mtime
        matches.append((score, mtime, path))

    matches.sort(key=lambda x: (-x[0], -x[1]))
    results: List[Dict[str, str]] = []
    for _, _, p in matches[: max(0, int(limit))]:
        rel = p.relative_to(PROJECT_ROOT)
        url = f"/{rel.as_posix()}"
        results.append({"path": str(rel), "url": url, "name": p.name})
    return results

