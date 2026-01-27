#!/usr/bin/env python3
"""
infra_speed_bench — 通信/ストレージの “実測” を残す（Mac-first意思決定用）

目的:
- 「どの端末→どの保存領域が遅いか」を観測事実で固定し、母艦（共有ストレージ）を決裁できるようにする。
- 速度議論を “体感/憶測” から “測定値” に寄せる。

このスクリプトは「測る」だけ（移行/削除/同期はしない）。
結果は JSON で出力し、必要なら `--out` でファイルにも保存する。

例:
  # 256MiB payload を生成（1回だけ）し、2ターゲットで read/write を測る
  python3 scripts/ops/infra_speed_bench.py \\
    --target "Mac SSD=/Users/dd/10_YouTube_Automation/factory_commentary/workspaces/tmp" \\
    --target "Acer workspace(SMB)=/Users/dd/mounts/workspace" \\
    --out workspaces/logs/ops/infra/infra_speed_bench_latest.json

  # ping も一緒に測る（avgのみ）
  python3 scripts/ops/infra_speed_bench.py \\
    --ping 192.168.11.14 --ping 100.98.188.38 \\
    --target "Acer doraemon(SMB)=/Users/dd/mounts/acer_doraemon_smb"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _bootstrap import bootstrap

REPO_ROOT = Path(bootstrap(load_env=False))


def _now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mbps(bytes_count: int, elapsed_sec: float) -> float:
    if elapsed_sec <= 0:
        return 0.0
    return (bytes_count / 1_000_000.0) / elapsed_sec


def _write_random_payload(*, path: Path, size_mib: int) -> None:
    """Write a random payload to `path` (best-effort fsync)."""
    size_bytes = int(size_mib) * 1024 * 1024
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    chunk = 1024 * 1024
    remaining = size_bytes
    with open(tmp, "wb") as f:
        while remaining > 0:
            n = chunk if remaining >= chunk else remaining
            f.write(os.urandom(n))
            remaining -= n
        f.flush()
        os.fsync(f.fileno())

    tmp.replace(path)


def ensure_payload(*, payload_path: Path, size_mib: int, force: bool) -> Path:
    want = int(size_mib) * 1024 * 1024
    if payload_path.exists() and not force:
        try:
            if payload_path.stat().st_size == want:
                return payload_path
        except Exception:
            pass
    _write_random_payload(path=payload_path, size_mib=size_mib)
    return payload_path


def _copy_with_fsync(*, src: Path, dst: Path, chunk_bytes: int) -> float:
    start = time.perf_counter()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        shutil.copyfileobj(fsrc, fdst, length=chunk_bytes)
        fdst.flush()
        os.fsync(fdst.fileno())
    return time.perf_counter() - start


def _read_discard(*, src: Path, chunk_bytes: int) -> float:
    start = time.perf_counter()
    with open(src, "rb") as f:
        while True:
            b = f.read(chunk_bytes)
            if not b:
                break
    return time.perf_counter() - start


def bench_target(
    *,
    label: str,
    root_dir: Path,
    payload_path: Path,
    bench_rel_dir: str,
    keep: bool,
    chunk_mib: int,
) -> Dict[str, Any]:
    bench_dir = root_dir / bench_rel_dir
    dst = bench_dir / "bench_payload.bin"
    size = payload_path.stat().st_size

    # Write
    write_elapsed = _copy_with_fsync(src=payload_path, dst=dst, chunk_bytes=int(chunk_mib) * 1024 * 1024)
    write_mbps = _mbps(size, write_elapsed)

    # Read (NOTE: OS cache affects this. If you need strictness, do remount between write/read.)
    read_elapsed = _read_discard(src=dst, chunk_bytes=int(chunk_mib) * 1024 * 1024)
    read_mbps = _mbps(size, read_elapsed)

    if not keep:
        try:
            dst.unlink()
        except Exception:
            pass

    return {
        "label": label,
        "root_dir": str(root_dir),
        "bench_dir": str(bench_dir),
        "size_bytes": size,
        "write": {"elapsed_sec": write_elapsed, "mbps": write_mbps},
        "read": {"elapsed_sec": read_elapsed, "mbps": read_mbps},
        "note": "read is best-effort (OS cache can inflate). For SMB strict read, unmount+remount then re-run read.",
    }


_PING_AVG_RE = re.compile(r"=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)\s*ms")


def ping_avg_ms(host: str, count: int) -> Optional[float]:
    # macOS: `ping -c N host` and parse "min/avg/max/stddev"
    try:
        proc = subprocess.run(
            ["ping", "-c", str(count), host],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            cwd=str(REPO_ROOT),
        )
    except Exception:
        return None

    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    m = _PING_AVG_RE.search(text)
    if not m:
        return None
    return float(m.group(2))


def _parse_target(spec: str) -> Tuple[str, Path]:
    if "=" not in spec:
        raise ValueError(f"invalid --target (expected LABEL=PATH): {spec}")
    label, raw = spec.split("=", 1)
    label = label.strip()
    raw = raw.strip()
    if not label or not raw:
        raise ValueError(f"invalid --target (empty label/path): {spec}")
    p = Path(raw).expanduser()
    return label, p


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure ping + read/write throughput for configured targets.")
    ap.add_argument("--payload-mib", type=int, default=256, help="Payload size (MiB). Default: 256.")
    ap.add_argument(
        "--payload-path",
        default="",
        help="Override payload file path. Default: <repo>/workspaces/tmp/_bench_payload/payload_<MiB>m.bin",
    )
    ap.add_argument("--force-payload", action="store_true", help="Regenerate payload even if it exists.")
    ap.add_argument(
        "--bench-rel-dir",
        default="_bench_speed",
        help="Relative directory created under each target root to place bench file. Default: _bench_speed",
    )
    ap.add_argument("--chunk-mib", type=int, default=1, help="Copy/read chunk size (MiB). Default: 1.")
    ap.add_argument("--keep", action="store_true", help="Keep the bench file on targets (default: delete).")
    ap.add_argument("--target", action="append", default=[], help="Benchmark target as LABEL=PATH (repeatable).")
    ap.add_argument("--ping", action="append", default=[], help="Ping host/IP and record avg(ms) (repeatable).")
    ap.add_argument("--ping-count", type=int, default=8, help="Ping count. Default: 8.")
    ap.add_argument("--out", default="", help="Write JSON to file path (in addition to stdout).")
    args = ap.parse_args()

    payload_path = Path(str(args.payload_path).strip()).expanduser() if str(args.payload_path).strip() else None
    if payload_path is None:
        payload_path = REPO_ROOT / "workspaces" / "tmp" / "_bench_payload" / f"payload_{int(args.payload_mib)}m.bin"

    payload_path = ensure_payload(payload_path=payload_path, size_mib=int(args.payload_mib), force=bool(args.force_payload))

    targets: List[Dict[str, Any]] = []
    for spec in list(args.target or []):
        label, root = _parse_target(spec)
        targets.append(
            bench_target(
                label=label,
                root_dir=root,
                payload_path=payload_path,
                bench_rel_dir=str(args.bench_rel_dir),
                keep=bool(args.keep),
                chunk_mib=int(args.chunk_mib),
            )
        )

    pings: List[Dict[str, Any]] = []
    for host in list(args.ping or []):
        avg = ping_avg_ms(host, count=int(args.ping_count))
        pings.append({"host": host, "count": int(args.ping_count), "avg_ms": avg})

    out: Dict[str, Any] = {
        "generated_at": _now_iso_utc(),
        "payload": {"path": str(payload_path), "size_bytes": payload_path.stat().st_size},
        "pings": pings,
        "targets": targets,
    }

    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)

    out_path = str(args.out).strip()
    if out_path:
        p = Path(out_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

