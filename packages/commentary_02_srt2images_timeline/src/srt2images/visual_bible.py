# Visual Bible Generator
# Generates character/setting consistency rules from SRT segments using LLM.

import json
import logging
import os
from pathlib import Path
from typing import List, Dict, Any

from factory_common.llm_router import get_router
from factory_common.paths import repo_root

logger = logging.getLogger("VisualBible")

_LEGACY_GLOBAL_BIBLE_PATH = repo_root() / "data" / "visual_bible.json"


def _resolve_visual_bible_path(out_dir: Path | None) -> Path:
    """
    Prefer per-run storage to avoid cross-channel leakage.
    Falls back to legacy global path only when out_dir is not provided.
    """
    if out_dir:
        return out_dir / "visual_bible.json"
    logger.warning("VisualBible: out_dir not provided; using legacy global cache (deprecated): %s", _LEGACY_GLOBAL_BIBLE_PATH)
    return _LEGACY_GLOBAL_BIBLE_PATH

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

    def generate(self, segments: List[Dict], out_dir: Path | None = None, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Generate or load visual bible.
        If file exists and not force_refresh, return loaded data.
        Otherwise, call LLM to generate.
        """
        bible_path = _resolve_visual_bible_path(out_dir)
        bible_path.parent.mkdir(parents=True, exist_ok=True)

        if bible_path.exists() and not force_refresh:
            try:
                data = json.loads(bible_path.read_text(encoding="utf-8"))
                logger.info("Loaded existing Visual Bible from %s", bible_path)
                return data
            except Exception as e:
                logger.warning(f"Failed to load existing bible: {e}. Regenerating.")

        logger.info("Generating new Visual Bible from script...")
        
        # Combine segments for context
        # parse_srt() returns segments without explicit "index", so fall back to enumeration.
        full_text = "\n".join(
            [
                f"[{s.get('index', i + 1)}] {s.get('text', '')}"
                for i, s in enumerate(segments)
            ]
        )
        # Truncate if too long (approx 20k chars safety limit for context window)
        if len(full_text) > 30000:
            full_text = full_text[:30000] + "...(truncated)"

        prompt = BIBLE_GEN_PROMPT.format(script_text=full_text)
        
        try:
            response = self.router.call(
                task="visual_bible",  # Dedicated task config (JSON, higher cap)
                messages=[{"role": "user", "content": prompt}],
                response_format="json_object",
                temperature=0.2
            )
            
            bible_data = self._parse_response(response)
            
            # Save
            bible_path.write_text(json.dumps(bible_data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("Saved Visual Bible to %s", bible_path)
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
