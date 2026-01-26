from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _safe_read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_tail_lines(path: Path, *, max_lines: int) -> List[str]:
    if max_lines <= 0:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return lines
    return lines[-max_lines:]


def _count_files(dir_path: Path, *, exts: Optional[set[str]] = None) -> int:
    if not dir_path.exists() or not dir_path.is_dir():
        return 0
    try:
        if exts:
            return sum(1 for p in dir_path.iterdir() if p.is_file() and p.suffix.lower() in exts)
        return sum(1 for p in dir_path.iterdir() if p.is_file())
    except Exception:
        return 0


def _dir_last_updated(dir_path: Path) -> Optional[str]:
    try:
        candidates = [dir_path]
        for name in (
            "capcut_draft_info.json",
            "auto_run_info.json",
            "image_cues.json",
            "belt_config.json",
            "timeline_manifest.json",
        ):
            p = dir_path / name
            if p.exists():
                candidates.append(p)
        mtime = max(p.stat().st_mtime for p in candidates)
        return _iso_from_ts(mtime)
    except Exception:
        return None


def _parse_channel_id(project_id: str, info: Dict[str, Any]) -> Optional[str]:
    value = info.get("channel_id") or info.get("channel") or ""
    if isinstance(value, str) and value.strip():
        return value.strip().upper()
    if "-" in project_id:
        head = project_id.split("-", 1)[0]
        if head:
            return head.strip().upper()
    return None


def _derive_status(project_dir: Path, info: Dict[str, Any]) -> tuple[str, Optional[str]]:
    # Minimal, UI-friendly heuristics (stringly-typed; UI treats as label only).
    cues_path = project_dir / "image_cues.json"
    images_dir = project_dir / "images"
    draft_path_ref = info.get("draft_path_ref")
    draft_path = str(info.get("draft_path") or "").strip()
    has_draft_hint = bool(draft_path_ref or draft_path)

    if has_draft_hint:
        return "capcut_ready", None
    if images_dir.exists() and _count_files(images_dir, exts=_IMAGE_EXTS) > 0:
        return "images_ready", "build_capcut_draft"
    if cues_path.exists():
        return "cues_ready", "regenerate_images"
    return "pending", "analyze_srt"


@dataclass
class ImageSample:
    path: str
    url: str = ""


@dataclass
class ImageAsset:
    path: str
    url: str = ""
    size_bytes: Optional[int] = None
    modified_at: Optional[str] = None


@dataclass
class ProjectSummary:
    id: str
    title: Optional[str] = None
    status: str = "unknown"
    next_action: Optional[str] = None
    template_used: Optional[str] = None
    image_count: int = 0
    log_count: int = 0
    created_at: Optional[str] = None
    last_updated: Optional[str] = None
    srt_file: Optional[str] = None
    draft_path: Optional[str] = None
    draft_path_ref: Optional[Dict[str, Any]] = None
    channel_id: Optional[str] = None
    channelId: Optional[str] = None


@dataclass
class ProjectDetail:
    summary: ProjectSummary
    images: List[ImageAsset] = field(default_factory=list)
    image_samples: List[ImageSample] = field(default_factory=list)
    log_excerpt: List[str] = field(default_factory=list)
    cues: List[Dict[str, Any]] = field(default_factory=list)
    belt: List[Dict[str, Any]] = field(default_factory=list)
    chapters: List[Dict[str, Any]] = field(default_factory=list)
    srt_preview: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    layers: List[Dict[str, Any]] = field(default_factory=list)


def _load_summary(project_dir: Path) -> ProjectSummary:
    project_id = project_dir.name
    info_path = project_dir / "capcut_draft_info.json"
    auto_path = project_dir / "auto_run_info.json"
    info = _safe_read_json(info_path) if info_path.exists() else {}
    auto = _safe_read_json(auto_path) if auto_path.exists() else {}

    channel_id = _parse_channel_id(project_id, {**auto, **info})
    status, next_action = _derive_status(project_dir, info)

    images_dir = project_dir / "images"
    image_count = _count_files(images_dir, exts=_IMAGE_EXTS)
    if not image_count and isinstance(auto.get("images"), int):
        image_count = int(auto.get("images") or 0)

    logs_dir = project_dir / "logs"
    log_count = _count_files(logs_dir)

    created_at = None
    raw_created = info.get("created_at") or auto.get("timestamp")
    if isinstance(raw_created, str) and raw_created.strip():
        created_at = raw_created.strip()
    else:
        try:
            created_at = _iso_from_ts(project_dir.stat().st_ctime)
        except Exception:
            created_at = None

    last_updated = _dir_last_updated(project_dir)

    title = info.get("title") if isinstance(info.get("title"), str) else None
    template_used = info.get("template_used") if isinstance(info.get("template_used"), str) else None
    srt_file = info.get("srt_file") if isinstance(info.get("srt_file"), str) else None
    draft_path = info.get("draft_path") if isinstance(info.get("draft_path"), str) else None
    draft_path_ref = info.get("draft_path_ref") if isinstance(info.get("draft_path_ref"), dict) else None

    return ProjectSummary(
        id=project_id,
        title=title,
        status=status,
        next_action=next_action,
        template_used=template_used,
        image_count=int(image_count),
        log_count=int(log_count),
        created_at=created_at,
        last_updated=last_updated,
        srt_file=srt_file,
        draft_path=draft_path,
        draft_path_ref=draft_path_ref,
        channel_id=channel_id,
        channelId=channel_id,
    )


