from __future__ import annotations

import json
import subprocess
import time
import uuid
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Body
from fastapi.responses import FileResponse
from PIL import Image

router = APIRouter(prefix="/api/swap", tags=["swap"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SAFE_SWAP = PROJECT_ROOT / "commentary_02_srt2images_timeline" / "tools" / "safe_image_swap.py"
LOG_DIR = PROJECT_ROOT / "logs" / "swap"
HISTORY_ROOT = LOG_DIR / "history"
WHITELIST_PATH = PROJECT_ROOT / "commentary_02_srt2images_timeline" / "config" / "track_whitelist.json"
IMAGE_ASSET_SUBDIR = "assets/image"
CAPCUT_ROOT = Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
OUTPUT_ROOT = PROJECT_ROOT / "commentary_02_srt2images_timeline" / "output"
IMAGE_CUES_NAME = "image_cues.json"
PROMPT_SNAPSHOT_NAME = "prompt_snapshots.json"
PROMPT_SNAPSHOT_NAME = "prompt_snapshots.json"


def _ensure_paths() -> None:
    if not SAFE_SWAP.exists():
        raise HTTPException(status_code=500, detail=f"safe_image_swap.py not found: {SAFE_SWAP}")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_ROOT.mkdir(parents=True, exist_ok=True)
    WHITELIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAPCUT_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def _write_log(content: str) -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"swap_{ts}.log"
    log_path.write_text(content, encoding="utf-8")
    return str(log_path)


@router.post("/images")
def swap_images(
    draft_path: str = Body(...),
    run_dir: str = Body(...),
    indices: List[int] | str = Body(...),
    custom_prompt: Optional[str] = Body(default=None),
    style_mode: str = Body(default="illustration"),
    only_allow_draft_substring: Optional[str] = Body(default=None),
    apply: bool = Body(default=False),
    validate_after: bool = Body(default=True),
    rollback_on_validate_fail: bool = Body(default=True),
) -> Dict[str, Any]:
    """
    Invoke safe_image_swap.py with validation/rollback options.
    """
    _ensure_paths()

    draft = Path(draft_path).expanduser().resolve()
    run = Path(run_dir).expanduser().resolve()
    if not draft.exists():
        raise HTTPException(status_code=400, detail=f"draft not found: {draft}")
    if not run.exists():
        raise HTTPException(status_code=400, detail=f"run_dir not found: {run}")

    if isinstance(indices, str):
        try:
            indices_list = [int(x.strip()) for x in indices.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="indices must be int list or comma-separated ints")
    else:
        indices_list = [int(x) for x in indices]
    if not indices_list or any(i <= 0 for i in indices_list):
        raise HTTPException(status_code=400, detail="indices must be >=1")
    if len(indices_list) != len(set(indices_list)):
        raise HTTPException(status_code=400, detail="indices contain duplicates")

    # snapshot current assets for rollback
    try:
        _backup_current_images(draft, indices_list)
    except Exception:
        # best-effort; do not block swap if backup fails
        pass
    try:
        _snapshot_prompts(run, indices_list)
    except Exception:
        pass
    try:
        _snapshot_prompts(run, indices_list)
    except Exception:
        pass

    cmd = [
        "python3",
        str(SAFE_SWAP),
        "--run-dir",
        str(run),
        "--draft",
        str(draft),
        "--indices",
        *[str(i) for i in indices_list],
        "--style-mode",
        style_mode,
        "--only-allow-draft-substring",
        only_allow_draft_substring or draft.name,
    ]
    if custom_prompt:
        cmd += ["--custom-prompt", custom_prompt]
    if apply:
        cmd.append("--apply")
    else:
        cmd.append("--dry-run")
    if validate_after:
        cmd.append("--validate-after")
    if validate_after and rollback_on_validate_fail:
        cmd.append("--rollback-on-validate-fail")

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT / "commentary_02_srt2images_timeline"),
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="safe_image_swap timeout")
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    log_content = stdout + "\n" + stderr
    log_path = _write_log(log_content)
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail=f"swap failed (exit={proc.returncode})", headers={"X-Log-Path": log_path})
    return {"ok": True, "log_path": log_path, "stdout": stdout, "stderr": stderr}


