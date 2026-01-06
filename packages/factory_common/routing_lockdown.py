from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable, List

from factory_common import paths as repo_paths


def _truthy(value: object) -> bool:
    if value is True:
        return True
    if value in (None, "", 0, False):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def routing_lockdown_enabled() -> bool:
    """
    Operator safety switch.

    Default: ON (prevents non-slot "ad-hoc overrides" that cause drift across agents).
    Set `YTM_ROUTING_LOCKDOWN=0` to temporarily restore legacy behavior.
    """
    raw = (os.getenv("YTM_ROUTING_LOCKDOWN") or "").strip()
    if raw == "":
        return True
    return _truthy(raw)


def emergency_override_enabled() -> bool:
    """
    Emergency bypass for debugging.

    When enabled, forbidden overrides are allowed for *this run*.
    """
    return _truthy((os.getenv("YTM_EMERGENCY_OVERRIDE") or "").strip())


def lockdown_active() -> bool:
    return routing_lockdown_enabled() and not emergency_override_enabled()


def _present(env_name: str) -> bool:
    return bool((os.getenv(str(env_name)) or "").strip())


def assert_env_absent(env_names: Iterable[str], *, context: str, hint: str) -> None:
    """
    Under lockdown, forbid certain env overrides that can silently change routing.
    """
    if not lockdown_active():
        return
    present: List[str] = [n for n in env_names if _present(n)]
    if not present:
        return
    joined = ", ".join(present)
    raise RuntimeError(
        "\n".join(
            [
                "[LOCKDOWN] Forbidden override env var(s) detected.",
                f"- context: {context}",
                f"- present: {joined}",
                "- policy: Use numeric slots/codes only (no model-name or direct mode overrides).",
                f"- hint: {hint}",
                "- emergency: set YTM_EMERGENCY_OVERRIDE=1 for this run (debug only)",
                "- legacy: set YTM_ROUTING_LOCKDOWN=0 to disable lockdown (not recommended)",
            ]
        )
    )


def assert_no_llm_model_overrides(*, context: str) -> None:
    """
    Under lockdown, forbid explicit model-chain overrides.

    Allowed:
      - LLM_MODEL_SLOT (numeric)
      - legacy numeric-only LLM_FORCE_MODELS (treated as slot id by some entrypoints)
    Forbidden:
      - LLM_FORCE_MODELS / LLM_FORCE_MODEL (non-numeric)
      - LLM_FORCE_TASK_MODELS_JSON
    """
    if not lockdown_active():
        return

    forced_all = (os.getenv("LLM_FORCE_MODELS") or os.getenv("LLM_FORCE_MODEL") or "").strip()
    forced_task = (os.getenv("LLM_FORCE_TASK_MODELS_JSON") or "").strip()

    bad: List[str] = []
    if forced_all and not forced_all.isdigit():
        bad.append("LLM_FORCE_MODELS/LLM_FORCE_MODEL")
    if forced_task:
        bad.append("LLM_FORCE_TASK_MODELS_JSON")
    if not bad:
        return

    raise RuntimeError(
        "\n".join(
            [
                "[LOCKDOWN] Forbidden LLM model override detected.",
                f"- context: {context}",
                f"- present: {', '.join(bad)}",
                "- policy: Use LLM_MODEL_SLOT (numeric) for all LLM routing. Do not pin model keys/names.",
                "- hint: use --llm-slot <N> (or set LLM_MODEL_SLOT=<N>)",
                "- emergency: set YTM_EMERGENCY_OVERRIDE=1 for this run (debug only)",
            ]
        )
    )


def _git_diff_clean(repo: Path, rel_path: str) -> bool | None:
    try:
        proc = subprocess.run(
            ["git", "diff", "--quiet", "--", rel_path],
            cwd=str(repo),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode == 0:
            return True
        if proc.returncode == 1:
            return False
        return None
    except Exception:
        return None


def assert_task_overrides_unchanged(*, context: str) -> None:
    """
    Under lockdown, forbid ad-hoc edits to task routing config.

    Why:
    - Model rewrites in tracked YAML cause drift across agents and wasted spend.
    - Runtime switching must happen via slots/codes, not by editing the SSOT files.
    """
    if not lockdown_active():
        return

    repo = repo_paths.repo_root()
    rel = "configs/llm_task_overrides.yaml"
    path = repo / rel
    if not path.exists():
        return

    # Hard-ban: azure_gpt5_mini must never be selected via task overrides (fallbackでも不可).
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    banned = ["az-gpt5-mini-1", "azure_gpt5_mini"]
    found = [b for b in banned if b in text]
    if found:
        raise RuntimeError(
            "\n".join(
                [
                    "[LOCKDOWN] Forbidden model key detected in task overrides.",
                    f"- context: {context}",
                    f"- file: {rel}",
                    f"- found: {', '.join(found)}",
                    "- policy: model rewrites are forbidden (fallbackでも不可). Use slots/codes only.",
                    "- hint: revert configs/llm_task_overrides.yaml and rerun (or set YTM_EMERGENCY_OVERRIDE=1 for debug only).",
                ]
            )
        )

    clean = _git_diff_clean(repo, rel)
    if clean is False:
        raise RuntimeError(
            "\n".join(
                [
                    "[LOCKDOWN] Uncommitted changes detected in task overrides.",
                    f"- context: {context}",
                    f"- file: {rel}",
                    "- policy: do not edit tracked routing YAML during ops. Use slots/codes only.",
                    "- hint: revert the file and rerun (or set YTM_EMERGENCY_OVERRIDE=1 for debug only).",
                ]
            )
        )
