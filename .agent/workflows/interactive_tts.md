---
description: Run TTS generation interactively via the agent for audio_tts_v2
---

1. Ensure the A-Text (display script) is ready in `workspaces/scripts/<CH>/<VIDEO>/content/assembled.md`.
2. Execute the TTS generation script. The agent will handle the LLM inference for reading and pauses automatically.
// turbo
3. Run the following command (replace `<CHANNEL>` and `<VIDEO_ID>`):
   `PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts --channel <CHANNEL> --video <VIDEO_ID> --input <PATH_TO_ASSEMBLED.MD>`

Example for CH06-001:
`PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts --channel CH06 --video 001 --input workspaces/scripts/CH06/001/content/assembled.md`
