# CH02 CapCut Draft Creation Process

This document details the complete workflow for creating CapCut drafts for the "哲学系" (Philosophy) channel (CH02) from SRT subtitle files.

## 🔄 Complete Workflow from SRT to CapCut Draft

The process follows this sequence:

### 1. Input and Configuration
- **Input**: SRT subtitle file (e.g., `CH02-015.srt`)
- **Channel ID**: CH02 with specific configuration from `config/channel_presets.json`
- **Entry Point**: `tools/factory.py` or `tools/auto_capcut_run.py`

### 2. Pipeline Execution (`tools/run_pipeline.py`)
The main pipeline executes these steps:

#### 2.1. SRT Parsing (`src/srt2images/srt_parser.py`)
- Parse the SRT file to extract timecodes and text segments
- Create a list of subtitle segments with start/end times

#### 2.2. Cue Generation (`src/srt2images/cue_maker.py`)
- Convert SRT segments into image cues based on timing
- For CH02, uses channel-specific parameters with 0.0 opening offset
- Creates a structured list of cues with timing information

#### 2.3. Visual Bible Generation (`src/srt2images/visual_bible.py`)
- Analyzes the script content to identify characters and visual elements
- Creates persona information for consistent character representation

#### 2.4. Contextual Prompt Refinement (`src/srt2images/llm_prompt_refiner.py`)
- Uses LLMRouter to enhance prompts with context and style information
- Integrates channel-specific style guidelines

#### 2.5. Role-based Asset Application (`src/srt2images/role_asset_router.py`)
- Applies channel-specific assets based on character roles

#### 2.6. Diversity Hint Generation
- Adds variation suggestions to avoid repetitive compositions
- Ensures different angles, distances, poses, and lighting across consecutive images

#### 2.7. Prompt Building (`src/srt2images/prompt_builder.py`)
- Builds detailed image generation prompts using:
  - Visual focus information from context analysis
  - Scene summaries
  - Emotional tone
  - Role guidance
  - Channel-specific style overrides
- Uses the "watercolor_gold_blue_strict.txt" template for CH02

#### 2.8. Image Generation
- Generates images based on the constructed prompts
- Uses Gemini 2.5 Flash for image generation with watercolor style
- Saves images to the output directory (`images/` folder)

#### 2.9. JSON Output
- Creates `image_cues.json` with the complete metadata:
  - Timing information (start_sec, end_sec)
  - Prompts for image generation
  - Summaries and visual focus

### 3. CapCut Draft Creation (`tools/auto_capcut_run.py`)

#### 3.1. Belt Configuration Generation (`src/srt2images/belt_generator.py`)
- Uses LLMRouter to analyze image cues and generate Japanese belt titles
- For CH02, configures belt generation with:
  - Belt mode: "main_only" (only 1 main belt, no subsections)
  - Opening offset: 0.0 seconds (no delay before content starts)
  - Generates time-based belt markers

#### 3.2. Title Generation
- Creates a Japanese YouTube title from the image cues
- Uses LLM to generate titles that are 18-28 characters, calm and trustworthy

#### 3.3. CapCut Draft Building (`tools/capcut_bulk_insert.py`)
- Duplicates the CH02 template (`CH02-テンプレ`)
- Inserts generated images with precise timing from image cues
- Applies watercolor-style positioning and scaling
- Adds subtitles from the original SRT file
- Updates belt tracks with generated titles
- Synchronizes `draft_info.json` and `draft_content.json`
  - Important: CapCut stores some template-only styling in `draft_info.json` segments (e.g., `belt_main_track` clip transform/scale + `extra_material_refs` for the blue belt background effect). The sync logic must **merge** rather than overwrite to avoid wiping the belt design.

#### 3.5. Belt Design Recovery (if a draft loses the right-top belt styling)
- If a generated draft shows the main belt style reset (position/background disappears), restore it from the template:
  - `python tools/restore_template_belt_design.py --template 'CH02-テンプレ' --draft-regex '^(...)_draft$'`
  - This copies the template `belt_main_track` segment `clip`/`extra_material_refs` and the referenced `materials.effects` into the target draft's `draft_info.json`, without changing timing.

