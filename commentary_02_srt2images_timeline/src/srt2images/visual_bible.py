# Visual Bible Generator
# Generates character/setting consistency rules from SRT segments using LLM.

import json
import logging
import os
from pathlib import Path
from typing import List, Dict, Any

from factory_common.llm_router import get_router

logger = logging.getLogger("VisualBible")

VISUAL_BIBLE_PATH = Path(__file__).resolve().parents[3] / "data" / "visual_bible.json"

BIBLE_GEN_PROMPT = """
You are an expert art director creating a 'Visual Bible' for an illustrated video series.
Input: A full Japanese script (SRT segments).

Goal: Identify recurring characters and key settings that must remain visually consistent.
Output: A JSON object with "characters" and "settings" arrays.

Schema:
{{
  "characters": [
    {{
      "name": "Name or Role (e.g. 'Old Man', 'Yuki')",
      "description": "Visual description (Age, ethnicity, hair style/color, clothing style/color, distinct features). Be specific and concise.",
      "consistency_rules": "Key traits to never change (e.g. 'Always wears red scarf', 'Round glasses')."
    }}
  ],
  "settings": [
    {{
      "name": "Location Name (e.g. 'Living Room', 'Park')",
      "description": "Visual atmosphere, lighting, key objects, era/style (e.g. 'Showa era tatami room, warm sunset light')."
    }}
  ]
}}

Rules:
1. Only include MAIN recurring characters. Ignore one-off background mobs.
2. If the narrator is a character (e.g. "Me"), define them.
3. Use English for descriptions (better for image generation prompts).
4. Keep descriptions concrete and visual (avoid abstract personality traits unless they affect appearance).
5. If no specific characters appear (abstract narration), return empty arrays.

Script:
{script_text}
"""

class VisualBibleGenerator:
    def __init__(self):
        self.router = get_router()

    def generate(self, segments: List[Dict], force_refresh: bool = False) -> Dict[str, Any]:
        """
        Generate or load visual bible.
        If file exists and not force_refresh, return loaded data.
        Otherwise, call LLM to generate.
        """
        # Ensure data dir exists
        VISUAL_BIBLE_PATH.parent.mkdir(parents=True, exist_ok=True)

        if VISUAL_BIBLE_PATH.exists() and not force_refresh:
            try:
                data = json.loads(VISUAL_BIBLE_PATH.read_text(encoding="utf-8"))
                logger.info(f"Loaded existing Visual Bible from {VISUAL_BIBLE_PATH}")
                return data
            except Exception as e:
                logger.warning(f"Failed to load existing bible: {e}. Regenerating.")

        logger.info("Generating new Visual Bible from script...")
        
        # Combine segments for context
        full_text = "\n".join([f"[{s['index']}] {s['text']}" for s in segments])
        # Truncate if too long (approx 20k chars safety limit for context window)
        if len(full_text) > 30000:
            full_text = full_text[:30000] + "...(truncated)"

        prompt = BIBLE_GEN_PROMPT.format(script_text=full_text)
        
        try:
            response = self.router.call(
                task="visual_persona", # Reusing visual_persona task config
                messages=[{"role": "user", "content": prompt}],
                response_format="json_object",
                temperature=0.2
            )
            
            bible_data = self._parse_response(response)
            
            # Save
            VISUAL_BIBLE_PATH.write_text(json.dumps(bible_data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"Saved Visual Bible to {VISUAL_BIBLE_PATH}")
            return bible_data

        except Exception as e:
            logger.error(f"Failed to generate Visual Bible: {e}")
            return {"characters": [], "settings": []}

    def _parse_response(self, text: str) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Simple salvage logic
            import re
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except:
                    pass
            logger.warning("Could not parse JSON from bible response. Returning empty.")
            return {"characters": [], "settings": []}

# Simple CLI for testing
if __name__ == "__main__":
    # Mock segments
    segments = [
        {"index": 1, "text": "昔々、あるところに住むおじいさんは、毎日山へ芝刈りに行きました。", "start": 0, "end": 5},
        {"index": 2, "text": "おじいさんは赤い手ぬぐいを頭に巻くのがトレードマークでした。", "start": 5, "end": 10},
        {"index": 3, "text": "山奥の小屋は古く、囲炉裏にはいつも火が灯っていました。", "start": 10, "end": 15}
    ]
    gen = VisualBibleGenerator()
    bible = gen.generate(segments, force_refresh=True)
    print(json.dumps(bible, indent=2))
