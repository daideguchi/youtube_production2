"""
Repo-level bootstrap shim.

Many operational scripts use `from _bootstrap import bootstrap` to:
  - discover the repo root (pyproject.toml search)
  - ensure repo_root/ and repo_root/packages/ are on sys.path
  - load `.env` fail-soft (does not override existing env vars)

Historically this worked when executing scripts via:
  python3 scripts/<tool>.py
because Python adds the script directory to sys.path.

However, running tools as modules (recommended for some orchestrators):
  python3 -m scripts.<tool>
does not put `scripts/` on sys.path, which would break `_bootstrap` imports.

This file makes `_bootstrap` importable from the repo root (sys.path already
includes repo root for `-m` execution), while delegating to the canonical
implementation in `scripts/_bootstrap.py` when available.
"""

from __future__ import annotations


def bootstrap(*, load_env: bool = True):  # type: ignore[override]
    try:
        from scripts._bootstrap import bootstrap as _bootstrap  # noqa: WPS433 (runtime import)

        return _bootstrap(load_env=load_env)
    except Exception:
        # Fallback: keep behavior roughly equivalent even if `scripts` cannot be imported.
        import os
        import sys
        from pathlib import Path

        def _discover_repo_root(start: Path) -> Path:
            cur = start if start.is_dir() else start.parent
            for candidate in (cur, *cur.parents):
                if (candidate / "pyproject.toml").exists():
                    return candidate.resolve()
            raise RuntimeError("repo root not found (pyproject.toml). Run from inside the repo or set YTM_REPO_ROOT.")

        def _load_env_files(paths: list[Path]) -> None:
            for env_path in paths:
                if not env_path.exists():
                    continue
                try:
                    for line in env_path.read_text(encoding="utf-8").splitlines():
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#") or "=" not in stripped:
                            continue
                        key, value = stripped.split("=", 1)
                        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
                except Exception:
                    continue

        override = os.getenv("YTM_REPO_ROOT") or os.getenv("YTM_ROOT")
        if override:
            repo_root = Path(override).expanduser().resolve()
        else:
            try:
                repo_root = _discover_repo_root(Path.cwd().resolve())
            except Exception:
                repo_root = _discover_repo_root(Path(__file__).resolve())

        for p in (repo_root, repo_root / "packages"):
            if not p.exists():
                continue
            p_str = str(p)
            if p_str not in sys.path:
                sys.path.insert(0, p_str)

        os.environ.setdefault("YTM_REPO_ROOT", str(repo_root))

        if load_env:
            _load_env_files([repo_root / ".env", Path.home() / ".env"])

        return repo_root


__all__ = ["bootstrap"]

