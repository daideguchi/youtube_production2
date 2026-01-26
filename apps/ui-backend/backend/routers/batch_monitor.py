from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from factory_common import paths as repo_paths
from factory_common.path_ref import resolve_path_ref

router = APIRouter(tags=["ops"])

REPO_ROOT = repo_paths.repo_root()
LOGS_ROOT = repo_paths.logs_root()
WORKSPACES_ROOT = repo_paths.workspace_root()
VIDEO_RUNS_ROOT = repo_paths.video_runs_root()

BATCH_ROOT = LOGS_ROOT / "batch"

_RUN_LINE_RE = re.compile(r"^\[(RUN|SKIP)\]\s+([A-Za-z0-9_-]+)\s*$")
_RUN_DIR_IN_LINE_RE = re.compile(r"workspaces/video/runs/([A-Za-z0-9_.-]+)")
_CHANNEL_IN_LINE_RE = re.compile(r"\bCH\d{2}\b")
_EP_IN_RUN_ID_RE = re.compile(r"^CH\d+-([0-9]{3})\b")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True
    return True


def _ps_etimes(pid: int) -> Optional[int]:
    """
    Returns elapsed seconds for PID using `ps`.
    """
    try:
        proc = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "etimes="],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    return _safe_int(raw)


def _tail_lines(path: Path, *, max_lines: int = 200, max_bytes: int = 128 * 1024) -> List[str]:
    if max_lines <= 0:
        return []
    try:
        st = path.stat()
    except Exception:
        return []
    size = int(st.st_size)
    try:
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(max(0, size - max_bytes))
            blob = f.read()
    except Exception:
        return []
    try:
        text = blob.decode("utf-8", errors="replace")
    except Exception:
        return []
    lines = text.splitlines()
    return lines[-max_lines:]


def _discover_batches() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not BATCH_ROOT.exists():
        return out

    for pid_path in sorted(BATCH_ROOT.glob("*.pid")):
        batch_id = pid_path.stem
        log_path = BATCH_ROOT / f"{batch_id}.log"
        mtime = 0.0
        try:
            mtime = float(log_path.stat().st_mtime) if log_path.exists() else float(pid_path.stat().st_mtime)
        except Exception:
            mtime = 0.0
        out.append(
            {
                "id": batch_id,
                "pid_path": str(pid_path),
                "log_path": str(log_path) if log_path.exists() else None,
                "mtime": mtime,
            }
        )

    out.sort(key=lambda x: float(x.get("mtime") or 0.0), reverse=True)
    return out


def _read_pid(pid_path: Path) -> Optional[int]:
    try:
        raw = pid_path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return None
    return _safe_int(raw)


def _last_run_from_log_tail(lines: List[str]) -> Tuple[Optional[str], Optional[str]]:
    last_run = None
    last_kind = None
    for line in reversed(lines):
        m = _RUN_LINE_RE.match(line.strip())
        if not m:
            continue
        last_kind = m.group(1)
        last_run = m.group(2)
        break
    if last_run:
        return last_run, last_kind

    # Fallback: infer run_dir name from tool logs (e.g. "Executing: ... --out .../workspaces/video/runs/<run>")
    for line in reversed(lines):
        m = _RUN_DIR_IN_LINE_RE.search(line)
        if not m:
            continue
        return m.group(1), "RUN"
    return last_run, last_kind


def _infer_channel_code(run_id: str) -> str:
    token = str(run_id or "").split("-", 1)[0].strip().upper()
    if token.startswith("CH") and token[2:].isdigit():
        return token
    return token or "UNKNOWN"


def _list_audio_final_episodes(channel: str) -> List[str]:
    ch = str(channel or "").strip().upper()
    root = WORKSPACES_ROOT / "audio" / "final" / ch
    if not root.exists():
        return []
    eps: List[str] = []
    for p in sorted(root.iterdir()):
        if p.is_dir() and p.name.isdigit():
            eps.append(p.name.zfill(3))
    return eps


def _run_dir_for_episode(channel: str, ep: str) -> Path:
    ch = str(channel or "").strip().upper()
    no = str(ep or "").strip().zfill(3)
    prefix = f"{ch}-{no}_"
    try:
        cands = [p for p in VIDEO_RUNS_ROOT.glob(prefix + "*") if p.is_dir()]
    except Exception:
        cands = []
    if cands:
        try:
            cands.sort(key=lambda p: float(p.stat().st_mtime), reverse=True)
        except Exception:
            pass
        return cands[0]
    # Backward-compatible stable name (older runs)
    return VIDEO_RUNS_ROOT / f"{ch}-{no}_fluxmax_grouped"