#### 3.4. Title Injection (`tools/inject_title_json.py`)
- Directly injects the title into the CapCut JSON files
- Ensures the title appears at the beginning of the video

### 4. CH02-Specific Configuration

From `config/channel_presets.json`, CH02 has these settings:

#### Basic Information:
- **Name**: "哲学系" (Philosophy)
- **Status**: Active
- **Opening Offset**: 0.0 seconds (content starts immediately)

#### Image Generation:
- **Template**: "templates/watercolor_gold_blue_strict.txt"
- **Style**: "calm watercolor + soft oil glaze, warm-gold light vs cool blue-gray stone, no text"
- **Prompt Suffix**: Emphasizes watercolor style, no text/characters by default, Japanese/Asian features when needed
- **Tone Profile**: Calm, contemplative, nostalgic with film grain
- **Character Policy**: No characters by default; only when explicitly in script

#### Visual Style:
- **Watercolor technique** with soft bleeding edges
- **Thin, pale brown-to-gray lines** with occasional breaks
- **Washi paper texture** across the whole frame
- **Warm colors**: Cream to honey gold, burnt sienna, muted oranges
- **Cool colors**: Gray-blue to blue-gray, slate gray, muted greens
- **Lighting**: Diffused backlight, highlighting with golden particles
- **No text, no UI, no signs** in the generated images

#### CapCut Settings:
- **Template**: "CH02-テンプレ"
- **Position**: Default (tx: 0.0, ty: 0.0, scale: 1.0)
- **Layout**: Standard positioning with 82% belt top percentage
- **Belt Configuration**: Main-only mode, max 1 section

### 5. Image Generation Process

The watercolor style is implemented with these specific elements:

#### Painting Technique:
- Digital watercolor with thin, pale brown-to-gray lines
- Soft color fields and bleeding edges instead of hard outlines
- Washi-like paper grain texture across the frame

#### Color Palette:
- Warm side: Cream, honey gold, burnt sienna, muted orange, terracotta brown
- Cool side: Gray-blue, blue-gray, slate gray, slightly green-tinted muted blue
- Warm & cool contrast: High lightness medium saturation cream/gold light vs low-to-medium lightness blue-gray/gray-blue shadows

#### Light and Atmosphere:
- Diffused backlight and ambient light instead of a single light source
- Soft highlights with fine golden particles around highlights
- Mist/haze with depth (atmospheric perspective): higher brightness, lower saturation, lower contrast in the background

#### Brushwork and Texture:
- Watercolor "edging": Slight density accumulation at the outer edge of dark colors
- Layering: Thin color → dry slightly → slightly darker color → shadow, transparent watercolor-style glazes in stages
- Granulation: Particle effect in blue-gray and shadows to recreate the depth of analog paint

### 6. Output Files

The pipeline creates these files in the output directory:
- `image_cues.json`: Complete metadata with timing and prompts
- `belt_config.json`: Generated belt titles and timing
- `images/`: Generated image files (0001.png, 0002.png, etc.)
- `capcut_draft/`: CapCut project files
- `capcut_draft_info.json`: Reference information for the CapCut draft
- `channel_preset.json`: Used channel configuration
- `auto_run_info.json`: Execution log and statistics

### 7. Key Features for CH02

1. **No Opening Delay**: Content starts immediately (0.0s offset)
2. **Watercolor Style**: Consistent artistic approach across all images
3. **Minimal Characters**: No people by default, only when script requires
4. **Abstract/Metaphorical Focus**: Emphasizes concepts over literal interpretations
5. **Nostalgic Atmosphere**: Film grain and color treatment creates contemplative mood
6. **Single Main Belt**: Only one main belt title instead of multiple sections

This process creates a complete CapCut draft that matches the philosophical, contemplative nature of CH02 content with a consistent watercolor aesthetic.
