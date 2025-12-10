# Turbo Mode: Logic & Architecture
**技名: ターボモード (Turbo Mode)**

This document defines the logic and execution path for the "Manual Intervention" (High-Speed Generation) technique.

## 1. Core Concept
Standard generation relies on LLM (Azure OpenAI) for reading disambiguation, which is high-quality but slow and API-limited.
**Turbo Mode** bypasses the LLM entirely for the "B-Text Generation" phase, strictly using:
1.  **MeCab (Local morphological analysis)**: For base Katakana conversion.
2.  **Agent Rules (Regex)**: For correcting known entities (e.g., `IBM`, `Leonardo da Vinci`) and removing artifacts (e.g., `・`).

## 2. Benefits
-   **Speed**: ~0.5 seconds per script (vs 30-60s for LLM).
-   **Stability**: Zero API dependency. No timeouts.
-   **Control**: 100% deterministic (same input = same output).

## 3. The Pipeline (The "Move")

### Phase 1: Preparation (Dump)
Script: `audio_tts_v2/scripts/dump_segments.py`
-   Loads `content/assembled.md`.
-   Splits text into chunks based on punctuation/headers.
-   Output: `temp_srt_blocks_CHXX_YYY.json` (Raw Text).

### Phase 2: The "Intervention" (Local Inference)
Script: `audio_tts_v2/scripts/local_inference.py`
-   **Input**: Temp JSON.
-   **Process**:
    -   `MeCab`: Parse text -> Extract Reading/Pronunciation features.
    -   `Rule Filter`: Apply `agent_write_b_text.py` dictionary replacements (`IBM` -> `アイビーエム`).
    -   `Sanitization`: Remove middle dots (`・`), trim spaces.
-   **Output**: `srt_blocks.json` (Draft B-Text).

### Phase 2.5: The "Review" (AI Audit) [NEW]
Script: `audio_tts_v2/scripts/audit_b_text.py`
-   **Input**: Draft B-Text.
-   **Process**:
    -   Pass text to a fast LLM (e.g., `gpt-4o-mini`).
    -   **Instruction**: "Proofread this. Remove remaining dots. Fix unnatural Latin readings."
    -   **Safety Net**: Final regex check to ensure middle dots are gone.
-   **Output**: `srt_blocks.json` (Certified B-Text).

### Phase 3: Synthesis (Voicevox)
Script: `audio_tts_v2/scripts/run_tts.py`
-   Detects existing `srt_blocks.json`.
-   **Skips LLM**: Because B-Text exists.
-   Sends text to Voicevox Engine.
-   Output: `.wav` audio files.

## 4. Trigger
Activate this logic using the agent command:
`/turbo [Channel] [VideoIDs]`

This ensures purely mechanical, high-speed mass production suitable for recovering delays or handling large backlogs (e.g., CH09's 31 scripts).