def list_projects(output_root: Path) -> List[ProjectSummary]:
    root = Path(output_root)
    if not root.exists():
        return []
    summaries: List[ProjectSummary] = []
    for p in sorted(root.iterdir()):
        if p.name.startswith("."):
            continue
        if not p.is_dir():
            continue
        # Heuristic: treat as project/run dir if it has core files.
        if not (
            (p / "image_cues.json").exists()
            or (p / "images").is_dir()
            or (p / "capcut_draft_info.json").exists()
            or (p / "auto_run_info.json").exists()
        ):
            continue
        summaries.append(_load_summary(p))
    # Newest first (fallback to id sort).
    summaries.sort(key=lambda s: (s.last_updated or "", s.id), reverse=True)
    return summaries


def load_project_detail(output_root: Path, project_id: str) -> Optional[ProjectDetail]:
    root = Path(output_root)
    project_dir = (root / project_id).resolve()
    if not project_dir.exists() or not project_dir.is_dir():
        return None

    summary = _load_summary(project_dir)

    images: List[ImageAsset] = []
    image_samples: List[ImageSample] = []
    images_dir = project_dir / "images"
    if images_dir.exists() and images_dir.is_dir():
        files = sorted([p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_EXTS])
        for img in files:
            try:
                rel = img.relative_to(root)
            except Exception:
                continue
            try:
                stat = img.stat()
                images.append(
                    ImageAsset(
                        path=str(rel),
                        url="",
                        size_bytes=int(stat.st_size),
                        modified_at=_iso_from_ts(stat.st_mtime),
                    )
                )
            except Exception:
                images.append(ImageAsset(path=str(rel), url=""))
        for img in files[: min(8, len(files))]:
            try:
                rel = img.relative_to(root)
            except Exception:
                continue
            image_samples.append(ImageSample(path=str(rel), url=""))

    log_excerpt: List[str] = []
    logs_dir = project_dir / "logs"
    if logs_dir.exists() and logs_dir.is_dir():
        try:
            log_files = sorted([p for p in logs_dir.iterdir() if p.is_file()], key=lambda p: p.stat().st_mtime)
        except Exception:
            log_files = []
        if log_files:
            log_excerpt = _safe_tail_lines(log_files[-1], max_lines=120)

    cues: List[Dict[str, Any]] = []
    cues_path = project_dir / "image_cues.json"
    if cues_path.exists():
        obj = _safe_read_json(cues_path)
        if isinstance(obj.get("cues"), list):
            cues = [c for c in obj.get("cues", []) if isinstance(c, dict)]

    belt: List[Dict[str, Any]] = []
    belt_path = project_dir / "belt_config.json"
    if belt_path.exists():
        obj = _safe_read_json(belt_path)
        belts = obj.get("belts") if isinstance(obj, dict) else None
        if isinstance(belts, list):
            belt = [b for b in belts if isinstance(b, dict)]

    chapters: List[Dict[str, Any]] = []
    plan_path = project_dir / "visual_cues_plan.json"
    if plan_path.exists():
        obj = _safe_read_json(plan_path)
        sections = obj.get("sections") if isinstance(obj, dict) else None
        if isinstance(sections, list):
            for idx, sec in enumerate(sections, start=1):
                if not isinstance(sec, dict):
                    continue
                title = str(sec.get("summary") or "").strip()
                if not title:
                    continue
                chapters.append({"key": f"section_{idx:02d}", "title": title})

    srt_preview: List[str] = []
    if summary.srt_file:
        try:
            srt_path = Path(summary.srt_file)
            if srt_path.exists() and srt_path.is_file():
                srt_preview = srt_path.read_text(encoding="utf-8", errors="ignore").splitlines()[:120]
        except Exception:
            srt_preview = []

    warnings: List[str] = []
    if not (project_dir / "image_cues.json").exists():
        warnings.append("image_cues.json が見つかりません（SRT解析が未実行の可能性）")
    if not (project_dir / "images").exists():
        warnings.append("images/ が見つかりません（画像生成が未実行の可能性）")

    return ProjectDetail(
        summary=summary,
        images=images,
        image_samples=image_samples,
        log_excerpt=log_excerpt,
        cues=cues,
        belt=belt,
        chapters=chapters,
        srt_preview=srt_preview,
        warnings=warnings,
        layers=[],
    )
