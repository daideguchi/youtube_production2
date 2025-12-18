import os
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    override = os.getenv("YTM_REPO_ROOT") or os.getenv("YTM_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


# Add backend dir to sys.path for stub packages (audio, core, app, tools)
backend_dir = Path(__file__).resolve().parent.parent
repo_root = _find_repo_root(backend_dir)
packages_root = repo_root / "packages"
for path in (backend_dir, repo_root, packages_root):
    p_str = str(path)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)
