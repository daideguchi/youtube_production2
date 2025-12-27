from __future__ import annotations

import ast
from pathlib import Path

from factory_common.paths import repo_root


FORBIDDEN_TOP_LEVEL_MODULES = {"core", "config", "srt2images", "src"}


def _iter_py_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*.py"):
        if p.name == "__init__.py":
            continue
        out.append(p)
    return sorted(out)


def _find_forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # Relative imports are explicitly allowed (they live under video_pipeline.src.*).
            if getattr(node, "level", 0):
                continue
            if not node.module:
                continue
            top = node.module.split(".", 1)[0]
            if top in FORBIDDEN_TOP_LEVEL_MODULES:
                offenders.append(f"{node.module} (line {node.lineno})")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top in FORBIDDEN_TOP_LEVEL_MODULES:
                    offenders.append(f"{alias.name} (line {node.lineno})")
    return offenders


def test_video_pipeline_has_no_legacy_top_level_imports():
    """
    Prevent reintroducing the old "pseudo top-level packages" that required sys.path hacks:
      - core / config / srt2images / src
    """
    root = repo_root()
    targets = [
        root / "packages" / "video_pipeline" / "src",
        root / "packages" / "video_pipeline" / "tools",
        root / "packages" / "video_pipeline" / "server",
    ]

    failures: list[str] = []
    for t in targets:
        if not t.exists():
            continue
        for p in _iter_py_files(t):
            offenders = _find_forbidden_imports(p)
            if offenders:
                rel = p.relative_to(root).as_posix()
                failures.append(f"{rel}: {offenders}")

    assert not failures, "Legacy imports detected:\n" + "\n".join(failures)
