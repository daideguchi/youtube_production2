from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "template_registry.json"
logger = logging.getLogger(__name__)


@dataclass
class TemplateEntry:
    id: str
    label: str
    scope: List[str]
    status: str = "active"


def load_template_registry() -> List[TemplateEntry]:
    if not REGISTRY_PATH.exists():
        logger.warning("Template registry not found: %s", REGISTRY_PATH)
        return []
    try:
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        entries = []
        for item in raw.get("templates", []):
            try:
                entries.append(
                    TemplateEntry(
                        id=item["id"],
                        label=item.get("label", item["id"]),
                        scope=item.get("scope", []),
                        status=item.get("status", "active"),
                    )
                )
            except Exception as exc:  # pragma: no cover
                logger.warning("Skip invalid template entry %s: %s", item, exc)
        return entries
    except Exception as exc:
        logger.error("Failed to load template registry: %s", exc)
        return []


def is_registered_template(path: str) -> bool:
    name = Path(path).name
    return any(e.id == name for e in load_template_registry())


def get_active_templates() -> List[TemplateEntry]:
    return [e for e in load_template_registry() if e.status == "active"]


def resolve_template_path(template_id: str) -> Path:
    path = Path(template_id)
    if not path.is_absolute():
        path = PROJECT_ROOT / "templates" / path.name
    return path
