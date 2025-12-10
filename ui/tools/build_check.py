#!/usr/bin/env python3
"""UI build & smoke test helper."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Iterable, Optional

YTM_ROOT = Path(__file__).resolve().parents[2]


def run(cmd, cwd: Path) -> None:
    print(f"\033[32m[INFO]\033[0m ({cwd}) {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(cwd), check=True)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="UI build checker")
    parser.add_argument(
        "--backend",
        action="store_true",
        help="Run FastAPI backend checks (pip install -r requirements.txt, python -m compileall).",
    )
    parser.add_argument(
        "--frontend",
        action="store_true",
        help="Run React frontend build (npm install, npm run build).",
    )
    parser.add_argument(
        "--all", action="store_true", help="Run both backend and frontend checks."
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    do_backend = args.backend or args.all or (not args.frontend and not args.backend)
    do_frontend = args.frontend or args.all or (not args.frontend and not args.backend)

    if do_backend:
        backend_dir = YTM_ROOT / "ui" / "backend"
        run(["pip3", "install", "-r", "requirements.txt"], backend_dir)
        run(["python3", "-m", "compileall", "main.py"], backend_dir)

    if do_frontend:
        frontend_dir = YTM_ROOT / "ui" / "frontend"
        run(["npm", "install"], frontend_dir)
        run(["npm", "run", "build"], frontend_dir)

    print("\033[32m[INFO]\033[0m Build check completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
