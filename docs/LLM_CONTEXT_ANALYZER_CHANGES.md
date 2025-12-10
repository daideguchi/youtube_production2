# LLM Context Analyzer Improvements

## Overview
The LLM Context Analyzer has been enhanced to be more robust against LLM response variations while maintaining the integrity of the image generation logic.

## Key Changes

### 1. Strict Mode Implementation
- Added environment variable `LLM_CONTEXT_ANALYZER_STRICT` to control strict behavior
- When `LLM_CONTEXT_ANALYZER_STRICT=true`, the analyzer will raise RuntimeErrors as before
- When `LLM_CONTEXT_ANALYZER_STRICT=false` (default), the analyzer will use fallback behavior

### 2. Robust JSON Parsing
- Added `_extract_json_content()` helper method to handle various JSON formats
- Supports JSON within code fences (```json ... ```)
- Handles JSON with surrounding text
- Tolerates trailing commas and other minor syntax issues

### 3. Flexible Schema Support
- Accepts both array and object formats:
  - `[ {...}, {...} ]` - direct array of sections
  - `{ "sections": [...] }` - sections wrapped in an object
  - Single section object `{...}` which gets wrapped in an array
- Provides fallback field names for robust parsing

### 4. Improved Error Handling
- Replaced RuntimeErrors with warning logs in most cases
- Returns empty lists instead of crashing when LLM returns no valid sections
- Added fallback segmentation when LLM analysis completely fails

### 5. Image Generation Safety
- Preserved all original SectionBreak field meanings:
  - `visual_focus` remains the primary visual instruction for image generation
  - Field fallbacks ensure `visual_focus` has priority over alternate fields
  - When merging sections, important fields like `visual_focus` are preserved

### 6. Fallback Segmentation
- Added `_create_fallback_sections()` method for when LLM completely fails
- Provides safe default segmentation by evenly dividing SRT segments
- Maintains pipeline flow even when LLM responses are unusable

## Visual Focus Priority
When creating SectionBreak objects, the priority for `visual_focus` is:
1. `entry.get("visual_focus")` - original field from LLM
2. `entry.get("visual")` - fallback 1
3. `entry.get("focus")` - fallback 2

This ensures that if the LLM provides a `visual_focus` field (which is what the image generation logic expects), it will be used unchanged, maintaining the image quality and consistency.