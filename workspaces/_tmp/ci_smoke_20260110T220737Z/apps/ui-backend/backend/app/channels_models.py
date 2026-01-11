from __future__ import annotations

"""
Channel-related Pydantic models shared across UI backend modules.

created: 2026-01-09
"""

from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field


class PlanningRequirementSummary(BaseModel):
    min_no: Optional[int] = Field(None, description="適用される No. の下限（None の場合は全件）")
    required_keys: List[str] = Field(default_factory=list, description="optional_fields_registry のキー一覧")
    required_columns: List[str] = Field(default_factory=list, description="channels CSV 上の列名一覧")


class BenchmarkChannelSpec(BaseModel):
    handle: Optional[str] = Field(None, description="YouTubeハンドル（@name 推奨・任意）")
    name: Optional[str] = Field(None, description="チャンネル表示名（任意）")
    url: Optional[str] = Field(None, description="https://www.youtube.com/@name 等（任意）")
    note: Optional[str] = Field(None, description="観測ポイント（任意）")


class BenchmarkScriptSampleSpec(BaseModel):
    base: Literal["research", "scripts"] = Field(..., description="UI のプレビュー基点")
    path: str = Field(..., description="workspaces/{base}/ 配下の相対パス")
    label: Optional[str] = Field(None, description="表示用ラベル（任意）")
    note: Optional[str] = Field(None, description="使いどころ（任意）")


class ChannelBenchmarksSpec(BaseModel):
    version: int = Field(1, ge=1, description="schema version")
    updated_at: Optional[str] = Field(None, description="更新日 (YYYY-MM-DD)")
    allow_empty_channels: bool = Field(False, description="競合を特定しない例外フラグ（channels を空にしてよい）")
    channels: List[BenchmarkChannelSpec] = Field(default_factory=list)
    script_samples: List[BenchmarkScriptSampleSpec] = Field(default_factory=list)
    notes: Optional[str] = Field(None, description="総評（任意）")


class VideoWorkflowSpec(BaseModel):
    key: Literal["capcut", "remotion"] = Field(..., description="制作型キー")
    id: int = Field(..., ge=1, le=4, description="制作型ID（メモ４互換: 1..4）")
    label: str = Field(..., description="表示名")
    description: str = Field(..., description="型の説明（短文）")


VIDEO_WORKFLOW_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "capcut": {
        "id": 3,
        "label": "capcut型（画像生成あり）",
        "description": "画像を生成してCapCutテンプレで運用。見た目の完成度は高いが、素材/テンプレ管理が必要。",
    },
    "remotion": {
        "id": 4,
        "label": "remotion型（画像生成あり）",
        "description": "画像生成＋Remotionでコード生成。量産と規格統一の最終形だが、初期実装と保守コストが高い。",
    },
}


def _resolve_video_workflow(info_payload: Dict[str, Any]) -> Optional[VideoWorkflowSpec]:
    raw = info_payload.get("video_workflow")
    if raw is None:
        return None
    key = str(raw).strip()
    if not key:
        return None
    definition = VIDEO_WORKFLOW_DEFINITIONS.get(key)
    if not isinstance(definition, dict):
        return None
    try:
        workflow_id = int(definition["id"])
    except Exception:
        return None
    return VideoWorkflowSpec(
        key=key,
        id=workflow_id,
        label=str(definition.get("label") or key),
        description=str(definition.get("description") or ""),
    )


class ChannelProfileResponse(BaseModel):
    channel_code: str
    channel_name: Optional[str] = None
    audience_profile: Optional[str] = None
    persona_summary: Optional[str] = None
    script_prompt: Optional[str] = None
    description: Optional[str] = None
    default_tags: Optional[List[str]] = None
    youtube_title: Optional[str] = None
    youtube_description: Optional[str] = None
    youtube_handle: Optional[str] = None
    video_workflow: Optional[VideoWorkflowSpec] = None
    benchmarks: Optional[ChannelBenchmarksSpec] = None
    audio_default_voice_key: Optional[str] = None
    audio_section_voice_rules: Dict[str, str] = Field(default_factory=dict)
    default_min_characters: int = Field(8000, ge=1000)
    default_max_characters: int = Field(12000, ge=1000)
    chapter_count: Optional[int] = Field(None, ge=1)
    llm_slot: Optional[int] = Field(
        None,
        ge=0,
        description="量産で使用するモデルスロット（LLM_MODEL_SLOT）。未指定ならデフォルトslot。",
    )
    llm_model: Optional[str] = Field(
        None,
        description="[deprecated] ブレ防止のため通常運用では禁止。数字だけ指定した場合は slot として解釈。",
    )
    quality_check_template: Optional[str] = None
    planning_persona: Optional[str] = Field(
        None, description="SSOT のチャンネル共通ペルソナ（channels CSV のターゲット層に使用）"
    )
    planning_persona_path: Optional[str] = Field(
        None, description="SSOT persona ドキュメントのパス（相対）"
    )
    planning_required_fieldsets: List[PlanningRequirementSummary] = Field(
        default_factory=list, description="企画シートで必須となる列の要件"
    )
    planning_description_defaults: Dict[str, str] = Field(
        default_factory=dict,
        description="説明文_リード / 説明文_この動画でわかること の既定値",
    )
    planning_template_path: Optional[str] = Field(
        None, description="channel 用 planning テンプレ CSV のパス"
    )
    planning_template_headers: List[str] = Field(
        default_factory=list, description="テンプレ CSV のヘッダー行"
    )
    planning_template_sample: List[str] = Field(
        default_factory=list, description="テンプレ CSV サンプル行（2行目）"
    )


