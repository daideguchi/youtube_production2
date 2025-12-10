---
description: Run TTS generation interactively via the agent for audio_tts_v2
---

1. Ensure the A-Text (display script) is ready in `content/assembled.md` (or specified path).
2. Execute the TTS generation script. The agent will handle the LLM inference for reading and pauses automatically.
// turbo
3. Run the following command (replace `<CHANNEL>` and `<VIDEO_ID>`):
   `python3 audio_tts_v2/scripts/run_tts.py --channel <CHANNEL> --video <VIDEO_ID> --input <PATH_TO_ASSEMBLED.MD>`

Example for CH06-001:
`python3 audio_tts_v2/scripts/run_tts.py --channel CH06 --video 001 --input audio_tts_v2/artifacts/final/CH06/001/a_text.txt` 
(or usually `content/assembled.md`)
