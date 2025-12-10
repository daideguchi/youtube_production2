# TTS Reading Generation Guidelines

## Core Rule: Standard Japanese Preservation
When generating "readings" (B-Text) for Voicevox/Voicepeak using LLM:

**DO NOT** convert the entire text into Katakana or Hiragana.

### Why?
Voicevox engines use internal dictionaries (MeCab/OpenJTalk) to infer accent and intonation from Kanji/Hiragana mixed text.
If you feed them a string of pure Katakana (e.g. `コニチハワタシハガイジンデス`), they lose context and produce a "Robotic/Flat" reading.

### Correct Strategy
The LLM should only rewrite parts that require disambiguation or normalization:
1.  **Ambiguous Numbers/Counters**: `1日` -> `ついたち` (or `いちにち`).
2.  **Heteronyms (同形異音語)**: `人気` -> `ひとけ` (if context implies), `辛い` -> `つらい`.
3.  **Proper Nouns**: Rare names (e.g. `那古野` -> `なごや`).
4.  **Alphabet**: Convert `AI` -> `エーアイ`, `cm` -> `センチメートル`.

### Implementation
- **Prompt**: Explicitly instructs to replace Heteronyms and Alphabet.
- **Validation**:
    - Rejects **All Katakana** dump (Robot check).
    - Rejects **Latin Characters** (Alphabet check).

### Example
**Bad Output (Rejected):**
`センロッピャクネンカン、ジンルイカラカクサレツヅケテキタ…`

**Good Output (Accepted):**
`1600年間、人類から隠され続けてきた一冊の本があります。`
(Or mixed: `1600年（センロッピャクネン）間...`)
