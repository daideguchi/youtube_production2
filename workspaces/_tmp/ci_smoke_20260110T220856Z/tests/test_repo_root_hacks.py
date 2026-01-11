from __future__ import annotations

from pathlib import Path

from factory_common.paths import repo_root


def _iter_py_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for base in (root / "packages", root / "scripts", root / "apps", root / "tests"):
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            candidates.append(p)
    return candidates


def test_no_brittle_path_file_parent_parent_hacks():
    root = repo_root()

    # Avoid embedding the exact forbidden substring in this file.
    forbidden = "Path(__file__).resolve()" + ".parent" + ".parent"

    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for p in _iter_py_files(root):
        if p == this_file:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if forbidden in text:
            offenders.append(str(p.relative_to(root)))

    assert not offenders, "Found brittle repo-path hacks: " + ", ".join(offenders)

