# Channel Configuration & CapCut Style Specification

## Overview
This document defines the schema and behavior for channel-specific configurations used in the automated video generation pipeline. It specifically details the `channel_presets.json` structure and the `capcut_settings` object used to enforce consistent styling across different video series.

## File Location
*   **Configuration**: `config/channel_presets.json`
*   **Resolver**: `src/config/channel_resolver.py`
*   **Implementation**: `tools/capcut_bulk_insert.py`

## Schema Definitions

### Channel Preset Object
Each channel key (e.g., `CH01`, `CH02`) maps to a configuration object:

| Field | Type | Description |
| :--- | :--- | :--- |
| `name` | string | Human-readable channel name. |
| `style` | string | Visual style description for image generation prompts. |
| `capcut_template` | string | Name of the base CapCut draft to duplicate. |
| `position` | object | Image transformation settings (`tx`, `ty`, `scale`). |
| `belt` | object | Configuration for the text belt (overlay) layer. |
| `capcut_settings` | object | **[New]** Detailed styling overrides for CapCut elements. |

### CapCut Settings Object (`capcut_settings`)
Defines overrides for subtitle styling, timeline positioning, and other draft-specific parameters. If omitted, hardcoded defaults or template settings may apply (though hardcoding is deprecated).

```json
"capcut_settings": {
  "subtitle": { ... },
  "timeline": { ... }
}
```

#### 1. Subtitle Settings (`subtitle`)
Controls the appearance of the generated SRT subtitle track.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `font_size` | float | `5.0` | Font size (CapCut internal unit). |
| `text_color` | hex | `#FFFFFF` | Font color. |
| `line_spacing` | float | `0.02` | Line spacing ratio. |
| `stroke_color` | hex | `#000000` | Text outline color. |
| `stroke_width` | float | `0.00016` | Text outline width. |
| `border_width` | float | `0.08` | Outer border width (distinct from stroke). |
| `position` | object | `{"x":0, "y":-0.8}` | Screen position (`y=-1.0` is bottom, `y=1.0` is top). |

**Background Settings (`subtitle.background`)**
PyJianYingDraft mapping: `background_style=1` (Solid)

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `enabled` | bool | `true` | Whether to enable the background box. |
| `color` | hex | `#000000` | Background color. |
| `alpha` | float | `1.0` | Opacity (0.0 - 1.0). |
| `round_radius` | float | `0.4` | Corner rounding radius. |
| `width` | float | `0.28` | Box width ratio relative to text/screen. |
| `height` | float | `0.28` | Box height ratio. |
| `horizontal_offset` | float | `-1.0` | **Critical** offset for tight fitting. |
| `vertical_offset` | float | `-1.0` | **Critical** offset for tight fitting. |

#### 2. Timeline Settings (`timeline`)
Controls the temporal arrangement of assets.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `opening_offset_sec` | float | `3.0` | Duration to skip at start before placing Voice/Subs/Images. |

## Implementation Notes
*   **Post-Processing**: Some styles (specifically `background_horizontal_offset` and `vertical_offset`) may not apply correctly via the standard `pyJianYingDraft` API. A post-processing step (`fix_subtitle_style_direct`) scans the generated `draft_content.json` and forcefully applies these JSON parameters.
*   **Hierarchy**: `capcut_settings` in JSON > Hardcoded Defaults.
