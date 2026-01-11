#!/usr/bin/env python3
"""
Gemini Batch: generate script "A-text" from per-episode prompt files.

Why:
- Fireworks/OpenRouterが使えない期間でも、台本本文（Aテキスト）を止めないための緊急導線。
- サイレントfallbackは禁止（SSOT: ssot/DECISIONS.md D-002 / D-018）。

This tool:
- submit: prompts -> JSONL -> upload -> create batch job -> write manifest.json
- fetch: poll job -> download results -> write workspaces/scripts/{CH}/{NNN}/content/assembled.md

Notes:
- Uses Gemini Developer API Batch via google-genai.
- Does NOT run any other script_pipeline stages (validation/semantic alignment/etc).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from _bootstrap import bootstrap

bootstrap(load_env=False)

try:
    import google.genai as genai  # type: ignore
    import google.genai.types as genai_types  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "google-genai is required for Gemini Batch. Install: pip install google-genai\n"
        f"Import error: {exc}"
    )

from factory_common import paths as repo_paths  # noqa: E402


WORKSPACES = repo_paths.workspace_root()
SCRATCH_ROOT = WORKSPACES / "_scratch" / "gemini_batch_scripts"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _z3(n: int | str) -> str:
    try:
        return str(int(n)).zfill(3)
    except Exception:
        return str(n).zfill(3)


def _parse_indices(expr: str) -> List[int]:
    raw = str(expr or "").strip()
    if not raw:
        return []
    out: List[int] = []
    for token in raw.split(","):
        t = token.strip()
        if not t:
            continue
        if "-" in t:
            a, b = [x.strip() for x in t.split("-", 1)]
            if not a or not b:
                raise SystemExit(f"Invalid --videos range: {t!r}")
            lo = int(a)
            hi = int(b)
            if hi < lo:
                lo, hi = hi, lo
            out.extend(list(range(lo, hi + 1)))
        else:
            out.append(int(t))
    return sorted(set([i for i in out if i > 0]))


def _parse_videos(expr: str) -> List[str]:
    ids = _parse_indices(expr)
    return [_z3(i) for i in ids]


def _resolve_api_key() -> str:
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise SystemExit(
            "GEMINI_API_KEY is not set.\n"
            "- Recommended: run via ./scripts/with_ytm_env.sh ...\n"
            "- Or export GEMINI_API_KEY in your shell."
        )
    return key


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    _ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _prompt_path(channel: str, video: str) -> Path:
    ch = str(channel).strip().upper()
    vv = str(video).strip()
    return repo_paths.repo_root() / "prompts" / "antigravity_gemini" / ch / f"{ch}_{vv}_FULL_PROMPT.md"


def _output_script_path(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "content" / "assembled.md"


@dataclass(frozen=True)
class ManifestItem:
    id: str
    channel: str
    video: str
    prompt_path: str
    output_path: str
    prompt_sha256: str


def _load_manifest_items(manifest: Dict[str, Any]) -> List[ManifestItem]:
    items = manifest.get("items") or []
    if not isinstance(items, list):
        raise SystemExit("Invalid manifest: items must be a list")
    out: List[ManifestItem] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(
            ManifestItem(
                id=str(item.get("id") or "").strip(),
                channel=str(item.get("channel") or "").strip(),
                video=str(item.get("video") or "").strip(),
                prompt_path=str(item.get("prompt_path") or "").strip(),
                output_path=str(item.get("output_path") or "").strip(),
                prompt_sha256=str(item.get("prompt_sha256") or "").strip(),
            )
        )
    return [x for x in out if x.id and x.channel and x.video and x.output_path and x.prompt_path]


def _extract_text_from_response_dict(resp: Dict[str, Any]) -> str:
    """
    Best-effort extraction of generated text from a Gemini generateContent response dict.
    """
    # Common shape: {"candidates":[{"content":{"parts":[{"text":"..."}]}}]}
    cands = resp.get("candidates")
    if isinstance(cands, list) and cands:
        cand0 = cands[0] if isinstance(cands[0], dict) else {}
        content = cand0.get("content") if isinstance(cand0, dict) else None
        if isinstance(content, dict):
            parts = content.get("parts")
            if isinstance(parts, list) and parts:
                texts: List[str] = []
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    t = p.get("text")
                    if isinstance(t, str) and t.strip():
                        texts.append(t)
                if texts:
                    return "\n".join(texts).strip()
    # Fallback: try common keys
    for key in ("text", "output_text", "content"):
        v = resp.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def submit_job(*, channel: str, videos: List[str], model: str, out_dir: Path, dry_run: bool) -> Path:
    ch = str(channel).strip().upper()
    if not re.fullmatch(r"CH\d{2}", ch):
        raise SystemExit(f"Invalid --channel: {channel!r} (expected CHxx)")

    _ensure_dir(out_dir)
    input_jsonl = out_dir / "batch_input.jsonl"

    items: List[ManifestItem] = []
    lines: List[str] = []
    for video in videos:
        script_id = f"{ch}-{video}"
        pp = _prompt_path(ch, video)
        if not pp.exists():
            raise SystemExit(f"Prompt not found: {pp} ({script_id})")
        prompt = _read_text(pp)
        prompt_hash = _sha256(prompt)

        line = {
            "request": {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt}],
                    }
                ]
            },
            "metadata": {"id": script_id},
        }
        lines.append(json.dumps(line, ensure_ascii=False))
        out_path = _output_script_path(ch, video)
        items.append(
            ManifestItem(
                id=script_id,
                channel=ch,
                video=video,
                prompt_path=str(pp),
                output_path=str(out_path),
                prompt_sha256=prompt_hash,
            )
        )

    if not items:
        raise SystemExit("No items selected")

    input_jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")

    uploaded_name: Optional[str] = None
    job_name: str = ""
    job_state: str = "DRY_RUN" if dry_run else ""

    if not dry_run:
        api_key = _resolve_api_key()
        client = genai.Client(api_key=api_key)
        uploaded = client.files.upload(
            file=str(input_jsonl),
            config=genai_types.UploadFileConfig(mime_type="application/json"),
        )
        uploaded_name = str(uploaded.name)
        job = client.batches.create(model=model, src=str(uploaded.name))
        job_name = str(getattr(job, "name", ""))
        job_state = str(getattr(job, "state", ""))

    manifest_path = out_dir / "manifest.json"
    _write_json(
        manifest_path,
        {
            "schema": "ytm.gemini_batch_scripts.v1",
            "created_at": _utc_now_iso(),
            "channel": ch,
            "model": model,
            "input": {
                "path": str(input_jsonl),
                "uploaded_file": uploaded_name,
                "count": len(items),
            },
            "job": {
                "name": job_name,
                "state": job_state,
            },
            "items": [
                {
                    "id": it.id,
                    "channel": it.channel,
                    "video": it.video,
                    "prompt_path": it.prompt_path,
                    "output_path": it.output_path,
                    "prompt_sha256": it.prompt_sha256,
                }
                for it in items
            ],
        },
    )

    if dry_run:
        print(f"[DRY] wrote: {input_jsonl}")
        print(f"[DRY] wrote: {manifest_path}")
        print(f"  - model: {model}")
        print(f"  - items: {len(items)}")
    else:
        print(f"✅ submitted batch job: {job_name}")
        print(f"  - model: {model}")
        print(f"  - items: {len(items)}")
        print(f"  - manifest: {manifest_path}")
    return manifest_path


def status_job(*, manifest_path: Path) -> None:
    manifest_path = manifest_path.expanduser().resolve()
    if not manifest_path.exists():
        raise SystemExit(f"manifest not found: {manifest_path}")
    manifest = _read_json(manifest_path)
    job_name = str(((manifest.get("job") or {}) if isinstance(manifest.get("job"), dict) else {}).get("name") or "").strip()
    if not job_name:
        raise SystemExit("Invalid manifest: job.name missing")

    api_key = _resolve_api_key()
    client = genai.Client(api_key=api_key)
    job = client.batches.get(name=job_name)
    state = str(getattr(job, "state", "") or "")
    dest = getattr(job, "dest", None)
    file_name = str(getattr(dest, "file_name", "") or "") if dest is not None else ""
    print(f"[JOB] {job_name} state={state}")
    if file_name:
        print(f"  - dest.file_name: {file_name}")


def fetch_job(*, manifest_path: Path, write: bool, backup_existing: bool) -> None:
    manifest_path = manifest_path.expanduser().resolve()
    if not manifest_path.exists():
        raise SystemExit(f"manifest not found: {manifest_path}")
    manifest = _read_json(manifest_path)
    job_name = str(((manifest.get("job") or {}) if isinstance(manifest.get("job"), dict) else {}).get("name") or "").strip()
    if not job_name:
        raise SystemExit("Invalid manifest: job.name missing")

    items = _load_manifest_items(manifest)
    if not items:
        raise SystemExit("Invalid manifest: no items")

    api_key = _resolve_api_key()
    client = genai.Client(api_key=api_key)

    job = client.batches.get(name=job_name)
    state = str(getattr(job, "state", "") or "")
    print(f"[JOB] {job_name} state={state}")

    if "SUCCEEDED" not in state and "JOB_STATE_SUCCEEDED" not in state:
        raise SystemExit("Batch job not finished yet (rerun fetch later).")

    id_to_item = {it.id: it for it in items}

    errors: List[str] = []
    written = 0

    def _maybe_backup(path: Path) -> Optional[Path]:
        if not backup_existing:
            return None
        try:
            if not path.exists() or not path.is_file():
                return None
        except Exception:
            return None
        bdir = path.parent / f"_backup_{_utc_stamp()}"
        _ensure_dir(bdir)
        try:
            dst = bdir / path.name
            if not dst.exists():
                path.rename(dst)
        except Exception:
            pass
        return bdir

    def _write_text_file(it: ManifestItem, text: str) -> None:
        out_path = Path(it.output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _maybe_backup(out_path)
        out_path.write_text(text.rstrip() + "\n", encoding="utf-8")

    dest = getattr(job, "dest", None)
    if dest is None:
        raise SystemExit("Batch job has no destination")

    inlined = getattr(dest, "inlined_responses", None)
    if isinstance(inlined, list) and inlined:
        # Inline responses: order matches input request order; metadata may be absent.
        if len(inlined) != len(items):
            print(f"[WARN] inlined_responses count mismatch: dest={len(inlined)} items={len(items)}")
        for i, it in enumerate(items):
            if i >= len(inlined):
                break
            resp_obj = inlined[i]
            err = getattr(resp_obj, "error", None)
            if err:
                errors.append(f"{it.id}: {err}")
                continue
            resp = getattr(resp_obj, "response", None)
            try:
                resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else {}
            except Exception:
                resp_dict = {}
            text = _extract_text_from_response_dict(resp_dict if isinstance(resp_dict, dict) else {})
            if not text:
                errors.append(f"{it.id}: empty response text")
                continue
            if write:
                _write_text_file(it, text)
            written += 1

        if errors:
            raise SystemExit(f"Completed with errors: {len(errors)} (see logs above)")
        msg = "[DRY]" if not write else "[DONE]"
        print(f"{msg} scripts={written}")
        return

    file_name = getattr(dest, "file_name", None)
    if not (isinstance(file_name, str) and file_name.strip()):
        raise SystemExit("No results found in job destination")

    raw_name = str(file_name).strip()
    name = raw_name.split("files/", 1)[1] if raw_name.startswith("files/") else raw_name
    url = f"https://generativelanguage.googleapis.com/v1beta/files/{name}:download"
    headers = {"x-goog-api-key": api_key}
    params = {"alt": "media"}

    with requests.get(url, headers=headers, params=params, stream=True, timeout=600) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            obj = json.loads(line)
            meta = obj.get("metadata") or {}
            if not isinstance(meta, dict):
                continue
            rid = str(meta.get("id") or "").strip()
            if not rid:
                continue
            it = id_to_item.get(rid)
            if it is None:
                continue

            if obj.get("error"):
                errors.append(f"{rid}: {obj.get('error')}")
                continue

            resp = obj.get("response") or {}
            if not isinstance(resp, dict):
                errors.append(f"{rid}: missing response")
                continue
            text = _extract_text_from_response_dict(resp)
            if not text:
                errors.append(f"{rid}: empty response text")
                continue
            if write:
                _write_text_file(it, text)
            written += 1

    if not write:
        print(f"[DRY] scripts={written} errors={len(errors)}")
        return

    if errors:
        print(f"[ERRORS] count={len(errors)} (showing up to 20)")
        for e in errors[:20]:
            print(f"  - {e}")
        raise SystemExit(f"Completed with errors: {len(errors)}")
    print(f"[DONE] scripts_written={written}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Gemini Batch: generate scripts from per-episode prompt files.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("submit", help="Create a batch job and write a manifest")
    sp.add_argument("--channel", required=True, help="Channel id (e.g., CH01)")
    sp.add_argument("--videos", required=True, help="Video ids/ranges (e.g., 251-290 or 001,002,010)")
    sp.add_argument("--model", default="gemini-3-flash-preview", help="Batch model name (Gemini generateContent model)")
    sp.add_argument("--dry-run", action="store_true", help="Write JSONL+manifest only (no upload / no job create)")
    sp.add_argument(
        "--out-dir",
        help="Output directory for JSONL+manifest (default: workspaces/_scratch/gemini_batch_scripts/<stamp>/)",
    )

    st = sub.add_parser("status", help="Print batch job state (no download)")
    st.add_argument("--manifest", required=True, help="Path to manifest.json from submit")

    fe = sub.add_parser("fetch", help="Download results and (optionally) write assembled.md")
    fe.add_argument("--manifest", required=True, help="Path to manifest.json from submit")
    fe.add_argument("--write", action="store_true", help="Write assembled.md for each item (default: dry-run)")
    fe.add_argument("--backup-existing", action="store_true", help="Backup existing assembled.md before overwrite")

    args = ap.parse_args()

    if args.cmd == "submit":
        out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else (SCRATCH_ROOT / _utc_stamp())
        submit_job(
            channel=str(args.channel),
            videos=_parse_videos(str(args.videos)),
            model=str(args.model),
            out_dir=out_dir,
            dry_run=bool(args.dry_run),
        )
        return 0

    if args.cmd == "status":
        status_job(manifest_path=Path(args.manifest))
        return 0

    if args.cmd == "fetch":
        fetch_job(
            manifest_path=Path(args.manifest),
            write=bool(args.write),
            backup_existing=bool(args.backup_existing),
        )
        return 0

    raise SystemExit(f"Unknown cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
