#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

label="wip"
while [ "${1:-}" != "" ]; do
  case "$1" in
    --label)
      label="${2:-wip}"
      shift 2
      ;;
    -h|--help)
      cat <<'USAGE'
save_patch.sh — save current working tree diff as a patch (no git add/commit).

Usage:
  scripts/ops/save_patch.sh --label <name>

Output:
  backups/patches/YYYYMMDD_HHMMSS_<name>.patch

Notes:
  - Includes tracked diffs + untracked TEXT files (allowlist by extension).
  - Intended for environments where git commit is unstable or restricted.
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

# 1) tracked diff
git diff >"$out" || true

# 2) untracked text files (git diff does not include them)
allow_re='\.((py|md|txt|yaml|yml|json|toml|ini|cfg|sh|bash|zsh|tsx|ts|js|css|html|sql|env|lock))$'
while IFS= read -r f; do
  # Skip empty
  [ -n "$f" ] || continue
  if [[ "$f" =~ $allow_re ]]; then
    git diff --no-index -- /dev/null "$f" >>"$out" || true
  else
    echo "[skip] untracked non-text: $f" >&2
  fi
done < <(git ls-files --others --exclude-standard || true)

echo "$out"
echo "[next] git apply \"$out\" && git add -A && git commit -m \"...\""

