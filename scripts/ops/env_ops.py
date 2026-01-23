from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat as stat_mod
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

from factory_common.paths import repo_root


_ASSIGN_RE = re.compile(r"^[A-Z0-9_][A-Z0-9_]*=")


@dataclass(frozen=True)
class EnvStatus:
    path: Path
    exists: bool
    mode_str: str
    octal_mode: str
    protected: bool


@dataclass(frozen=True)
class EnvCandidate:
    path: Path
    mtime: float
    size_bytes: int
    assign_lines: int
    known_key_hits: int
    sha256_prefix: str


def env_path() -> Path:
    return repo_root() / ".env"


def env_example_path() -> Path:
    return repo_root() / ".env.example"


def _sha256_prefix(path: Path, *, prefix_len: int = 16) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[: int(prefix_len)]


def _read_text_lossy(path: Path, *, max_bytes: int) -> str:
    data = path.read_bytes()
    if max_bytes > 0:
        data = data[: int(max_bytes)]
    return data.decode("utf-8", errors="ignore")


def _extract_env_keys_from_example() -> Set[str]:
    p = env_example_path()
    if not p.exists():
        return set()
    keys: Set[str] = set()
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Z0-9_][A-Z0-9_]*)=", line)
        if m:
            keys.add(m.group(1))
    return keys


def env_status() -> EnvStatus:
    p = env_path()
    if not p.exists():
        return EnvStatus(path=p, exists=False, mode_str="-", octal_mode="-", protected=False)

    st = p.stat()
    mode_str = stat_mod.filemode(st.st_mode)
    octal_mode = oct(st.st_mode & 0o777)

    flags = int(getattr(st, "st_flags", 0) or 0)
    uf_immutable = int(getattr(stat_mod, "UF_IMMUTABLE", 0) or 0)
    protected = bool(flags & uf_immutable) if uf_immutable else False
    return EnvStatus(path=p, exists=True, mode_str=mode_str, octal_mode=octal_mode, protected=protected)


def protect_env() -> None:
    p = env_path()
    if not p.exists():
        raise FileNotFoundError(f".env not found: {p}")
    if sys.platform == "darwin":
        subprocess.run(["chflags", "uchg", str(p)], check=True)
        return
    chattr = shutil.which("chattr")
    if chattr:
        subprocess.run([chattr, "+i", str(p)], check=True)
        return
    raise RuntimeError("env protect is unsupported on this OS (expected darwin/chflags or linux/chattr)")


def unprotect_env() -> None:
    p = env_path()
    if not p.exists():
        raise FileNotFoundError(f".env not found: {p}")
    if sys.platform == "darwin":
        subprocess.run(["chflags", "nouchg", str(p)], check=True)
        return
    chattr = shutil.which("chattr")
    if chattr:
        subprocess.run([chattr, "-i", str(p)], check=True)
        return
    raise RuntimeError("env unprotect is unsupported on this OS (expected darwin/chflags or linux/chattr)")


def _history_roots(source: str) -> List[Path]:
    home = Path.home()
    roots: List[Path] = []
    want = (source or "auto").strip().lower()
    if want in {"auto", "windsurf"}:
        roots.append(home / "Library" / "Application Support" / "Windsurf" / "User" / "History")
    if want in {"auto", "cursor"}:
        roots.append(home / "Library" / "Application Support" / "Cursor" / "User" / "History")
    return roots


def _iter_files(roots: Sequence[Path], *, max_depth: int = 4) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        root = root.resolve()
        for dirpath, dirnames, filenames in os.walk(root):
            try:
                depth = len(Path(dirpath).relative_to(root).parts)
            except Exception:
                depth = 999
            if depth > int(max_depth):
                dirnames[:] = []
                continue
            for fn in filenames:
                yield Path(dirpath) / fn


def _count_assign_lines(text: str) -> int:
    n = 0
    for raw in text.splitlines():
        if _ASSIGN_RE.match(raw.strip()):
            n += 1
    return n


def _count_known_key_hits(text: str, known: Set[str]) -> int:
    if not known:
        return 0
    hits = 0
    for k in known:
        if f"{k}=" in text:
            hits += 1
    return hits


def find_env_candidates(
    *,
    source: str = "auto",
    min_assign_lines: int = 30,
    min_known_key_hits: int = 5,
    max_bytes: int = 250_000,
    max_depth: int = 4,
    limit: int = 5000,
) -> List[EnvCandidate]:
    known = _extract_env_keys_from_example()
    roots = _history_roots(source)

    out: List[EnvCandidate] = []
    seen = 0
    for p in _iter_files(roots, max_depth=max_depth):
        seen += 1
        if limit > 0 and seen > int(limit):
            break
        try:
            st = p.stat()
        except Exception:
            continue
        if not stat_mod.S_ISREG(st.st_mode):
            continue
        if st.st_size <= 0 or st.st_size > int(max_bytes):
            continue
        try:
            txt = _read_text_lossy(p, max_bytes=max_bytes)
        except Exception:
            continue

        assigns = _count_assign_lines(txt)
        if assigns < int(min_assign_lines):
            continue
        hits = _count_known_key_hits(txt, known)
        if hits < int(min_known_key_hits):
            continue

        try:
            sha = _sha256_prefix(p)
        except Exception:
            sha = "-"
        out.append(
            EnvCandidate(
                path=p,
                mtime=float(st.st_mtime),
                size_bytes=int(st.st_size),
                assign_lines=int(assigns),
                known_key_hits=int(hits),
                sha256_prefix=str(sha),
            )
        )

    out.sort(key=lambda c: (c.mtime, c.known_key_hits, c.assign_lines), reverse=True)
    return out


def format_mtime_utc(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return "-"


def recover_env_from_candidate(
    *,
    candidate: EnvCandidate,
    apply: bool,
    overwrite: bool = False,
) -> str:
    dst = env_path()
    if dst.exists() and not overwrite:
        return f"skip: .env exists (refusing to overwrite) path={dst}"
    if not apply:
        return f"dry-run: would write .env from {candidate.path}"

    dst_bytes = candidate.path.read_bytes()
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(dst_bytes)
    try:
        os.chmod(dst, 0o600)
    except Exception:
        pass
    return f"restored: wrote .env from {candidate.path}"

