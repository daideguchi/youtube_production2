#!/usr/bin/env python3
from __future__ import annotations

"""
gemini_cli_generate_scripts.py — Gemini CLI (non-batch) script writer helper (manual/opt-in)

Purpose:
- Provide an explicit, operator-invoked route to generate/patch A-text via `gemini` CLI.
- Keep it safe-by-default (dry-run unless --run).
- Write A-text SoT to: workspaces/scripts/{CH}/{NNN}/content/assembled_human.md

Notes:
- This is NOT a silent fallback for script_pipeline. Operators must invoke it explicitly.
- Prompt source is the Git-tracked antigravity prompt files:
    prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha1_text(text: str) -> str:
    h = hashlib.sha1()
    h.update((text or "").encode("utf-8"))
    return h.hexdigest()


def _z3(value: int | str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        raise SystemExit(f"Invalid video: {value!r}")
    return f"{int(digits):03d}"


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
    return [f"{i:03d}" for i in _parse_indices(expr)]


def _normalize_newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _a_text_spoken_char_count(text: str) -> int:
    """
    Count "spoken" characters, matching script_pipeline.validate_a_text intent:
    - exclude pause-only lines (`---`)
    - exclude whitespace/newlines
    """
    normalized = _normalize_newlines(text)
    lines: List[str] = []
    for line in normalized.split("\n"):
        if line.strip() == "---":
            continue
        lines.append(line)
    compact = "".join(lines)
    compact = compact.replace(" ", "").replace("\t", "").replace("\u3000", "")
    return len(compact.strip())


def _parse_target_chars_min(prompt: str) -> Optional[int]:
    m = re.search(r"\btarget_chars_min\s*:\s*(\d{3,})\b", str(prompt or ""), flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _parse_chapter_count(prompt: str) -> Optional[int]:
    m = re.search(r"\bchapter_count\s*:\s*(\d{1,3})\b", str(prompt or ""), flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _strip_edge_pause_lines(text: str) -> str:
    """
    Normalize a partial A-text chunk for safe concatenation:
    - remove leading/trailing blank lines
    - remove leading/trailing pause-only lines (`---`) (including blanks around them)
    """
    normalized = _normalize_newlines(text)
    lines = normalized.split("\n")
    # leading blanks
    while lines and not lines[0].strip():
        lines.pop(0)
    # leading pauses (allow blanks between)
    changed = True
    while changed:
        changed = False
        while lines and not lines[0].strip():
            lines.pop(0)
            changed = True
        if lines and lines[0].strip() == "---":
            lines.pop(0)
            changed = True
    # trailing blanks
    while lines and not lines[-1].strip():
        lines.pop()
    # trailing pauses (allow blanks between)
    changed = True
    while changed:
        changed = False
        while lines and not lines[-1].strip():
            lines.pop()
            changed = True
        if lines and lines[-1].strip() == "---":
            lines.pop()
            changed = True
    out = "\n".join(lines).strip()
    return out + ("\n" if out else "")


def _parse_section_splits(expr: str) -> List[tuple[int, int]]:
    raw = str(expr or "").strip()
    if not raw:
        return []
    out: List[tuple[int, int]] = []
    for token in raw.split(","):
        t = token.strip()
        if not t:
            continue
        if "-" in t:
            a, b = [x.strip() for x in t.split("-", 1)]
            lo = int(a)
            hi = int(b)
            if hi < lo:
                lo, hi = hi, lo
            out.append((lo, hi))
        else:
            i = int(t)
            out.append((i, i))
    return out


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _prompt_path(channel: str, video: str) -> Path:
    ch = str(channel).strip().upper()
    vv = _z3(video)
    return repo_paths.repo_root() / "prompts" / "antigravity_gemini" / ch / f"{ch}_{vv}_FULL_PROMPT.md"


def _output_a_text_path(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "content" / "assembled_human.md"


def _logs_dir(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "logs"


def _scratch_dir() -> Path:
    # Run gemini CLI from a scratch directory to avoid scanning the whole repo as "workspace context".
    return repo_paths.workspace_root() / "_scratch" / "gemini_cli"


def _gemini_home_dir(channel: str, video: str) -> Path:
    ch = str(channel).strip().upper()
    vv = _z3(video)
    # Isolate gemini's global state (settings/tmp/history) per-episode to avoid cross-agent collisions
    # and to avoid mutating the user's real ~/.gemini settings.
    return _scratch_dir() / "home" / f"{ch}-{vv}"


def _ensure_gemini_settings(*, home_dir: Path, auth_type: str) -> Path:
    """
    Prepare an isolated HOME so gemini CLI can run non-interactively using GEMINI_API_KEY.

    gemini CLI stores global settings under: $HOME/.gemini/settings.json
    We write the minimal auth selection there.
    """
    gemini_dir = home_dir / ".gemini"
    _ensure_dir(gemini_dir)
    settings_path = gemini_dir / "settings.json"
    # Note: keep this file self-contained to avoid relying on the user's ~/.gemini config.
    # Provide a dedicated long-form alias suitable for A-text generation.
    payload: Dict[str, Any] = {
        "security": {"auth": {"selectedType": str(auth_type)}},
        "modelConfigs": {
            "customAliases": {
                # High maxOutputTokens + no thinking for long-form prose generation.
                "antigravity-script": {
                    "extends": "base",
                    "modelConfig": {
                        "model": "gemini-2.5-flash",
                        "generateContentConfig": {
                            "temperature": 0.9,
                            "topP": 0.95,
                            "topK": 64,
                            "maxOutputTokens": 24000,
                            "thinkingConfig": {"thinkingBudget": 0},
                        },
                    },
                }
            }
        },
    }
    settings_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return settings_path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    _ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    _ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _find_gemini_bin(explicit: str | None) -> str:
    if explicit:
        p = Path(str(explicit)).expanduser()
        if p.exists():
            return str(p)
        raise SystemExit(f"gemini not found at --gemini-bin: {explicit}")
    found = shutil.which("gemini")
    if found:
        return found
    raise SystemExit("gemini CLI not found. Install `gemini` and ensure it is on PATH.")


def _build_prompt(*, base_prompt: str, instruction: str | None, include_current: bool, current_a_text: str | None) -> str:
    parts: List[str] = [str(base_prompt or "").rstrip()]

    if include_current and current_a_text:
        parts.append("<<<CURRENT_A_TEXT_START>>>")
        parts.append(str(current_a_text).rstrip())
        parts.append("<<<CURRENT_A_TEXT_END>>>")

    if instruction:
        parts.append("<<<OPERATOR_INSTRUCTION_START>>>")
        parts.append(str(instruction).strip())
        parts.append("<<<OPERATOR_INSTRUCTION_END>>>")

    joined = "\n\n".join([p for p in parts if str(p).strip()]).strip()
    return joined + "\n"


def _read_current_a_text(channel: str, video: str) -> Optional[str]:
    content_dir = repo_paths.video_root(channel, video) / "content"
    human = content_dir / "assembled_human.md"
    mirror = content_dir / "assembled.md"
    path = human if human.exists() else mirror
    if not path.exists():
        return None
    try:
        return _read_text(path)
    except Exception:
        return None


def _run_gemini_cli(
    *,
    gemini_bin: str,
    prompt: str,
    model: str | None,
    sandbox: bool,
    approval_mode: str | None,
    yolo: bool,
    home_dir: Path | None,
    timeout_sec: int,
) -> tuple[int, str, str, float]:
    cmd: List[str] = [gemini_bin, "--output-format", "text"]
    if model:
        cmd += ["--model", str(model)]
    if sandbox:
        cmd.append("--sandbox")
    if approval_mode:
        cmd += ["--approval-mode", str(approval_mode)]
    elif yolo:
        cmd.append("--yolo")

    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")
    if home_dir is not None:
        env["HOME"] = str(home_dir)

    start = time.time()
    proc = subprocess.run(
        cmd,
        input=str(prompt),
        text=True,
        capture_output=True,
        cwd=str(_scratch_dir()),
        env=env,
        timeout=max(1, int(timeout_sec)),
    )
    elapsed = time.time() - start
    return int(proc.returncode), str(proc.stdout or ""), str(proc.stderr or ""), float(elapsed)


def _backup_if_diff(path: Path, new_text: str) -> Optional[Path]:
    if not path.exists():
        return None
    try:
        old_text = path.read_text(encoding="utf-8")
    except Exception:
        old_text = ""
    if _sha1_text(old_text) == _sha1_text(new_text):
        return None
    backup = path.with_name(path.name + f".bak.{_utc_now_compact()}")
    _write_text(backup, old_text)
    return backup


def _reject_obviously_non_script(text: str) -> Optional[str]:
    stripped = (text or "").lstrip()
    if not stripped:
        return "empty_output"
    if stripped.startswith("[NEEDS_INPUT]"):
        return "needs_input"
    return None


def cmd_run(args: argparse.Namespace) -> int:
    channel = str(args.channel or "").strip().upper()
    if not re.fullmatch(r"CH\d{2}", channel):
        raise SystemExit(f"Invalid --channel: {args.channel!r} (expected CHxx)")

    section_splits = _parse_section_splits(args.split_sections) if str(args.split_sections or "").strip() else []
    if section_splits and bool(args.include_current):
        raise SystemExit("--split-sections cannot be used with --include-current (concatenate parts instead).")

    videos: List[str] = []
    if args.video:
        videos = [_z3(args.video)]
    elif args.videos:
        videos = _parse_videos(args.videos)
    else:
        raise SystemExit("Specify --video NNN or --videos NNN-NNN")

    gemini_bin = _find_gemini_bin(args.gemini_bin)
    _ensure_dir(_scratch_dir())

    failures: List[str] = []

    for vv in videos:
        script_id = f"{channel}-{vv}"
        prompt_path = _prompt_path(channel, vv)
        out_path = _output_a_text_path(channel, vv)
        logs_dir = _logs_dir(channel, vv)

        if not prompt_path.exists():
            raise SystemExit(f"Prompt not found: {prompt_path} ({script_id})")

        base_prompt = _read_text(prompt_path)
        current = _read_current_a_text(channel, vv) if bool(args.include_current) else None
        final_prompt = _build_prompt(
            base_prompt=base_prompt,
            instruction=str(args.instruction) if args.instruction else None,
            include_current=bool(args.include_current),
            current_a_text=current,
        )

        home_dir: Path | None = None
        settings_path: Path | None = None
        if not bool(args.gemini_use_user_home):
            home_dir = _gemini_home_dir(channel, vv)
            settings_path = _ensure_gemini_settings(home_dir=home_dir, auth_type=str(args.gemini_auth_type))

        if not args.run:
            print(f"[DRY-RUN] {script_id}")
            print(f"- gemini: {gemini_bin}")
            if args.gemini_model:
                print(f"- model: {args.gemini_model}")
            if args.gemini_sandbox:
                print("- sandbox: true")
            if args.gemini_approval_mode:
                print(f"- approval_mode: {args.gemini_approval_mode}")
            elif args.gemini_yolo:
                print("- yolo: true")
            if home_dir is not None:
                print(f"- gemini_auth_type: {args.gemini_auth_type}")
                if settings_path is not None:
                    print(f"- gemini_settings: {settings_path}")
            print(f"- prompt: {prompt_path}")
            print(f"- output: {out_path}")
            if args.include_current:
                print("- include_current: true")
            if args.instruction:
                print("- instruction: (provided)")
            print("")
            continue

        _ensure_dir(logs_dir)
        prompt_log = logs_dir / "gemini_cli_prompt.txt"
        stdout_log = logs_dir / "gemini_cli_stdout.txt"
        stderr_log = logs_dir / "gemini_cli_stderr.txt"
        meta_log = logs_dir / "gemini_cli_meta.json"

        _write_text(prompt_log, final_prompt)

        if section_splits:
            chapter_count = _parse_chapter_count(final_prompt)
            if chapter_count:
                for lo, hi in section_splits:
                    if lo < 1 or hi < 1 or lo > chapter_count or hi > chapter_count:
                        raise SystemExit(
                            f"--split-sections out of range: {lo}-{hi} (chapter_count={chapter_count} from prompt)"
                        )

            parts: List[str] = []
            for idx, (lo, hi) in enumerate(section_splits, start=1):
                part_suffix = f"part{idx:02d}"
                part_instruction = (
                    f"分割生成。全{chapter_count or 'N'}セクションのうち、"
                    f"セクション{lo}からセクション{hi}のみを本文として出力する。"
                    f"それ以外のセクションは一切書かない。"
                    "このパートの先頭と末尾に---は置かない。"
                    "セクション境界の区切りは---のみを最小限に使う。"
                )
                merged_instruction = (str(args.instruction or "").strip() + "\n\n" + part_instruction).strip()
                part_prompt = _build_prompt(
                    base_prompt=base_prompt,
                    instruction=merged_instruction,
                    include_current=False,
                    current_a_text=None,
                )
                part_prompt_log = logs_dir / f"gemini_cli_prompt_{part_suffix}.txt"
                part_stdout_log = logs_dir / f"gemini_cli_stdout_{part_suffix}.txt"
                part_stderr_log = logs_dir / f"gemini_cli_stderr_{part_suffix}.txt"
                part_meta_log = logs_dir / f"gemini_cli_meta_{part_suffix}.json"
                _write_text(part_prompt_log, part_prompt)

                rc, stdout, stderr, elapsed = _run_gemini_cli(
                    gemini_bin=gemini_bin,
                    prompt=part_prompt,
                    model=args.gemini_model,
                    sandbox=bool(args.gemini_sandbox),
                    approval_mode=str(args.gemini_approval_mode) if args.gemini_approval_mode else None,
                    yolo=bool(args.gemini_yolo),
                    home_dir=home_dir,
                    timeout_sec=int(args.timeout_sec),
                )
                _write_text(part_stdout_log, stdout)
                _write_text(part_stderr_log, stderr)
                _write_json(
                    part_meta_log,
                    {
                        "schema_version": 1,
                        "tool": "gemini_cli_generate_scripts",
                        "at": _utc_now_iso(),
                        "script_id": script_id,
                        "part": {"index": idx, "split": {"from": lo, "to": hi}},
                        "prompt_path": str(prompt_path),
                        "output_path": str(out_path),
                        "gemini_bin": gemini_bin,
                        "gemini_model": args.gemini_model,
                        "gemini_sandbox": bool(args.gemini_sandbox),
                        "gemini_approval_mode": args.gemini_approval_mode,
                        "gemini_yolo": bool(args.gemini_yolo),
                        "gemini_use_user_home": bool(args.gemini_use_user_home),
                        "gemini_auth_type": str(args.gemini_auth_type),
                        "gemini_home_dir": str(home_dir) if home_dir is not None else "",
                        "timeout_sec": int(args.timeout_sec),
                        "elapsed_sec": elapsed,
                        "exit_code": rc,
                    },
                )
                if rc != 0:
                    failures.append(f"{script_id}: gemini_exit={rc} (see {part_stderr_log})")
                    parts = []
                    break
                part_text = _normalize_newlines(stdout).rstrip() + "\n"
                reject_reason = _reject_obviously_non_script(part_text)
                if reject_reason:
                    failures.append(f"{script_id}: rejected_output={reject_reason} (see {part_stdout_log})")
                    parts = []
                    break
                parts.append(_strip_edge_pause_lines(part_text))

            if not parts:
                continue
            # Join parts with a single pause line between.
            joined = ""
            for i, part in enumerate(parts):
                if i == 0:
                    joined = part.rstrip() + "\n"
                    continue
                joined = joined.rstrip() + "\n\n---\n\n" + part.lstrip()
            a_text = joined.rstrip() + "\n"
            _write_text(stdout_log, a_text)
            _write_text(stderr_log, "")
            _write_json(
                meta_log,
                {
                    "schema_version": 1,
                    "tool": "gemini_cli_generate_scripts",
                    "at": _utc_now_iso(),
                    "script_id": script_id,
                    "multipart": {"enabled": True, "splits": [{"from": a, "to": b} for a, b in section_splits]},
                    "prompt_path": str(prompt_path),
                    "output_path": str(out_path),
                    "gemini_bin": gemini_bin,
                    "gemini_model": args.gemini_model,
                    "gemini_sandbox": bool(args.gemini_sandbox),
                    "gemini_approval_mode": args.gemini_approval_mode,
                    "gemini_yolo": bool(args.gemini_yolo),
                    "gemini_use_user_home": bool(args.gemini_use_user_home),
                    "gemini_auth_type": str(args.gemini_auth_type),
                    "gemini_home_dir": str(home_dir) if home_dir is not None else "",
                    "timeout_sec": int(args.timeout_sec),
                },
            )
        else:
            rc, stdout, stderr, elapsed = _run_gemini_cli(
                gemini_bin=gemini_bin,
                prompt=final_prompt,
                model=args.gemini_model,
                sandbox=bool(args.gemini_sandbox),
                approval_mode=str(args.gemini_approval_mode) if args.gemini_approval_mode else None,
                yolo=bool(args.gemini_yolo),
                home_dir=home_dir,
                timeout_sec=int(args.timeout_sec),
            )

            _write_text(stdout_log, stdout)
            _write_text(stderr_log, stderr)
            _write_json(
                meta_log,
                {
                    "schema_version": 1,
                    "tool": "gemini_cli_generate_scripts",
                    "at": _utc_now_iso(),
                    "script_id": script_id,
                    "prompt_path": str(prompt_path),
                    "output_path": str(out_path),
                    "gemini_bin": gemini_bin,
                    "gemini_model": args.gemini_model,
                    "gemini_sandbox": bool(args.gemini_sandbox),
                    "gemini_approval_mode": args.gemini_approval_mode,
                    "gemini_yolo": bool(args.gemini_yolo),
                    "gemini_use_user_home": bool(args.gemini_use_user_home),
                    "gemini_auth_type": str(args.gemini_auth_type),
                    "gemini_home_dir": str(home_dir) if home_dir is not None else "",
                    "timeout_sec": int(args.timeout_sec),
                    "elapsed_sec": elapsed,
                    "exit_code": rc,
                },
            )

            if rc != 0:
                failures.append(f"{script_id}: gemini_exit={rc} (see {stderr_log})")
                continue

            a_text = _normalize_newlines(stdout).rstrip() + "\n"
            reject_reason = _reject_obviously_non_script(a_text)
            if reject_reason:
                failures.append(f"{script_id}: rejected_output={reject_reason} (see {stdout_log})")
                continue

        detected_min = _parse_target_chars_min(final_prompt)
        min_spoken_chars = int(args.min_spoken_chars or 0)
        if min_spoken_chars <= 0 and detected_min:
            min_spoken_chars = int(detected_min)
        if min_spoken_chars > 0 and not bool(args.allow_short):
            spoken_chars = _a_text_spoken_char_count(a_text)
            if spoken_chars < min_spoken_chars:
                failures.append(
                    f"{script_id}: rejected_output=too_short spoken_chars={spoken_chars} < min={min_spoken_chars} "
                    f"(see {stdout_log})"
                )
                continue

        backup = _backup_if_diff(out_path, a_text)
        _write_text(out_path, a_text)

        backup_note = f" (backup: {backup.name})" if backup else ""
        print(f"[OK] {script_id} -> {out_path}{backup_note}")

    if failures:
        print("[ERROR] Some items failed:", file=sys.stderr)
        for msg in failures:
            print(f"- {msg}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gemini_cli_generate_scripts.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="generate A-text via gemini CLI (dry-run by default)")
    sp.add_argument("--channel", required=True, help="e.g. CH06")
    mg = sp.add_mutually_exclusive_group(required=True)
    mg.add_argument("--video", help="e.g. 035")
    mg.add_argument("--videos", help="e.g. 035-040 or 35,36,40")
    sp.add_argument("--run", action="store_true", help="Execute gemini and write assembled_human.md (default: dry-run)")

    sp.add_argument("--include-current", dest="include_current", action="store_true", help="Include current A-text in the prompt")
    sp.add_argument("--instruction", default="", help="Optional operator instruction appended to the prompt")
    sp.add_argument(
        "--split-sections",
        default="",
        help="Generate in multiple parts by section ranges, e.g. '1-4,5-7' (cannot be used with --include-current)",
    )
    sp.add_argument(
        "--min-spoken-chars",
        type=int,
        default=0,
        help="Reject overwrite if output spoken chars is below this minimum (0=auto-detect from prompt target_chars_min)",
    )
    sp.add_argument(
        "--allow-short",
        action="store_true",
        help="Allow overwriting even if output is below target_chars_min / --min-spoken-chars (not recommended)",
    )

    sp.add_argument("--gemini-bin", default="", help="Explicit gemini binary path (optional)")
    sp.add_argument("--gemini-model", default="", help="Gemini model (passed to gemini --model)")
    sp.add_argument("--gemini-sandbox", action="store_true", help="Run gemini CLI with --sandbox")
    sp.add_argument(
        "--gemini-auth-type",
        default="gemini-api-key",
        choices=["gemini-api-key", "oauth-personal", "vertex-ai", "cloud-shell", "compute-default-credentials"],
        help="Auth type for gemini CLI (default: gemini-api-key via isolated HOME + GEMINI_API_KEY).",
    )
    sp.add_argument(
        "--gemini-use-user-home",
        action="store_true",
        help="Use the user's real HOME/.gemini settings (not recommended for automation).",
    )
    sp.add_argument(
        "--gemini-approval-mode",
        choices=["default", "auto_edit", "yolo"],
        default="",
        help="Gemini CLI approval mode (non-interactive default excludes approval tools)",
    )
    sp.add_argument("--gemini-yolo", action="store_true", help="Legacy: pass gemini --yolo (ignored if --gemini-approval-mode is set)")

    sp.add_argument("--timeout-sec", type=int, default=1800, help="Timeout seconds per episode (default: 1800)")
    sp.set_defaults(func=cmd_run)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
