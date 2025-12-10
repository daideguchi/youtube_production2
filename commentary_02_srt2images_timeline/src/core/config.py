import os
import logging
from pathlib import Path
from typing import Optional

# Setup logging
logger = logging.getLogger(__name__)

class AppConfig:
    """
    Single Source of Truth (SSOT) for application configuration and environment variables.
    Handles loading from .env files and provides typed accessors.
    """

    def __init__(self):
        self._load_env_vars()

    def _load_env_vars(self):
        """
        Load environment variables from .env files with priority:
        1. OS Environment Variables (already loaded)
        2. Local User .env ($HOME/.env)
        3. Project Root .env
        """
        # We don't overwrite existing env vars (OS priority), 
        # so we load files and set only if not set.
        
        # 1. Project Root .env
        # Assuming we are in src/core/config.py, project root is 3 levels up
        project_root = Path(__file__).resolve().parents[3]
        self._load_from_file(project_root / ".env")

        # 2. Local User .env
        self._load_from_file(Path.home() / ".env")

    def _load_from_file(self, path: Path):
        if not path.exists():
            return
        
        try:
            content = path.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip().replace("export ", "")
                    value = value.strip().strip("'").strip('"')
                    
                    if key not in os.environ:
                        os.environ[key] = value
        except Exception as e:
            logger.warning(f"Failed to load env file {path}: {e}")

    @property
    def GEMINI_API_KEY(self) -> str:
        """
        Returns the Gemini API Key.
        Strictly enforces that a key must be present.
        """
        # Check for specific user config file first as requested
        user_config_path = Path(__file__).resolve().parents[3] / ".gemini_config"
        if user_config_path.exists():
            try:
                content = user_config_path.read_text(encoding="utf-8").strip()
                # Expecting format GEMINI_API_KEY=xyz or just key? 
                # The user showed "GEMINI_API_KEY=..."
                for line in content.splitlines():
                    if line.startswith("GEMINI_API_KEY="):
                        return line.split("=", 1)[1].strip()
            except Exception as e:
                logger.warning(f"Failed to read .gemini_config: {e}")

        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError(
                "GEMINI_API_KEY is missing! "
                "Please set it in your environment, .env file, or .gemini_config."
            )
        return key

    @property
    def PROJECT_ROOT(self) -> Path:
        return Path(__file__).resolve().parents[3]

# Global instance
config = AppConfig()
