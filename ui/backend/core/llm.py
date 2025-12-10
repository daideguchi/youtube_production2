# Minimal stub for LLMFactory and related enums used in tests
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any

class LLMProvider(Enum):
    openai = "openai"
    azure = "azure"
    gemini = "gemini"
    openrouter = "openrouter"

class ModelPhase(Enum):
    caption = "caption"
    phase1 = "phase1"
    phase2 = "phase2"

@dataclass
class ModelConfig:
    provider: LLMProvider
    model: str
    label: str = ""

class _Registry:
    def __init__(self) -> None:
        self.phases = {
            ModelPhase.caption: ModelConfig(provider=LLMProvider.openai, model="gpt-5-chat"),
        }

class LLMFactory:
    _registry = _Registry()

    @classmethod
    def get_registry(cls) -> _Registry:
        return cls._registry
