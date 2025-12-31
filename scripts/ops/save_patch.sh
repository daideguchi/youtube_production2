#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

label="wip"
all=false
declare -a paths=()
declare -a excludes=()
while [ "${1:-}" != "" ]; do
  case "$1" in
    --label)
      label="${2:-wip}"
      shift 2
      ;;
    --all)
      all=true
      shift
      ;;
    --path|--paths)
      paths+=("${2:?missing --path value}")
      shift 2
      ;;
    --exclude)
      excludes+=("${2:?missing --exclude value}")
      shift 2
      ;;
    -h|--help)
      cat <<'USAGE'
save_patch.sh — save current working tree diff as a patch (no git add/commit).

Usage:
  scripts/ops/save_patch.sh --label <name> [--path <pathspec> ...] [--exclude <pathspec> ...]
  scripts/ops/save_patch.sh --label <name> --all [--exclude <pathspec> ...]

Output:
  backups/patches/YYYYMMDD_HHMMSS_<name>.patch

Notes:
  - Includes tracked diffs + untracked TEXT files (allowlist by extension).
  - Path filtering is applied to both tracked/untracked; use --exclude to omit noisy runtime files.
  - Intended for environments where git commit is unstable or restricted.
  - Multi-agent safety:
      - If active locks exist and --path is omitted, this defaults to *your* active lock scopes (LLM_AGENT_NAME).
      - Use --all only when you intentionally want a full-repo patch snapshot.
USAGE
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

ts="$(date +%Y%m%d_%H%M%S)"
mkdir -p backups/patches
out="backups/patches/${ts}_${label}.patch"

# Auto-scope to my active lock scopes when running in a parallel multi-agent repo.
# This reduces accidental "everything.patch" that includes other agents' in-progress work.
if [ "${#paths[@]}" -eq 0 ] && [ "$all" != "true" ]; then
  agent="${LLM_AGENT_NAME:-${AGENT_NAME:-}}"
  agent="${agent#"${agent%%[![:space:]]*}"}"
  agent="${agent%"${agent##*[![:space:]]}"}"

  any_active="0"
  declare -a lock_scopes=()
  while IFS= read -r line; do
    if [[ "$line" == ANY_ACTIVE=* ]]; then
      any_active="${line#ANY_ACTIVE=}"
      continue
    fi
    [ -n "$line" ] || continue
    lock_scopes+=("$line")
  done < <(
    YTM_REPO_ROOT="$ROOT" \
    LLM_AGENT_NAME="$agent" \
    PYTHONPATH="$ROOT:$ROOT/packages" \
    python3 - <<'PY'
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from factory_common.agent_mode import get_queue_dir


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


agent = (os.environ.get("LLM_AGENT_NAME") or os.environ.get("AGENT_NAME") or "").strip()
now = datetime.now(timezone.utc)
locks_dir = get_queue_dir() / "coordination" / "locks"

any_active = False
scopes: set[str] = set()
if locks_dir.exists():
    for fp in locks_dir.glob("*.json"):
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        exp = parse_iso(obj.get("expires_at"))
        if exp is not None and exp <= now:
            continue
        any_active = True
        if agent and str(obj.get("created_by") or "").strip() == agent:
            raw_scopes = obj.get("scopes") or []
            if not isinstance(raw_scopes, list):
                raw_scopes = [raw_scopes]
            for s in raw_scopes:
                s = str(s).replace("\\", "/").strip()
                if s:
                    scopes.add(s)

print(f"ANY_ACTIVE={1 if any_active else 0}")
for s in sorted(scopes):
    print(s)
PY
  )

  if [ "${#lock_scopes[@]}" -gt 0 ]; then
    paths=("${lock_scopes[@]}")
    echo "[info] save_patch.sh: scoped to my active lock scopes (${#paths[@]})" >&2
  elif [ "$any_active" = "1" ]; then
    cat >&2 <<EOF
[FAIL] save_patch.sh refused to create an unscoped patch while active locks exist.

Fix:
  - Set agent identity: export LLM_AGENT_NAME=dd-<area>-01
  - Take a lock for what you touched: python3 scripts/agent_org.py lock 'path/**' --mode no_touch --ttl-min 60
  - Or pass explicit scopes: scripts/ops/save_patch.sh --label <name> --path 'path/**'
  - Or (break-glass) request full snapshot: scripts/ops/save_patch.sh --label <name> --all
EOF
    exit 2
  fi
fi

# git pathspec (include + exclude)
declare -a pathspec=()
if [ "${#paths[@]}" -gt 0 ]; then
  pathspec+=("${paths[@]}")
elif [ "$all" = "true" ]; then
  pathspec+=(".")
else
  pathspec+=(".")
fi
if [ "${#excludes[@]}" -gt 0 ]; then
  for ex in "${excludes[@]}"; do
    pathspec+=(":(exclude)${ex}")
  done
fi

# 1) tracked diff
# - Use rename detection to keep large moves (e.g., workspaces cutover) patchable.
# - Include binary diffs for safety (db, images, etc).
git diff --binary -M -- "${pathspec[@]}" >"$out" || true

# 2) untracked text files (git diff does not include them)
allow_re='(\.gitignore$|\.((py|md|txt|yaml|yml|json|toml|ini|cfg|sh|bash|zsh|tsx|ts|js|css|html|sql|env|lock)))$'
while IFS= read -r f; do
  # Skip empty
  [ -n "$f" ] || continue
  if [[ "$f" =~ $allow_re ]]; then
    git diff --no-index -- /dev/null "$f" >>"$out" || true
  else
    echo "[skip] untracked non-text: $f" >&2
  fi
done < <(git ls-files --others --exclude-standard -- "${pathspec[@]}" || true)

echo "$out"
echo "[next] git apply \"$out\" && git add -A && git commit -m \"...\""
