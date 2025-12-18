"""Video production API routes (commentary_02 integration)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Annotated, Literal, Tuple
import subprocess
import re

from fastapi import APIRouter, HTTPException, Query, UploadFile, File as FastAPIFile, Form, Path as FastAPIPath
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from factory_common.artifacts.utils import atomic_write_json, utc_now_iso
from factory_common.artifacts.visual_cues_plan import VisualCuesPlanArtifactV1, VisualCuesPlanSection
from factory_common.paths import (
    audio_artifacts_root,
    logs_root,
    repo_root as ssot_repo_root,
    script_data_root,
    video_pkg_root,
    video_runs_root,
)

PROJECT_ROOT = ssot_repo_root()
COMMENTARY02_ROOT = video_pkg_root()
VALID_PROJECT_ID_PATTERN = r"^[A-Za-z0-9_-]+$"


class CapcutInstallRequest(BaseModel):
    overwrite: bool = False


class CapcutInstallResult(BaseModel):
    status: str
    source: str
    target: str
    overwrite: bool


class ImageRegeneratePayload(BaseModel):
    prompt: Optional[str] = None
    prompt_suffix: Optional[str] = None


class BeltEntry(BaseModel):
    text: str
    start: float
    end: float


class BeltPatchEntry(BaseModel):
    index: int
    text: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None


class BeltPatchPayload(BaseModel):
    entries: List[BeltPatchEntry]


class CapcutTransformPayload(BaseModel):
    tx: Optional[float] = None
    ty: Optional[float] = None
    scale: Optional[float] = None
    crossfade_sec: Optional[float] = None
    fade_duration_sec: Optional[float] = None
    opening_offset: Optional[float] = None


class ChannelPresetPositionPayload(BaseModel):
    tx: Optional[float] = None
    ty: Optional[float] = None
    scale: Optional[float] = None


class ChannelPresetBeltPayload(BaseModel):
    enabled: Optional[bool] = None
    opening_offset: Optional[float] = None
    requires_config: Optional[bool] = None


class ChannelPresetUpdatePayload(BaseModel):
    name: Optional[str] = None
    prompt_template: Optional[str] = None
    style: Optional[str] = None
    capcut_template: Optional[str] = None
    persona_required: Optional[bool] = None
    image_min_bytes: Optional[int] = Field(default=None, ge=0)
    position: Optional[ChannelPresetPositionPayload] = None
    belt: Optional[ChannelPresetBeltPayload] = None
    notes: Optional[str] = None
    status: Optional[str] = None


class VisualCuesPlanUpdatePayload(BaseModel):
    status: Literal["pending", "ready"]
    sections: List[VisualCuesPlanSection]
    style_hint: Optional[str] = None


if COMMENTARY02_ROOT.exists():
    for candidate in (COMMENTARY02_ROOT, COMMENTARY02_ROOT / "src"):
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)

try:
    from src.data.projects import list_projects, load_project_detail  # type: ignore
    from commentary_02_srt2images_timeline.server.jobs import (  # type: ignore
        JobManager,
        evaluate_capcut_guard,
        job_to_dict,
    )
except Exception:  # pragma: no cover - commentary_02 assets missing
    list_projects = None  # type: ignore
    load_project_detail = None  # type: ignore
    JobManager = None  # type: ignore
    video_router: Optional[APIRouter] = None
else:
    OUTPUT_ROOT = video_runs_root()
    TOOLS_ROOT = COMMENTARY02_ROOT / "tools"
    INPUT_ROOT = audio_artifacts_root() / "final"
    CONFIG_ROOT = COMMENTARY02_ROOT / "config"
    CHANNEL_PRESETS_PATH = CONFIG_ROOT / "channel_presets.json"
    _env_capcut_root = os.getenv("CAPCUT_DRAFT_ROOT")
    CAPCUT_DRAFT_ROOT = (
        Path(_env_capcut_root).expanduser()
        if _env_capcut_root
        else Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    )
    JOB_LOG_ROOT = logs_root() / "ui_hub" / "video_production"
    # 旧 commentary_01_srtfile_v2 -> script_pipeline に移行済み
    COMMENTARY01_DATA_ROOT = script_data_root()
    CHANNEL_CODE_PATTERN = re.compile(r"([A-Za-z]{2}\d{2})", re.IGNORECASE)
    VIDEO_NUMBER_PATTERN = re.compile(r"(?:(?<=-)|(?<=_)|^)(\d{3})(?=$|[^0-9])")

    def _load_project_detail(project_id: str):
        return load_project_detail(OUTPUT_ROOT, project_id)

    job_manager = JobManager(
        project_root=COMMENTARY02_ROOT,
        output_root=OUTPUT_ROOT,
        tools_root=TOOLS_ROOT,
        log_root=JOB_LOG_ROOT,
        scripts_root=PROJECT_ROOT / "scripts",
        project_loader=_load_project_detail,
        python_executable=sys.executable,
    )

    _CHANNEL_CACHE: Dict[str, Any] = {"mtime": None, "data": {}}
    GENERATION_OPTIONS_FILENAME = "video_generation_options.json"
    DEFAULT_GENERATION_OPTIONS = {
        "imgdur": 20.0,
        "crossfade": 0.5,
        "fps": 30,
        "style": "",
        "size": "1920x1080",
        "fit": "cover",
        "margin": 0,
    }

    class VideoGenerationOptionsPayload(BaseModel):
        imgdur: float = Field(20.0, ge=1, le=300)
        crossfade: float = Field(0.0, ge=0, le=30)
        fps: int = Field(30, ge=1, le=240)
        style: str = ""
        size: str = Field("1920x1080", pattern=r"^\d+x\d+$")
        fit: Literal["cover", "contain", "fill"] = "cover"
        margin: int = Field(0, ge=0, le=500)

    video_router = APIRouter(prefix="/api/video-production", tags=["video-production"])

    @video_router.get("/projects")
    def get_projects():
        summaries = list_projects(OUTPUT_ROOT)
        enriched: List[Dict[str, Any]] = []
        for summary in summaries:
            data = jsonable_encoder(summary)
            data["source_status"] = _compute_source_status(data.get("id"))
            enriched.append(data)
        return enriched

    @video_router.get("/projects/{project_id}")
    def get_project(project_id: str):
        detail = load_project_detail(OUTPUT_ROOT, project_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="project not found")

        project_dir = _resolve_project_dir(project_id)
        summary = getattr(detail, "summary", None)
        channel_code: Optional[str] = None
        if summary is not None:
            channel_code = getattr(summary, "channelId", None) or getattr(summary, "channel_id", None)
        if not channel_code and "-" in project_id:
            channel_code = project_id.split("-", 1)[0]
        preset = _get_channel_preset(channel_code)
        guard = evaluate_capcut_guard(project_dir, preset, raise_on_failure=False)

        for sample in detail.image_samples:
            sample.url = f"/api/video-production/assets/{sample.path}"
        for asset in detail.images:
            asset.url = f"/api/video-production/assets/{asset.path}"

        payload = jsonable_encoder(detail)
        payload["guard"] = guard
        payload["capcut"] = _load_capcut_settings(project_dir)
        payload["source_status"] = _compute_source_status(project_id)
        payload["generation_options"] = _load_generation_options(project_id)
        payload["artifacts"] = _summarize_project_artifacts(project_dir)
        return payload

    @video_router.get("/projects/{project_id}/srt-segments")
    def get_project_srt_segments(project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)]):
        project_dir = _resolve_project_dir(project_id)
        seg_path = project_dir / "srt_segments.json"
        if not seg_path.exists():
            raise HTTPException(status_code=404, detail="srt_segments.json not found")
        data = _safe_read_json_limited(seg_path)
        if not data:
            raise HTTPException(status_code=400, detail="srt_segments.json is invalid or too large")
        return data

    @video_router.get("/projects/{project_id}/visual-cues-plan")
    def get_project_visual_cues_plan(project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)]):
        project_dir = _resolve_project_dir(project_id)
        plan_path = project_dir / "visual_cues_plan.json"
        if not plan_path.exists():
            raise HTTPException(status_code=404, detail="visual_cues_plan.json not found")
        data = _safe_read_json_limited(plan_path)
        if not data:
            raise HTTPException(status_code=400, detail="visual_cues_plan.json is invalid or too large")
        try:
            plan = VisualCuesPlanArtifactV1.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid visual cues plan: {exc}") from exc
        return plan.model_dump(mode="json", by_alias=True)

    @video_router.put("/projects/{project_id}/visual-cues-plan")
    def update_project_visual_cues_plan(
        project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)],
        payload: VisualCuesPlanUpdatePayload,
    ):
        project_dir = _resolve_project_dir(project_id)
        plan_path = project_dir / "visual_cues_plan.json"
        if not plan_path.exists():
            raise HTTPException(status_code=404, detail="visual_cues_plan.json not found")
        current_obj = _safe_read_json_limited(plan_path)
        if not current_obj:
            raise HTTPException(status_code=400, detail="visual_cues_plan.json is invalid or too large")
        try:
            current = VisualCuesPlanArtifactV1.model_validate(current_obj)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid visual cues plan: {exc}") from exc

        updated_obj = current.model_dump(mode="json", by_alias=True)
        updated_obj["generated_at"] = utc_now_iso()
        updated_obj["status"] = payload.status
        updated_obj["sections"] = [s.model_dump(mode="json") for s in payload.sections]
        if payload.style_hint is not None:
            updated_obj["style_hint"] = payload.style_hint

        try:
            updated = VisualCuesPlanArtifactV1.model_validate(updated_obj)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid visual cues plan: {exc}") from exc

        atomic_write_json(plan_path, updated.model_dump(mode="json", by_alias=True))
        return updated.model_dump(mode="json", by_alias=True)

    @video_router.get("/projects/{project_id}/generation-options")
    def read_generation_options(project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)]):
        _resolve_project_dir(project_id)
        return _load_generation_options(project_id)

    @video_router.put("/projects/{project_id}/generation-options")
    def update_generation_options(
        project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)],
        payload: VideoGenerationOptionsPayload,
    ):
        _resolve_project_dir(project_id)
        return _save_generation_options(project_id, payload.dict())

    @video_router.post("/projects", status_code=201)
    async def create_project(
        project_id: str = Form(...),
        channel_id: Optional[str] = Form(default=None),
        target_sections: Optional[int] = Form(default=None),
        existing_srt_path: Optional[str] = Form(default=None),
        srt_file: Optional[UploadFile] = FastAPIFile(None),
    ):
        project_dir = (OUTPUT_ROOT / project_id).resolve()
        if project_dir.exists():
            raise HTTPException(status_code=400, detail=f"project already exists: {project_id}")

        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "logs").mkdir(exist_ok=True)
        (project_dir / "images").mkdir(exist_ok=True)

        if existing_srt_path:
            src_path = (PROJECT_ROOT / existing_srt_path).resolve()
            if not src_path.exists():
                raise HTTPException(status_code=404, detail=f"Existing SRT not found: {existing_srt_path}")
            srt_target = project_dir / src_path.name
            shutil.copy(src_path, srt_target)
        else:
            if not srt_file or not srt_file.filename.endswith(".srt"):
                raise HTTPException(status_code=400, detail="SRTファイルをアップロードしてください")
            srt_target = project_dir / srt_file.filename
            with open(srt_target, "wb") as buffer:
                shutil.copyfileobj(srt_file.file, buffer)

        info_path = project_dir / "capcut_draft_info.json"
        info = {
            "project_id": project_id,
            "channel_id": channel_id,
            "srt_file": str(srt_target.relative_to(COMMENTARY02_ROOT)),
            "template_used": None,
            "draft_path": None,
            "title": None,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "last_updated": datetime.utcnow().isoformat() + "Z",
            "transform": {},
        }

        preset = _get_channel_preset(channel_id)
        if preset:
            template_name = preset.get("capcut_template")
            if template_name:
                info["template_used"] = template_name
            position = preset.get("position") or {}
            tx = float(position.get("tx", 0.0))
            ty = float(position.get("ty", 0.0))
            scale = float(position.get("scale", 1.0))
            info["transform"] = {"tx": tx, "ty": ty, "scale": scale}
            belt_cfg = preset.get("belt") or {}
            if "opening_offset" in belt_cfg:
                info["opening_offset"] = belt_cfg["opening_offset"]
            if "requires_config" in belt_cfg:
                info["belt_requires_config"] = belt_cfg.get("requires_config")
        else:
            info["transform"] = {"tx": 0.0, "ty": 0.0, "scale": 1.0}
        info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "project_id": project_id,
            "output_dir": str(project_dir),
            "srt_file": str(srt_target.relative_to(COMMENTARY02_ROOT)),
            "channel_id": channel_id,
            "target_sections": target_sections,
        }

    @video_router.post("/projects/{project_id}/jobs")
    def create_job(project_id: str, payload: Dict[str, Any]):
        action = payload.get("action")
        allowed_actions = {
            "analyze_srt",
            "regenerate_images",
            "generate_belt",
            "validate_capcut",
            "build_capcut_draft",
            "render_remotion",
            "upload_remotion_drive",
        }
        if action not in allowed_actions:
            raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")
        project_dir = OUTPUT_ROOT / project_id
        if not project_dir.exists():
            raise HTTPException(status_code=404, detail=f"Project output not found: {project_id}")

        merged_options: Dict[str, Any] = dict(payload.get("options") or {})
        if action in {"analyze_srt", "regenerate_images", "render_remotion"}:
            generation_defaults = _load_generation_options(project_id)
            for key, value in generation_defaults.items():
                merged_options.setdefault(key, value)

        record = job_manager.create_job(
            project_id=project_id,
            action=action,
            options=merged_options,
            note=payload.get("note"),
        )
        return job_to_dict(record)

    @video_router.get("/jobs")
    def list_jobs(project_id: Optional[str] = Query(default=None), limit: Optional[int] = Query(default=None, ge=1, le=100)):
        records = job_manager.list_jobs(project_id=project_id, limit=limit)
        return [job_to_dict(record) for record in records]

    @video_router.get("/jobs/{job_id}")
    def get_job(job_id: str):
        record = job_manager.get_job(job_id)
        if not record:
            raise HTTPException(status_code=404, detail="job not found")
        return job_to_dict(record)

    @video_router.get("/jobs/{job_id}/log")
    def get_job_log(
        job_id: str,
        tail: Optional[int] = Query(
            default=None,
            ge=1,
            le=5000,
            description="Return only the last N lines",
        ),
        search: Optional[str] = Query(
            default=None,
            description="Case-insensitive substring filter"
        ),
        response_format: Literal["text", "json"] = Query(
            default="text",
            alias="format",
            description="Response format (text or json)",
        ),
    ):
        record = job_manager.get_job(job_id)
        if not record or not record.log_path or not record.log_path.exists():
            raise HTTPException(status_code=404, detail="log not found")
        raw_text = record.log_path.read_text(encoding="utf-8")
        lines = raw_text.splitlines()
        total_lines = len(lines)
        filtered = lines
        if search:
            keyword = search.lower()
            filtered = [line for line in filtered if keyword in line.lower()]
        if tail is not None and tail < len(filtered):
            filtered = filtered[-tail:]

        if response_format == "json":
            return {
                "job_id": job_id,
                "total_lines": total_lines,
                "returned_lines": len(filtered),
                "tail": tail,
                "search": search,
                "lines": filtered,
            }

        text = "\n".join(filtered)
        if text and not text.endswith("\n"):
            text += "\n"
        return PlainTextResponse(text)

    @video_router.get("/assets/{rest_path:path}")
    def serve_assets(rest_path: str):
        target = OUTPUT_ROOT / rest_path
        if not target.exists():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(target)

    @video_router.post("/projects/{project_id}/images/replace")
    async def replace_project_image(
        project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)],
        image_path: str = Form(...),
        file: UploadFile = FastAPIFile(...),
    ):
        if not image_path:
            raise HTTPException(status_code=400, detail="image_path is required")
        project_dir = _resolve_project_dir(project_id)
        rel_path = Path(image_path)
        if rel_path.is_absolute():
            raise HTTPException(status_code=400, detail="image_path must be relative")
        target_path = (OUTPUT_ROOT / rel_path).resolve()
        try:
            relative_to_project = target_path.relative_to(project_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="image_path must belong to the project directory") from exc
        if "images" not in relative_to_project.parts:
            raise HTTPException(status_code=400, detail="image_path must point inside the images directory")
        if not target_path.exists() or not target_path.is_file():
            raise HTTPException(status_code=404, detail="image not found")
        if target_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise HTTPException(status_code=400, detail="image_path must point to an image asset")
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="uploaded file must be an image")

        temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
        try:
            with open(temp_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            os.replace(temp_path, target_path)
        except Exception as exc:  # pragma: no cover - I/O failure
            if temp_path.exists():
                temp_path.unlink()
            raise HTTPException(status_code=500, detail=f"failed to replace image: {exc}") from exc

        stat = target_path.stat()
        rel = target_path.relative_to(OUTPUT_ROOT)
        return {
            "path": str(rel),
            "url": f"/api/video-production/assets/{rel}",
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }

    @video_router.get("/projects/{project_id}/srt")
    def get_project_srt(project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)]):
        srt_path = _resolve_srt_path(project_id)
        if not srt_path.exists():
            raise HTTPException(status_code=404, detail=f"SRT not found: {srt_path}")
        if srt_path.suffix.lower() != ".srt":
            raise HTTPException(status_code=400, detail="SRT path is not an .srt file")
        try:
            stat = srt_path.stat()
            content = srt_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to read srt: {exc}") from exc
        return {
            "name": srt_path.name,
            "path": str(srt_path),
            "content": content,
            "size_bytes": stat.st_size,
            "modified_time": stat.st_mtime,
        }

    @video_router.put("/projects/{project_id}/srt")
    def update_project_srt(
        project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)],
        payload: Dict[str, Any],
    ):
        srt_path = _resolve_srt_path(project_id)
        content = payload.get("content", "")
        if srt_path.suffix.lower() != ".srt":
            raise HTTPException(status_code=400, detail="SRT path is not an .srt file")
        try:
            srt_path.parent.mkdir(parents=True, exist_ok=True)
            srt_path.write_text(str(content), encoding="utf-8")
            stat = srt_path.stat()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to write srt: {exc}") from exc
        return {
            "ok": True,
            "name": srt_path.name,
            "path": str(srt_path),
            "size_bytes": stat.st_size,
            "modified_time": stat.st_mtime,
        }

    @video_router.get("/projects/{project_id}/belt")
    def get_project_belt(project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)]):
        project_dir = _resolve_project_dir(project_id)
        belt_path = project_dir / "belt_config.json"
        data = _safe_read_json(belt_path)
        belts = data.get("belts", [])
        return {"belts": belts}

    @video_router.patch("/projects/{project_id}/belt")
    def patch_project_belt(
        project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)],
        payload: BeltPatchPayload,
    ):
        project_dir = _resolve_project_dir(project_id)
        belt_path = project_dir / "belt_config.json"
        data = _safe_read_json(belt_path)
        belts = data.get("belts")
        if not isinstance(belts, list):
            raise HTTPException(status_code=400, detail="belt_config.json does not contain a belts array")
        for entry in payload.entries:
            if entry.index < 0 or entry.index >= len(belts):
                raise HTTPException(status_code=404, detail=f"belt index {entry.index} not found")
            target = belts[entry.index]
            if entry.text is not None:
                target["text"] = entry.text
            if entry.start is not None:
                target["start"] = entry.start
            if entry.end is not None:
                target["end"] = entry.end
        belt_path.write_text(json.dumps({"belts": belts}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"belts": belts}

    @video_router.get("/projects/{project_id}/capcut-settings")
    def get_project_capcut_settings(project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)]):
        project_dir = _resolve_project_dir(project_id)
        return _load_capcut_settings(project_dir)

    @video_router.patch("/projects/{project_id}/capcut-settings")
    def patch_project_capcut_settings(
        project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)],
        payload: CapcutTransformPayload,
    ):
        project_dir = _resolve_project_dir(project_id)
        info_path = project_dir / "capcut_draft_info.json"
        info = _safe_read_json(info_path)
        if not info:
            info = {}
        transform = info.get("transform") or {}
        changed = False
        for key in ("tx", "ty", "scale"):
            value = getattr(payload, key)
            if value is not None:
                transform[key] = float(value)
                changed = True
        if transform:
            info["transform"] = transform
        if payload.crossfade_sec is not None:
            info["crossfade_sec"] = float(payload.crossfade_sec)
            changed = True
            _update_image_cues_crossfade(project_dir, float(payload.crossfade_sec))
        if payload.fade_duration_sec is not None:
            info["fade_duration_sec"] = float(payload.fade_duration_sec)
            changed = True
        if payload.opening_offset is not None:
            info["opening_offset"] = float(payload.opening_offset)
            changed = True
        if changed:
            info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return _load_capcut_settings(project_dir)

    @video_router.post("/projects/{project_id}/images/{image_index}/regenerate")
    def regenerate_project_image(
        project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)],
        image_index: int,
        payload: ImageRegeneratePayload,
    ):
        project_dir = _resolve_project_dir(project_id)
        cues_path = project_dir / "image_cues.json"
        if not cues_path.exists():
            raise HTTPException(status_code=404, detail="image_cues.json not found")
        try:
            cues_data = json.loads(cues_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"failed to parse image_cues.json: {exc}") from exc
        cues = cues_data.get("cues")
        if not isinstance(cues, list):
            raise HTTPException(status_code=400, detail="image_cues.json does not contain a cues array")
        if image_index < 0 or image_index >= len(cues):
            raise HTTPException(status_code=404, detail=f"image index {image_index} out of range")
        cue_entry = cues[image_index]
        if payload.prompt is not None:
            cue_entry["prompt"] = payload.prompt
        cues_path.write_text(json.dumps(cues_data, ensure_ascii=False, indent=2), encoding="utf-8")

        command = [
            sys.executable,
            str(TOOLS_ROOT / "regenerate_single_image.py"),
            "--run-dir",
            str(project_dir),
            "--image-index",
            str(image_index + 1),
        ]
        if payload.prompt_suffix:
            command.extend(["--prompt-suffix", payload.prompt_suffix])
        try:
            subprocess.run(command, cwd=COMMENTARY02_ROOT, check=True)
        except subprocess.CalledProcessError as exc:  # pragma: no cover - external script failure
            raise HTTPException(status_code=500, detail=f"image regeneration failed: {exc}") from exc

        images_dir = project_dir / "images"
        if not images_dir.exists():
            raise HTTPException(status_code=500, detail="images directory missing after regeneration")
        image_files = sorted(
            [
                child
                for child in images_dir.iterdir()
                if child.is_file() and child.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ]
        )
        if image_index >= len(image_files):
            raise HTTPException(status_code=500, detail="regenerated image not found at expected index")
        target_path = image_files[image_index]
        rel = target_path.relative_to(OUTPUT_ROOT)
        stat = target_path.stat()
        return {
            "path": str(rel),
            "url": f"/api/video-production/assets/{rel}",
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }

    def _resolve_project_dir(project_id: str) -> Path:
        project_dir = (OUTPUT_ROOT / project_id).resolve()
        if not project_dir.exists() or not project_dir.is_dir():
            raise HTTPException(status_code=404, detail="project not found")
        return project_dir

    def _resolve_project_detail(project_id: str):
        detail = load_project_detail(OUTPUT_ROOT, project_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="project not found")
        return detail

    def _resolve_srt_path(project_id: str) -> Path:
        detail = _resolve_project_detail(project_id)
        srt_rel = getattr(detail.summary, "srt_file", None)
        if not srt_rel:
            raise HTTPException(status_code=404, detail="SRT not linked for this project")
        candidate = Path(srt_rel)
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / srt_rel).resolve()
        repo_root = PROJECT_ROOT.parent.resolve()
        try:
            candidate.relative_to(repo_root)
        except Exception as exc:
            raise HTTPException(status_code=403, detail="SRT path is outside repository") from exc
        if candidate.suffix.lower() != ".srt":
            raise HTTPException(status_code=400, detail="SRT path is not an .srt file")
        return candidate

    def _generation_options_path(project_id: str) -> Path:
        return _resolve_project_dir(project_id) / GENERATION_OPTIONS_FILENAME

    def _normalize_generation_options(data: Dict[str, Any]) -> Dict[str, Any]:
        merged = {
            **DEFAULT_GENERATION_OPTIONS,
            **{k: v for k, v in (data or {}).items() if v is not None},
        }
        model = VideoGenerationOptionsPayload(**merged)
        return {
            "imgdur": float(model.imgdur),
            "crossfade": float(model.crossfade),
            "fps": int(model.fps),
            "style": model.style or "",
            "size": model.size,
            "fit": model.fit,
            "margin": int(model.margin),
        }

    def _load_generation_options(project_id: str) -> Dict[str, Any]:
        path = _generation_options_path(project_id)
        if not path.exists():
            return dict(DEFAULT_GENERATION_OPTIONS)
        try:
            with path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return dict(DEFAULT_GENERATION_OPTIONS)
        return _normalize_generation_options(raw)

    def _save_generation_options(project_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        options = _normalize_generation_options(data)
        path = _generation_options_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(options, handle, ensure_ascii=False, indent=2)
        return options

    def _resolve_capcut_source(project_dir: Path) -> Optional[Path]:
        info_path = project_dir / "capcut_draft_info.json"
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                info = {}
            draft_path_value = info.get("draft_path") if isinstance(info, dict) else None
            if draft_path_value:
                draft_path = Path(draft_path_value)
                if not draft_path.is_absolute():
                    draft_path = (COMMENTARY02_ROOT / draft_path).resolve()
                if draft_path.exists():
                    return draft_path
        candidate = project_dir / "capcut_draft"
        if candidate.exists():
            return candidate
        return None

    def _update_image_cues_crossfade(project_dir: Path, crossfade: float) -> None:
        cues_path = project_dir / "image_cues.json"
        if not cues_path.exists():
            return
        try:
            cues_data = json.loads(cues_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(cues_data, dict):
            if cues_data.get("crossfade") == crossfade:
                return
            cues_data["crossfade"] = crossfade
            try:
                cues_path.write_text(json.dumps(cues_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                return

    def _load_capcut_settings(project_dir: Path) -> Dict[str, Any]:
        info_path = project_dir / "capcut_draft_info.json"
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            info = {}

        project_id = project_dir.name
        channel_guess = _guess_channel_code(
            info.get("project_id"),
            info.get("channel_id"),
            info.get("draft_name"),
            project_id,
        )
        if channel_guess and not channel_guess.upper().startswith("CH"):
            channel_guess = None
        preset = _get_channel_preset(channel_guess)
        transform_defaults = (preset or {}).get("position", {}) if preset else {}
        transform_info = info.get("transform") or {}
        transform = {
            "tx": float(transform_info.get("tx", transform_defaults.get("tx", 0.0) or 0.0)),
            "ty": float(transform_info.get("ty", transform_defaults.get("ty", 0.0) or 0.0)),
            "scale": float(transform_info.get("scale", transform_defaults.get("scale", 1.0) or 1.0)),
        }
        crossfade = info.get("crossfade_sec")
        if crossfade is None:
            crossfade = DEFAULT_GENERATION_OPTIONS.get("crossfade", 0.5)
        fade_duration = info.get("fade_duration_sec")
        if fade_duration is None:
            fade_duration = crossfade
        opening_offset = info.get("opening_offset")
        if opening_offset is None and preset:
            belt = preset.get("belt") or {}
            if belt.get("opening_offset") is not None:
                opening_offset = float(belt["opening_offset"])
        if opening_offset is None:
            opening_offset = 0.0

        return {
            "channel_id": channel_guess,
            "template_used": info.get("template_used") or (preset.get("capcut_template") if preset else None),
            "draft_name": info.get("draft_name"),
            "draft_path": info.get("draft_path"),
            "transform": transform,
            "crossfade_sec": float(crossfade),
            "fade_duration_sec": float(fade_duration),
            "opening_offset": float(opening_offset),
        }

    @video_router.get("/projects/{project_id}/archive")
    def download_project_archive(project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)]):
        project_dir = _resolve_project_dir(project_id)
        temp_dir = Path(tempfile.mkdtemp(prefix=f"video_project_{project_id}_"))
        archive_base = temp_dir / project_id
        archive_path_str = shutil.make_archive(str(archive_base), "zip", root_dir=project_dir)
        archive_path = Path(archive_path_str)
        background = BackgroundTask(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=f"{project_id}.zip",
            background=background,
        )

    @video_router.post("/projects/{project_id}/capcut/install", response_model=CapcutInstallResult)
    def install_capcut_draft(
        project_id: Annotated[str, FastAPIPath(pattern=VALID_PROJECT_ID_PATTERN)],
        payload: CapcutInstallRequest,
    ):
        if not CAPCUT_DRAFT_ROOT.exists():
            raise HTTPException(status_code=500, detail=f"CapCut draft root not found: {CAPCUT_DRAFT_ROOT}")
        project_dir = _resolve_project_dir(project_id)
        source = _resolve_capcut_source(project_dir)
        if not source:
            raise HTTPException(status_code=404, detail="capcut_draft が見つかりません。ジョブで生成してください。")
        target_dir = CAPCUT_DRAFT_ROOT / project_id
        if target_dir.exists():
            if not payload.overwrite:
                raise HTTPException(status_code=409, detail=f"target exists: {target_dir}. overwrite=true で上書きできます。")
            shutil.rmtree(target_dir)
        try:
            shutil.copytree(source, target_dir)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"CapCut ドラフトのコピーに失敗しました: {exc}") from exc
        return CapcutInstallResult(
            status="ok",
            source=str(source),
            target=str(target_dir),
            overwrite=payload.overwrite,
        )

    def _load_channel_presets() -> Dict[str, Any]:
        if not CHANNEL_PRESETS_PATH.exists():
            return {}
        mtime = CHANNEL_PRESETS_PATH.stat().st_mtime
        if _CHANNEL_CACHE["mtime"] == mtime:
            return _CHANNEL_CACHE["data"]
        with CHANNEL_PRESETS_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        _CHANNEL_CACHE["mtime"] = mtime
        _CHANNEL_CACHE["data"] = data
        return data

    def _write_channel_presets(data: Dict[str, Any]) -> None:
        CHANNEL_PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CHANNEL_PRESETS_PATH.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        _CHANNEL_CACHE["mtime"] = CHANNEL_PRESETS_PATH.stat().st_mtime
        _CHANNEL_CACHE["data"] = data

    def _get_channel_preset(channel_code: Optional[str]) -> Optional[Dict[str, Any]]:
        if not channel_code:
            return None
        presets = _load_channel_presets().get("channels", {})
        key = channel_code.upper()
        return presets.get(key)

    def _guess_channel_code(*candidates: Optional[str]) -> Optional[str]:
        for text in candidates:
            if not text:
                continue
            match = CHANNEL_CODE_PATTERN.search(str(text).upper())
            if match:
                return match.group(1)
        return None

    def _guess_video_number(*candidates: Optional[str]) -> Optional[str]:
        for text in candidates:
            if not text:
                continue
            match = VIDEO_NUMBER_PATTERN.search(str(text))
            if match:
                return match.group(1).zfill(3)
        return None

    def _guess_project_id(
        channel_id: Optional[str],
        video_number: Optional[str],
        *candidates: Optional[str],
    ) -> Optional[str]:
        for text in candidates:
            if not text:
                continue
            normalized = str(text).strip()
            if not normalized:
                continue
            candidate_dir = (OUTPUT_ROOT / normalized).resolve()
            if candidate_dir.exists():
                return normalized
            if "-" in normalized and not channel_id:
                prefix = normalized.split("-", 1)[0]
                maybe_channel = _guess_channel_code(prefix)
                if maybe_channel:
                    channel_id = maybe_channel
        if channel_id and video_number:
            return f"{channel_id}-{video_number}"
        return None

    def _safe_read_json(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _safe_read_json_limited(path: Path, *, max_bytes: int = 2_000_000) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            stat = path.stat()
            if stat.st_size > max_bytes:
                return {}
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    _IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

    def _summarize_project_artifacts(project_dir: Path) -> Dict[str, Any]:
        def _file_entry(
            *,
            key: str,
            label: str,
            rel_path: str,
            meta: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            path = project_dir / rel_path
            exists = _path_exists(path)
            size_info = _stat_path(path) if exists and path and path.is_file() else None
            out: Dict[str, Any] = {
                "key": key,
                "label": label,
                "path": rel_path,
                "kind": "file",
                "exists": exists,
                "size_bytes": size_info[0] if size_info else None,
                "modified_time": datetime.fromtimestamp(size_info[1]).isoformat() if size_info else None,
            }
            if meta:
                out["meta"] = meta
            return out

        def _dir_entry(
            *,
            key: str,
            label: str,
            rel_path: str,
            meta: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            path = project_dir / rel_path
            exists = _path_exists(path)
            size_info = _stat_path(path) if exists else None
            out: Dict[str, Any] = {
                "key": key,
                "label": label,
                "path": rel_path,
                "kind": "dir",
                "exists": exists,
                "size_bytes": size_info[0] if size_info else None,
                "modified_time": datetime.fromtimestamp(size_info[1]).isoformat() if size_info else None,
            }
            if meta:
                out["meta"] = meta
            return out

        def _srt_entry() -> Dict[str, Any]:
            srts = sorted([p for p in project_dir.glob("*.srt") if p.is_file()])
            if not srts:
                return {
                    "key": "srt",
                    "label": "SRT",
                    "path": "*.srt",
                    "kind": "file",
                    "exists": False,
                    "size_bytes": None,
                    "modified_time": None,
                }
            newest = max(srts, key=lambda p: p.stat().st_mtime)
            stat = newest.stat()
            return {
                "key": "srt",
                "label": "SRT",
                "path": newest.name,
                "kind": "file",
                "exists": True,
                "size_bytes": stat.st_size,
                "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "meta": {"count": len(srts), "files": [p.name for p in srts[:5]]},
            }

        def _json_meta(path: Path) -> Dict[str, Any]:
            data = _safe_read_json_limited(path)
            return data if isinstance(data, dict) else {}

        items: List[Dict[str, Any]] = []

        items.append(_srt_entry())

        capcut_info_path = project_dir / "capcut_draft_info.json"
        capcut_meta = {}
        capcut_info = _json_meta(capcut_info_path)
        if capcut_info:
            capcut_meta = {
                "template_used": capcut_info.get("template_used"),
                "draft_name": capcut_info.get("draft_name"),
                "draft_path": capcut_info.get("draft_path"),
            }
        items.append(
            _file_entry(
                key="capcut_draft_info",
                label="CapCut draft info",
                rel_path="capcut_draft_info.json",
                meta=capcut_meta or None,
            )
        )

        seg_meta = {}
        seg_obj = _json_meta(project_dir / "srt_segments.json")
        segs = seg_obj.get("segments") if isinstance(seg_obj, dict) else None
        if isinstance(segs, list):
            seg_meta = {"segment_count": len(segs)}
        items.append(
            _file_entry(
                key="srt_segments",
                label="SRT segments",
                rel_path="srt_segments.json",
                meta=seg_meta or None,
            )
        )

        plan_meta = {}
        plan_obj = _json_meta(project_dir / "visual_cues_plan.json")
        if plan_obj:
            secs = plan_obj.get("sections")
            plan_meta = {
                "status": plan_obj.get("status"),
                "section_count": len(secs) if isinstance(secs, list) else None,
                "base_seconds": plan_obj.get("base_seconds"),
            }
        items.append(
            _file_entry(
                key="visual_cues_plan",
                label="Visual cues plan",
                rel_path="visual_cues_plan.json",
                meta=plan_meta or None,
            )
        )

        cues_meta = {}
        cues_obj = _json_meta(project_dir / "image_cues.json")
        cues_arr = cues_obj.get("cues") if isinstance(cues_obj, dict) else None
        if isinstance(cues_arr, list):
            cues_meta = {
                "cue_count": len(cues_arr),
                "fps": cues_obj.get("fps"),
                "imgdur": cues_obj.get("imgdur"),
                "crossfade": cues_obj.get("crossfade"),
            }
        items.append(
            _file_entry(
                key="image_cues",
                label="Image cues",
                rel_path="image_cues.json",
                meta=cues_meta or None,
            )
        )

        belt_meta = {}
        belt_obj = _json_meta(project_dir / "belt_config.json")
        belts = belt_obj.get("belts") if isinstance(belt_obj, dict) else None
        if isinstance(belts, list):
            belt_meta = {"belt_count": len(belts)}
        items.append(
            _file_entry(
                key="belt_config",
                label="Belt config",
                rel_path="belt_config.json",
                meta=belt_meta or None,
            )
        )

        persona_path = project_dir / "persona.txt"
        persona_stat = _stat_path(persona_path) if _path_exists(persona_path) and persona_path.is_file() else None
        items.append(
            {
                "key": "persona",
                "label": "Persona",
                "path": "persona.txt",
                "kind": "file",
                "exists": bool(persona_stat),
                "size_bytes": persona_stat[0] if persona_stat else None,
                "modified_time": datetime.fromtimestamp(persona_stat[1]).isoformat() if persona_stat else None,
            }
        )

        images_dir = project_dir / "images"
        image_count = None
        if _path_exists(images_dir) and images_dir.is_dir():
            try:
                image_count = len(
                    [
                        child
                        for child in images_dir.iterdir()
                        if child.is_file() and child.suffix.lower() in _IMAGE_EXTENSIONS
                    ]
                )
            except OSError:
                image_count = None
        items.append(
            _dir_entry(
                key="images",
                label="Images",
                rel_path="images",
                meta={"count": image_count} if image_count is not None else None,
            )
        )

        items.append(
            _file_entry(
                key="timeline_manifest",
                label="Timeline manifest",
                rel_path="timeline_manifest.json",
            )
        )
        items.append(
            _dir_entry(
                key="capcut_draft",
                label="CapCut draft folder",
                rel_path="capcut_draft",
            )
        )

        return {"project_dir": str(project_dir), "items": items}

    def _resolve_repo_path(candidate: Optional[str]) -> Optional[Path]:
        if not candidate:
            return None
        try:
            path = Path(candidate)
        except TypeError:
            return None
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return path

    def _path_exists(path: Optional[Path]) -> bool:
        if not path:
            return False
        try:
            return path.exists()
        except OSError:
            return False

    def _stat_path(path: Optional[Path]) -> Optional[Tuple[int, float]]:
        if not path:
            return None
        try:
            stat = path.stat()
            return stat.st_size, stat.st_mtime
        except OSError:
            return None

    def _build_remotion_asset(label: str, path: Optional[Path], asset_type: str) -> Dict[str, Any]:
        exists = _path_exists(path)
        size_info = _stat_path(path) if exists and path and path.is_file() else None
        return {
            "label": label,
            "path": str(path) if path else None,
            "exists": exists,
            "type": asset_type,
            "size_bytes": size_info[0] if size_info else None,
            "modified_time": datetime.fromtimestamp(size_info[1]).isoformat() if size_info else None,
        }

    def _gather_remotion_projects() -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for project_dir in sorted(OUTPUT_ROOT.iterdir()):
            if not project_dir.is_dir():
                continue
            entries.append(_build_remotion_project_entry(project_dir))
        entries.sort(key=lambda item: item["project_id"])
        return entries

    def _build_remotion_project_entry(project_dir: Path) -> Dict[str, Any]:
        project_id = project_dir.name
        info = _safe_read_json(project_dir / "capcut_draft_info.json")
        auto_info = _safe_read_json(project_dir / "auto_run_info.json")
        episode_info = _safe_read_json(project_dir / "episode_info.json")
        remotion_run_info = _safe_read_json(project_dir / "remotion_run_info.json")

        channel_id = (
            (info.get("channel_id") if isinstance(info, dict) else None)
            or (auto_info.get("channel") if isinstance(auto_info, dict) else None)
            or (episode_info.get("channel_id") if isinstance(episode_info, dict) else None)
            or _guess_channel_code(project_id)
        )
        if isinstance(channel_id, str):
            channel_id = channel_id.upper()

        video_number = _guess_video_number(
            project_id,
            (info.get("project_id") if isinstance(info, dict) else None),
            (auto_info.get("srt") if isinstance(auto_info, dict) else None),
            (info.get("srt_file") if isinstance(info, dict) else None),
        )

        title = (
            (info.get("title") if isinstance(info, dict) else None)
            or (episode_info.get("title") if isinstance(episode_info, dict) else None)
            or project_dir.name
        )
        duration_sec = (
            (auto_info.get("duration_sec") if isinstance(auto_info, dict) else None)
            or (remotion_run_info.get("duration_sec") if isinstance(remotion_run_info, dict) else None)
        )

        required_assets: List[Dict[str, Any]] = []
        optional_assets: List[Dict[str, Any]] = []

        # SRT: prefer capcut_draft_info.json, fallback to first *.srt in the run_dir
        srt_path: Optional[Path] = None
        if isinstance(info, dict):
            srt_path = _resolve_repo_path(info.get("srt_file"))
        if not _path_exists(srt_path):
            candidates = sorted(project_dir.glob("*.srt"))
            srt_path = candidates[0] if candidates else srt_path

        cues_path = project_dir / "image_cues.json"
        belt_path = project_dir / "belt_config.json"
        chapters_path = project_dir / "chapters.json"
        episode_info_path = project_dir / "episode_info.json"
        images_dir = project_dir / "images"

        # Audio is required for rendering, but the file may live outside run_dir (audio_tts_v2 artifacts).
        audio_path: Optional[Path] = None
        audio_candidates: List[Path] = []
        for ext in (".wav", ".mp3", ".m4a", ".flac"):
            audio_candidates.extend(project_dir.glob(f"*{ext}"))
        audio_candidates = [p for p in audio_candidates if p.is_file()]
        if audio_candidates:
            audio_path = sorted(audio_candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
        elif channel_id and video_number:
            audio_path = (
                PROJECT_ROOT
                / "audio_tts_v2"
                / "artifacts"
                / "final"
                / channel_id
                / video_number
                / f"{channel_id}-{video_number}.wav"
            ).resolve()

        asset_matrix = [
            ("Audio (voice)", audio_path, "file", True),
            ("SRT", srt_path, "file", True),
            ("Image cues", cues_path, "file", True),
            ("Belt config", belt_path, "file", True),
            ("Chapters", chapters_path, "file", False),
            ("Episode info", episode_info_path, "file", False),
            ("Images directory", images_dir, "directory", True),
        ]

        for label, path, kind, required in asset_matrix:
            asset = _build_remotion_asset(label, path, kind)
            if required:
                required_assets.append(asset)
            else:
                optional_assets.append(asset)

        image_count = 0
        if _path_exists(images_dir):
            try:
                image_count = len(
                    [
                        child
                        for child in images_dir.iterdir()
                        if child.is_file() and child.suffix.lower() in {".png", ".jpg", ".jpeg"}
                    ]
                )
            except OSError:
                image_count = 0

        remotion_dir = project_dir / "remotion"
        remotion_exists = _path_exists(remotion_dir)
        timeline_path = remotion_dir / "timeline.json" if remotion_exists else None
        timeline_asset = _build_remotion_asset(
            "Remotion timeline",
            timeline_path if timeline_path and timeline_path.exists() else None,
            "file",
        )

        outputs: List[Dict[str, Any]] = []
        if remotion_exists:
            candidates = list(remotion_dir.glob("*.mp4")) + list((remotion_dir / "output").glob("*.mp4"))
            seen_names = set()
            for mp4 in candidates:
                if not mp4.exists() or mp4.name in seen_names:
                    continue
                seen_names.add(mp4.name)
                stat_info = _stat_path(mp4)
                rel = None
                url = None
                try:
                    rel = mp4.relative_to(OUTPUT_ROOT).as_posix()
                    url = f"/api/video-production/assets/{rel}"
                except Exception:
                    rel = None
                    url = None
                outputs.append(
                    {
                        "path": str(mp4),
                        "file_name": mp4.name,
                        "url": url,
                        "rel_path": rel,
                        "size_bytes": stat_info[0] if stat_info else None,
                        "modified_time": datetime.fromtimestamp(stat_info[1]).isoformat() if stat_info else None,
                    }
                )

        outputs.sort(key=lambda entry: entry.get("modified_time") or "", reverse=True)
        last_rendered = outputs[0]["modified_time"] if outputs else None

        drive_upload_path = remotion_dir / "drive_upload.json" if remotion_exists else None
        drive_upload = _safe_read_json(drive_upload_path) if drive_upload_path else {}

        missing_required = [asset for asset in required_assets if not asset["exists"]]
        issues = [f"{asset['label']} missing" for asset in missing_required]
        if image_count == 0:
            issues.append("images directory has no assets")

        status = "missing_assets"
        if not missing_required and image_count > 0:
            status = "assets_ready"
            if timeline_asset["exists"]:
                status = "scaffolded"
            if outputs:
                status = "rendered"

        assets = required_assets + optional_assets
        assets.append(timeline_asset)

        return {
            "project_id": project_id,
            "channel_id": channel_id,
            "title": title,
            "duration_sec": duration_sec,
            "status": status,
            "issues": issues,
            "metrics": {
                "image_count": image_count,
                "asset_ready": sum(1 for asset in required_assets if asset["exists"]),
                "asset_total": len(required_assets),
            },
            "assets": assets,
            "outputs": outputs,
            "remotion_dir": str(remotion_dir) if remotion_exists else None,
            "timeline_path": str(timeline_path) if timeline_path and timeline_path.exists() else None,
            "last_rendered": last_rendered,
            "drive_upload": drive_upload or None,
        }

    def _list_channel_srts(channel_id: str) -> List[Dict[str, Any]]:
        if not INPUT_ROOT.exists():
            return []
        results: List[Dict[str, Any]] = []
        for child in INPUT_ROOT.iterdir():
            if not child.name.startswith(channel_id):
                continue
            candidates: List[Path]
            if child.is_dir():
                candidates = sorted(child.rglob("*.srt"))
            elif child.is_file() and child.suffix.lower() == ".srt":
                candidates = [child]
            else:
                continue
            for path in candidates:
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                try:
                    rel = path.relative_to(PROJECT_ROOT) if path.is_absolute() else path
                except ValueError:
                    rel = path
                results.append(
                    {
                        "channel_id": channel_id,
                        "name": path.name,
                        "relative_path": str(rel),
                        "absolute_path": str(path),
                        "size": stat.st_size,
                        "modified_time": stat.st_mtime,
                        "modified_time_iso": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    }
                )
        results.sort(key=lambda item: item["modified_time"], reverse=True)
        return results

    @video_router.get("/channels")
    def list_channels(include_srts: bool = Query(default=False)):
        raw = _load_channel_presets().get("channels", {})
        channels = []
        for channel_id, payload in raw.items():
            entry = {
                "channel_id": channel_id,
                "name": payload.get("name", channel_id),
                "prompt_template": payload.get("prompt_template"),
                "style": payload.get("style"),
                "capcut_template": payload.get("capcut_template"),
                "position": payload.get("position", {}),
                "belt": payload.get("belt", {}),
                "persona_required": payload.get("persona_required"),
                "image_min_bytes": payload.get("image_min_bytes"),
                "notes": payload.get("notes", ""),
                "status": payload.get("status", "active"),
            }
            if include_srts:
                entry["srt_files"] = _list_channel_srts(channel_id)
            channels.append(entry)
        channels.sort(key=lambda item: item["channel_id"])
        return channels

    @video_router.get("/channels/{channel_id}/srts")
    def list_channel_srts(channel_id: str):
        presets = _load_channel_presets().get("channels", {})
        if channel_id not in presets:
            raise HTTPException(status_code=404, detail=f"Channel not found: {channel_id}")
        return {"channel_id": channel_id, "srt_files": _list_channel_srts(channel_id)}

    @video_router.get("/channel-presets/{channel_id}")
    def get_channel_preset(channel_id: str):
        presets = _load_channel_presets().get("channels", {})
        entry = presets.get(channel_id)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Channel not found: {channel_id}")
        return {"channel_id": channel_id, **entry}

    @video_router.patch("/channel-presets/{channel_id}")
    def patch_channel_preset(channel_id: str, payload: ChannelPresetUpdatePayload):
        presets_data = _load_channel_presets()
        channels = presets_data.setdefault("channels", {})
        entry = channels.get(channel_id)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Channel not found: {channel_id}")

        updates = payload.dict(exclude_unset=True)
        if "capcut_template" in updates and updates["capcut_template"] is not None and CAPCUT_DRAFT_ROOT.exists():
            template_name = str(updates["capcut_template"]).strip()
            if not template_name:
                raise HTTPException(status_code=400, detail="capcut_template must be a non-empty string")
            template_dir = CAPCUT_DRAFT_ROOT / template_name
            if not template_dir.exists() or not template_dir.is_dir():
                raise HTTPException(status_code=400, detail=f"capcut_template not found: {template_dir}")
            content_path = template_dir / "draft_content.json"
            info_path = template_dir / "draft_info.json"
            has_content = content_path.exists() and content_path.stat().st_size > 0
            has_info = info_path.exists() and info_path.stat().st_size > 0
            if not has_content and not has_info:
                raise HTTPException(status_code=400, detail=f"capcut_template has no draft JSON: {template_dir}")
            try:
                source_path = content_path if has_content else info_path
                with source_path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                tracks = data.get("tracks") if isinstance(data, dict) else None
                if not isinstance(tracks, list) or not tracks:
                    raise HTTPException(status_code=400, detail=f"capcut_template has no tracks: {template_dir}")
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"capcut_template JSON invalid: {template_dir} ({exc})") from exc
        if "position" in updates and updates["position"] is not None:
            position = entry.get("position", {}).copy()
            for key, value in updates["position"].items():
                if value is None:
                    position.pop(key, None)
                else:
                    position[key] = value
            entry["position"] = position
            updates.pop("position")
        if "belt" in updates and updates["belt"] is not None:
            belt = entry.get("belt", {}).copy()
            for key, value in updates["belt"].items():
                if value is None:
                    belt.pop(key, None)
                else:
                    belt[key] = value
            entry["belt"] = belt
            updates.pop("belt")

        for key, value in updates.items():
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value

        channels[channel_id] = entry
        _write_channel_presets(presets_data)
        return {"channel_id": channel_id, **entry}

    @video_router.get("/drafts")
    def list_drafts():
        if not CAPCUT_DRAFT_ROOT.exists():
            return []
        drafts: List[Dict[str, Any]] = []
        for draft_dir in CAPCUT_DRAFT_ROOT.iterdir():
            if not draft_dir.is_dir():
                continue
            draft_info_path = draft_dir / "draft_info.json"
            if not draft_info_path.exists():
                continue
            try:
                draft_info = _safe_read_json(draft_info_path)
                if not draft_info:
                    continue
                draft_meta_path = draft_dir / "draft_meta_info.json"
                draft_meta = _safe_read_json(draft_meta_path)
                image_dir = draft_dir / "assets" / "image"
                image_count = len(list(image_dir.glob("*.png"))) if image_dir.exists() else 0
                channel_guess = _guess_channel_code(
                    draft_info.get("project_id"),
                    draft_info.get("draft_name"),
                    draft_dir.name,
                )
                video_guess = _guess_video_number(
                    draft_info.get("project_id"),
                    draft_info.get("draft_name"),
                    draft_dir.name,
                )
                project_hint = draft_info.get("project_id")
                project_id = _guess_project_id(channel_guess, video_guess, project_hint, draft_dir.name)
                project_dir = (OUTPUT_ROOT / project_id).resolve() if project_id else None
                project_exists = bool(project_dir and project_dir.exists())
                channel_preset = _get_channel_preset(channel_guess)
                duration_micros = (
                    draft_info.get("duration")
                    or draft_meta.get("tm_duration")
                    or draft_meta.get("duration")
                    or 0
                )
                drafts.append(
                    {
                        "name": draft_dir.name,
                        "path": str(draft_dir),
                        "title": draft_info.get("draft_name", draft_dir.name),
                        "image_count": image_count,
                        "duration": duration_micros / 1_000_000,
                        "modified_time": draft_dir.stat().st_mtime,
                        "modified_time_iso": datetime.fromtimestamp(draft_dir.stat().st_mtime).isoformat(),
                        "channel_id": channel_guess,
                        "channel_name": channel_preset.get("name") if channel_preset else None,
                        "video_number": video_guess,
                        "project_id": project_id if project_exists else None,
                        "project_hint": project_id or draft_dir.name,
                        "project_exists": project_exists,
                    }
                )
            except Exception:
                continue
        drafts.sort(key=lambda item: item["modified_time"], reverse=True)
        return drafts

    @video_router.get("/drafts/{draft_name}")
    def get_draft_detail(draft_name: str):
        draft_dir = CAPCUT_DRAFT_ROOT / draft_name
        if not draft_dir.exists():
            raise HTTPException(status_code=404, detail="Draft not found")
        draft_info_path = draft_dir / "draft_info.json"
        if not draft_info_path.exists():
            raise HTTPException(status_code=404, detail="Draft files not found")
        draft_info = _safe_read_json(draft_info_path)
        if not draft_info:
            raise HTTPException(status_code=404, detail="Draft files not found")
        draft_content_path = draft_dir / "draft_content.json"
        draft_content = _safe_read_json(draft_content_path)
        if not draft_content:
            # Fallback to metadata (CapCut new format) or empty payload
            draft_meta_path = draft_dir / "draft_meta_info.json"
            draft_content = _safe_read_json(draft_meta_path)
        if not draft_content:
            return {
                "draft": draft_info,
                "segments": [],
            }
        material_map = {}
        for video in draft_content.get("materials", {}).get("videos", []):
            material_map[video.get("id")] = video.get("path", "")
        image_segments = []
        for track in draft_content.get("tracks", []):
            if track.get("type") != "video":
                continue
            for segment in track.get("segments", []):
                material_id = segment.get("material_id")
                if not material_id or material_id not in material_map:
                    continue
                path = material_map[material_id]
                if "assets/image/" not in path:
                    continue
                timerange = segment.get("target_timerange", {})
                start_sec = timerange.get("start", 0) / 1_000_000
                duration_sec = timerange.get("duration", 0) / 1_000_000
                image_segments.append(
                    {
                        "material_id": material_id,
                        "path": path[path.index("assets/image/"):],
                        "filename": Path(path).name,
                        "start_sec": round(start_sec, 2),
                        "end_sec": round(start_sec + duration_sec, 2),
                        "duration_sec": round(duration_sec, 2),
                    }
                )
        return {
            "draft": draft_info,
            "segments": image_segments,
        }

    @video_router.get("/remotion/projects")
    def list_remotion_status():
        return _gather_remotion_projects()

    def _compute_source_status(project_id: Optional[str]) -> Dict[str, Any]:
        if not project_id:
            return {
                "channel": None,
                "video_number": None,
                "srt_ready": False,
                "audio_ready": False,
                "srt_path": None,
                "audio_path": None,
            }

        if "-" not in project_id:
            return {
                "channel": project_id,
                "video_number": None,
                "srt_ready": False,
                "audio_ready": False,
                "srt_path": None,
                "audio_path": None,
            }

        channel_code, video_number = project_id.split("-", 1)
        channel_code = channel_code.upper()
        video_number = video_number[:3].zfill(3)
        base_dir = COMMENTARY01_DATA_ROOT / channel_code / video_number
        audio_dir = base_dir / "output" / "audio"
        srt_path = audio_dir / f"{channel_code}-{video_number}_final.srt"
        audio_path = audio_dir / f"{channel_code}-{video_number}_final.wav"

        return {
            "channel": channel_code,
            "video_number": video_number,
            "srt_ready": srt_path.exists(),
            "audio_ready": audio_path.exists(),
            "srt_path": str(srt_path) if srt_path.exists() else None,
            "audio_path": str(audio_path) if audio_path.exists() else None,
        }

__all__ = ['video_router']
