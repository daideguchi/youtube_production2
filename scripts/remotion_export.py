#!/usr/bin/env python3
"""
Remotion export helper (UI job runner).

This script is meant to be called from the UI backend job system to:
  1) Render a run_dir into an mp4 using the shared Remotion project (apps/remotion)
  2) Upload the mp4 to Google Drive (OAuth) and persist the webViewLink back into the run_dir

Why this exists:
  - commentary_02 run_dir does not contain audio wav by default
  - Remotion scaffolding in srt2images is intentionally lightweight
  - UI needs a single command with logs + deterministic output locations
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from factory_common.paths import audio_final_dir
from typing import Any, Dict, Optional, Tuple

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _drive_libs():
    """
    Lazy import so the repo can run without Drive deps unless this feature is used.
    """
    try:
        from google.oauth2.credentials import Credentials  # type: ignore
        from google.auth.exceptions import RefreshError  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        from googleapiclient.http import MediaFileUpload  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "❌ Google Drive libraries are missing.\n"
            "Install:\n"
            "  pip3 install google-api-python-client google-auth google-auth-oauthlib\n"
            f"detail: {exc}"
        )
    return Credentials, RefreshError, build, MediaFileUpload


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_repo_path(value: str) -> Path:
    p = Path(value).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (_repo_root() / p).resolve()


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _infer_episode(run_dir: Path, explicit_channel: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Try to infer (CHxx, NNN) from run_dir name / filenames / metadata.
    """
    if explicit_channel:
        explicit_channel = explicit_channel.strip().upper()[:4]

    patterns = [run_dir.name]
    for srt in sorted(run_dir.glob("*.srt")):
        patterns.append(srt.stem)
    for json_name in ("auto_run_info.json", "capcut_draft_info.json"):
        meta = _read_json(run_dir / json_name) or {}
        for key in ("channel", "channel_id", "project_id", "srt", "run_dir"):
            value = meta.get(key)
            if isinstance(value, str) and value:
                patterns.append(value)

    inferred_channel: Optional[str] = None
    inferred_video: Optional[str] = None

    for text in patterns:
        m = re.search(r"(CH\d{2})[-_ ]?(\d{3})", text, flags=re.IGNORECASE)
        if m:
            inferred_channel = m.group(1).upper()
            inferred_video = m.group(2)
            break

    if inferred_video is None:
        for text in patterns:
            m = re.search(r"(?:(?<=-)|(?<=_)|^)(\d{3})(?=$|[^0-9])", text)
            if m:
                inferred_video = m.group(1)
                break

    if inferred_channel is None:
        for text in patterns:
            m = re.search(r"(CH\d{2})", text, flags=re.IGNORECASE)
            if m:
                inferred_channel = m.group(1).upper()
                break

    channel = explicit_channel or inferred_channel
    video = inferred_video
    return channel, video


def _find_srt(run_dir: Path, explicit: Optional[str]) -> Path:
    if explicit:
        srt = _resolve_repo_path(explicit)
        if not srt.exists():
            raise FileNotFoundError(f"SRT not found: {srt}")
        return srt
    candidates = sorted(run_dir.glob("*.srt"))
    if not candidates:
        raise FileNotFoundError(f"SRT not found in run_dir: {run_dir}")
    return candidates[0]


def _resolve_audio_wav(run_dir: Path, channel: Optional[str], video: Optional[str], explicit: Optional[str]) -> Path:
    if explicit:
        wav = _resolve_repo_path(explicit)
        if not wav.exists():
            raise FileNotFoundError(f"audio wav not found: {wav}")
        return wav

    # Try local audio next (some runs keep the voice track in run_dir).
    local_audio: list[Path] = []
    for ext in (".wav", ".mp3", ".m4a", ".flac"):
        local_audio.extend(sorted(run_dir.glob(f"*{ext}")))
    local_audio = [p for p in local_audio if p.is_file()]
    if local_audio:
        # Prefer matching the run_dir name or any SRT stem, otherwise take newest.
        preferred_stems = {run_dir.name}
        preferred_stems.update({p.stem for p in run_dir.glob("*.srt")})
        for cand in local_audio:
            if cand.stem in preferred_stems:
                return cand
        local_audio.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return local_audio[0]

    manifest = _read_json(run_dir / "timeline_manifest.json")
    if manifest:
        rel = (((manifest.get("source") or {}).get("audio_wav") or {}).get("path")) or ""
        if isinstance(rel, str) and rel:
            wav = _resolve_repo_path(rel)
            if wav.exists():
                return wav

    auto_info = _read_json(run_dir / "auto_run_info.json")
    if auto_info:
        for key in ("audio_wav_effective", "voice_file", "voice_wav", "audio_wav"):
            value = auto_info.get(key)
            if isinstance(value, str) and value:
                wav = _resolve_repo_path(value)
                if wav.exists():
                    return wav

    if channel and video:
        ch = channel.strip().upper()
        no = str(video).zfill(3)
        wav = audio_final_dir(ch, no) / f"{ch}-{no}.wav"
        if wav.exists():
            return wav

    raise FileNotFoundError("audio wav could not be resolved (provide --audio)")


