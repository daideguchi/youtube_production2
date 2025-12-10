---
description: Run TTS generation in batch mode (non-interactive)
---

To regenerate audio for a video (or multiple videos), use the `run_tts.py` script directly. This script now mandates LLM inference for high-quality reading and pause generation.

### Strict Auto Mode (Recommended)
This mode enforces mechanical segmentation, fixed pauses, and mandatory B-Text generation.
```bash
PYTHONPATH=audio_tts_v2 \
python3 audio_tts_v2/scripts/run_tts.py \
  --mode auto \
  --channel <CHANNEL> \
  --video <VIDEO_ID> \
  --input content/assembled.md \
  --phase full
```

### Batch (Multiple Videos)
Use a loop or specific batch script if available. 
Common usage for CH06 regeneration:
```bash
# Example for CH06-001
python3 audio_tts_v2/scripts/run_tts.py --channel CH06 --video 001 --input audio_tts_v2/artifacts/final/CH06/001/a_text.txt
```

**Note:** The script will automatically perform:
1. **LLM Segmentation & Reading:** Generating B-Text with correct pronunciation and natural pauses.
2. **Synthesis:** Generating audio using the specified engine (Voicevox/Voicepeak/ElevenLabs).
3. **Alignment:** Producing a synchronized SRT file.
