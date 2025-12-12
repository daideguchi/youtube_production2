#!/usr/bin/env bash
# Regenerate CH05 audio/SRT from latest assembled scripts WITHOUT LLM cost.
# - Uses SKIP_TTS_READING=1 to skip auditor/LLM (dictionary-only).
# - Writes outputs to audio_tts_v2/artifacts/final/CH05/<video>/CH05-<video>.{wav,srt,log.json}
#
# Usage:
#   ./scripts/with_ytm_env.sh bash scripts/regenerate_ch05_audio_no_llm.sh
#
# Note:
#   Voicevox engine must be running locally (default http://127.0.0.1:50021).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANNEL="CH05"

export SKIP_TTS_READING=1

# Preflight: ensure CH05 voice config pins 波音リツ speaker_id=9.
python3 - <<'PY'
import json, pathlib, sys
cfg = pathlib.Path("script_pipeline/audio/channels/CH05/voice_config.json")
if not cfg.exists():
    print("❌ voice_config.json missing for CH05")
    sys.exit(1)
data = json.loads(cfg.read_text(encoding="utf-8"))
key = data.get("default_voice_key")
voice = (data.get("voices") or {}).get(key) or {}
sid = voice.get("voicevox_speaker_id")
char = voice.get("character")
if sid != 9 or char != "波音リツ":
    print(f"❌ CH05 voice config mismatch: character={char} speaker_id={sid} (expected 波音リツ / 9)")
    sys.exit(1)
print("✅ CH05 voice config OK: 波音リツ / speaker_id=9")
PY

# Preflight: Voicevox engine must be up.
python3 - <<'PY'
import json, pathlib, sys, urllib.request
cfg = pathlib.Path("audio_tts_v2/configs/routing.json")
url = "http://127.0.0.1:50021"
if cfg.exists():
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        url = (data.get("voicevox") or {}).get("url") or url
    except Exception:
        pass
try:
    with urllib.request.urlopen(url + "/speakers", timeout=3) as r:
        if r.status != 200:
            raise RuntimeError(r.status)
except Exception as e:
    print(f"❌ Voicevox not reachable at {url}: {e}")
    sys.exit(1)
print(f"✅ Voicevox reachable: {url}")
PY

for n in $(seq 1 30); do
  VIDEO="$(printf "%03d" "$n")"
  CONTENT_DIR="$ROOT_DIR/script_pipeline/data/$CHANNEL/$VIDEO/content"
  INPUT_HUMAN="$CONTENT_DIR/assembled_human.md"
  INPUT_ASSEMBLED="$CONTENT_DIR/assembled.md"

  if [[ -f "$INPUT_HUMAN" ]]; then
    INPUT="$INPUT_HUMAN"
  else
    INPUT="$INPUT_ASSEMBLED"
  fi

  if [[ ! -f "$INPUT" ]]; then
    echo "❌ Missing input for $CHANNEL-$VIDEO: $INPUT"
    exit 1
  fi

  OUT_DIR="$ROOT_DIR/audio_tts_v2/artifacts/final/$CHANNEL/$VIDEO"
  OUT_WAV="$OUT_DIR/${CHANNEL}-${VIDEO}.wav"
  OUT_LOG="$OUT_DIR/log.json"

  mkdir -p "$OUT_DIR"
  rm -rf "$OUT_DIR/chunks" || true

  echo "=================================================="
  echo "🎙️  Regenerating $CHANNEL-$VIDEO (no LLM) from $INPUT"
  echo "=================================================="

  PYTHONPATH="$ROOT_DIR/audio_tts_v2:$ROOT_DIR" python3 "$ROOT_DIR/audio_tts_v2/scripts/run_tts.py" \
    --channel "$CHANNEL" \
    --video "$VIDEO" \
    --input "$INPUT" \
    --out-wav "$OUT_WAV" \
    --log "$OUT_LOG"

  # Keep a_text.txt in sync for downstream tools.
  cp "$INPUT" "$OUT_DIR/a_text.txt"
done

echo "✅ CH05 001-030 audio regeneration complete."
