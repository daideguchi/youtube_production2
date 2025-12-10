---
description: Execute "Turbo Mode" (Local Inference) to instantly generate B-Text and synthesized audio, bypassing LLM latency.
---

# Turbo Mode (Local Inference Acceleration)

This workflow executes the "Manual Intervention" technique:
1.  **Dump Segments**: Prepares JSON inputs from source markdown.
2.  **Local Inference**: Uses MeCab + Regex Rules to generate Katakana readings instantly (Bypassing LLM).
3.  **Synthesis**: Immediately runs Voicevox synthesis.

**Usage**:
`/turbo [CHANNEL] [VIDEO_ID_START] [VIDEO_ID_END]` (Range)
or
`/turbo [CHANNEL] [VIDEO_ID]` (Single)

## Logic Steps

1.  **Environment Setup**: Ensure `PYTHONPATH` includes `audio_tts_v2`.
2.  **Loop Execution**:
    - Run `dump_segments.py`.
    - Run `local_inference.py` (MeCab).
    - Run `run_tts.py` (Voicevox).

## Command Template

```bash
# Example for a specific list
export PYTHONPATH=$(pwd)
for vid in {START..END}; do
    # Pad with zeros
    v=$(printf "%03d" $vid)
    echo "============================================"
    echo "⚡️ TURBO MODE ACTIVATED: ${CHANNEL}-${v}"
    echo "============================================"
    
    # 1. Dump
    python audio_tts_v2/scripts/dump_segments.py --channel ${CHANNEL} --video $v
    
    # 2. Local Inference (The "Trick")
    python audio_tts_v2/scripts/local_inference.py temp_srt_blocks_${CHANNEL}_$v.json

    # 2.5. AI Audit (Refining)
    echo "🔍 AI Reviewing..."
    python audio_tts_v2/scripts/audit_b_text.py audio_tts_v2/artifacts/final/${CHANNEL}/$v/srt_blocks.json
    
    # 3. Synthesize
    # Ensure specific speaker IDs if needed (e.g. CH05=9)
    # Default fallback is usually fine via routing.json
    python audio_tts_v2/scripts/run_tts.py --channel ${CHANNEL} --video $v
done
```

## Special Speaker Rules
- **CH05**: `export NAMINE_SPEAKER_ID=9`
- **CH09**: Uses default (Voicevox:青山流星) unless specified.
