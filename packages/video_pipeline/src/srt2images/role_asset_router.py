from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional

from factory_common import paths


@dataclass
class RoleAssetRule:
    """設定型で役割別アセットを紐付けるためのルール."""

    role_tag: str
    asset_dir: Path
    glob: str = "*.mp4"
    max_use: int = 2
    min_use: int = 0
    probability: float = 0.35
    attach_field: str = "role_asset"  # cueに付けるキー名
    asset_kind: str = "video"  # "video" | "image" など
    prefer_front_back: bool = False


@dataclass
class RoleAssetConfig:
    """チャネルごとのアセットルール集合."""

    channel_id: str
    rules: List[RoleAssetRule] = field(default_factory=list)


class RoleAssetRouter:
    """セクション役割タグに応じてアセットをcueへ付与する."""

    def __init__(self, project_root: Path):
        # NOTE: `project_root` was historically passed from a package-local root.
        # Static assets are repo-tracked SoT under `asset/`, so we resolve via paths SSOT.
        self.project_root = project_root
        self.assets_root = paths.assets_root()
        self.configs: Dict[str, RoleAssetConfig] = self._load_default_configs()

    def _load_default_configs(self) -> Dict[str, RoleAssetConfig]:
        configs: Dict[str, RoleAssetConfig] = {}

        # CH01: 視聴者への語りかけ(viewer_address)パートに既存の動画アセットを提示
        ch01_assets = self.assets_root / "ch01"
        if ch01_assets.exists():
            configs["CH01"] = RoleAssetConfig(
                channel_id="CH01",
                rules=[
                    RoleAssetRule(
                        role_tag="viewer_address",
                        asset_dir=ch01_assets,
                        glob="buddha_0*.mp4",
                        max_use=3,
                        min_use=3,
                        probability=1.0,
                        prefer_front_back=True,
                        attach_field="role_asset",
                        asset_kind="video",
                    )
                ],
            )

        return configs

    def apply(self, cues: List[Dict], channel_id: Optional[str]) -> None:
        if not channel_id:
            return
        cfg = self.configs.get(channel_id.upper())
        if not cfg:
            return

        # 総尺を把握（前後配置用）
        total_duration = 0.0
        if cues:
            start0 = float(cues[0].get("start_sec") or 0.0)
            end_last = float(cues[-1].get("end_sec") or start0)
            total_duration = max(0.0, end_last - start0)

        for rule in cfg.rules:
            matches = sorted(rule.asset_dir.glob(rule.glob))
            if not matches:
                continue

            # role_tag優先で候補を集め、足りなければ全体から補完
            candidates = [c for c in cues if c.get("role_tag") == rule.role_tag]
            if rule.min_use and len(candidates) < rule.min_use:
                fallback = [c for c in cues if c.get("role_tag") != rule.role_tag]
                candidates = candidates + fallback

            def _start(c):
                try:
                    return float(c.get("start_sec") or 0.0)
                except Exception:
                    return 0.0

            candidates = sorted(candidates, key=_start)

            selected_cues = []
            if rule.prefer_front_back and total_duration > 0:
                front_cut = 0.35 * total_duration
                back_cut = 0.65 * total_duration
                early = [c for c in candidates if _start(c) <= front_cut]
                late = [c for c in candidates if _start(c) >= back_cut]
                mid = [c for c in candidates if c not in early and c not in late]

                if early:
                    selected_cues.append(early[0])
                if late:
                    selected_cues.append(late[0])

                remaining = [c for c in early[1:] + mid + late[1:] if c not in selected_cues]
            else:
                remaining = list(candidates)

            while len(selected_cues) < rule.min_use and remaining:
                selected_cues.append(remaining.pop(0))

            selected_cues = selected_cues[: rule.max_use]
            if not selected_cues:
                continue

            random.shuffle(matches)

            for idx, cue in enumerate(selected_cues):
                if idx >= rule.max_use:
                    break
                if idx >= rule.min_use and random.random() > rule.probability:
                    continue
                if rule.attach_field in cue:
                    continue

                asset_path = matches[idx % len(matches)]
                cue[rule.attach_field] = {
                    "path": str(asset_path),
                    "kind": rule.asset_kind,
                    "role_tag": rule.role_tag,
                    "note": f"preset: {rule.role_tag}",
                }