def _run_status(run_dir: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "run_dir": str(run_dir),
        "exists": run_dir.exists(),
    }
    if not run_dir.exists():
        out["stage"] = "pending"
        return out

    cues_total = None
    cues_path = run_dir / "image_cues.json"
    if cues_path.exists():
        try:
            obj = json.loads(cues_path.read_text(encoding="utf-8"))
            cues_total = len(obj.get("cues") or []) if isinstance(obj, dict) else None
        except Exception:
            cues_total = None

    img_dir = run_dir / "images"
    images_count = 0
    if img_dir.exists():
        try:
            images_count = len([p for p in img_dir.glob("*.png") if p.is_file()])
        except Exception:
            images_count = 0

    capcut_done = (run_dir / "capcut_draft_info.json").exists()
    out.update(
        {
            "cues_total": cues_total,
            "images_count": images_count,
            "capcut_done": capcut_done,
        }
    )

    if capcut_done:
        out["stage"] = "done"
    elif cues_total is not None and cues_total > 0 and images_count < cues_total:
        out["stage"] = "generating_images"
    elif cues_total is not None and cues_total > 0 and images_count >= cues_total:
        out["stage"] = "capcut_inserting"
    else:
        out["stage"] = "initializing"

    # Surface the CapCut draft path if available (symlink).
    info_path = run_dir / "capcut_draft_info.json"
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            info = {}
        if isinstance(info, dict):
            ref = info.get("draft_path_ref")
            legacy = str(info.get("draft_path") or "").strip()
            if isinstance(ref, dict):
                out["capcut_draft_ref"] = ref
                resolved = resolve_path_ref(ref)
                if resolved is not None:
                    out["capcut_draft"] = str(resolved)
                else:
                    out["capcut_draft"] = f"{ref.get('root')}:{ref.get('rel')}"
            elif legacy:
                out["capcut_draft"] = legacy
    else:
        draft_link = run_dir / "capcut_draft"
        if draft_link.exists() or draft_link.is_symlink():
            try:
                out["capcut_draft"] = str(draft_link.resolve())
            except Exception:
                out["capcut_draft"] = str(draft_link)

    # Tail the run log to show the current phase/cooldowns.
    srt2images_log = run_dir / "logs" / "srt2images.log"
    if srt2images_log.exists():
        out["srt2images_log_tail"] = _tail_lines(srt2images_log, max_lines=20)
        try:
            out["srt2images_log_mtime"] = float(srt2images_log.stat().st_mtime)
        except Exception:
            pass

    return out


@router.get("/api/ops/batch-monitor/batches")
def list_batches():
    return {"now": _utc_now_iso(), "repo_root": str(REPO_ROOT), "batches": _discover_batches()}


