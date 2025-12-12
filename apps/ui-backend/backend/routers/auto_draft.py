from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Body

from factory_common.paths import audio_artifacts_root, repo_root, video_pkg_root, video_runs_root

router = APIRouter(prefix="/api/auto-draft", tags=["auto-draft"])

PROJECT_ROOT = video_pkg_root()
REPO_ROOT = repo_root()
INPUT_ROOT = audio_artifacts_root() / "final"
SCRIPT = PROJECT_ROOT / "tools" / "auto_capcut_run.py"
TEMPLATE_ROOT = PROJECT_ROOT / "templates"


def _ensure_paths():
    if not INPUT_ROOT.exists():
        raise HTTPException(status_code=500, detail=f"input dir not found: {INPUT_ROOT}")
    if not SCRIPT.exists():
        raise HTTPException(status_code=500, detail=f"auto_capcut_run.py not found: {SCRIPT}")
    if not TEMPLATE_ROOT.exists():
        TEMPLATE_ROOT.mkdir(parents=True, exist_ok=True)


@router.get("/srts")
def list_srts() -> Dict[str, Any]:
    """List available SRT files under input/."""
    _ensure_paths()
    items: List[Dict[str, str]] = []
    for p in INPUT_ROOT.rglob("*.srt"):
        rel = p.relative_to(INPUT_ROOT)
        items.append({"name": str(rel), "path": str(p)})
    items = sorted(items, key=lambda x: x["name"])
    return {"items": items, "input_root": str(INPUT_ROOT)}


@router.get("/srt")
def read_srt(path: str) -> Dict[str, Any]:
    """
    Return the content of a selected SRT for quick preview in UI.
    """
    _ensure_paths()
    target = Path(path).expanduser().resolve()
    try:
        target.relative_to(INPUT_ROOT)
    except Exception:
        raise HTTPException(status_code=400, detail=f"srt_path must be under {INPUT_ROOT}")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"srt not found: {target}")
    if target.suffix.lower() != ".srt":
        raise HTTPException(status_code=400, detail="target is not an .srt file")
    try:
        stat = target.stat()
        content = target.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:  # pragma: no cover - I/O guard
        raise HTTPException(status_code=500, detail=f"failed to read srt: {exc}") from exc
    return {
        "name": target.name,
        "path": str(target),
        "size_bytes": stat.st_size,
        "modified_time": stat.st_mtime,
        "content": content,
    }


