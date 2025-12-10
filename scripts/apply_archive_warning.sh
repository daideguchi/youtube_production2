#!/bin/bash
set -euo pipefail
HDR='> **ARCHIVE NOTE (READ-ONLY)**: 最新仕様・稼働手順は `ssot/README.md` と `ssot/core/DOCS_INDEX.md` を参照してください。本ファイルは歴史的参考資料です。内容を更新/実装の根拠にしないでください。\n\n'
for f in "$@"; do
  if file "$f" | grep -qi 'text'; then
    tmp=$(mktemp)
    printf "%b" "$HDR" > "$tmp"
    cat "$f" >> "$tmp"
    mv "$tmp" "$f"
  fi
done