def _default_mp4_path(run_dir: Path) -> Path:
    return (run_dir / "remotion" / "output" / "final.mp4").resolve()


def _find_mp4_for_upload(run_dir: Path, explicit: Optional[str]) -> Path:
    if explicit:
        p = _resolve_repo_path(explicit)
        if not p.exists():
            raise FileNotFoundError(f"mp4 not found: {p}")
        return p

    preferred = _default_mp4_path(run_dir)
    if preferred.exists():
        return preferred

    candidates = []
    for glob_pat in (
        "remotion/output/*.mp4",
        "remotion/*.mp4",
        "*.mp4",
    ):
        candidates.extend(run_dir.glob(glob_pat))
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        raise FileNotFoundError(f"mp4 not found under run_dir: {run_dir}")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _stream_subprocess(cmd: list[str], *, cwd: Path, env: Dict[str, str]) -> int:
    print(f"▶ {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip("\n"), flush=True)
    return proc.wait()


def _load_drive_credentials(token_path: Path):
    Credentials, _RefreshError, _build, _MediaFileUpload = _drive_libs()
    if not token_path.exists():
        raise FileNotFoundError(
            f"OAuth token not found at {token_path}\n"
            "先に scripts/drive_oauth_setup.py を実行してトークンを取得してください。"
        )
    return Credentials.from_authorized_user_file(str(token_path), SCOPES)


def _drive_find_or_create_folder(service, parent_id: str, name: str) -> str:
    safe = name.replace("'", "\\'")
    q = (
        "mimeType='application/vnd.google-apps.folder' "
        f"and name='{safe}' and '{parent_id}' in parents and trashed=false"
    )
    res = service.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=10).execute()
    files = res.get("files", []) or []
    if files:
        return files[0]["id"]
    created = (
        service.files()
        .create(
            body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
            fields="id",
        )
        .execute()
    )
    return created["id"]


def _resolve_drive_final_folder_id(service, *, base_folder_id: str, channel: Optional[str]) -> Tuple[str, str]:
    """
    Resolve a good destination folder:
      <DRIVE_FOLDER_ID>/uploads/final/<CHxx>
    """
    override = os.environ.get("DRIVE_UPLOADS_FINAL_FOLDER_ID")
    if override:
        path_label = override
        return override, path_label

    uploads_id = _drive_find_or_create_folder(service, base_folder_id, "uploads")
    final_id = _drive_find_or_create_folder(service, uploads_id, "final")
    folder_id = final_id
    folder_path = "uploads/final"
    if channel:
        ch = channel.strip().upper()[:4]
        folder_id = _drive_find_or_create_folder(service, final_id, ch)
        folder_path = f"uploads/final/{ch}"
    return folder_id, folder_path


def _drive_upload_file(
    *,
    token_path: Path,
    local_path: Path,
    folder_id: str,
    dest_name: str,
) -> Dict[str, Any]:
    Credentials, _RefreshError, build, MediaFileUpload = _drive_libs()
    creds = _load_drive_credentials(token_path)
    service = build("drive", "v3", credentials=creds)

    if not local_path.exists():
        raise FileNotFoundError(f"file not found: {local_path}")

    mime = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    if local_path.suffix.lower() == ".mp4":
        mime = "video/mp4"
    media = MediaFileUpload(str(local_path), mimetype=mime, resumable=True)
    body = {"name": dest_name, "parents": [folder_id]}
    return (
        service.files()
        .create(body=body, media_body=media, fields="id,name,webViewLink,parents")
        .execute()
    )


def _write_drive_artifacts(run_dir: Path, *, payload: Dict[str, Any]) -> Path:
    remotion_dir = (run_dir / "remotion").resolve()
    remotion_dir.mkdir(parents=True, exist_ok=True)
    out_path = remotion_dir / "drive_upload.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    url = (((payload.get("drive") or {}).get("webViewLink")) or "").strip()
    if url:
        (remotion_dir / "drive_url.txt").write_text(url + "\n", encoding="utf-8")
    return out_path