@router.get("/api/ops/batch-monitor/status")
def batch_status(batch_id: Optional[str] = Query(None, description="workspaces/logs/batch/<id>.pid の <id>")):
    batches = _discover_batches()
    if not batches:
        return {
            "now": _utc_now_iso(),
            "running": False,
            "error": f"batch_dir_not_found_or_empty: {BATCH_ROOT}",
            "batches": [],
        }

    selected = None
    if batch_id:
        for b in batches:
            if b.get("id") == batch_id:
                selected = b
                break
    if selected is None:
        selected = batches[0]

    pid_path = Path(str(selected["pid_path"]))
    pid = _read_pid(pid_path) or 0
    alive = bool(pid and _pid_alive(pid))
    etimes = _ps_etimes(pid) if alive else None

    log_path = Path(selected["log_path"]) if selected.get("log_path") else None
    log_tail: List[str] = _tail_lines(log_path, max_lines=200) if log_path and log_path.exists() else []
    current_run, current_kind = _last_run_from_log_tail(log_tail)

    current_run_status = None
    channels: List[str] = []
    progress: Dict[str, Any] = {}
    if current_run:
        ch = _infer_channel_code(current_run)
        if ch:
            channels.append(ch)
        run_dir = VIDEO_RUNS_ROOT / current_run
        current_run_status = _run_status(run_dir)

    # If we can infer channels from log history, add them.
    seen_channels: set[str] = set(channels)
    for line in log_tail:
        m = _RUN_LINE_RE.match(line.strip())
        if not m:
            # Also accept plain "CHxx" tokens in logs (works even when the batch doesn't write [RUN] lines).
            for tok in _CHANNEL_IN_LINE_RE.findall(line):
                t = str(tok or "").strip().upper()
                if t and t not in seen_channels:
                    seen_channels.add(t)
            continue
        rid = m.group(2)
        ch = _infer_channel_code(rid)
        if ch and ch not in seen_channels and ch.startswith("CH") and ch[2:].isdigit():
            seen_channels.add(ch)
    channels = sorted(seen_channels, key=lambda x: (int(x[2:]) if x.startswith("CH") and x[2:].isdigit() else 9999, x))

    # Per-channel progress (done/total, plus "current" marker)
    for ch in channels:
        eps = _list_audio_final_episodes(ch)
        done = 0
        for ep in eps:
            if (_run_dir_for_episode(ch, ep) / "capcut_draft_info.json").exists():
                done += 1
        progress[ch] = {
            "total": len(eps),
            "done": done,
            "pending": max(0, len(eps) - done),
        }
        if current_run and _infer_channel_code(current_run) == ch:
            m = _EP_IN_RUN_ID_RE.match(str(current_run))
            if m:
                progress[ch]["current_episode"] = m.group(1)

    # Detect "stalled" when log isn't updated.
    stalled = False
    log_mtime = None
    if log_path and log_path.exists():
        try:
            log_mtime = float(log_path.stat().st_mtime)
        except Exception:
            log_mtime = None
    if alive and log_mtime is not None:
        stalled = (time.time() - float(log_mtime)) > 90.0

    return {
        "now": _utc_now_iso(),
        "batch": {
            "id": selected["id"],
            "pid": pid,
            "running": alive,
            "elapsed_sec": etimes,
            "pid_path": str(pid_path),
            "log_path": str(log_path) if log_path else None,
            "log_mtime": log_mtime,
            "stalled": stalled,
        },
        "current": {
            "kind": current_kind,
            "run_id": current_run,
            "run": current_run_status,
        },
        "channels": channels,
        "progress": progress,
        "log_tail": log_tail[-80:],
        "batches": batches,
    }