@router.put("/srt")
def write_srt(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Overwrite a selected SRT with provided content.
    """
    _ensure_paths()
    path = payload.get("path")
    content = payload.get("content", "")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    target = Path(str(path)).expanduser().resolve()
    try:
        target.relative_to(INPUT_ROOT)
    except Exception:
        raise HTTPException(status_code=400, detail=f"srt_path must be under {INPUT_ROOT}")
    if target.suffix.lower() != ".srt":
        raise HTTPException(status_code=400, detail="target is not an .srt file")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        stat = target.stat()
    except Exception as exc:  # pragma: no cover - I/O guard
        raise HTTPException(status_code=500, detail=f"failed to write srt: {exc}") from exc
    return {
        "ok": True,
        "name": target.name,
        "path": str(target),
        "size_bytes": stat.st_size,
        "modified_time": stat.st_mtime,
    }


@router.get("/prompt-templates")
def list_prompt_templates() -> Dict[str, Any]:
    """List available prompt templates under templates/."""
    _ensure_paths()
    items: List[Dict[str, str]] = []
    for p in TEMPLATE_ROOT.glob("*.txt"):
        items.append({"name": p.name, "path": str(p)})
    items = sorted(items, key=lambda x: x["name"])
    return {"items": items, "template_root": str(TEMPLATE_ROOT)}


@router.get("/prompt-template")
def get_prompt_template(path: str) -> Dict[str, Any]:
    """
    Return the content of a prompt template (only under templates/).
    Accepts either an absolute path or a filename relative to templates/.
    """
    _ensure_paths()
    target = Path(path)
    if not target.is_absolute():
        # tolerate "templates/..." prefix to avoid templates/templates duplication
        clean = str(target)
        if clean.startswith("templates/"):
            clean = clean[len("templates/") :]
        elif clean.startswith("/templates/"):
            clean = clean[len("/templates/") :]
        target = (TEMPLATE_ROOT / clean).resolve()
    else:
        target = target.resolve()
        # if someone passed an absolute path under project root, remap to templates root by filename
        try:
            target.relative_to(TEMPLATE_ROOT)
        except Exception:
            if target.parent == TEMPLATE_ROOT.parent:
                target = (TEMPLATE_ROOT / target.name).resolve()
            else:
                raise HTTPException(status_code=400, detail="path must be under templates/")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"template not found: {target}")
    content = target.read_text(encoding="utf-8", errors="ignore")
    return {"name": target.name, "path": str(target), "content": content, "template_root": str(TEMPLATE_ROOT)}


@router.post("/create")
def create_draft(
    srt_path: str = Body(..., embed=True),
    channel: str | None = Body(None, embed=True),
    run_name: str | None = Body(None, embed=True),
    title: str | None = Body(None, embed=True),
    labels: str | None = Body(None, embed=True),
    template: str | None = Body(None, embed=True),
    prompt_template: str | None = Body(None, embed=True),
    belt_mode: str | None = Body(None, embed=True),
    chapters_json: str | None = Body(None, embed=True),
    episode_info_json: str | None = Body(None, embed=True),
    imgdur: float | None = Body(None, embed=True),
) -> Dict[str, Any]:
    """Run auto_capcut_run.py for the given SRT."""
    _ensure_paths()
    srt = Path(srt_path).expanduser().resolve()
    try:
        srt.relative_to(INPUT_ROOT)
    except Exception:
        raise HTTPException(status_code=400, detail=f"srt_path must be under {INPUT_ROOT}")
    if not srt.exists():
        raise HTTPException(status_code=400, detail=f"srt not found: {srt}")

    # channel inference: take first component after input (e.g., input/CH01_xxx/yyy.srt -> CH01_xxx)
    parts = srt.relative_to(INPUT_ROOT).parts
    channel_guess = parts[0] if parts else "CH01"
    inferred_channel = None
    if channel_guess.startswith("CH") and len(channel_guess) >= 4 and channel_guess[2:4].isdigit():
        inferred_channel = channel_guess[:4]
    else:
        inferred_channel = channel_guess
    if channel:
        # Safety: prevent cross-channel wiring (wrong template/preset applied to another channel's SRT)
        if inferred_channel and channel.upper() != inferred_channel.upper():
            raise HTTPException(status_code=400, detail=f"channel mismatch: srt belongs to {inferred_channel} but channel={channel}")
    else:
        channel = inferred_channel
    if not run_name:
        ts = time.strftime("%Y%m%d_%H%M%S")
        stem = srt.stem
        if stem.isdigit():
            stem = f"{channel}-{stem.zfill(3)}"
        run_name = f"{stem}_{ts}"
    if not belt_mode:
        belt_mode = "llm"

    # Prepare run_dir early (for grouped uploads)
    run_dir = video_runs_root() / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    # Normalize prompt_template path (avoid templates/templates duplication)
    normalized_prompt_template = None
    if prompt_template:
        pt = Path(prompt_template)
        if not pt.is_absolute():
            clean = str(pt)
            if clean.startswith("templates/"):
                clean = clean[len("templates/") :]
            elif clean.startswith("/templates/"):
                clean = clean[len("/templates/") :]
            pt = (TEMPLATE_ROOT / clean).resolve()
        else:
            pt = pt.resolve()
        normalized_prompt_template = str(pt)

    cmd = [
        "python3",
        str(SCRIPT),
        "--channel",
        channel,
        "--srt",
        str(srt),
        "--run-name",
        run_name,
        "--title",
        title,
    ]
    if labels:
        cmd += ["--labels", labels]
    if template:
        cmd += ["--template", template]
    if normalized_prompt_template:
        cmd += ["--prompt-template", normalized_prompt_template]
    if belt_mode:
        cmd += ["--belt-mode", belt_mode]
    if imgdur:
        cmd += ["--imgdur", str(imgdur)]

    # If grouped mode, require both JSONs and write to run_dir before execution
    if belt_mode == "grouped":
        if not chapters_json or not episode_info_json:
            raise HTTPException(status_code=400, detail="grouped requires chapters_json and episode_info_json")
        try:
            (run_dir / "chapters.json").write_text(chapters_json, encoding="utf-8")
            (run_dir / "episode_info.json").write_text(episode_info_json, encoding="utf-8")
        except Exception as exc:  # pragma: no cover - file IO guard
            raise HTTPException(status_code=500, detail=f"failed to write grouped inputs: {exc}")

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="auto_capcut_run timeout")

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"auto_capcut_run failed (exit={proc.returncode})\n{stdout}{stderr}",
        )

    return {
        "ok": True,
        "stdout": stdout,
        "stderr": stderr,
        "run_name": run_name,
        "title": title,
        "channel": channel,
        "run_dir": str(run_dir.relative_to(REPO_ROOT)) if run_dir.is_relative_to(REPO_ROOT) else str(run_dir),
        "run_dir_abs": str(run_dir),
    }
