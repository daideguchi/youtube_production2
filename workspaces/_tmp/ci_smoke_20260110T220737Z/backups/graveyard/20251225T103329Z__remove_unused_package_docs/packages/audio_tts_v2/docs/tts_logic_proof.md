# TTS Pipeline Logic & Flow Validation

## 1. Core Logic (The "100-Point" Engine)
All modes (Auto/Interactive) share this indestructible processing core.

### A. Reading Logic (Hybrid 3-Layer)
Based on `audio_tts_v2/tts/llm_adapter.py` (prompt rules) + the TTS pipeline’s downstream constraints.
1.  **Layer 1: User Priority**: `Text（Reading）` -> Extracted to `Reading`. (Code: Prompt Rule #2)
2.  **Layer 2: LLM Correction**: Numbers, English, Symbols, Heteronyms -> Converted to Kana. (Code: Prompt Rule #3, #4)
3.  **Layer 3: MeCab Standard**: Common Kanji -> Left as Kanji for Voicevox natural intonation. (Code: Prompt Rule #1)
4.  **Validation / Guard (現状)**:
    - 実行時に強制する「Validator」は未統合（設計はあるが、フローに組み込まれていない）。
    - 現状は `tests/` と運用監査（SSOTの読み監査）で “おかしな読み” を検出する。

### B. Pause Logic (Strict Mechanical)
Based on `audio_tts_v2/tts/orchestrator.py`.
- **Headings**: 1.0s (Fixed)
- **Paragraphs**: 0.75s
- **Sentences**: 0.3s
- **Commas**: 0.25s
- **No AI Guessing**: Pauses are strictly determined by syntax (`#`, `\n`, `。`, `、`).

---

## 2. Flow Definitions (The "Two Routes")

### Route 1: Non-Interactive (Auto/Batch)
**Goal**: Mass production with zero interruptions.
**Flow**:
1.  **Input**: `a_text.txt`.
2.  **Split**: Mechanical segmentation (SRT blocks).
3.  **Reading**: LLM applies "100-Point" logic.
4.  **Synthesis**: Voicevox generates audio.
5.  **Output**: WAV + SRT (Synchronized).

### Route 2: Interactive (Manual Verification)
**Goal**: Human quality assurance before synthesis.
**Flow**:
1.  **Input**: `a_text.txt`.
2.  **Split**: Mechanical segmentation.
3.  **Reading**: LLM applies "100-Point" logic.
4.  **User Review (STOP)**: System pauses (if implemented via CLI or Agent step) to allow human inspection of `srt_blocks.json`.
    - *Current Implementation*: `run_tts.py` supports `--phase srt_only` which stops here.
    - *Agent Workflow*: Run `srt_only`, `notify_user` with JSON, User approves, Run `full`.
5.  **Synthesis**: Voicevox generates audio using *approved* JSON.

---

## 3. Proof of Implementation

### Code Evidence
- **Prompt**: `llm_adapter.py` Lines 737-750 (Strict Rules).
- **Headings**: `orchestrator.py` (Validation of `#`).

### Artifact Evidence
- **Test ID 997**:
    - Input: `AI（エーアイ）` -> Output: `エーアイ`. (Proves English/Paren logic)
    - Input: `100%` -> Output: `100パーセント`. (Proves Symbol logic)
    - Input: `1600年間` -> Output: `1600年` (Kanji preserved). (Proves MeCab logic)