def cmd_render(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.exists():
        raise FileNotFoundError(f"run_dir not found: {run_dir}")

    channel, video = _infer_episode(run_dir, args.channel)
    srt_path = _find_srt(run_dir, args.srt)
    audio_wav = _resolve_audio_wav(run_dir, channel, video, args.audio)

    out_mp4 = Path(args.out).expanduser() if args.out else _default_mp4_path(run_dir)
    if not out_mp4.is_absolute():
        out_mp4 = (run_dir / out_mp4).resolve()
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    repo = _repo_root()
    node_script = (repo / "remotion" / "scripts" / "render.js").resolve()
    if not node_script.exists():
        raise FileNotFoundError(f"remotion render script not found: {node_script}")

    cmd = [
        "node",
        str(node_script),
        "--run",
        str(run_dir),
        "--srt",
        str(srt_path),
        "--bgm",
        str(audio_wav),
        "--out",
        str(out_mp4),
        "--fps",
        str(args.fps),
        "--size",
        str(args.size),
        "--crossfade",
        str(args.crossfade),
    ]
    if channel:
        cmd += ["--channel", channel]
    if args.title:
        cmd += ["--title", args.title]

    env = os.environ.copy()
    rc = _stream_subprocess(cmd, cwd=repo, env=env)
    if rc != 0:
        print(f"❌ Remotion render failed (exit={rc})", file=sys.stderr)
        return rc

    print(f"✅ Rendered mp4: {out_mp4}", flush=True)
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    _Credentials, RefreshError, build, _MediaFileUpload = _drive_libs()
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.exists():
        raise FileNotFoundError(f"run_dir not found: {run_dir}")

    channel, _video = _infer_episode(run_dir, args.channel)
    mp4_path = _find_mp4_for_upload(run_dir, args.file)
    dest_name = args.name or f"{run_dir.name}.mp4"

    base_folder_id = args.folder or os.environ.get("DRIVE_FOLDER_ID")
    if not base_folder_id:
        raise SystemExit("Drive folder id missing. Set DRIVE_FOLDER_ID or pass --folder.")
    token_path = Path(
        os.environ.get("DRIVE_OAUTH_TOKEN_PATH", _repo_root() / "credentials" / "drive_oauth_token.json")
    ).expanduser()

    try:
        creds = _load_drive_credentials(token_path)
        service = build("drive", "v3", credentials=creds)
        final_folder_id, folder_path = _resolve_drive_final_folder_id(
            service,
            base_folder_id=base_folder_id,
            channel=channel,
        )

        sha1 = _sha1_file(mp4_path)
        result = _drive_upload_file(
            token_path=token_path,
            local_path=mp4_path,
            folder_id=final_folder_id,
            dest_name=dest_name,
        )
    except RefreshError as exc:
        print(
            "❌ Google Drive OAuth トークンが無効です (invalid_grant).\n"
            "次を実行してブラウザで再認可してください:\n"
            "  python3 scripts/drive_oauth_setup.py\n"
            f"token: {token_path}\n"
            f"detail: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 2

    payload = {
        "engine": "remotion",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "local": {
            "path": str(mp4_path),
            "sha1": sha1,
            "size_bytes": mp4_path.stat().st_size,
        },
        "destination": {
            "folder_id": final_folder_id,
            "folder_path": folder_path,
            "drive_root_folder_id": base_folder_id,
        },
        "drive": result,
    }
    out_path = _write_drive_artifacts(run_dir, payload=payload)

    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"✅ Drive upload saved: {out_path}", flush=True)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Remotion render/export helper (UI jobs).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser("render", help="Render run_dir to mp4 (no upload)")
    p_render.add_argument("--run-dir", required=True, help="commentary_02 output run_dir")
    p_render.add_argument("--channel", help="CHxx (preset/layout lookup)")
    p_render.add_argument("--title", help="Optional title override")
    p_render.add_argument("--srt", help="Optional SRT path (defaults to first *.srt in run_dir)")
    p_render.add_argument("--audio", help="Optional audio wav path (defaults to timeline_manifest/auto_run_info/final)")
    p_render.add_argument("--fps", type=int, default=30)
    p_render.add_argument("--size", default="1920x1080")
    p_render.add_argument("--crossfade", type=float, default=0.5)
    p_render.add_argument("--out", help="Output mp4 path (default: <run_dir>/remotion/output/final.mp4)")
    p_render.set_defaults(func=cmd_render)

    p_upload = sub.add_parser("upload", help="Upload an existing mp4 to Drive and persist URL back to run_dir")
    p_upload.add_argument("--run-dir", required=True, help="commentary_02 output run_dir")
    p_upload.add_argument("--channel", help="CHxx (used for Drive folder path)")
    p_upload.add_argument("--file", help="mp4 path (default: <run_dir>/remotion/output/final.mp4 or latest mp4 under run_dir)")
    p_upload.add_argument("--name", help="Drive file name (default: <run_dir.name>.mp4)")
    p_upload.add_argument("--folder", help="Drive folder id (default: env DRIVE_FOLDER_ID)")
    p_upload.set_defaults(func=cmd_upload)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
