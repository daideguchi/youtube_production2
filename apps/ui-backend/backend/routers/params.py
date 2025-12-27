from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from factory_common.paths import repo_root

PARAMS_PATH = repo_root() / "apps" / "ui-backend" / "backend" / "app" / "ui_params.json"


def _load_params() -> Dict[str, Any]:
    if not PARAMS_PATH.exists():
        return _default_params()
    try:
        return json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load params file.")


def _save_params(data: Dict[str, Any]) -> None:
    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_params() -> Dict[str, Any]:
    return {
        "image_track_target_count": 44,  # 画像セグ本数の目安
        "belt_segments": 5,              # 帯の分割数
        "belt_text_limit": 20,           # 帯テキスト1行の最大文字数目安
        "start_offset_sec": 3.0,         # 開始オフセット（画像・帯を置き始める秒）
        "max_duration_sec": 960.0,       # srt2imagesトラックの目安終了秒（約16分）
        "allow_extra_video_tracks": True # 手動編集の背景トラックを許容するか
    }


class ParamUpdate(BaseModel):
    image_track_target_count: Optional[int] = Field(default=None, ge=1, le=300)
    belt_segments: Optional[int] = Field(default=None, ge=1, le=20)
    belt_text_limit: Optional[int] = Field(default=None, ge=5, le=80)
    start_offset_sec: Optional[float] = Field(default=None, ge=0.0, le=30.0)
    max_duration_sec: Optional[float] = Field(default=None, ge=10.0, le=7200.0)
    allow_extra_video_tracks: Optional[bool] = None


router = APIRouter(prefix="/api/params", tags=["params"])


@router.get("", summary="現在のUIパラメータを取得")
def get_params():
    data = _load_params()
    return {"params": data}


@router.post("", summary="UIパラメータを更新（指定されたキーのみ上書き）")
def update_params(payload: ParamUpdate):
    data = _load_params()
    new_data = data.copy()
    for field, value in payload.model_dump(exclude_none=True).items():
        new_data[field] = value
    _save_params(new_data)
    return {"params": new_data}
