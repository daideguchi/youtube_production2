"""FastAPI backend for the React UI (commentary_02)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
import json
import shutil

from fastapi import FastAPI, HTTPException, Query, UploadFile, File as FastAPIFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Path bootstrap (ensure src/ can be imported before other modules)
# ---------------------------------------------------------------------------
def _bootstrap_repo_root() -> Path:
    start = Path(__file__).resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur


_BOOTSTRAP_REPO = _bootstrap_repo_root()
if str(_BOOTSTRAP_REPO) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_REPO))

from factory_common.paths import (  # noqa: E402
    audio_artifacts_root,
    audio_pkg_root,
    logs_root,
    repo_root,
    script_data_root,
    video_pkg_root,
    video_runs_root,
)

PROJECT_ROOT = video_pkg_root()
REPO_ROOT = repo_root()
SRC_ROOT = PROJECT_ROOT / "src"
UI_ROOT = PROJECT_ROOT / "ui"

OUTPUT_ROOT = video_runs_root()
TOOLS_ROOT = PROJECT_ROOT / "tools"
STATIC_ROOT = OUTPUT_ROOT
JOB_LOG_ROOT = logs_root() / "jobs" / "video_production"
SCRIPT_PIPELINE_DATA_ROOT = script_data_root()

INPUT_ROOT = audio_artifacts_root() / "final"
CONFIG_ROOT = PROJECT_ROOT / "config"
CHANNEL_PRESETS_PATH = CONFIG_ROOT / "channel_presets.json"
KB_PATH = audio_pkg_root() / "data" / "global_knowledge_base.json"

for candidate in (PROJECT_ROOT, SRC_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from src.data.projects import list_projects, load_project_detail  # noqa: E402
from .jobs import JobManager, job_to_dict  # noqa: E402

# ---------------------------------------------------------------------------
# FastAPI application setup
# ---------------------------------------------------------------------------
app = FastAPI(title="commentary_02 React API", version="0.2.0")

default_origin = os.environ.get("REACT_UI_ORIGIN", "http://127.0.0.1:5174")
allow_origins = {
    default_origin,
    default_origin.replace("127.0.0.1", "localhost"),
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allow_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# CapCut drafts root
CAPCUT_DRAFT_ROOT = Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
_CHANNEL_CACHE: Dict[str, Any] = {"mtime": None, "data": {}}


def _load_project_detail(project_id: str):
    return load_project_detail(OUTPUT_ROOT, project_id)


job_manager = JobManager(
    project_root=PROJECT_ROOT,
    output_root=OUTPUT_ROOT,
    tools_root=TOOLS_ROOT,
    log_root=JOB_LOG_ROOT,
    scripts_root=REPO_ROOT / "scripts",
    project_loader=_load_project_detail,
    python_executable=sys.executable,
)


class JobCreateRequest(BaseModel):
    action: str = Field(
        description="Job Type"
    )
    options: Dict[str, Any] = Field(default_factory=dict, description="CLI Options")
    note: Optional[str] = Field(default=None, description="Note")


VALID_ACTIONS = {
    "analyze_srt",
    "regenerate_images",
    "generate_belt",
    "validate_capcut",
    "build_capcut_draft",
    "render_remotion",
    "swap_images",
}


@app.get("/api/projects")
def get_projects():
    return list_projects(OUTPUT_ROOT)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    detail = load_project_detail(OUTPUT_ROOT, project_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="project not found")
    for sample in detail.image_samples:
        sample.url = f"/static/{sample.path}"
    return detail


@app.post("/api/projects/{project_id}/jobs")
def create_job(project_id: str, payload: JobCreateRequest):
    if payload.action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported action: {payload.action}")
    project_dir = OUTPUT_ROOT / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project output not found: {project_id}")

    try:
        record = job_manager.create_job(
            project_id=project_id,
            action=payload.action,
            options=payload.options,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return job_to_dict(record)


@app.post("/api/projects", status_code=201)
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
        src_path = (REPO_ROOT / existing_srt_path).resolve()
        if not src_path.exists():
            raise HTTPException(status_code=404, detail=f"Existing SRT not found: {existing_srt_path}")
        srt_target = project_dir / src_path.name
        shutil.copy(src_path, srt_target)
    else:
        if not srt_file or not srt_file.filename.endswith(".srt"):
            raise HTTPException(status_code=400, detail="Upload SRT file")
        srt_target = project_dir / srt_file.filename
        with open(srt_target, "wb") as buffer:
            shutil.copyfileobj(srt_file.file, buffer)

    info_path = project_dir / "capcut_draft_info.json"
    info = {
        "project_id": project_id,
        "channel_id": channel_id,
        "srt_file": str(srt_target.relative_to(PROJECT_ROOT)),
        "template_used": None,
        "draft_path": None,
        "title": None,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "transform": {},
    }
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    response = {
        "project_id": project_id,
        "output_dir": str(project_dir),
        "srt_file": str(srt_target.relative_to(PROJECT_ROOT)),
        "channel_id": channel_id,
        "target_sections": target_sections,
    }
    return response


@app.get("/api/jobs")
def list_jobs(project_id: Optional[str] = Query(default=None), limit: Optional[int] = Query(default=None, ge=1, le=100)):
    records = job_manager.list_jobs(project_id=project_id, limit=limit)
    return [job_to_dict(record) for record in records]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    record = job_manager.get_job(job_id)
    if not record:
        raise HTTPException(status_code=404, detail="job not found")
    return job_to_dict(record)


@app.get("/api/jobs/{job_id}/log")
def get_job_log(job_id: str):
    record = job_manager.get_job(job_id)
    if not record or not record.log_path:
        raise HTTPException(status_code=404, detail="job not found")
    if not record.log_path.exists():
        raise HTTPException(status_code=404, detail="log file not found")
    content = record.log_path.read_text(encoding="utf-8")
    return PlainTextResponse(content)


@app.get("/static/{rest_path:path}")
def serve_static(rest_path: str):
    target = STATIC_ROOT / rest_path
    if not target.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target)


def _load_channel_presets() -> Dict[str, Any]:
    if not CHANNEL_PRESETS_PATH.exists():
        return {}
    mtime = CHANNEL_PRESETS_PATH.stat().st_mtime
    if _CHANNEL_CACHE["mtime"] == mtime:
        return _CHANNEL_CACHE["data"]
    with open(CHANNEL_PRESETS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    _CHANNEL_CACHE["mtime"] = mtime
    _CHANNEL_CACHE["data"] = data
    return data


def _list_channel_srt_files(channel_id: str) -> List[Dict[str, Any]]:
    if not INPUT_ROOT.exists():
        return []
    results: List[Dict[str, Any]] = []
    for child in INPUT_ROOT.iterdir():
        if not child.name.startswith(channel_id):
            continue
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
                relative = path.relative_to(REPO_ROOT) if path.is_absolute() else path
            except ValueError:
                relative = path
            results.append({
                "channel_id": channel_id,
                "name": path.name,
                "relative_path": str(relative),
                "absolute_path": str(path),
                "size": stat.st_size,
                "modified_time": stat.st_mtime,
                "modified_time_iso": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    results.sort(key=lambda item: item["modified_time"], reverse=True)
    return results


@app.get("/api/channels")
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
            "notes": payload.get("notes", ""),
            "status": payload.get("status", "active"),
        }
        if include_srts:
            entry["srt_files"] = _list_channel_srt_files(channel_id)
        channels.append(entry)
    channels.sort(key=lambda item: item["channel_id"])
    return channels


@app.get("/api/channels/{channel_id}/srts")
def list_channel_srts(channel_id: str):
    presets = _load_channel_presets().get("channels", {})
    if channel_id not in presets:
        raise HTTPException(status_code=404, detail=f"Channel not found: {channel_id}")
    return {
        "channel_id": channel_id,
        "srt_files": _list_channel_srt_files(channel_id),
    }


@app.get("/api/drafts")
def list_drafts():
    """List all CapCut drafts."""
    if not CAPCUT_DRAFT_ROOT.exists():
        return []

    drafts = []
    for draft_dir in CAPCUT_DRAFT_ROOT.iterdir():
        if not draft_dir.is_dir():
            continue

        draft_info_path = draft_dir / "draft_info.json"
        draft_content_path = draft_dir / "draft_content.json"

        if not draft_info_path.exists() or not draft_content_path.exists():
            continue

        try:
            with open(draft_info_path, "r", encoding="utf-8") as f:
                draft_info = json.load(f)

            image_dir = draft_dir / "assets" / "image"
            image_count = len(list(image_dir.glob("*.png"))) if image_dir.exists() else 0

            drafts.append({
                "name": draft_dir.name,
                "path": str(draft_dir),
                "title": draft_info.get("draft_name", draft_dir.name),
                "image_count": image_count,
                "duration": draft_info.get("duration", 0) / 1_000_000,
                "modified_time": draft_dir.stat().st_mtime,
            })
        except Exception:
            continue

    drafts.sort(key=lambda x: x["modified_time"], reverse=True)
    return drafts


@app.get("/api/drafts/{draft_name}")
def get_draft_detail(draft_name: str):
    draft_dir = CAPCUT_DRAFT_ROOT / draft_name
    if not draft_dir.exists():
        raise HTTPException(status_code=404, detail="Draft not found")

    draft_info_path = draft_dir / "draft_info.json"
    draft_content_path = draft_dir / "draft_content.json"

    if not draft_info_path.exists() or not draft_content_path.exists():
        raise HTTPException(status_code=404, detail="Draft files not found")

    try:
        with open(draft_info_path, "r", encoding="utf-8") as f:
            draft_info = json.load(f)

        with open(draft_content_path, "r", encoding="utf-8") as f:
            draft_content = json.load(f)

        material_map = {}
        for video in draft_content.get("materials", {}).get("videos", []):
            material_map[video.get("id")] = video.get("path", "")

        image_segments = []
        for track in draft_content.get("tracks", []):
            if track.get("type") == "video":
                for segment in track.get("segments", []):
                    material_id = segment.get("material_id")
                    if material_id and material_id in material_map:
                        path = material_map[material_id]
                        if "assets/image/" in path:
                            timerange = segment.get("target_timerange", {})
                            start_us = timerange.get("start", 0)
                            duration_us = timerange.get("duration", 0)

                            start_sec = start_us / 1_000_000
                            duration_sec = duration_us / 1_000_000
                            end_sec = start_sec + duration_sec

                            if "assets/image/" in path:
                                relative_path = path[path.index("assets/image/"):]
                            else:
                                relative_path = path

                            image_segments.append({
                                "material_id": material_id,
                                "path": relative_path,
                                "filename": Path(path).name,
                                "start_sec": round(start_sec, 2),
                                "end_sec": round(end_sec, 2),
                                "duration_sec": round(duration_sec, 2),
                            })

        image_segments.sort(key=lambda x: x["start_sec"])

        cue_map = {}
        image_cues_path = draft_dir / "image_cues.json"
        if image_cues_path.exists():
            try:
                with open(image_cues_path, "r", encoding="utf-8") as f:
                    cues_data = json.load(f)
                    cues = cues_data.get("cues", []) if isinstance(cues_data, dict) else cues_data
                    for cue in cues:
                        cue_index = cue.get("index", 0) - 1
                        if cue_index >= 0:
                            cue_map[cue_index] = cue
            except Exception as e:
                print(f"Warning: Failed to load image_cues.json: {e}")

        images = []
        for idx, seg in enumerate(image_segments):
            cue_info = cue_map.get(idx, {})
            images.append({
                "index": idx,
                "path": seg["path"],
                "filename": seg["filename"],
                "url": f"/drafts/{draft_name}/{seg['path']}",
                "start_sec": seg["start_sec"],
                "end_sec": seg["end_sec"],
                "duration_sec": seg["duration_sec"],
                "cue_info": {
                    "prompt": cue_info.get("prompt", ""),
                    "summary": cue_info.get("summary", ""),
                    "context_reason": cue_info.get("context_reason", ""),
                    "emotional_tone": cue_info.get("emotional_tone", ""),
                    "visual_focus": cue_info.get("visual_focus", ""),
                }
            })

        return {
            "name": draft_name,
            "path": str(draft_dir),
            "title": draft_info.get("draft_name", draft_name),
            "duration": draft_info.get("duration", 0) / 1_000_000,
            "images": images,
            "track_count": len(draft_content.get("tracks", [])),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/drafts/{draft_name}/images/{image_index}")
async def replace_draft_image(draft_name: str, image_index: int, file: UploadFile = FastAPIFile(...)):
    draft_dir = CAPCUT_DRAFT_ROOT / draft_name
    if not draft_dir.exists():
        raise HTTPException(status_code=404, detail="Draft not found")

    draft_info_path = draft_dir / "draft_info.json"
    if not draft_info_path.exists():
        raise HTTPException(status_code=404, detail="Draft info not found")

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Invalid file type. Must be an image.")

    image_dir = draft_dir / "assets" / "image"
    if not image_dir.exists():
        raise HTTPException(status_code=404, detail="Image directory not found")

    image_files = sorted(image_dir.glob("*.png"))
    if image_index >= len(image_files):
        raise HTTPException(status_code=404, detail=f"Image index {image_index} not found")

    target_image = image_files[image_index]

    try:
        temp_path = image_dir / f"temp_{target_image.name}"
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        target_image.unlink()
        temp_path.rename(target_image)

        return {
            "success": True,
            "message": f"Image {image_index} replaced successfully",
            "filename": target_image.name,
            "path": str(target_image.relative_to(draft_dir)),
        }
    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink()
        raise HTTPException(status_code=500, detail=f"Failed to replace image: {str(exc)}") from exc


@app.get("/drafts/{draft_name}/{rest_path:path}")
def serve_draft_file(draft_name: str, rest_path: str):
    target = CAPCUT_DRAFT_ROOT / draft_name / rest_path
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target)


@app.get("/api/projects/{project_id}/output/{rest_path:path}")
def serve_project_output_file(project_id: str, rest_path: str):
    output_dir = PROJECT_ROOT / "output" / project_id
    target = output_dir / rest_path

    try:
        target = target.resolve()
        output_dir = output_dir.resolve()
        if not str(target).startswith(str(output_dir)):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=403, detail="Access denied")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(target)


@app.get("/healthz")
def health_check():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Audio Integrity API
# ---------------------------------------------------------------------------

@app.get("/api/audio-check/{channel_id}/{video_id}")
def get_audio_integrity_log(channel_id: str, video_id: str):
    """Retrieve audio integrity logs from log.json."""
    log_path = SCRIPT_PIPELINE_DATA_ROOT / channel_id / video_id / "audio_prep" / "log.json"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Audio log not found. Run Strict Pipeline first.")
    
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse log.json: {e}")

@app.get("/api/kb")
def get_knowledge_base():
    """Retrieve Global Knowledge Base."""
    if not KB_PATH.exists():
        return {"version": 1, "entries": {}}
    
    try:
        with open(KB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load KB: {e}")

@app.delete("/api/kb/{entry_key}")
def delete_knowledge_base_entry(entry_key: str):
    """Delete an entry from GKB."""
    if not KB_PATH.exists():
        raise HTTPException(status_code=404, detail="KB not found")
    
    try:
        with open(KB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if entry_key in data["entries"]:
            del data["entries"][entry_key]
            
            # Atomic write
            temp_path = KB_PATH.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            shutil.move(temp_path, KB_PATH)
            
            return {"success": True, "message": f"Deleted key {entry_key}"}
        else:
            raise HTTPException(status_code=404, detail="Entry key not found")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update KB: {e}")