@router.get("/ops/batch-monitor", response_class=HTMLResponse)
def batch_monitor_ui():
    # Self-contained single-page UI (no frontend build required).
    return HTMLResponse(
        content="""
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Batch Monitor</title>
  <style>
    body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 16px; color: #111; }
    .row { display: flex; gap: 12px; flex-wrap: wrap; }
    .card { border: 1px solid #ddd; border-radius: 10px; padding: 12px; background: #fff; min-width: 320px; flex: 1; }
    .k { color: #666; font-size: 12px; }
    .v { font-weight: 600; }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; }
    .ok { background: #e8fff0; color: #0a7a2a; border: 1px solid #b6f3c9; }
    .bad { background: #fff0f0; color: #b11212; border: 1px solid #ffd0d0; }
    .warn { background: #fff8e6; color: #8a5a00; border: 1px solid #ffe1a6; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; white-space: pre-wrap; }
    .progress { height: 10px; background: #eee; border-radius: 999px; overflow: hidden; }
    .bar { height: 100%; background: #2563eb; width: 0%; }
    select, button { padding: 6px 10px; border-radius: 8px; border: 1px solid #ccc; background: #fff; }
    button { cursor: pointer; }
    details > summary { cursor: pointer; }
  </style>
</head>
<body>
  <h2>Batch Monitor</h2>
  <div class="row" style="align-items:center; margin-bottom: 10px;">
    <div class="k">Batch:</div>
    <select id="batchSelect"></select>
    <button id="refreshBtn">更新</button>
    <div class="k">自動更新:</div>
    <select id="intervalSelect">
      <option value="2">2s</option>
      <option value="5" selected>5s</option>
      <option value="10">10s</option>
      <option value="30">30s</option>
      <option value="0">OFF</option>
    </select>
    <div class="k">now:</div>
    <div class="mono" id="now"></div>
  </div>

  <div class="row">
    <div class="card">
      <div class="k">状態</div>
      <div id="state"></div>
      <div class="k" style="margin-top:8px;">現在RUN</div>
      <div class="mono" id="currentRun"></div>
      <div class="k" style="margin-top:8px;">進捗（channels）</div>
      <div id="progress"></div>
    </div>
    <div class="card">
      <div class="k">現在RUNの詳細</div>
      <div class="mono" id="currentDetails"></div>
      <details style="margin-top:10px;">
        <summary>現在RUNのログ（srt2images.log tail）</summary>
        <div class="mono" id="runLog"></div>
      </details>
    </div>
    <div class="card">
      <div class="k">バッチログ tail</div>
      <div class="mono" id="batchLog"></div>
    </div>
  </div>

  <script>
    let timer = null;

    function fmtSec(sec) {
      if (sec === null || sec === undefined) return "-";
      sec = Number(sec);
      if (!Number.isFinite(sec)) return "-";
      const h = Math.floor(sec / 3600);
      const m = Math.floor((sec % 3600) / 60);
      const s = Math.floor(sec % 60);
      return `${h}h${String(m).padStart(2,'0')}m${String(s).padStart(2,'0')}s`;
    }

    function pill(text, cls) {
      return `<span class="pill ${cls}">${text}</span>`;
    }

    async function loadBatches() {
      const res = await fetch('/api/ops/batch-monitor/batches');
      const data = await res.json();
      const sel = document.getElementById('batchSelect');
      sel.innerHTML = '';
      for (const b of (data.batches || [])) {
        const opt = document.createElement('option');
        opt.value = b.id;
        opt.textContent = b.id;
        sel.appendChild(opt);
      }
    }

    async function refresh() {
      const sel = document.getElementById('batchSelect');
      const batchId = sel.value || '';
      const url = batchId ? `/api/ops/batch-monitor/status?batch_id=${encodeURIComponent(batchId)}` : '/api/ops/batch-monitor/status';
      const res = await fetch(url);
      const data = await res.json();

      document.getElementById('now').textContent = data.now || '';

      const b = (data.batch || {});
      const running = !!b.running;
      const stalled = !!b.stalled;
      const stateBits = [];
      stateBits.push(running ? pill('RUNNING', 'ok') : pill('STOPPED', 'bad'));
      if (stalled) stateBits.push(pill('STALED(>90s)', 'warn'));
      stateBits.push(`<span class="k">pid</span> <span class="mono">${b.pid || '-'}</span>`);
      stateBits.push(`<span class="k">elapsed</span> <span class="mono">${fmtSec(b.elapsed_sec)}</span>`);
      stateBits.push(`<div class="k">log</div><div class="mono">${b.log_path || '-'}</div>`);
      document.getElementById('state').innerHTML = stateBits.join(' ');

      const cur = (data.current || {});
      document.getElementById('currentRun').textContent = cur.run_id ? `${cur.kind || ''} ${cur.run_id}` : '-';

      const prog = (data.progress || {});
      const progDiv = document.getElementById('progress');
      progDiv.innerHTML = '';
      for (const [ch, p] of Object.entries(prog)) {
        const total = Number(p.total || 0);
        const done = Number(p.done || 0);
        const pct = total > 0 ? Math.round(done / total * 100) : 0;
        const wrap = document.createElement('div');
        wrap.style.marginBottom = '10px';
        wrap.innerHTML = `
          <div class="row" style="justify-content:space-between;">
            <div><span class="v">${ch}</span> <span class="k">done</span> <span class="mono">${done}/${total}</span></div>
            <div class="mono">${pct}%</div>
          </div>
          <div class="progress"><div class="bar" style="width:${pct}%;"></div></div>
          ${p.current_episode ? `<div class="k">current_episode: <span class="mono">${p.current_episode}</span></div>` : ``}
        `;
        progDiv.appendChild(wrap);
      }

      const run = (cur.run || {});
      const details = [];
      if (run && run.exists) {
        details.push(`run_dir: ${run.run_dir}`);
        details.push(`stage: ${run.stage || '-'}`);
        if (run.cues_total !== null && run.cues_total !== undefined) details.push(`cues: ${run.images_count || 0}/${run.cues_total}`);
        if (run.capcut_done) details.push(`capcut_done: YES`);
        if (run.capcut_draft) details.push(`capcut_draft: ${run.capcut_draft}`);
      } else {
        details.push('-');
      }
      document.getElementById('currentDetails').textContent = details.join('\\n');

      const runLog = (run.srt2images_log_tail || []).join('\\n');
      document.getElementById('runLog').textContent = runLog || '';

      const batchLog = (data.log_tail || []).join('\\n');
      document.getElementById('batchLog').textContent = batchLog || '';
    }

    function resetTimer() {
      if (timer) { clearInterval(timer); timer = null; }
      const sec = Number(document.getElementById('intervalSelect').value || '5');
      if (sec > 0) timer = setInterval(refresh, sec * 1000);
    }

    document.getElementById('refreshBtn').addEventListener('click', refresh);
    document.getElementById('intervalSelect').addEventListener('change', resetTimer);
    document.getElementById('batchSelect').addEventListener('change', refresh);

    (async () => {
      await loadBatches();
      await refresh();
      resetTimer();
    })();
  </script>
</body>
</html>
""".strip(),
    )
