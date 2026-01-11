from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from factory_common.paths import logs_root, repo_root as ssot_repo_root

router = APIRouter(prefix="/api/agent-org", tags=["agent_org"])

REPO_ROOT = ssot_repo_root()
DEFAULT_QUEUE_DIR = logs_root() / "agent_tasks"


def _queue_dir() -> Path:
    raw = (os.getenv("LLM_AGENT_QUEUE_DIR") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (REPO_ROOT / p)
    return DEFAULT_QUEUE_DIR


def _coord_dir() -> Path:
    return _queue_dir() / "coordination"


def _read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _locked_update_json(path: Path, update_fn) -> dict:
    """
    Best-effort locked read-modify-write to avoid clobbering concurrent updates.
    Falls back to atomic replace when flock isn't available.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl  # unix-only
    except Exception:
        cur = _read_json(path) if path.exists() else {}
        nxt = update_fn(cur if isinstance(cur, dict) else {})
        if not isinstance(nxt, dict):
            nxt = {}
        _atomic_write_json(path, nxt)
        return nxt

    with path.open("a+", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            cur = _read_json(path) if path.exists() else {}
            nxt = update_fn(cur if isinstance(cur, dict) else {})
            if not isinstance(nxt, dict):
                nxt = {}
            _atomic_write_json(path, nxt)
            return nxt

        f.seek(0)
        raw = f.read()
        try:
            cur = json.loads(raw) if raw.strip() else {}
        except Exception:
            cur = {}
        if not isinstance(cur, dict):
            cur = {}

        nxt = update_fn(cur)
        if not isinstance(nxt, dict):
            nxt = {}

        f.seek(0)
        f.truncate()
        f.write(json.dumps(nxt, ensure_ascii=False, indent=2) + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
        return nxt


def _append_event(payload: dict) -> None:
    p = _coord_dir() / "events.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


def _new_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}__{stamp}__{secrets.token_hex(4)}"


def _parse_csv_list(raw: Optional[str]) -> List[str]:
    if raw is None:
        return []
    out: List[str] = []
    for part in str(raw).split(","):
        s = part.strip()
        if s:
            out.append(s)
    seen = set()
    uniq: List[str] = []
    for s in out:
        if s in seen:
            continue
        uniq.append(s)
        seen.add(s)
    return uniq


def _board_path() -> Path:
    return _coord_dir() / "board.json"


def _board_default_payload(now_iso: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "agent_board",
        "updated_at": now_iso,
        "agents": {},
        "areas": {},
        "log": [],
    }


def _board_ensure_shape(obj: dict, now_iso: str) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        obj = {}
    base = _board_default_payload(now_iso)
    obj.setdefault("schema_version", base["schema_version"])
    obj.setdefault("kind", base["kind"])
    obj.setdefault("updated_at", base["updated_at"])
    obj.setdefault("agents", {})
    obj.setdefault("areas", {})
    obj.setdefault("log", [])
    if not isinstance(obj.get("agents"), dict):
        obj["agents"] = {}
    if not isinstance(obj.get("areas"), dict):
        obj["areas"] = {}
    if not isinstance(obj.get("log"), list):
        obj["log"] = []
    return obj  # type: ignore[return-value]


class BoardStatusUpdateRequest(BaseModel):
    from_agent: str = Field("ui", alias="from", description="actor name")
    doing: Optional[str] = Field(default=None)
    blocked: Optional[str] = Field(default=None)
    next: Optional[str] = Field(default=None)
    note: Optional[str] = Field(default=None)
    tags: Optional[str] = Field(default=None, description="comma-separated tags")
    clear: bool = Field(default=False)


class BoardNoteCreateRequest(BaseModel):
    from_agent: str = Field("ui", alias="from", description="actor name")
    topic: str = Field(..., description="thread/topic title (BEP-1 recommended)")
    message: str = Field(..., description="body")
    reply_to: Optional[str] = Field(default=None, description="reply to existing note_id")
    tags: Optional[str] = Field(default=None, description="comma-separated tags")


class BoardAreaSetRequest(BaseModel):
    from_agent: str = Field("ui", alias="from", description="actor name")
    area: str = Field(..., description="area key (e.g., script/audio/ui)")
    owner: Optional[str] = Field(default=None)
    reviewers: Optional[str] = Field(default=None, description="comma-separated reviewers")
    note: Optional[str] = Field(default=None)
    clear: bool = Field(default=False)


@router.get("/board")
def get_board() -> Dict[str, Any]:
    q = _queue_dir()
    p = _board_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    obj = _read_json(p) if p.exists() else {}
    board = _board_ensure_shape(obj if isinstance(obj, dict) else {}, now_iso)
    return {"queue_dir": str(q), "board_path": str(p), "board": board}


@router.post("/board/status")
def set_status(req: BoardStatusUpdateRequest) -> Dict[str, Any]:
    q = _queue_dir()
    p = _board_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    actor = (req.from_agent or "ui").strip() or "ui"
    tags = _parse_csv_list(req.tags) if req.tags is not None else None

    def _update(cur: dict) -> dict:
        cur = _board_ensure_shape(cur if isinstance(cur, dict) else {}, now_iso)
        agents = cur.get("agents") if isinstance(cur.get("agents"), dict) else {}
        if not isinstance(agents, dict):
            agents = {}

        if req.clear:
            agents.pop(actor, None)
            cur["agents"] = agents
            cur["updated_at"] = now_iso
            return cur

        st = agents.get(actor)
        if not isinstance(st, dict):
            st = {}

        if req.doing is not None:
            st["doing"] = str(req.doing)
        if req.blocked is not None:
            st["blocked"] = str(req.blocked)
        if req.next is not None:
            st["next"] = str(req.next)
        if req.note is not None:
            st["note"] = str(req.note)
        if tags is not None:
            st["tags"] = tags

        st["updated_at"] = now_iso
        agents[actor] = st
        cur["agents"] = agents
        cur["updated_at"] = now_iso
        return cur

    board = _locked_update_json(p, _update)
    _append_event(
        {
            "schema_version": 1,
            "kind": "event",
            "created_at": now_iso,
            "actor": actor,
            "action": "board_set",
            "board_path": str(p),
        }
    )
    return {"queue_dir": str(q), "board_path": str(p), "board": board}


@router.post("/board/area")
def set_area(req: BoardAreaSetRequest) -> Dict[str, Any]:
    q = _queue_dir()
    p = _board_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    actor = (req.from_agent or "ui").strip() or "ui"
    area = (req.area or "").strip()
    if not area:
        raise HTTPException(status_code=400, detail="area is required")

    reviewers = _parse_csv_list(req.reviewers) if req.reviewers is not None else None

    def _update(cur: dict) -> dict:
        cur = _board_ensure_shape(cur if isinstance(cur, dict) else {}, now_iso)
        areas = cur.get("areas") if isinstance(cur.get("areas"), dict) else {}
        if not isinstance(areas, dict):
            areas = {}

        if req.clear:
            areas.pop(area, None)
            cur["areas"] = areas
            cur["updated_at"] = now_iso
            return cur

        st = areas.get(area)
        if not isinstance(st, dict):
            st = {}
        if req.owner is not None:
            st["owner"] = str(req.owner).strip() if str(req.owner).strip() else None
        if reviewers is not None:
            st["reviewers"] = reviewers
        if req.note is not None:
            st["note"] = str(req.note)
        st["updated_at"] = now_iso
        st["updated_by"] = actor
        areas[area] = st
        cur["areas"] = areas
        cur["updated_at"] = now_iso
        return cur

    board = _locked_update_json(p, _update)
    _append_event(
        {
            "schema_version": 1,
            "kind": "event",
            "created_at": now_iso,
            "actor": actor,
            "action": "board_area_set",
            "board_path": str(p),
            "area": area,
        }
    )
    return {"queue_dir": str(q), "board_path": str(p), "board": board}


@router.post("/board/note")
def post_note(req: BoardNoteCreateRequest) -> Dict[str, Any]:
    q = _queue_dir()
    p = _board_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    actor = (req.from_agent or "ui").strip() or "ui"
    topic = (req.topic or "").strip()
    message = (req.message or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    note_id = _new_id("note")
    reply_to = (req.reply_to or "").strip() or None
    tags = _parse_csv_list(req.tags) if req.tags else []
    entry: Dict[str, Any] = {
        "id": note_id,
        "thread_id": note_id,  # overwritten for replies
        "ts": now_iso,
        "agent": actor,
        "topic": topic,
        "message": message,
        "tags": tags,
    }

    def _update(cur: dict) -> dict:
        cur = _board_ensure_shape(cur if isinstance(cur, dict) else {}, now_iso)
        agents = cur.get("agents") if isinstance(cur.get("agents"), dict) else {}
        if not isinstance(agents, dict):
            agents = {}
        st = agents.get(actor)
        if not isinstance(st, dict):
            st = {}
        st["last_note_at"] = now_iso
        st.setdefault("updated_at", now_iso)
        agents[actor] = st
        cur["agents"] = agents

        log = cur.get("log") if isinstance(cur.get("log"), list) else []
        if not isinstance(log, list):
            log = []

        if reply_to:
            parent = None
            for e in reversed(log):
                if isinstance(e, dict) and str(e.get("id") or "").strip() == reply_to:
                    parent = e
                    break
            if not parent:
                raise ValueError(f"reply_to note not found: {reply_to}")
            entry["reply_to"] = reply_to
            entry["thread_id"] = str(parent.get("thread_id") or parent.get("id") or reply_to)

        log.append(entry)
        max_log = 1000
        if len(log) > max_log:
            log = log[-max_log:]
        cur["log"] = log
        cur["updated_at"] = now_iso
        return cur

    try:
        _locked_update_json(p, _update)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    _append_event(
        {
            "schema_version": 1,
            "kind": "event",
            "created_at": now_iso,
            "actor": actor,
            "action": "board_note",
            "board_path": str(p),
            "topic": topic,
        }
    )
    return {"note_id": note_id, "thread_id": str(entry.get("thread_id") or note_id)}

