from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path
from typing import Literal

def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    raise RuntimeError(
        "repo root not found (pyproject.toml). Run from inside the repo or set PYTHONPATH/ YTM_REPO_ROOT."
    )

try:
    from _bootstrap import bootstrap
except ModuleNotFoundError:
    # Allow running as a script without PYTHONPATH:
    #   python3 packages/audio_tts/scripts/run_tts.py ...
    _repo_root = _discover_repo_root(Path(__file__).resolve())
    for p in (_repo_root, _repo_root / "packages"):
        if not p.exists():
            continue
        p_str = str(p)
        if p_str not in sys.path:
            sys.path.insert(0, p_str)
    from _bootstrap import bootstrap


REPO_ROOT = bootstrap(load_env=True)

# requests is optional; fall back to urllib when not installed.
try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

from audio_tts.tts.routing import decide_engine, load_routing_config


def _utc_now_compact() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _ensure_a_text_mirror_consistency(*, content_dir: Path, channel: str, video: str) -> None:
    """
    A-text SoT:
      - assembled_human.md (if exists)
      - else assembled.md

    assembled.md is treated as a mirror for backward-compat.
    If both exist and differ:
      - human newer  -> auto-sync mirror (safe)
      - assembled newer (or equal mtime) -> STOP (explicit resolution required)
    """
    human_path = content_dir / "assembled_human.md"
    assembled_path = content_dir / "assembled.md"
    if not human_path.exists():
        return

    if not assembled_path.exists():
        assembled_path.parent.mkdir(parents=True, exist_ok=True)
        assembled_path.write_text(human_path.read_text(encoding="utf-8"), encoding="utf-8")
        print("[SYNC] assembled.md created from assembled_human.md (mirror materialized)")
        return

    try:
        if _sha1_file(human_path) == _sha1_file(assembled_path):
            return
    except Exception as exc:
        raise SystemExit(f"[ERROR] Failed to compare A-text mirror files: {exc}") from exc

    try:
        human_mtime = human_path.stat().st_mtime
        assembled_mtime = assembled_path.stat().st_mtime
    except OSError as exc:
        raise SystemExit(f"[ERROR] Failed to stat A-text files: {exc}") from exc

    if human_mtime > assembled_mtime:
        backup = assembled_path.with_suffix(f".md.bak.{_utc_now_compact()}")
        try:
            backup.write_text(assembled_path.read_text(encoding="utf-8"), encoding="utf-8")
            assembled_path.write_text(human_path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as exc:
            raise SystemExit(f"[ERROR] Failed to sync assembled_human.md -> assembled.md: {exc}") from exc
        print(f"[SYNC] assembled_human.md -> assembled.md (backup: {backup.name})")
        return

    raise SystemExit(
        "\n".join(
            [
                "[CONFLICT] A-text split-brain detected (STOP).",
                f"- episode: {channel}-{video}",
                f"- SoT: {human_path}",
                f"- mirror: {assembled_path}",
                "",
                "assembled.md is newer than assembled_human.md (or same mtime) AND content differs.",
                "Do NOT proceed to TTS until you explicitly resolve which one is canonical.",
                "",
                "Resolve (choose one):",
                f"  python3 scripts/episode_ssot.py confirm-a --channel {channel} --video {video} --prefer human",
                f"  python3 scripts/episode_ssot.py confirm-a --channel {channel} --video {video} --prefer assembled",
            ]
        )
    )


def _resolve_input_mode_and_path(
    *,
    repo_root_path: Path,
    video_dir: Path,
    final_dir: Path,
    provided: Path,
) -> tuple[Literal["a_text", "b_text"], Path]:
    p = provided
    if not p.is_absolute():
        p = (repo_root_path / p).resolve(strict=False)
    else:
        p = p.resolve(strict=False)

    content_dir = video_dir / "content"
    audio_prep_dir = video_dir / "audio_prep"

    a_human = (content_dir / "assembled_human.md").resolve(strict=False)
    a_auto = (content_dir / "assembled.md").resolve(strict=False)
    b_prep = (audio_prep_dir / "script_sanitized.txt").resolve(strict=False)
    b_final = (final_dir / "a_text.txt").resolve(strict=False)

    if p == a_human or p == a_auto:
        return "a_text", p
    if p == b_prep or p == b_final:
        return "b_text", p

    raise SystemExit(
        "\n".join(
            [
                f"[ERROR] Unsupported --input path: {p}",
                "Expected one of:",
                f"  - {a_human}",
                f"  - {a_auto}",
                f"  - {b_prep} (explicit B-text regeneration)",
                f"  - {b_final} (explicit B-text snapshot regeneration)",
            ]
        )
    )


def _sanitize_for_b_compare(text: str) -> str:
    candidate = _normalize_newlines(text)
    try:
        from factory_common.text_sanitizer import strip_meta_from_script

        return _normalize_newlines(strip_meta_from_script(candidate).text)
    except Exception:
        return candidate


def _enforce_b_text_not_stale(*, channel: str, video: str, a_path: Path, b_path: Path) -> None:
    """
    B-text regen safety:
    - If B equals sanitize(A): OK.
    - Else if B is newer than A: OK (treat as explicit override; user edited B after A).
    - Else: STOP (likely stale B after A edits).
    """
    if not a_path.exists():
        raise SystemExit(f"[ERROR] A-text not found for B-text regeneration: {a_path}")
    if not b_path.exists():
        raise SystemExit(f"[ERROR] B-text not found for B-text regeneration: {b_path}")
    try:
        a_text = a_path.read_text(encoding="utf-8")
        b_text = b_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise SystemExit(f"[ERROR] Failed to read A/B text for freshness check: {exc}") from exc

    a_expected_b = _sanitize_for_b_compare(a_text)
    b_norm = _normalize_newlines(b_text)

    if b_norm == a_expected_b:
        return

    try:
        a_mtime = a_path.stat().st_mtime
        b_mtime = b_path.stat().st_mtime
    except OSError as exc:
        raise SystemExit(f"[ERROR] Failed to stat A/B text for freshness check: {exc}") from exc

    if b_mtime >= a_mtime:
        print(
            f"[WARN] Using explicit B-text override (B differs from sanitize(A)): {channel}-{video}\n"
            f"       A: {a_path}\n"
            f"       B: {b_path}"
        )
        return

    raise SystemExit(
        "\n".join(
            [
                "[STALE] B-text appears stale relative to current A-text (STOP).",
                f"- episode: {channel}-{video}",
                f"- A (SoT): {a_path}",
                f"- B (input): {b_path}",
                "",
                "B differs from sanitize(A) AND B is older than A.",
                "This is unsafe (likely: A was edited but B was not regenerated).",
                "",
                "Fix:",
                f"  python3 -m audio_tts.scripts.run_tts --channel {channel} --video {video} --input {a_path}",
            ]
        )
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run audio_tts STRICT pipeline")
    p.add_argument("--channel", required=True, help="Channel ID (e.g. CH05)")
    p.add_argument("--video", required=True, help="Video ID (e.g. 001)")
    p.add_argument(
        "--input",
        required=True,
        type=Path,
        help=(
            "Input path (explicit). "
            "Allowed: content/assembled_human.md or content/assembled.md (A-text), "
            "audio_prep/script_sanitized.txt or audio/final/*/a_text.txt (explicit B-text)."
        ),
    )
    
    # Optional overrides
    p.add_argument("--out-wav", type=Path, help="Output WAV path override")
    p.add_argument("--log", type=Path, help="Output Log path override")
    p.add_argument(
        "--engine-override",
        choices=["voicevox", "voicepeak", "elevenlabs"],
        help="Force specific engine",
    )
    p.add_argument(
        "--reading-source",
        choices=["mecab", "voicevox"],
        help="Reading source override (currently informational for STRICT pipeline; used by legacy kana selection).",
    )
    
    # LLM Settings (Now managed via .env and Router)
    p.add_argument("--llm-model", help="Force LLM router model key(s) for this run (comma-separated).")
    p.add_argument(
        "--llm-slot",
        type=int,
        help="Force numeric LLM routing slot (sets LLM_MODEL_SLOT). Preferred over --llm-model.",
    )
    p.add_argument(
        "--llm-task-model",
        action="append",
        help="Per-task LLM override: TASK=MODELKEY[,MODELKEY...] (repeatable).",
    )
    p.add_argument("--llm-api-key", help="[Deprecated] LLM API Key (Ignored, uses Router)")
    p.add_argument("--llm-timeout", type=int, default=120)
    p.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="Allow TTS even when script_validation is not completed (not recommended).",
    )

    p.add_argument(
        "--finalize-existing",
        action="store_true",
        help=(
            "Do not synthesize. Instead, validate existing final wav/srt "
            "and write/update a_text.txt, log.json, audio_manifest.json."
        ),
    )
    p.add_argument(
        "--force-overwrite-final",
        action="store_true",
        help="Allow overwriting existing manually-finalized audio in workspaces/audio/final (danger).",
    )

    # Voicepeak specific (Optional, maybe move to config later)
    p.add_argument("--voicepeak-narrator")
    p.add_argument("--voicepeak-speed", type=int, help="Voicepeak speed (50-200)")
    p.add_argument("--voicepeak-pitch", type=int, help="Voicepeak pitch (-300 to 300)")
    p.add_argument("--voicepeak-emotion", type=str, help="Voicepeak emotion (happy,sad,angry,fun)")
    
    # Partial Regeneration
    p.add_argument("--indices", type=str, help="Comma-separated segment indices to regenerate (0-based). Example: '3,10'")
    p.add_argument("--resume", action="store_true", help="Resume from existing chunks (skip generation if chunk exists)")
    # Prepass (reading only, no synthesis)
    p.add_argument("--prepass", action="store_true", help="Reading-only pass (no wav synthesis). Generates log.json with readings.")

    return p.parse_args()


def _write_contract_audio_manifest(
    *,
    channel: str,
    video: str,
    final_dir: Path,
    final_wav: Path,
    final_srt: Path,
    final_log: Path,
    notes: str = "",
) -> None:
    """
    Write `workspaces/audio/final/<CH>/<NNN>/audio_manifest.json` (schema: ytm.audio_manifest.v1).
    This must never change the synthesis result; it only records pointers/checksums.
    """
    from factory_common.artifacts.utils import atomic_write_json, utc_now_iso
    from factory_common.paths import repo_root as ssot_repo_root
    from factory_common.timeline_manifest import (
        sha1_file,
        wav_duration_seconds,
        srt_end_seconds,
        srt_entry_count,
    )

    repo_root_path = ssot_repo_root()

    def _safe_relpath(path: Path) -> str:
        try:
            return str(path.resolve().relative_to(repo_root_path.resolve()))
        except Exception:
            return str(path)

    a_text_path = final_dir / "a_text.txt"
    manifest: dict[str, object] = {
        "schema": "ytm.audio_manifest.v1",
        "generated_at": utc_now_iso(),
        "repo_root": str(repo_root_path),
        "episode": {
            "id": f"{channel}-{video}",
            "channel": channel,
            "video": video,
        },
        "final_dir": _safe_relpath(final_dir),
        "source": {
            "a_text": {
                "path": _safe_relpath(a_text_path),
                "sha1": sha1_file(a_text_path) if a_text_path.exists() else None,
            },
        },
        "artifacts": {
            "wav": {
                "path": _safe_relpath(final_wav),
                "sha1": sha1_file(final_wav) if final_wav.exists() else None,
                "duration_sec": wav_duration_seconds(final_wav) if final_wav.exists() else None,
            },
            "srt": {
                "path": _safe_relpath(final_srt),
                "sha1": sha1_file(final_srt) if final_srt.exists() else None,
                "end_sec": srt_end_seconds(final_srt) if final_srt.exists() else None,
                "entries": srt_entry_count(final_srt) if final_srt.exists() else None,
            },
            "log": {
                "path": _safe_relpath(final_log),
                "sha1": sha1_file(final_log) if final_log.exists() else None,
            },
        },
        "notes": str(notes or ""),
    }

    try:
        if final_log.exists():
            log_obj = json.loads(final_log.read_text(encoding="utf-8"))
            if isinstance(log_obj, dict):
                manifest["log_summary"] = {
                    "engine": log_obj.get("engine") or (log_obj.get("audio") or {}).get("engine"),
                    "timestamp": log_obj.get("timestamp"),
                }
    except Exception:
        pass

    atomic_write_json(final_dir / "audio_manifest.json", manifest)


def _mark_audio_synthesis_completed(
    *,
    channel: str,
    video: str,
    engine: str,
    mode: str,
    final_dir: Path,
    wav_path: Path,
    srt_path: Path,
) -> None:
    """
    Update `workspaces/scripts/{CH}/{VID}/status.json` stage:
      stages.audio_synthesis.status = completed

    This is required for manual workflows where the user places wav/srt under workspaces/video/input
    and we only ingest + validate (no synthesis).
    """
    from factory_common.artifacts.utils import atomic_write_json, utc_now_iso
    from factory_common.paths import status_path as _status_path, repo_root as _repo_root

    st_path = _status_path(channel, video)
    if not st_path.exists():
        return
    try:
        payload = json.loads(st_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    stages = payload.get("stages")
    if not isinstance(stages, dict):
        return

    stage = stages.get("audio_synthesis")
    if not isinstance(stage, dict):
        stage = {"status": "pending", "details": {}}
    details = stage.get("details")
    if not isinstance(details, dict):
        details = {}

    repo_root_path = _repo_root()

    def _rel(p: Path) -> str:
        try:
            return str(p.resolve().relative_to(repo_root_path.resolve()))
        except Exception:
            return str(p)

    details.update(
        {
            "completed_at": utc_now_iso(),
            "engine": str(engine),
            "mode": str(mode),
            "final_dir": _rel(final_dir),
            "wav": _rel(wav_path),
            "srt": _rel(srt_path),
        }
    )
    stage["status"] = "completed"
    stage["details"] = details
    stages["audio_synthesis"] = stage
    payload["stages"] = stages

    atomic_write_json(st_path, payload)


def _find_video_input_file(channel: str, filename: str) -> Path | None:
    """
    Find `<filename>` under `workspaces/video/input/<CH>_*` and return the newest match.
    """
    from factory_common.paths import video_input_root

    root = video_input_root()
    prefix = f"{channel}_"
    candidates: list[Path] = []
    for d in sorted(root.glob(f"{prefix}*")):
        if not d.is_dir():
            continue
        p = d / filename
        if p.exists():
            candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> None:
    args = parse_args()
    from factory_common.routing_lockdown import (
        assert_no_llm_model_overrides,
        assert_task_overrides_unchanged,
        lockdown_active,
    )

    # Policy: TTS reading LLM (auditor) is disabled by default.
    # 推論/判断は対話型AIエージェントが担当し、VOICEVOXは prepass mismatch=0 を合格条件にする。
    # - Default: SKIP_TTS_READING=1
    # - Under routing lockdown (default ON): SKIP_TTS_READING=0 is forbidden (prevents accidental LLM path).
    skip_tts_reading = os.environ.get("SKIP_TTS_READING", "1") not in ("0", "", None)
    if lockdown_active() and not skip_tts_reading:
        raise SystemExit(
            "\n".join(
                [
                    "[POLICY] Forbidden: SKIP_TTS_READING=0 under YTM_ROUTING_LOCKDOWN=1.",
                    "- required: SKIP_TTS_READING=1 (読みLLM（auditor）禁止; 推論=対話型AIエージェント / 辞書+override+prepass mismatch=0)",
                    "- emergency: set YTM_EMERGENCY_OVERRIDE=1 for this run (debug only)",
                ]
            )
        )

    # Policy: engine must be SSOT-driven (no ad-hoc overrides under lockdown).
    # Engine selection is derived from:
    # - packages/script_pipeline/audio/channels/<CH>/voice_config.json
    # - packages/audio_tts/configs/routing.json (script_override / channel_override / engine_default)
    if lockdown_active():
        if args.engine_override:
            raise SystemExit(
                "\n".join(
                    [
                        "[POLICY] Forbidden: --engine-override under YTM_ROUTING_LOCKDOWN=1.",
                        "- policy: Engine is auto-decided by SSOT (voice_config.json / routing.json).",
                        "- fix: remove --engine-override; change SSOT configs if needed.",
                        "- emergency: set YTM_EMERGENCY_OVERRIDE=1 for this run (debug only)",
                    ]
                )
            )
        if (os.getenv("ENGINE_DEFAULT_OVERRIDE") or "").strip():
            raise SystemExit(
                "\n".join(
                    [
                        "[POLICY] Forbidden: ENGINE_DEFAULT_OVERRIDE under YTM_ROUTING_LOCKDOWN=1.",
                        "- policy: Engine is auto-decided by SSOT (voice_config.json / routing.json).",
                        "- fix: unset ENGINE_DEFAULT_OVERRIDE and rerun.",
                        "- emergency: set YTM_EMERGENCY_OVERRIDE=1 for this run (debug only)",
                    ]
                )
            )

    # Lockdown policy (default ON): forbid ad-hoc model overrides that cause drift across agents.
    assert_no_llm_model_overrides(context="audio_tts.run_tts (startup)")
    assert_task_overrides_unchanged(context="audio_tts.run_tts (startup)")
    if args.reading_source:
        # STRICT pipeline currently uses MeCab tokenization for readings; keep this flag
        # for compatibility with UI/backends that pass it (future support).
        print(f"[INFO] reading_source override requested: {args.reading_source}")

    # Optional: allow CLI-driven model overrides without editing router config.
    if getattr(args, "llm_slot", None) is not None:
        try:
            slot = int(args.llm_slot)
        except Exception:
            raise SystemExit(f"--llm-slot must be an integer; got: {args.llm_slot}")
        if slot < 0:
            raise SystemExit(f"--llm-slot must be >= 0; got: {slot}")
        os.environ["LLM_MODEL_SLOT"] = str(slot)

    if args.llm_model:
        raw = str(args.llm_model).strip()
        # Compatibility: pure integer means slot selection.
        if raw.isdigit():
            os.environ["LLM_MODEL_SLOT"] = raw
        else:
            if lockdown_active():
                raise SystemExit(
                    "Forbidden: --llm-model with non-numeric values under YTM_ROUTING_LOCKDOWN=1. "
                    "Use --llm-slot (numeric) instead, or set YTM_EMERGENCY_OVERRIDE=1 for one-off debugging."
                )
            os.environ["LLM_FORCE_MODELS"] = raw
    if args.llm_task_model:
        mapping: dict[str, list[str]] = {}
        for raw in args.llm_task_model or []:
            spec = str(raw).strip()
            if not spec:
                continue
            if "=" not in spec:
                raise SystemExit(f"--llm-task-model must be TASK=MODELKEY[,MODELKEY...]; got: {spec}")
            task, models = spec.split("=", 1)
            task = task.strip()
            if not task:
                raise SystemExit(f"--llm-task-model task is empty: {spec}")
            model_keys = [m.strip() for m in models.split(",") if m.strip()]
            if not model_keys:
                raise SystemExit(f"--llm-task-model models are empty: {spec}")
            mapping[task] = model_keys
        if mapping:
            if lockdown_active():
                raise SystemExit(
                    "Forbidden: --llm-task-model under YTM_ROUTING_LOCKDOWN=1. "
                    "Use numeric slots (LLM_MODEL_SLOT) and SSOT task routing instead."
                )
            os.environ["LLM_FORCE_TASK_MODELS_JSON"] = json.dumps(mapping, ensure_ascii=False)

    if len(args.video) != 3 or not args.video.isdigit():
        raise SystemExit(f"Video number must be 3 digits (e.g., 001); got '{args.video}'")

    cfg = load_routing_config()

    # Manual workflow (user-provided audio):
    # If the user has already placed `<CH>-<VID>.wav` and `<CH>-<VID>.srt` under workspaces/video/input,
    # we must NEVER synthesize audio. Instead we ingest/validate and mark audio_synthesis as completed.
    manual_wav_src = _find_video_input_file(args.channel, f"{args.channel}-{args.video}.wav")
    manual_srt_src = _find_video_input_file(args.channel, f"{args.channel}-{args.video}.srt")
    if (
        manual_wav_src is not None
        and manual_srt_src is not None
        and not args.finalize_existing
        and not args.prepass
        and not args.force_overwrite_final
    ):
        print("[AUTO] Detected manual wav+srt under workspaces/video/input; switching to finalize_existing (no synthesis).")
        args.finalize_existing = True
    
    # Determine Engine
    engine = args.engine_override or decide_engine(args.channel, args.video, cfg)
    print(f"[RUN] Channel={args.channel} Video={args.video} Engine={engine} StrictMode=ON")

    if engine not in ("voicevox", "voicepeak", "elevenlabs"):
        raise SystemExit(f"[ERROR] Unsupported engine: {engine}")

    # SSOT: Keep Voicepeak's local user dictionary in sync with the repo-managed SoT
    # to prevent "dict not applied" regressions across machines/sessions.
    if engine == "voicepeak" and not args.finalize_existing:
        try:
            from audio_tts.scripts.sync_voicepeak_user_dict import sync_voicepeak_user_dict

            res = sync_voicepeak_user_dict(dry_run=False)
            if res.changed:
                print(f"[VoicepeakDict] synced: {res.dst}")
            else:
                print(f"[VoicepeakDict] already up-to-date: {res.dst}")
        except Exception as e:
            msg = "\n".join(
                [
                    "[POLICY] Voicepeak dict sync failed.",
                    f"- error: {e}",
                    "- SoT: packages/audio_tts/data/voicepeak/dic.json",
                    "- fix: run `PYTHONPATH='.:packages' python3 -m audio_tts.scripts.sync_voicepeak_user_dict` and rerun",
                    "- emergency: set YTM_EMERGENCY_OVERRIDE=1 for this run (debug only)",
                ]
            )
            if lockdown_active():
                raise SystemExit(msg)
            print(f"[WARN] {msg}")

    if engine == "voicevox" and not args.finalize_existing:
        vv_url = cfg.voicevox_url
        try:
            if requests is not None:
                r = requests.get(f"{vv_url}/speakers", timeout=10)
                r.raise_for_status()
            else:
                with urllib.request.urlopen(f"{vv_url}/speakers", timeout=10) as resp:  # noqa: S310
                    if resp.status != 200:
                        raise RuntimeError(resp.status)
        except Exception as e:
            raise SystemExit(f"[ERROR] Voicevox not reachable at {vv_url}: {e}")

    # IO Setup (SSOT paths)
    from factory_common.paths import repo_root, video_root, audio_final_dir, channels_csv_path, status_path
    from factory_common.alignment import ALIGNMENT_SCHEMA, planning_hash_from_row, sha1_file as sha1_file_bytes

    repo_root_path = repo_root()
    video_dir = video_root(args.channel, args.video)
    final_dir = audio_final_dir(args.channel, args.video)

    # Guard: do not overwrite manual-finalized audio unless explicitly forced.
    if not args.prepass and not args.finalize_existing and not args.force_overwrite_final:
        manifest_path = final_dir / "audio_manifest.json"
        if manifest_path.exists():
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                notes = str(payload.get("notes") or "") if isinstance(payload, dict) else ""
                if notes.startswith("finalize_existing:"):
                    raise SystemExit(
                        "\n".join(
                            [
                                "[GUARD] Final audio was finalized manually (STOP).",
                                f"- episode: {args.channel}-{args.video}",
                                f"- manifest: {manifest_path}",
                                f"- notes: {notes}",
                                "",
                                "If you really intend to regenerate and overwrite final artifacts, rerun with:",
                                "  --force-overwrite-final",
                            ]
                        )
                    )
            except SystemExit:
                raise
            except Exception:
                # If manifest is unreadable, don't guess; let the run continue.
                pass

    input_mode, input_path = _resolve_input_mode_and_path(
        repo_root_path=repo_root_path,
        video_dir=video_dir,
        final_dir=final_dir,
        provided=args.input,
    )

    # --- Alignment guard: Planning(title/thumbnail) <-> Script(A-text) -----------------
    # Prevent generating audio from a script that drifted from the current planning title/thumbnail.
    try:
        st_path = status_path(args.channel, args.video)
        if st_path.exists():
            payload = json.loads(st_path.read_text(encoding="utf-8"))
            meta = payload.get("metadata") if isinstance(payload, dict) else None
            align = meta.get("alignment") if isinstance(meta, dict) else None
            if not (isinstance(align, dict) and align.get("schema") == ALIGNMENT_SCHEMA):
                raise SystemExit(
                    f"[ALIGN] alignment stamp missing. Run `python scripts/enforce_alignment.py --channels {args.channel} --apply` "
                    f"or `python -m script_pipeline.cli reconcile --channel {args.channel} --video {args.video}`."
                )
            if not args.allow_unvalidated and not args.finalize_existing:
                stages = payload.get("stages") if isinstance(payload, dict) else None
                sv = stages.get("script_validation") if isinstance(stages, dict) else None
                sv_status = sv.get("status") if isinstance(sv, dict) else None
                if sv_status != "completed":
                    raise SystemExit(
                        f"[VALIDATION] script_validation is not completed for {args.channel}-{args.video}. "
                        f"Run `python -m script_pipeline.cli run --channel {args.channel} --video {args.video} --stage script_validation` "
                        "or pass --allow-unvalidated."
                    )
            stored_planning_hash = align.get("planning_hash")
            stored_script_hash = align.get("script_hash")
            if not (isinstance(stored_planning_hash, str) and isinstance(stored_script_hash, str)):
                raise SystemExit("[ALIGN] alignment stamp incomplete (missing hashes). Re-stamp alignment.")

            # Current planning hash (CSV)
            csv_path = channels_csv_path(args.channel)
            planning_row: dict[str, str] = {}
            if csv_path.exists():
                with csv_path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        v = row.get("動画番号") or row.get("No.") or ""
                        digits = "".join(ch for ch in str(v) if ch.isdigit())
                        if digits and f"{int(digits):03d}" == args.video:
                            planning_row = dict(row)
                            break
            if not planning_row:
                raise SystemExit(f"[ALIGN] planning row not found in {csv_path} for video={args.video}")
            current_planning_hash = planning_hash_from_row(planning_row)

            # Current script hash (A-text SoT)
            content_dir = video_root(args.channel, args.video) / "content"
            current_script_path = content_dir / "assembled_human.md"
            if not current_script_path.exists():
                current_script_path = content_dir / "assembled.md"
            if not current_script_path.exists():
                raise SystemExit(f"[ALIGN] script not found: {current_script_path}")
            current_script_hash = sha1_file_bytes(current_script_path)

            mismatches = []
            if current_planning_hash != stored_planning_hash:
                mismatches.append("planning(title/thumbnail)")
            if current_script_hash != stored_script_hash:
                mismatches.append("script(A-text)")
            if mismatches:
                raise SystemExit(
                    "[ALIGN] mismatch detected (" + " & ".join(mismatches) + "). "
                    "Regenerate/reconcile the script and re-run alignment stamping before TTS."
                )
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"[ALIGN] failed to verify alignment: {exc}")

    # --- Global guard: resolve canonical inputs (STOP on ambiguous A/B) --------------
    # A-text SoT: content/assembled_human.md (if exists) else content/assembled.md
    # B-text explicit: audio_prep/script_sanitized.txt or audio/final/*/a_text.txt
    #
    # No implicit fallback between A/B modes; the caller must pick --input explicitly.
    content_dir = video_dir / "content"
    _ensure_a_text_mirror_consistency(content_dir=content_dir, channel=args.channel, video=args.video)

    human_path = content_dir / "assembled_human.md"
    assembled_path = content_dir / "assembled.md"
    canonical_a = human_path if human_path.exists() else assembled_path

    if not input_path.exists():
        raise SystemExit(f"[ERROR] Input file not found: {input_path}")
    if not canonical_a.exists():
        raise SystemExit(f"[ERROR] A-text not found: {canonical_a}")

    if input_mode == "a_text":
        print(f"[INPUT] A-text: {canonical_a}")
    else:
        print(f"[INPUT] B-text: {input_path} (A-text SoT: {canonical_a})")

    # Output to workspaces/scripts/... (legacy: script_pipeline/data/...).
    artifact_root = video_dir / "audio_prep"

    def _latest_mtime(path: Path) -> float:
        mtimes = []
        if path.exists():
            mtimes.append(path.stat().st_mtime)
            for p in path.rglob("*"):
                if p.exists():
                    try:
                        mtimes.append(p.stat().st_mtime)
                    except OSError:
                        continue
        return max(mtimes) if mtimes else 0.0

    # If input script is newer than existing artifacts, purge audio_prep (A-text mode only).
    # NOTE: In explicit B-text mode, never purge the whole directory (it may contain the authoritative input).
    if input_mode == "a_text":
        script_mtime = canonical_a.stat().st_mtime
        if artifact_root.exists():
            artifacts_mtime = _latest_mtime(artifact_root)
            if script_mtime > artifacts_mtime:
                try:
                    shutil.rmtree(artifact_root)
                    print("[CLEAN] audio_prep purged (A-text newer than artifacts)")
                except Exception as e:
                    raise SystemExit(f"[ERROR] Failed to purge old audio_prep: {e}")

    artifact_root.mkdir(parents=True, exist_ok=True)
    
    out_wav = args.out_wav or artifact_root / f"{args.channel}-{args.video}.wav"
    log_path = args.log or artifact_root / "log.json"
    
    # Voicepeak settings
    voicepeak_overrides = {
        k: v for k, v in {
            "narrator": args.voicepeak_narrator,
            "speed": args.voicepeak_speed,
            "pitch": args.voicepeak_pitch,
            "emotion": args.voicepeak_emotion,
        }.items() if v is not None
    }
    
    # Resolve actual synthesis input.
    if input_mode == "a_text":
        input_text = canonical_a.read_text(encoding="utf-8")
    else:
        _enforce_b_text_not_stale(
            channel=args.channel,
            video=args.video,
            a_path=canonical_a,
            b_path=input_path,
        )
        input_text = input_path.read_text(encoding="utf-8")
    # Always materialize a TTS input under audio_prep/ (required by SSOT).
    # Prefer sanitized text (strip meta citations/URLs), but if sanitize fails,
    # write raw input so downstream/UI never silently falls back.
    sanitized_path = artifact_root / "script_sanitized.txt"
    try:
        from factory_common.text_sanitizer import strip_meta_from_script

        sanitized = strip_meta_from_script(input_text)
        if sanitized.removed_counts:
            print(f"[SANITIZE] Removed meta tokens from input: {sanitized.removed_counts}")
        input_text = sanitized.text
    except Exception as exc:
        print(f"[WARN] Failed to sanitize input text; continuing with raw input: {exc}")

    try:
        sanitized_path.write_text(input_text, encoding="utf-8")
    except Exception as exc:
        raise SystemExit(f"[ERROR] Failed to write script_sanitized.txt: {exc}")

    if args.finalize_existing:
        from factory_common.artifacts.utils import atomic_write_json
        from factory_common.timeline_manifest import wav_duration_seconds, srt_end_seconds

        final_dir.mkdir(parents=True, exist_ok=True)
        final_wav = final_dir / f"{args.channel}-{args.video}.wav"
        final_srt = final_dir / f"{args.channel}-{args.video}.srt"
        final_log = final_dir / "log.json"

        # Manual workflow: prefer artifacts under workspaces/video/input/<CH>_*
        # (user may overwrite them; treat them as authoritative when present).
        for name, target in [(final_wav.name, final_wav), (final_srt.name, final_srt)]:
            src = _find_video_input_file(args.channel, name)
            if src is None:
                continue
            try:
                should_copy = (not target.exists()) or (src.stat().st_size != target.stat().st_size) or (
                    src.stat().st_mtime > target.stat().st_mtime
                )
            except Exception:
                should_copy = True
            if should_copy:
                shutil.copy2(src, target)

        if not final_wav.exists() or not final_srt.exists():
            raise SystemExit(
                "\n".join(
                    [
                        "[FINALIZE] Missing final artifacts (STOP).",
                        f"- wav: {final_wav} ({'OK' if final_wav.exists() else 'MISSING'})",
                        f"- srt: {final_srt} ({'OK' if final_srt.exists() else 'MISSING'})",
                        "",
                        "Fix:",
                        "- Export wav+srt from your TTS (Voicepeak) into workspaces/video/input or workspaces/audio/final, then retry.",
                    ]
                )
            )

        wav_dur = wav_duration_seconds(final_wav)
        srt_end = srt_end_seconds(final_srt)
        if wav_dur is not None and srt_end is not None:
            drift = abs(float(wav_dur) - float(srt_end))
            if drift > 1.0:
                raise SystemExit(
                    "\n".join(
                        [
                            "[FINALIZE] Audio/SRT duration mismatch (CODE RED).",
                            f"- wav duration: {wav_dur:.3f}s",
                            f"- srt end:      {srt_end:.3f}s",
                            f"- |diff|:       {drift:.3f}s",
                            "",
                            "This usually means you overwrote only wav OR only srt.",
                            "Re-export wav+srt as a pair (same session/settings) and retry.",
                        ]
                    )
                )

        # Prefer manual export text (video/input/<CH>_*/<CH>-<NNN>.txt) when present.
        manual_txt = _find_video_input_file(args.channel, f"{args.channel}-{args.video}.txt")
        a_text_src = manual_txt or sanitized_path
        shutil.copy2(a_text_src, final_dir / "a_text.txt")

        # Record that this episode was finalized from existing artifacts.
        notes = f"finalize_existing:engine={engine}"
        log_engine = str(engine)
        vp_defaults: dict[str, object] = {}
        if engine == "voicepeak":
            try:
                from audio_tts.tts.routing import load_default_voice_config

                vc = load_default_voice_config(args.channel)
                engine_opts = (vc or {}).get("engine_options") if isinstance(vc, dict) else {}
                if isinstance(engine_opts, dict):
                    vp_defaults = {
                        "narrator": engine_opts.get("narrator"),
                        "speed": engine_opts.get("speed"),
                        "pitch": engine_opts.get("pitch"),
                        "emotion": engine_opts.get("emotion"),
                    }
            except Exception:
                pass
        voicepeak_meta = {
            "narrator": args.voicepeak_narrator or vp_defaults.get("narrator"),
            "speed": args.voicepeak_speed if args.voicepeak_speed is not None else vp_defaults.get("speed"),
            "pitch": args.voicepeak_pitch if args.voicepeak_pitch is not None else vp_defaults.get("pitch"),
            "emotion": args.voicepeak_emotion if args.voicepeak_emotion is not None else vp_defaults.get("emotion"),
        }
        log_obj = {
            "timestamp": time.time(),
            "engine": log_engine,
            "mode": "finalize_existing",
            "notes": notes,
            "voicepeak": {k: v for k, v in voicepeak_meta.items() if v not in (None, "")},
        }
        atomic_write_json(final_log, log_obj)
        _write_contract_audio_manifest(
            channel=args.channel,
            video=args.video,
            final_dir=final_dir,
            final_wav=final_wav,
            final_srt=final_srt,
            final_log=final_log,
            notes=notes,
        )
        try:
            _mark_audio_synthesis_completed(
                channel=args.channel,
                video=args.video,
                engine=str(engine),
                mode="finalize_existing",
                final_dir=final_dir,
                wav_path=final_wav,
                srt_path=final_srt,
            )
        except Exception:
            pass

        print(f"[SUCCESS] Finalized existing wav/srt into {final_dir}")
        return

    # Import Lazy to avoid circular dependency if any
    from audio_tts.tts.strict_orchestrator import run_strict_pipeline

    try:
        # Strict Mode Pipeline
        run_strict_pipeline(
            channel=args.channel,
            video_no=args.video,
            input_text=input_text,
            output_wav=out_wav,
            output_log=log_path,
            engine=engine,
            voicepeak_config=voicepeak_overrides,
            artifact_root=artifact_root,
            target_indices=[int(i) for i in args.indices.split(",")] if args.indices else None,
            resume=args.resume,
            prepass=args.prepass,
            skip_tts_reading=skip_tts_reading,
        )
        if args.prepass:
            print(f"[SUCCESS] Prepass completed. Log: {log_path}")
        else:
            print(f"[SUCCESS] Pipeline completed. Output: {out_wav}")

            # --- Final artifacts sync -------------------------------------------------
            # Regardless of where --out-wav points, always sync latest outputs to
            # workspaces/audio/final/<CH>/<VIDEO>/ so downstream tools
            # (CapCut, AutoDraft, UI preview) never pick stale audio/SRT.
            final_dir = audio_final_dir(args.channel, args.video)
            final_dir.mkdir(parents=True, exist_ok=True)
            final_wav = final_dir / f"{args.channel}-{args.video}.wav"
            final_srt = final_dir / f"{args.channel}-{args.video}.srt"
            final_log = final_dir / "log.json"
            try:
                if out_wav.resolve() != final_wav.resolve():
                    shutil.copy2(out_wav, final_wav)
                else:
                    final_wav = out_wav
            except Exception as e:
                raise SystemExit(f"[ERROR] Failed to sync final wav: {e}")

            try:
                srt_path = out_wav.with_suffix(".srt")
                if srt_path.exists():
                    if srt_path.resolve() != final_srt.resolve():
                        shutil.copy2(srt_path, final_srt)
                    else:
                        final_srt = srt_path
            except Exception as e:
                raise SystemExit(f"[ERROR] Failed to sync final srt: {e}")

            try:
                if log_path.exists():
                    if log_path.resolve() != final_log.resolve():
                        shutil.copy2(log_path, final_log)
            except Exception as e:
                raise SystemExit(f"[ERROR] Failed to sync final log: {e}")

            try:
                # Keep a_text.txt (authoritative input) alongside final artifacts
                if sanitized_path is not None and sanitized_path.exists():
                    shutil.copy2(sanitized_path, final_dir / "a_text.txt")
                else:
                    shutil.copy2(input_path, final_dir / "a_text.txt")
            except Exception:
                pass
            try:
                _write_contract_audio_manifest(
                    channel=args.channel,
                    video=args.video,
                    final_dir=final_dir,
                    final_wav=final_wav,
                    final_srt=final_srt,
                    final_log=final_log,
                )
            except Exception as e:
                print(f"[WARN] Failed to write audio_manifest.json: {e}", file=sys.stderr)
            try:
                _mark_audio_synthesis_completed(
                    channel=args.channel,
                    video=args.video,
                    engine=str(engine),
                    mode="synthesized",
                    final_dir=final_dir,
                    wav_path=final_wav,
                    srt_path=final_srt,
                )
            except Exception:
                pass

        # Metadata
        from datetime import datetime
        meta_path = artifact_root / "inference_metadata.txt"
        meta_content = (
            f"inference_logic: v2_strict_router\n"
            f"model: router_managed\n"
            f"timestamp: {datetime.now().isoformat()}\n"
            "status: success\n"
        )
        meta_path.write_text(meta_content, encoding="utf-8")

    except Exception as e:
        print(f"[ERROR] Pipeline failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