class PersonaDocumentResponse(BaseModel):
    channel: str
    path: str
    content: str


class PersonaDocumentUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1, description="ペルソナドキュメント全文")


class PlanningTemplateResponse(BaseModel):
    channel: str
    path: str
    content: str
    headers: List[str]
    sample: List[str]


class PlanningTemplateUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1, description="planningテンプレ CSV 全文")


class ChannelProfileUpdateAudio(BaseModel):
    default_voice_key: Optional[str] = Field(
        None, description="audio/channels/CHxx/voice_config.json 内の voice preset key"
    )
    section_voice_rules: Optional[Dict[str, str]] = Field(
        None, description="セクション別に適用する voice preset のマッピング"
    )


class ChannelProfileUpdateRequest(BaseModel):
    script_prompt: Optional[str] = Field(None, description="Qwen台本プロンプト全文")
    description: Optional[str] = Field(None, description="チャンネル説明文")
    youtube_title: Optional[str] = Field(None, description="YouTube上のチャンネルタイトル")
    youtube_description: Optional[str] = Field(None, description="YouTube上の説明文 / 投稿テンプレ")
    youtube_handle: Optional[str] = Field(None, description="YouTubeハンドル (@name)")
    default_tags: Optional[List[str]] = Field(
        None, description="投稿時に使うデフォルトタグ（配列）"
    )
    benchmarks: Optional[ChannelBenchmarksSpec] = Field(
        None, description="チャンネル別ベンチマーク設定（SoT: channel_info.json）"
    )
    audio: Optional[ChannelProfileUpdateAudio] = None


class ChannelRegisterRequest(BaseModel):
    channel_code: str = Field(..., description="新規チャンネルコード (例: CH17)")
    channel_name: str = Field(..., description="内部表示名（チャンネルディレクトリの suffix に使用）")
    youtube_handle: str = Field(..., description="YouTubeハンドル (@name)")
    description: Optional[str] = Field(None, description="チャンネル説明文（任意）")
    youtube_description: Optional[str] = Field(None, description="YouTube上の説明文 / 投稿テンプレ（任意）")
    default_tags: Optional[List[str]] = Field(None, description="投稿時に使うデフォルトタグ（任意）")
    benchmarks: Optional[ChannelBenchmarksSpec] = Field(None, description="チャンネル別ベンチマーク（任意）")
    chapter_count: Optional[int] = Field(None, ge=1, description="章数（任意。sources.yaml に反映）")
    target_chars_min: Optional[int] = Field(None, ge=0, description="目標文字数min（任意。sources.yaml に反映）")
    target_chars_max: Optional[int] = Field(None, ge=0, description="目標文字数max（任意。sources.yaml に反映）")


class ChannelBranding(BaseModel):
    avatar_url: Optional[str] = None
    banner_url: Optional[str] = None
    title: Optional[str] = None
    subscriber_count: Optional[int] = None
    view_count: Optional[int] = None
    video_count: Optional[int] = None
    custom_url: Optional[str] = None
    handle: Optional[str] = None
    url: Optional[str] = None
    launch_date: Optional[str] = None
    theme_color: Optional[str] = None
    updated_at: Optional[str] = None


class ChannelSummaryResponse(BaseModel):
    code: str
    name: Optional[str] = None
    description: Optional[str] = None
    video_count: int = 0
    branding: Optional[ChannelBranding] = None
    spreadsheet_id: Optional[str] = None
    youtube_title: Optional[str] = None
    youtube_handle: Optional[str] = None
    video_workflow: Optional[VideoWorkflowSpec] = None
    genre: Optional[str] = None


class ChannelAuditItemResponse(BaseModel):
    code: str
    name: Optional[str] = None
    youtube_handle: Optional[str] = None
    youtube_url: Optional[str] = None
    avatar_url: Optional[str] = None
    has_youtube_description: bool = False
    default_tags_count: int = 0
    benchmark_channels_count: int = 0
    benchmark_script_samples_count: int = 0
    planning_rows: int = 0
    planning_csv_exists: bool = False
    persona_exists: bool = False
    script_prompt_exists: bool = False
    issues: List[str] = Field(default_factory=list)
