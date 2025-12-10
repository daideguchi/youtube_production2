import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict


def parse_size(size_str: str) -> Dict[str, int]:
    m = re.match(r"^(\d+)x(\d+)$", size_str.strip())
    if not m:
        raise ValueError(f"Invalid size format: {size_str}. Use WIDTHxHEIGHT (e.g., 1920x1080)")
    return {"width": int(m.group(1)), "height": int(m.group(2))}


def ensure_out_dirs(out_dir: Path):
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)


def setup_logging(out_dir: Path):
    log_file = out_dir / "logs" / "srt2images.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.info("Logging initialized at %s", log_file)


def save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
