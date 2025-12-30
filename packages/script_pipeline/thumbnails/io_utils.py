from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from PIL import Image


PngOutputMode = Literal["draft", "final"]


@dataclass(frozen=True)
class PngSaveOptions:
    optimize: bool
    compress_level: int


def resolve_png_save_options(mode: PngOutputMode) -> PngSaveOptions:
    if mode == "draft":
        # Faster for iteration (no quality loss; only affects PNG compression work/size).
        return PngSaveOptions(optimize=False, compress_level=1)
    if mode == "final":
        # Smaller outputs; slower but acceptable for final delivery.
        return PngSaveOptions(optimize=True, compress_level=6)
    raise ValueError(f"unknown PNG output mode: {mode!r}")


def save_png_atomic(
    img: Image.Image,
    path: Path,
    *,
    mode: PngOutputMode = "final",
    optimize: Optional[bool] = None,
    compress_level: Optional[int] = None,
    verify: bool = True,
) -> None:
    """
    Save a PNG to `path` via temp file + replace to avoid truncated/partial files.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    opts = resolve_png_save_options(mode)
    effective_optimize = opts.optimize if optimize is None else bool(optimize)
    effective_compress = opts.compress_level if compress_level is None else int(compress_level)

    tmp_path: Optional[Path] = None
    handle = tempfile.NamedTemporaryFile(
        prefix=f"{path.name}.",
        suffix=path.suffix + ".tmp",
        dir=str(path.parent),
        delete=False,
    )
    try:
        tmp_path = Path(handle.name)
    finally:
        handle.close()

    try:
        img.save(
            tmp_path,
            format="PNG",
            optimize=effective_optimize,
            compress_level=max(0, min(9, effective_compress)),
        )
        if verify:
            with Image.open(tmp_path) as probe:
                probe.verify()
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

