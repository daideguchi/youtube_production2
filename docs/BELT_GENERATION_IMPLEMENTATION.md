# Belt Generation System Implementation

## Overview
The belt generation system has been successfully updated to use LLMRouter instead of the deprecated google.genai library. This resolves the "cannot import name 'genai' from 'google'" error while maintaining all functionality.

## Key Changes

### 1. New Belt Generator Module
- Created `commentary_02_srt2images_timeline/src/srt2images/belt_generator.py`
- Implements belt generation using LLMRouter with azure_gpt5_mini model
- Takes SRT content and generates Japanese belt titles
- Includes robust JSON parsing and error handling

### 2. Updated LLM Router Configuration
- Added `belt_generation` task to `configs/llm_router.yaml`
- Added `title_generation` task to `configs/llm_router.yaml`
- Both tasks use the standard tier (azure_gpt5_mini as primary model)

### 3. Modified auto_capcut_run.py
- Replaced old `make_llm_belt_from_cues` function that used direct google.genai calls
- Now uses the new belt generator module
- Updated `generate_title_from_cues` function to use LLMRouter
- Implemented graceful error handling - failures don't stop the pipeline

### 4. Backward Compatibility
- Pipeline continues even if belt generation fails
- Returns empty belt configuration as fallback
- No hard-coded belt text (preserving the requirement that all belt text comes from SRT content)

## Technical Details

### Belt Generation Flow
1. `auto_capcut_run.py` calls `generate_belt_from_script` from the new module
2. The new module constructs a prompt from image_cues.json content
3. Uses LLMRouter to call azure_gpt5_mini with "belt_generation" task
4. Parses the JSON response and returns a BeltConfig structure
5. Falls back to empty config if parsing fails

### Error Handling
- If LLM fails to generate belts, the system creates an empty configuration
- The pipeline continues to run (doesn't crash)
- Warning messages are logged for troubleshooting

### Image Generation Logic Preservation
- All changes preserve the existing image generation pipeline
- SectionBreak objects and all their fields maintain the same meaning
- Visual focus and other image generation fields are unaffected

## Files Modified
- `commentary_02_srt2images_timeline/tools/auto_capcut_run.py` - Updated to use new belt generator
- `configs/llm_router.yaml` - Added belt_generation and title_generation tasks
- `commentary_02_srt2images_timeline/src/srt2images/belt_generator.py` - New belt generation module

## Files Created
- `commentary_02_srt2images_timeline/src/srt2images/belt_generator.py`

## Environment Configuration
The system now uses the LLMRouter configuration, which respects:
- `AZURE_OPENAI_API_KEY` environment variable
- `AZURE_OPENAI_ENDPOINT` environment variable
- Configuration in `configs/llm_router.yaml`

No changes required to .env files as the system continues to use the same Azure credentials.