@router.get("/logs")
def list_logs(filter: str = Query(default="all", enum=["all", "fail_only"]), limit: int = Query(default=50, ge=1, le=200)) -> List[str]:
    _ensure_paths()
    files = sorted(LOG_DIR.glob("swap_*.log"), reverse=True)
    names: List[str] = []
    for f in files:
        if filter == "fail_only":
            try:
                head = f.read_text(encoding="utf-8")[:8000].lower()
                if not ("❌" in head or "error" in head or "fail" in head or "exit=1" in head or "exit=2" in head):
                    continue
            except Exception:
                continue
        names.append(f.name)
        if len(names) >= limit:
            break
    return names


@router.get("/logs/{log_name}")
def read_log(log_name: str) -> Dict[str, Any]:
    _ensure_paths()
    log_path = LOG_DIR / log_name
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="log not found")
    try:
        content = log_path.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to read log: {e}")
    return {"log": content, "path": str(log_path)}


@router.get("/whitelist")
def get_whitelist() -> Dict[str, Any]:
    _ensure_paths()
    if not WHITELIST_PATH.exists():
        return {"video": [], "audio": []}
    try:
        return json.loads(WHITELIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="failed to parse whitelist")


@router.post("/whitelist")
def update_whitelist(video: List[str] = Body(default=[]), audio: List[str] = Body(default=[])) -> Dict[str, Any]:
    _ensure_paths()
    data = {"video": [v.strip() for v in video if v.strip()], "audio": [a.strip() for a in audio if a.strip()]}
    try:
        WHITELIST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to write whitelist: {e}")
    return {"ok": True, "whitelist": data}


