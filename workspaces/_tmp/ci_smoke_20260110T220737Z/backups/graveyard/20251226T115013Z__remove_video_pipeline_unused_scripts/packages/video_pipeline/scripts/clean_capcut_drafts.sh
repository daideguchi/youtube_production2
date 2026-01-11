#!/usr/bin/env bash
set -euo pipefail

# Base path for CapCut drafts
BASE="$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft"
ARCHIVE="$HOME/Movies/CapCut/Archive_$(date +%Y%m%d_%H%M%S)"

# Patterns to keep explicitly
KEEP_PATTERNS=(
  "★0??_CH06-*"
  "CH06-00?_ダーク図書館_capcut_v10"
  "CH06-00?_capcut_v10"
  "CH06_テンプレ*"
  "CH06-001_ダーク図書館テンプレ"
  "CH06-UNK_テンプレ用"
  "CH01_人生の道標_191_完成版"
  "CH01-UNK_道標_最新テンプレ"
  "CH02-UNK_テンプレ"
  "CH03-000_シニアの朗読テンプレ"
  "CH04-UNK_*"
  "CH05-XXX_シニア恋愛物語_完璧版"
  "CH05-XXX_シニア恋愛物語_日本人版_完璧版"
  "CH05-UNK_恋愛テンプレ*"
)

# Latest N drafts to keep per channel (simple heuristic)
KEEP_LATEST_PER_CHANNEL=5

mkdir -p "$ARCHIVE"

echo "Archiving to: $ARCHIVE"

cd "$BASE"

is_kept() {
  local name="$1"
  for pat in "${KEEP_PATTERNS[@]}"; do
    if [[ "$name" == $pat ]]; then
      return 0
    fi
  done
  return 1
}

# Build list
ALL=()
while IFS= read -r entry; do
  ALL+=("$entry")
done < <(ls -1)

# Collect per-channel latest (simple: sort by mtime) without associative arrays or mapfile
KEEP_SET=()
channels=$(printf "%s\n" "${ALL[@]}" | sed 's/_.*//' | grep -E '^CH[0-9]{2}' | sort | uniq)
for chan in $channels; do
  lines=$(for entry in "${ALL[@]}"; do
    if [[ "${entry%%_*}" == "$chan" ]]; then
      ts=$(stat -f "%m" "$entry")
      printf "%s %s\n" "$ts" "$entry"
    fi
  done | sort -rn | head -n "$KEEP_LATEST_PER_CHANNEL")
  while IFS= read -r ln; do
    fname="${ln#* }"
    KEEP_SET+=("$fname")
  done <<< "$lines"
done

move_entry() {
  local entry="$1"
  mv "$entry" "$ARCHIVE/$entry"
  echo "Archived: $entry"
}

for entry in "${ALL[@]}"; do
  if is_kept "$entry"; then
    echo "Keep (pattern): $entry"
    continue
  fi
  keep_flag=0
  for k in "${KEEP_SET[@]}"; do
    if [[ "$entry" == "$k" ]]; then
      keep_flag=1
      break
    fi
  done
  if [[ $keep_flag -eq 1 ]]; then
    echo "Keep (latest per channel): $entry"
    continue
  fi
  move_entry "$entry"
done

echo "Done. Archived items are in $ARCHIVE"
