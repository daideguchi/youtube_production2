from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Literal, Tuple

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
) -> Tuple[Literal["a_text", "b_text"], Path]:
    p = provided
    if not p.is_absolute():
        p = (repo_root_path / p).resolve()
    else:
        p = p.resolve()

    content_dir = video_dir / "content"
    audio_prep_dir = video_dir / "audio_prep"

    a_candidates = {
        (content_dir / "assembled_human.md").resolve(),
        (content_dir / "assembled.md").resolve(),
    }
    b_candidates = {
        (audio_prep_dir / "script_sanitized.txt").resolve(),
        (final_dir / "a_text.txt").resolve(),
    }
    if p in a_candidates:
        return "a_text", p
    if p in b_candidates:
        return "b_text", p
    raise SystemExit(
        "\n".join(
            [
                f"[ERROR] Unsupported --input path: {p}",
                "Expected one of:",
                f"  - {content_dir / 'assembled_human.md'}",
                f"  - {content_dir / 'assembled.md'}",
                f"  - {audio_prep_dir / 'script_sanitized.txt'} (explicit B-text regeneration)",
                f"  - {final_dir / 'a_text.txt'} (explicit B-text snapshot regeneration)",
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
        help="Input path (A-text: content/assembled*.md) or explicit B-text (audio_prep/script_sanitized.txt or final/a_text.txt)",
    )
    
    # Optional overrides
    p.add_argument("--out-wav", type=Path, help="Output WAV path override")
    p.add_argument("--log", type=Path, help="Output Log path override")
    p.add_argument("--engine-override", choices=["voicevox", "voicepeak", "elevenlabs"], help="Force specific engine")
    p.add_argument(
        "--reading-source",
        choices=["mecab", "voicevox"],
        help="Reading source override (currently informational for STRICT pipeline; used by legacy kana selection).",
    )
    
    # LLM Settings (Now managed via .env and Router)
    p.add_argument("--llm-model", help="Force LLM router model key(s) for this run (comma-separated).")
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

def main() -> None:
    skip_tts_reading = os.environ.get("SKIP_TTS_READING", "0") not in ("0", "", None)
    args = parse_args()
    if args.reading_source:
        # STRICT pipeline currently uses MeCab tokenization for readings; keep this flag
        # for compatibility with UI/backends that pass it (future support).
        print(f"[INFO] reading_source override requested: {args.reading_source}")

    # Optional: allow CLI-driven model overrides without editing router config.
    if args.llm_model:
        os.environ["LLM_FORCE_MODELS"] = str(args.llm_model).strip()
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
            os.environ["LLM_FORCE_TASK_MODELS_JSON"] = json.dumps(mapping, ensure_ascii=False)

    if len(args.video) != 3 or not args.video.isdigit():
        raise SystemExit(f"Video number must be 3 digits (e.g., 001); got '{args.video}'")

    cfg = load_routing_config()
    
    # Determine Engine
    engine = args.engine_override or decide_engine(args.channel, args.video, cfg)
    print(f"[RUN] Channel={args.channel} Video={args.video} Engine={engine} StrictMode=ON")

    if engine == "voicevox":
        vv_url = cfg.voicevox_url
        try:
            if requests is not None:
                r = requests.get(f"{vv_url}/speakers", timeout=3)
                r.raise_for_status()
            else:
                with urllib.request.urlopen(f"{vv_url}/speakers", timeout=3) as resp:  # noqa: S310
                    if resp.status != 200:
                        raise RuntimeError(resp.status)
        except Exception as e:
            raise SystemExit(f"[ERROR] Voicevox not reachable at {vv_url}: {e}")

    # IO Setup (SSOT paths)
    from factory_common.paths import repo_root, video_root, audio_final_dir, channels_csv_path, status_path
    from factory_common.alignment import ALIGNMENT_SCHEMA, planning_hash_from_row, sha1_file as sha1_file_bytes

    base_dir = repo_root()
    video_dir = video_root(args.channel, args.video)
    final_dir = audio_final_dir(args.channel, args.video)
    input_mode, input_path = _resolve_input_mode_and_path(
        repo_root_path=base_dir,
        video_dir=video_dir,
        final_dir=final_dir,
        provided=args.input,
    )
    if not input_path.exists():
        raise SystemExit(f"[ERROR] Input file not found: {input_path}")

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
            if not args.allow_unvalidated:
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

    # --- Global guard: assemble the right script before synthesis -----------------
    # ルール:
    # - チャンネル/動画配下の content に assembled_human.md があれば、それが最終確定版。
    # - assembled_human.md が存在し、assembled.md と内容が異なる場合は、
    #   assembled_human.md を assembled.md に自動で同期してから進む。
    # - assembled_human.md が無ければ、assembled.md をそのまま使う。
    #
    # これにより「古い assembled.md を参照して誤って合成する」事故を防ぐ。
    content_dir = video_dir / "content"
    human_path = content_dir / "assembled_human.md"
    assembled_path = content_dir / "assembled.md"
    _ensure_a_text_mirror_consistency(content_dir=content_dir, channel=args.channel, video=args.video)

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
        canonical_a = human_path if human_path.exists() else assembled_path
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
        canonical_a = human_path if human_path.exists() else assembled_path
        input_text = canonical_a.read_text(encoding="utf-8")
    else:
        canonical_a = human_path if human_path.exists() else assembled_path
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
            # --- Contract manifest (final_dir SoT summary) -------------------------
            # This must never change the synthesis result; it only records pointers
            # + checksums so downstream can run mechanically with consistent inputs.
            try:
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
                manifest = {
                    "schema": "ytm.audio_manifest.v1",
                    "generated_at": utc_now_iso(),
                    "repo_root": str(repo_root_path),
                    "episode": {
                        "id": f"{args.channel}-{args.video}",
                        "channel": args.channel,
                        "video": args.video,
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
                    "notes": "",
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
            except Exception as e:
                print(f"[WARN] Failed to write audio_manifest.json: {e}", file=sys.stderr)

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
