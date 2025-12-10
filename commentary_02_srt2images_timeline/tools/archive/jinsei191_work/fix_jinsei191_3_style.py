#!/usr/bin/env python3
import sys
from pathlib import Path

# Ensure src is in path
root_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_dir))

try:
    from src.srt2images.image_regenerator import ImageRegenerator
except ImportError:
    sys.path.insert(0, str(Path.cwd()))
    from src.srt2images.image_regenerator import ImageRegenerator

def main():
    run_dir = Path("output/jinsei191_3")
    regenerator = ImageRegenerator(run_dir)
    
    # STRICT STYLE DEFINITION to prevent photorealism
    base_style = """
    STYLE: Warm Japanese digital illustration. Soft textures, gentle brushwork, hand-drawn aesthetic.
    NEGATIVE PROMPT: Photorealistic, live action, photography, 3D render, uncanny valley, harsh realism, photograph, 8k photo.
    """

    # Consistency Group: Masako (6, 7, 16, 19)
    # "Realistic" removed, replaced with "Detailed character illustration"
    masako_desc = """
    CHARACTER: Masako, a 62-year-old Japanese woman. Short, neat, light-grayish/brown hair. Kind, gentle face.
    CLOTHING: Modest, everyday Japanese clothing (simple blouse/apron).
    DRAWING STYLE: Consistent anime-influenced but mature illustration style. Soft lines, warm colors.
    """
    
    # 1: Impact (But Illustration)
    prompt_1 = f"""
    SCENE: A high-impact, emotional opening illustration.
    VISUAL: A dramatic silhouette illustration of an elderly person standing by a window against a stunning, deep茜 (akane/deep orange) sunset.
    ATMOSPHERE: Melancholic but beautiful. The light washes over the room in warm orange tones.
    {base_style}
    """

    prompt_6 = f"""
    SCENE: Masako at the supermarket register, looking distressed.
    VISUAL: Masako (illustrated character) bowing slightly to a colleague. She looks embarrassed.
    BACKGROUND: Supermarket interior, painted in a soft, blurry style.
    {masako_desc}
    {base_style}
    """

    prompt_7 = f"""
    SCENE: Masako on a bus at night.
    VISUAL: Masako sitting on a bus seat, gazing out. The city lights outside are painted as soft, colorful bokeh orbs. Her reflection is visible in the glass.
    {masako_desc}
    {base_style}
    """

    prompt_14 = f"""
    SCENE: Psychological metaphor: Fog lifting.
    VISUAL: Masako's face in a soft illustration style. A grey mist (representing negative thoughts) is slowly clearing away from around her, revealing warm light.
    ATMOSPHERE: Gentle, healing.
    {masako_desc}
    {base_style}
    """

    prompt_16 = f"""
    SCENE: Masako writing.
    VISUAL: Close-up illustration of Masako's hands holding a pen, writing on paper. Her face is visible, looking focused and calm.
    {masako_desc}
    {base_style}
    """

    prompt_19 = f"""
    SCENE: Masako on the bus, happy.
    VISUAL: Masako on the bus again. She is smiling gently at her reflection. The atmosphere is warm and peaceful.
    {masako_desc}
    {base_style}
    """

    prompt_27 = f"""
    SCENE: 'Word Karma' metaphor.
    VISUAL: A beautiful fantasy illustration. Golden light flows from a silhouette's mouth and turns into small, glowing flowers on the ground.
    STYLE: Ethereal storybook illustration. Soft edges, magical atmosphere.
    {base_style}
    """

    # Map indices to new prompts
    tasks = {
        1: prompt_1,
        6: prompt_6,
        7: prompt_7,
        14: prompt_14,
        16: prompt_16,
        19: prompt_19,
        27: prompt_27
    }

    print(f"🎨 Regenerating {len(tasks)} images in STRICT ILLUSTRATION STYLE...")

    for idx, prompt in tasks.items():
        print(f"Processing image {idx}...")
        success = regenerator.regenerate_image(idx, custom_prompt=prompt)
        if success:
            print(f"✅ Success: {idx}")
        else:
            print(f"❌ Failed: {idx}")

if __name__ == "__main__":
    main()
