#!/usr/bin/env python3
"""
pre_push_final_check — SSOT ↔ implementation consistency check before commit/push.

This is a read-only (or minimal-log) checklist intended to be run by humans.
It intentionally avoids touching the thumbnail pipeline (still WIP).

Usage:
  python3 scripts/ops/pre_push_final_check.py
  python3 scripts/ops/pre_push_final_check.py --write-ssot-report
  python3 scripts/ops/pre_push_final_check.py --run-tests
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)


def _run(cmd: list[str]) -> int:
    print(f"$ {' '.join(cmd)}")
    p = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return int(p.returncode)

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _assert_not_contains(path: Path, needle: str) -> int:
    text = _read_text(path)
    if needle in text:
        print(f"[FAIL] SSOT invariant violated: {path} contains forbidden text: {needle!r}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Final SSOT/logic checks before commit/push.")
    ap.add_argument("--write-ssot-report", action="store_true", help="Write ssot_audit JSON under workspaces/logs/ssot/.")
    ap.add_argument("--run-tests", action="store_true", help="Also run a small pytest subset (fast).")
    args = ap.parse_args(argv)

    rc = 0

    # Repo layout + symlink safety (SSOT compliance).
    rc = max(rc, _run([sys.executable, "scripts/ops/repo_sanity_audit.py", "--verbose"]))

    # SSOT index + path/link integrity.
    ssot_cmd = [sys.executable, "scripts/ops/ssot_audit.py", "--path-audit", "--link-audit"]
    if args.write_ssot_report:
        ssot_cmd.append("--write")
    rc = max(rc, _run(ssot_cmd))

    # SSOT invariants (keep the declared SoT consistent with the running implementation).
    flow_doc = REPO_ROOT / "ssot" / "ops" / "OPS_CONFIRMED_PIPELINE_FLOW.md"
    rc = max(rc, _assert_not_contains(flow_doc, "redoフラグは Planning CSV"))

    # LLM hardcode guard (prevent direct provider calls outside LLMRouter/ImageClient).
    rc = max(rc, _run([sys.executable, "scripts/ops/llm_hardcode_audit.py"]))

    # Python syntax guard (catches broken scripts not covered by pytest imports).
    rc = max(
        rc,
        _run(
            [
                sys.executable,
                "-m",
                "compileall",
                "-q",
                "packages",
                "scripts",
                "apps/ui-backend/backend",
            ]
        ),
    )

    # Optional: focused tests (avoid broad suite; keep it fast).
    if args.run_tests:
        if (REPO_ROOT / "tests").exists():
            rc = max(
                rc,
                _run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        "tests/test_repo_root_cleanliness.py",
                        "tests/test_a_text_prompt_injection.py",
                    ]
                ),
            )

    if rc == 0:
        print("[OK] pre-push checks passed")
    else:
        print("[FAIL] pre-push checks failed (see output above)")

    # Reminder: commit/push require `.git` to be unlocked (see ssot/ops/OPS_GIT_SAFETY.md).
    lock_script = Path("scripts/ops/git_write_lock.py")
    if lock_script.exists():
        print(f"note: `.git` lock status -> `{sys.executable} {lock_script} status`")

    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