@router.get("/drafts")
def list_drafts(limit: int = Query(default=200, ge=1, le=500)) -> Dict[str, Any]:
    """List CapCut draft directories under CAPCUT_ROOT."""
    _ensure_paths()
    if not CAPCUT_ROOT.exists():
        return {"items": []}
    dirs = sorted([p for p in CAPCUT_ROOT.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    items = [
        {
            "name": d.name,
            "path": str(d),
        }
        for d in dirs[:limit]
    ]
    return {"items": items}


@router.get("/run-dirs")
def list_run_dirs(limit: int = Query(default=200, ge=1, le=500)) -> Dict[str, Any]:
    """List output run directories under commentary_02_srt2images_timeline/output."""
    _ensure_paths()
    if not OUTPUT_ROOT.exists():
        return {"items": []}
    dirs = sorted([p for p in OUTPUT_ROOT.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    items = [
        {
            "name": d.name,
            "path": str(d),
            "mtime": d.stat().st_mtime,
        }
        for d in dirs[:limit]
    ]
    return {"items": items}


@router.get("/auto-run-dir")
def auto_run_dir(draft_name: str) -> Dict[str, Any]:
    """Pick a likely run_dir for a draft name (heuristic)."""
    _ensure_paths()
    cand = _auto_run_dir_for_draft(draft_name)
    return {"run_dir": cand}


def _find_srt2images_track(draft_path: Path):
    content_path = draft_path / "draft_content.json"
    if not content_path.exists():
        raise HTTPException(status_code=400, detail="draft_content.json not found")
    data = json.loads(content_path.read_text(encoding="utf-8"))
    tracks = data.get("tracks") or data.get("script", {}).get("tracks") or []
    for t in tracks:
        nm = t.get("name") or t.get("id") or ""
        if nm.startswith("srt2images_"):
            return t, data
    raise HTTPException(status_code=400, detail="srt2images track not found")


def _history_dir_for(draft_dir: Path, index: int) -> Path:
    safe_name = draft_dir.name
    return HISTORY_ROOT / safe_name / f"{index:04d}"


def _backup_current_images(draft_dir: Path, indices: List[int]) -> List[str]:
    """Copy current assets for the given indices into HISTORY_ROOT for rollback."""
    try:
        track, data = _find_srt2images_track(draft_dir)
    except HTTPException:
        return []
    videos = data.get("materials", {}).get("videos", [])
    by_id = {m.get("id"): m for m in videos}
    copied: List[str] = []
    ts = time.strftime("%Y%m%d_%H%M%S")
    for idx, seg in enumerate(track.get("segments") or [], start=1):
        if idx not in indices:
            continue
        mid = seg.get("material_id")
        mname = seg.get("material_name") or (by_id.get(mid) or {}).get("material_name")
        if not mname:
            continue
        src = draft_dir / IMAGE_ASSET_SUBDIR / mname
        if not src.exists():
            continue
        dst_dir = _history_dir_for(draft_dir, idx) / ts
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        shutil.copy2(src, dst)
        copied.append(str(dst))
    return copied


def _update_draft_with_existing_image(draft_dir: Path, index: int, image_path: Path) -> bool:
    """Replace the image for a given index using an existing image file (no regeneration)."""
    content_json = draft_dir / "draft_content.json"
    info_json = draft_dir / "draft_info.json"
    if not content_json.exists() or not info_json.exists():
        return False
    try:
        content_data = json.loads(content_json.read_text(encoding="utf-8"))
        info_data = json.loads(info_json.read_text(encoding="utf-8"))
    except Exception:
        return False

    def _find_track(data):
        return (data.get("tracks") or data.get("script", {}).get("tracks") or [])

    def _find_track_entry(data):
        for t in _find_track(data):
            nm = t.get("name") or t.get("id") or ""
            if nm.startswith("srt2images_"):
                return t
        return None

    content_track = _find_track_entry(content_data)
    info_track = _find_track_entry(info_data)
    if not content_track or not info_track:
        return False

    def _materials(data):
        return data.get("materials", {}).get("videos", [])

    content_mats = _materials(content_data)
    info_mats = _materials(info_data)

    def _find_material_by_id_or_name(mats, target_id, idx):
        target_index_str = f"{idx:04d}"
        found = None
        for m in mats:
            mid = m.get("id")
            name = m.get("material_name", "")
            path = m.get("path", "")
            if mid == target_id:
                return m
            if target_index_str in name or target_index_str in Path(path).name:
                found = m
        return found

    def _replace_in_track(track, old_id, new_id):
        replaced = 0
        for seg in track.get("segments", []):
            if seg.get("material_id") == old_id:
                seg["material_id"] = new_id
                replaced += 1
            if "extra_material_refs" in seg:
                refs = seg["extra_material_refs"]
                for i, ref in enumerate(refs):
                    if ref == old_id:
                        refs[i] = new_id
                        replaced += 1
        return replaced

    try:
        target_seg = (content_track.get("segments") or [])[index - 1]
    except Exception:
        return False
    old_id = target_seg.get("material_id")
    if not old_id:
        return False

    content_mat = _find_material_by_id_or_name(content_mats, old_id, index)
    info_mat = _find_material_by_id_or_name(info_mats, old_id, index)
    if not content_mat:
        return False

    asset_dir = draft_dir / "assets" / "image"
    asset_dir.mkdir(parents=True, exist_ok=True)
    draft_image_path = asset_dir / image_path.name
    shutil.copy2(image_path, draft_image_path)

    new_id = str(uuid.uuid4())
    for mat in [content_mat, info_mat]:
        if mat is None:
            continue
        mat["id"] = new_id
        mat["path"] = str(draft_image_path)
        mat["material_name"] = image_path.name
        try:
            with Image.open(image_path) as img:
                mat["width"] = img.width
                mat["height"] = img.height
        except Exception:
            pass

    replaced = _replace_in_track(content_track, old_id, new_id) + _replace_in_track(info_track, old_id, new_id)
    if replaced == 0:
        return False

    shutil.copy2(content_json, str(content_json) + ".bak_revert")
    shutil.copy2(info_json, str(info_json) + ".bak_revert")
    content_json.write_text(json.dumps(content_data, ensure_ascii=False, indent=2), encoding="utf-8")
    info_json.write_text(json.dumps(info_data, ensure_ascii=False, indent=2), encoding="utf-8")
    info_json.touch()
    return True


@router.get("/images/list")
def list_draft_images(draft_path: str) -> Dict[str, Any]:
    """List srt2images material_name by index (order in timeline)."""
    draft = Path(draft_path).expanduser().resolve()
    if not draft.exists():
        raise HTTPException(status_code=400, detail="draft not found")
    track, data = _find_srt2images_track(draft)
    videos = data.get("materials", {}).get("videos", [])
    by_id = {m.get("id"): m for m in videos}
    result = []
    for idx, seg in enumerate(track.get("segments") or [], start=1):
        mid = seg.get("material_id")
        mname = seg.get("material_name") or (by_id.get(mid) or {}).get("material_name")
        tr = seg.get("target_timerange") or {}
        start_ms = tr.get("start")
        duration_ms = tr.get("duration")
        result.append(
            {
                "index": idx,
                "material_id": mid,
                "material_name": mname,
                "start_ms": start_ms,
                "duration_ms": duration_ms,
                "asset_path": f"{draft}/{IMAGE_ASSET_SUBDIR}/{mname}" if mname else None,
            }
        )
    return {"items": result}


@router.get("/images/file")
def get_image_file(draft_path: str, material_name: str):
    """Serve image file from draft assets/image. material_name is sanitized to a filename."""
    draft = Path(draft_path).expanduser().resolve()
    if not draft.exists():
        raise HTTPException(status_code=400, detail="draft not found")
    if "/" in material_name or "\\" in material_name:
        raise HTTPException(status_code=400, detail="invalid material_name")
    img_path = draft / IMAGE_ASSET_SUBDIR / material_name
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(img_path)


@router.get("/image-cues")
def get_image_cues(run_dir: str) -> Dict[str, Any]:
    """Return image cues (prompts) from run_dir/image_cues.json if available."""
    path = Path(run_dir).expanduser().resolve()
    cues_path = path / IMAGE_CUES_NAME
    if not cues_path.exists():
        return {"items": []}
    try:
        data = json.loads(cues_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to read image_cues.json: {e}")
    cues = data.get("sections") or data.get("cues") or []
    items = []
    for idx, cue in enumerate(cues, start=1):
        prompt = cue.get("prompt") or cue.get("positive") or cue.get("raw_prompt") or ""
        items.append({"index": idx, "prompt": prompt})
    return {"items": items}


@router.get("/prompt-snapshots")
def get_prompt_snapshots(run_dir: str) -> Dict[str, Any]:
    """Return prompt snapshots from run_dir/prompt_snapshots.json if available."""
    path = Path(run_dir).expanduser().resolve()
    snap_path = path / PROMPT_SNAPSHOT_NAME
    if not snap_path.exists():
        return {"items": []}
    try:
        data = json.loads(snap_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return {"items": []}
        items = []
        for row in data:
            idx = row.get("index")
            prompt = row.get("prompt") or ""
            ts = row.get("timestamp") or ""
            if isinstance(idx, int):
                items.append({"index": idx, "prompt": prompt, "timestamp": ts})
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to read prompt_snapshots.json: {e}")


@router.get("/images/history")
def list_history(draft_path: str, index: int = Query(ge=1), limit: int = Query(default=20, ge=1, le=200)) -> Dict[str, Any]:
    draft = Path(draft_path).expanduser().resolve()
    dirpath = _history_dir_for(draft, index)
    if not dirpath.exists():
        return {"items": []}
    items = []
    for ts_dir in sorted([p for p in dirpath.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True):
        for img in sorted(ts_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            items.append({"ts": ts_dir.name, "filename": img.name, "path": str(img)})
            if len(items) >= limit:
                return {"items": items}
    return {"items": items}


@router.get("/images/history/file")
def get_history_file(path: str):
    p = Path(path).expanduser().resolve()
    try:
        p.relative_to(HISTORY_ROOT)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid history path")
    if not p.exists():
        raise HTTPException(status_code=404, detail="history image not found")
    return FileResponse(p)


@router.post("/images/rollback")
def rollback_image(
    draft_path: str = Body(...),
    index: int = Body(..., ge=1),
    history_path: str = Body(...),
) -> Dict[str, Any]:
    draft = Path(draft_path).expanduser().resolve()
    hpath = Path(history_path).expanduser().resolve()
    try:
        hpath.relative_to(HISTORY_ROOT)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid history path")
    if not draft.exists():
        raise HTTPException(status_code=400, detail="draft not found")
    if not hpath.exists():
        raise HTTPException(status_code=404, detail="history image missing")
    ok = _update_draft_with_existing_image(draft, index, hpath)
    if not ok:
        raise HTTPException(status_code=500, detail="rollback failed (could not swap ids)")
    return {"ok": True}
def _auto_run_dir_for_draft(draft_name: str) -> Optional[str]:
    # simple heuristic: find a run dir whose name contains draft_name prefix digits
    if not draft_name:
        return None
    prefix = None
    for token in draft_name.replace("【", "_").replace("】", "_").split("_"):
        if token.isdigit():
            prefix = token
            break
    candidates = sorted([p for p in OUTPUT_ROOT.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    for cand in candidates:
        if prefix and prefix in cand.name:
            return str(cand)
    return candidates[0].as_posix() if candidates else None


def _snapshot_prompts(run_dir: Path, indices: List[int]) -> None:
    """Best-effort: append prompts for given indices to run_dir/prompt_snapshots.json."""
    cues_path = run_dir / IMAGE_CUES_NAME
    if not cues_path.exists():
        return
    try:
        data = json.loads(cues_path.read_text(encoding="utf-8"))
    except Exception:
        return
    cues = data.get("cues") or data.get("sections") or []
    by_idx: Dict[int, str] = {}
    for i, cue in enumerate(cues, start=1):
        prompt = cue.get("prompt") or cue.get("raw_prompt") or cue.get("positive") or ""
        by_idx[i] = prompt
    rows = []
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    for idx in indices:
        rows.append({"index": idx, "prompt": by_idx.get(idx, ""), "timestamp": ts})
    snap_path = run_dir / PROMPT_SNAPSHOT_NAME
    try:
        existing = []
        if snap_path.exists():
            existing = json.loads(snap_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        existing.extend(rows)
        snap_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def _snapshot_prompts(run_dir: Path, indices: List[int]) -> None:
    """Best-effort: append prompts for given indices to run_dir/prompt_snapshots.json."""
    cues_path = run_dir / IMAGE_CUES_NAME
    if not cues_path.exists():
        return
    try:
        data = json.loads(cues_path.read_text(encoding="utf-8"))
    except Exception:
        return
    cues = data.get("cues") or data.get("sections") or []
    by_idx: Dict[int, str] = {}
    for i, cue in enumerate(cues, start=1):
        prompt = cue.get("prompt") or cue.get("raw_prompt") or cue.get("positive") or ""
        by_idx[i] = prompt
    rows = []
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    for idx in indices:
        rows.append({"index": idx, "prompt": by_idx.get(idx, ""), "timestamp": ts})
    snap_path = run_dir / PROMPT_SNAPSHOT_NAME
    try:
        existing = []
        if snap_path.exists():
            existing = json.loads(snap_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        existing.extend(rows)
        snap_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return
