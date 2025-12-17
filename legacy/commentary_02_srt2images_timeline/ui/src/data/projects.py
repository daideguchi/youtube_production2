"""Utilities for reading video production project data from output directories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import json

SEC = 1_000_000.0


@dataclass
class ProjectImageAsset:
    path: str
    url: str
    size_bytes: int
    modified_at: str


@dataclass
class ProjectImageSample:
    path: str
    url: str


@dataclass
class ProjectSummary:
    id: str
    title: Optional[str]
    status: str
    next_action: str
    template_used: Optional[str]
    image_count: int
    log_count: int
    created_at: Optional[str]
    last_updated: Optional[str]
    srt_file: Optional[str]
    draft_path: Optional[str]
    channel_id: Optional[str]


@dataclass
class ProjectCue:
    index: int
    start_sec: float
    end_sec: float
    duration_sec: float
    summary: Optional[str]
    text: Optional[str]
    visual_focus: Optional[str]
    emotional_tone: Optional[str]
    prompt: Optional[str]
    context_reason: Optional[str]
    section_type: Optional[str] = None
    refined_prompt: Optional[str] = None
    role_tag: Optional[str] = None
    role_asset: Optional[Dict[str, Any]] = None


@dataclass
class ProjectBeltEntry:
    text: str
    start: float
    end: float


@dataclass
class ProjectChapterEntry:
    key: str
    title: str


@dataclass
class ProjectDetail:
    summary: ProjectSummary
    images: List[ProjectImageAsset]
    image_samples: List[ProjectImageSample]
    log_excerpt: List[str]
    cues: List[ProjectCue]
    belt: List[ProjectBeltEntry]
    chapters: List[ProjectChapterEntry]
    srt_preview: List[str]
    warnings: List[str]
    layers: List["ProjectLayer"]


@dataclass
class ProjectLayerSegment:
    id: str
    start_sec: float
    end_sec: float
    duration_sec: float
    material_id: Optional[str]
    material_name: Optional[str]
    material_path: Optional[str]
    transition_name: Optional[str]
    transition_duration_sec: Optional[float]


@dataclass
class ProjectLayer:
    id: str
    name: str
    type: str
    segment_count: int
    duration_sec: float
    has_fade: bool
    segments: List[ProjectLayerSegment]


VIDEO_STATUS_MESSAGES = {
    "draft_ready": "CapCutドラフトを開いてタイムラインを確認する",
    "images_ready": "CapCutドラフトを生成する",
    "storyboard_ready": "画像生成を実行する",
    "pending": "SRTからストーリーボードを生成する",
}


def list_projects(output_root: Path) -> List[ProjectSummary]:
    summaries: List[ProjectSummary] = []
    for project_dir in sorted(_iter_project_dirs(output_root), key=lambda p: p.name):
        summary = _build_project_summary(project_dir, output_root)
        if summary:
            summaries.append(summary)
    return summaries


def load_project_detail(output_root: Path, project_id: str) -> Optional[ProjectDetail]:
    project_dir = (output_root / project_id).resolve()
    if not project_dir.exists() or project_dir.parent.resolve() != output_root.resolve():
        return None
    summary = _build_project_summary(project_dir, output_root)
    if summary is None:
        return None
    return _build_project_detail(project_dir, summary, output_root)


def _iter_project_dirs(output_root: Path) -> Iterable[Path]:
    if not output_root.exists():
        return []
    try:
        return [child for child in output_root.iterdir() if child.is_dir()]
    except Exception:
        return []


def _build_project_summary(project_dir: Path, output_root: Path) -> Optional[ProjectSummary]:
    info = _load_json(project_dir / "capcut_draft_info.json")
    images_dir = project_dir / "images"
    logs_dir = project_dir / "logs"
    chapters_path = project_dir / "chapters.json"
    capcut_link = project_dir / "capcut_draft"

    image_count = _count_files(images_dir, {".png", ".jpg", ".jpeg", ".webp"})
    log_count = _count_files(logs_dir, {".log", ".txt"})
    status = _determine_status(capcut_link.exists(), image_count, chapters_path.exists())
    next_action = VIDEO_STATUS_MESSAGES.get(status, "進捗を確認してください")

    channel_token: Optional[str] = None
    if isinstance(info, dict):
        channel_token = info.get("channel_id")
    if not channel_token:
        channel_token = project_dir.name.split("-", 1)[0] if "-" in project_dir.name else None

    return ProjectSummary(
        id=project_dir.name,
        title=info.get("title") if isinstance(info, dict) else None,
        status=status,
        next_action=next_action,
        template_used=info.get("template_used") if isinstance(info, dict) else None,
        image_count=image_count,
        log_count=log_count,
        created_at=info.get("created_at") if isinstance(info, dict) else None,
        last_updated=_collect_last_updated(project_dir, info_path=project_dir / "capcut_draft_info.json", images_dir=images_dir, logs_dir=logs_dir),
        srt_file=info.get("srt_file") if isinstance(info, dict) else None,
        draft_path=info.get("draft_path") if isinstance(info, dict) else None,
        channel_id=channel_token,
    )


def _build_project_detail(project_dir: Path, summary: ProjectSummary, output_root: Path) -> ProjectDetail:
    image_assets: List[ProjectImageAsset] = []
    images_dir = project_dir / "images"
    if images_dir.exists():
        for image in sorted(images_dir.iterdir()):
            if image.is_file() and image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                rel = image.relative_to(output_root)
                stat = image.stat()
                modified_at = datetime.fromtimestamp(stat.st_mtime).isoformat()
                image_assets.append(
                    ProjectImageAsset(
                        path=str(rel),
                        url=str(rel),
                        size_bytes=stat.st_size,
                        modified_at=modified_at,
                    )
                )

    image_samples: List[ProjectImageSample] = [
        ProjectImageSample(path=asset.path, url=asset.url)
        for asset in image_assets[:8]
    ]

    log_excerpt = _tail_log_lines(project_dir / "logs", max_lines=12)

    cues_json = _load_json(project_dir / "image_cues.json")
    cues: List[ProjectCue] = []
    for cue in cues_json.get("cues", []):
        try:
            cues.append(
                ProjectCue(
                    index=int(cue.get("index", 0)),
                    start_sec=float(cue.get("start_sec", 0.0)),
                    end_sec=float(cue.get("end_sec", 0.0)),
                    duration_sec=float(cue.get("duration_sec", 0.0)),
                    summary=cue.get("summary"),
                    text=cue.get("text"),
                    visual_focus=cue.get("visual_focus"),
                    emotional_tone=cue.get("emotional_tone"),
                    prompt=cue.get("prompt"),
                    context_reason=cue.get("context_reason"),
                    section_type=cue.get("section_type"),
                    refined_prompt=cue.get("refined_prompt"),
                    role_tag=cue.get("role_tag") or cue.get("role"),
                    role_asset=cue.get("role_asset"),
                )
            )
        except Exception:
            continue

    belt_json = _load_json(project_dir / "belt_config.json")
    belt: List[ProjectBeltEntry] = []
    for entry in belt_json.get("belts", []):
        try:
            belt.append(
                ProjectBeltEntry(
                    text=str(entry.get("text", "")),
                    start=float(entry.get("start", 0.0)),
                    end=float(entry.get("end", 0.0)),
                )
            )
        except Exception:
            continue

    chapters_json = _load_json(project_dir / "chapters.json")
    chapters: List[ProjectChapterEntry] = []
    for key in sorted(chapters_json.keys()):
        value = chapters_json.get(key)
        if isinstance(value, str):
            chapters.append(ProjectChapterEntry(key=key, title=value))

    srt_preview: List[str] = []
    warnings: List[str] = []
    if summary.srt_file:
        srt_path = Path(summary.srt_file)
        if not srt_path.is_absolute():
            srt_path = (output_root.parent / summary.srt_file).resolve()
        if srt_path.exists():
            try:
                with srt_path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for idx, line in enumerate(handle):
                        srt_preview.append(line.rstrip("\n"))
                        if idx >= 15:
                            break
            except Exception:
                warnings.append("SRTファイルの読み取りに失敗しました。")
        else:
            warnings.append("SRTファイルが見つかりません。")

    if not (project_dir / "capcut_draft").exists():
        warnings.append("CapCutドラフトへのリンクが見つかりません。")
    if summary.status != "draft_ready":
        warnings.append("ドラフトが完成していません。")
    if summary.image_count == 0:
        warnings.append("画像が生成されていません。")

    layers, layer_warnings = _build_layer_summaries(project_dir, summary)
    warnings.extend(layer_warnings)

    deduped_warnings = list(dict.fromkeys(warnings))

    return ProjectDetail(
        summary=summary,
        images=image_assets,
        image_samples=image_samples,
        log_excerpt=log_excerpt,
        cues=cues,
        belt=belt,
        chapters=chapters,
        srt_preview=srt_preview,
        warnings=deduped_warnings,
        layers=layers,
    )


def _build_layer_summaries(project_dir: Path, summary: ProjectSummary) -> tuple[List[ProjectLayer], List[str]]:
    warnings: List[str] = []
    draft_dir = _resolve_capcut_draft_dir(project_dir, summary)
    if not draft_dir:
        return [], warnings

    draft_content_path = draft_dir / "draft_content.json"
    if not draft_content_path.exists():
        warnings.append("draft_content.json が見つからず、レイヤー情報を取得できませんでした。")
        return [], warnings

    try:
        data = json.loads(draft_content_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        warnings.append(f"draft_content.json の解析に失敗しました: {exc}")
        return [], warnings

    media_lookup, transition_lookup = _index_materials(data.get("materials", {}))
    layers: List[ProjectLayer] = []
    tracks = data.get("tracks", []) or []

    for idx, track in enumerate(tracks):
        track_id = str(track.get("id") or f"track_{idx}")
        track_name = track.get("name") or track_id
        track_type = track.get("type", "unknown")
        segment_summaries: List[ProjectLayerSegment] = []

        for seg_index, seg in enumerate(track.get("segments", []) or []):
            seg_id = str(seg.get("id") or f"segment_{seg_index}")
            target = seg.get("target_timerange", {}) or {}
            start_sec = _micro_to_seconds(target.get("start"))
            duration_sec = _micro_to_seconds(target.get("duration"))
            end_sec = start_sec + duration_sec

            material_id = seg.get("material_id")
            if material_id:
                media = media_lookup.get(str(material_id)) or {}
            else:
                media = {}
            transition_name, transition_duration = _resolve_transition(seg, transition_lookup)

            segment_summaries.append(
                ProjectLayerSegment(
                    id=seg_id,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    duration_sec=duration_sec,
                    material_id=str(material_id) if material_id else None,
                    material_name=media.get("name"),
                    material_path=media.get("path"),
                    transition_name=transition_name,
                    transition_duration_sec=transition_duration,
                )
            )

        total_duration = sum(item.duration_sec for item in segment_summaries)
        has_fade = any(item.transition_name for item in segment_summaries)

        layers.append(
            ProjectLayer(
                id=track_id,
                name=track_name,
                type=track_type,
                segment_count=len(segment_summaries),
                duration_sec=total_duration,
                has_fade=has_fade,
                segments=segment_summaries,
            )
        )

    return layers, warnings


def _resolve_transition(segment: Dict, transition_lookup: Dict[str, Dict[str, Optional[float]]]) -> tuple[Optional[str], Optional[float]]:
    for ref in segment.get("extra_material_refs", []) or []:
        ref_id = str(ref)
        if ref_id in transition_lookup:
            info = transition_lookup[ref_id]
            return info.get("name"), info.get("duration_sec")
    return None, None


def _index_materials(materials: Dict[str, object]) -> tuple[Dict[str, Dict[str, Optional[str]]], Dict[str, Dict[str, Optional[float]]]]:
    media_lookup: Dict[str, Dict[str, Optional[str]]] = {}
    transition_lookup: Dict[str, Dict[str, Optional[float]]] = {}

    for bucket_name, entries in materials.items():
        if not isinstance(entries, list):
            continue
        if bucket_name in {"videos", "images", "audios", "flowers", "effects", "placeholders"}:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                mid = entry.get("id") or entry.get("material_id")
                if not mid:
                    continue
                media_lookup[str(mid)] = {
                    "path": entry.get("path") or entry.get("media_path"),
                    "name": entry.get("material_name") or entry.get("name"),
                }
        elif bucket_name == "transitions":
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                tid = entry.get("id")
                if not tid:
                    continue
                transition_lookup[str(tid)] = {
                    "name": entry.get("name") or "transition",
                    "duration_sec": _micro_to_seconds(entry.get("duration")),
                }

    return media_lookup, transition_lookup


def _resolve_capcut_draft_dir(project_dir: Path, summary: ProjectSummary) -> Optional[Path]:
    candidate = project_dir / "capcut_draft"
    if candidate.exists():
        try:
            return candidate.resolve()
        except OSError:
            return candidate

    if summary.draft_path:
        draft_path = Path(summary.draft_path)
        if not draft_path.is_absolute():
            draft_path = (project_dir / summary.draft_path).resolve()
        if draft_path.exists():
            return draft_path
    return None


def _micro_to_seconds(value: Optional[float]) -> float:
    try:
        return float(value) / SEC
    except (TypeError, ValueError):
        return 0.0


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _count_files(directory: Path, extensions: set[str]) -> int:
    if not directory.exists():
        return 0
    total = 0
    for child in directory.iterdir():
        if child.is_file() and child.suffix.lower() in extensions:
            total += 1
    return total


def _collect_last_updated(project_dir: Path, info_path: Path, images_dir: Path, logs_dir: Path) -> Optional[str]:
    timestamps: List[float] = []

    def push(path: Path) -> None:
        try:
            timestamps.append(path.stat().st_mtime)
        except FileNotFoundError:
            return

    push(project_dir)
    push(info_path)
    for directory in (images_dir, logs_dir):
        if directory.exists():
            for child in directory.iterdir():
                if child.is_file():
                    push(child)

    if not timestamps:
        return None
    return datetime.fromtimestamp(max(timestamps)).isoformat(timespec="seconds")


def _tail_log_lines(logs_dir: Path, max_lines: int) -> List[str]:
    if not logs_dir.exists():
        return []
    log_files = [child for child in logs_dir.iterdir() if child.is_file()]
    if not log_files:
        return []
    latest = max(log_files, key=lambda item: item.stat().st_mtime)
    try:
        lines = latest.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    return lines[-max_lines:]


def _determine_status(has_draft: bool, image_count: int, has_storyboard: bool) -> str:
    if has_draft:
        return "draft_ready"
    if image_count > 0:
        return "images_ready"
    if has_storyboard:
        return "storyboard_ready"
    return "pending"
