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
    
    # Define specific overrides
    # 1: Impact
    prompt_1 = """
    SCENE: A cinematic, high-impact opening shot.
    VISUAL: A dramatic silhouette of an elderly person against a stunning, deep茜 (akane) sunset. The lighting is emotional and melancholic. The atmosphere conveys the heavy weight of aging but with artistic beauty.
    STYLE: Masterpiece, 8k resolution, highly detailed, dramatic lighting, cinematic composition, emotional.
    NOTE: No text, no thought bubbles. Pure visual storytelling.
    """

    # Consistency Group: Masako (6, 7, 16, 19)
    masako_consistency = """
    CHARACTER: Masako, a 62-year-old Japanese woman. Short, neat, light-grayish/brown hair. Kind, gentle face.
    CLOTHING: Modest, everyday Japanese clothing (e.g., a simple blouse or apron).
    KEY REQUIREMENT: She must look like a realistic, ordinary Japanese woman in her 60s. Consistent facial features. NO distortion.
    """
    
    prompt_6 = f"""
    SCENE: Masako at the supermarket register, looking distressed after a mistake.
    VISUAL: Masako (62yo Japanese woman) bowing slightly in apology to a colleague. She looks embarrassed and pained.
    {masako_consistency}
    """

    prompt_7 = f"""
    SCENE: Masako on a bus at night, looking out the window.
    VISUAL: Masako sitting on a bus seat, gazing at the city lights blurring outside. Her reflection in the window looks sad.
    {masako_consistency}
    """

    prompt_16 = f"""
    SCENE: Masako writing on a paper.
    VISUAL: Close up of Masako's hands and face as she thoughtfully writes. She looks focused and calm.
    {masako_consistency}
    """

    prompt_19 = f"""
    SCENE: Masako on the bus again, but happy.
    VISUAL: Masako on the bus, looking at her reflection with a gentle, relieved smile. The city lights outside look warmer now.
    {masako_consistency}
    """

    # Fix Creepy (14, 27)
    prompt_14 = f"""
    SCENE: Masako realizing the weight of her words.
    VISUAL: Masako's face in a soft, pensive close-up. Instead of scary nets, use a visual metaphor of a gray mist or heavy fog slowly lifting from around her shoulders.
    ATMOSPHERE: Gentle, psychological, safe. NOT scary or dark. Warm, soft lighting.
    {masako_consistency}
    """

    prompt_27 = """
    SCENE: The concept of 'Word Karma' (Kogyo).
    VISUAL: A beautiful, abstract representation. Golden particles of light flowing gently from a person's silhouette, landing on the ground and sprouting into small, glowing flowers.
    ATMOSPHERE: Magical, ethereal, spiritual, warm, healing. NO body horror, no seeds coming out of mouth directly.
    STYLE: Fantasy art, soft focus, dreamlike.
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

    print(f"Regenerating {len(tasks)} images with specific fixes...")

    for idx, prompt in tasks.items():
        print(f"Processing image {idx}...")
        success = regenerator.regenerate_image(idx, custom_prompt=prompt)
        if success:
            print(f"✅ Success: {idx}")
        else:
            print(f"❌ Failed: {idx}")

if __name__ == "__main__":
    main()
