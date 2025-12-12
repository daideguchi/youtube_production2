import sys
from pathlib import Path
# Add backend dir to sys.path for stub packages (audio, core, app, tools)
backend_dir = Path(__file__).resolve().parents[1]
root_dir = backend_dir.parents[1]
for path in (backend_dir, root_dir):
    p_str = str(path)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)
