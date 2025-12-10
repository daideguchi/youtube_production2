#!/usr/bin/env python3
import sys
import json
from pathlib import Path
import logging

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.domain.style_schema import VideoStyle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def validate_styles(json_path: Path):
    if not json_path.exists():
        logger.error(f"File not found: {json_path}")
        return False

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        styles = data.get("styles", {})
        valid_count = 0
        
        for key, style_data in styles.items():
            try:
                # Validate against Pydantic model
                style = VideoStyle(**style_data)
                logger.info(f"✅ Style '{key}' is valid.")
                valid_count += 1
            except Exception as e:
                logger.error(f"❌ Style '{key}' is INVALID: {e}")
        
        logger.info(f"Validation complete. {valid_count}/{len(styles)} styles are valid.")
        return valid_count == len(styles)

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
        return False

if __name__ == "__main__":
    target = Path("config/master_styles_v2.json")
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    
    validate_styles(target)
