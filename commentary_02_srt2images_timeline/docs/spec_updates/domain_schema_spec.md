# Domain Schema & Adapter Specification

## Overview
This document specifies the domain schemas and platform adapters used to ensure type safety and platform independence across the YouTube Master system.

## 1. Domain Models (`src/core/domain/`)

### 1.1 Style Schema (`style_schema.py`)
Defines the logical structure of a video style, independent of the rendering engine (CapCut/Remotion).

*   **Typography**: `TextStyle` (Font, Color, Stroke, Background, Logical Position)
*   **Audio**: `AudioMixing` (Volume levels, Ducking)
*   **Visuals**: `VisualStyle` (Fit mode, Zoom, Transitions)
*   **Structure**: `TimelineStructure` (Opening/Ending durations)

### 1.2 Channel Schema (`channel_schema.py`)
Defines the structure of `channel_presets.json`.

*   `ChannelRegistry`: Root object containing all channels.
*   `ChannelConfig`: Individual channel settings.
    *   Links to `VideoStyle` for aesthetic definitions.
    *   Contains pipeline-specific flags (`persona_required`, `image_min_bytes`).

### 1.3 Asset Schema (`asset_schema.py`)
Defines the structure of intermediate asset files (`image_cues.json`).

*   `ImageCuesData`: Sequence of images and subtitles.
*   `ImageCue`: Individual segment data (Timing, Text, Prompt, Path).

## 2. Master Style SSOT (`config/master_styles_v2.json`)

The Single Source of Truth for video aesthetics.
Defined using the `VideoStyle` schema.

**Example (Jinsei Standard V2):**
```json
{
  "styles": {
    "jinsei_standard_v2": {
      "subtitle_style": {
        "font_size_pt": 50.0,
        "text_color": "#FFFFFF",
        "position_y": 0.8
      },
      "platform_overrides": {
        "capcut": { ... }
      }
    }
  }
}
```

## 3. Adapters (`src/adapters/`)

### 3.1 CapCut Adapter (`capcut/style_mapper.py`)
Converts `VideoStyle` objects into CapCut-compatible JSON structures.

*   **Coordinate System**: Inverts Y-axis (Logical 1.0 = Bottom -> CapCut -1.0).
*   **Units**: Scales logical points to CapCut internal font units.

## 4. Usage Guidelines for UI/AI Agents

1.  **Read Configs**: Always load `channel_presets.json` and `master_styles_v2.json` via their respective Pydantic models (`ChannelRegistry`, `VideoStyle`).
2.  **Validation**: Use the models to validate any user input or LLM-generated content before saving.
3.  **No Hardcoding**: Do not embed style values (colors, sizes) in code. Reference the SSOT.
