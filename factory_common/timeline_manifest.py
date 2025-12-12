from __future__ import annotations

import hashlib
import json
import re
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from factory_common.paths import audio_final_dir, repo_root


MANIFEST_FILENAME = "timeline_manifest.json"
MANIFEST_SCHEMA = "ytm.timeline_manifest.v1"


_EP_RE = re.compile(r"(CH\d{2})[-_]?(\d{3})", re.IGNORECASE)
_SRT_TC_RE = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2})(?P<sms>[\.,]\d{1,3})?\s+-->\s+"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2})(?P<ems>[\.,]\d{1,3})?\s*$"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_relpath(path: Path, root: Path) -> str:
    p = path.expanduser().resolve()
    try:
        return str(p.relative_to(root))
    except Exception:
        return str(p)


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        fr = w.getframerate()
        if not fr:
            return 0.0
        return float(w.getnframes()) / float(fr)


def srt_end_seconds(path: Path) -> float:
    text = path.read_text(encoding="utf-8", errors="ignore")
    end_sec = 0.0
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = _SRT_TC_RE.match(line.strip())
        if not m:
            continue
        eh, em, es, ems = int(m.group("eh")), int(m.group("em")), int(m.group("es")), m.group("ems")
        ms = 0
        if ems:
            frac = (ems[1:] + "000")[:3]
            ms = int(frac)
        end_sec = max(end_sec, eh * 3600 + em * 60 + es + ms / 1000.0)
    return float(end_sec)


def srt_entry_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return len(_SRT_TC_RE.findall(text))


@dataclass(frozen=True)
class EpisodeId:
    channel: str
    video: str  # 3 digits

    @property
    def episode(self) -> str:
        return f"{self.channel}-{self.video}"


def parse_episode_id(text: str) -> Optional[EpisodeId]:
    if not text:
        return None
    m = _EP_RE.search(str(text))
    if not m:
        return None
    return EpisodeId(channel=m.group(1).upper(), video=m.group(2).zfill(3))


def resolve_final_audio_srt(episode: EpisodeId) -> tuple[Path, Path]:
    """
    Resolve audio_tts_v2 final artifacts for an episode.
    Returns (wav_path, srt_path). Raises FileNotFoundError if missing.
    """
    d = audio_final_dir(episode.channel, episode.video)
    wav = d / f"{episode.episode}.wav"
    srt = d / f"{episode.episode}.srt"
    missing = [p for p in (wav, srt) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing final artifacts for {episode.episode}: {', '.join(str(p) for p in missing)}")
    return wav, srt


def build_timeline_manifest(
    *,
    run_dir: Path,
    episode: EpisodeId,
    audio_wav: Path,
    audio_srt: Path,
    image_cues_path: Path,
    belt_config_path: Optional[Path] = None,
    capcut_draft_dir: Optional[Path] = None,
    notes: str = "",
    validate: bool = True,
    tolerance_sec: float = 1.0,
) -> dict[str, Any]:
    root = repo_root()
    run_dir = run_dir.resolve()

    audio_dur = wav_duration_seconds(audio_wav)
    srt_end = srt_end_seconds(audio_srt)
    cues_json = json.loads(image_cues_path.read_text(encoding="utf-8"))
    cues = cues_json.get("cues") or []
    cues_end = float(cues[-1].get("end_sec", 0.0)) if cues else 0.0

    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": _utc_now_iso(),
        "repo_root": str(root),
        "episode": {
            "id": episode.episode,
            "channel": episode.channel,
            "video": episode.video,
        },
        "source": {
            "audio_wav": {
                "path": _safe_relpath(audio_wav, root),
                "sha1": sha1_file(audio_wav),
                "duration_sec": round(audio_dur, 3),
            },
            "audio_srt": {
                "path": _safe_relpath(audio_srt, root),
                "sha1": sha1_file(audio_srt),
                "end_sec": round(srt_end, 3),
                "entries": int(srt_entry_count(audio_srt)),
            },
        },
        "derived": {
            "run_dir": _safe_relpath(run_dir, root),
            "image_cues": {
                "path": _safe_relpath(image_cues_path, run_dir),
                "sha1": sha1_file(image_cues_path),
                "count": int(len(cues)),
                "end_sec": round(cues_end, 3),
                "fps": int(cues_json.get("fps", 30)),
                "size": cues_json.get("size") or {},
                "crossfade": cues_json.get("crossfade", 0.0),
                "imgdur": cues_json.get("imgdur", 0.0),
            },
        },
        "notes": notes or "",
    }

    if belt_config_path and belt_config_path.exists():
        manifest["derived"]["belt_config"] = {
            "path": _safe_relpath(belt_config_path, run_dir),
            "sha1": sha1_file(belt_config_path),
        }
    if capcut_draft_dir and capcut_draft_dir.exists():
        manifest["derived"]["capcut_draft"] = {
            "path": str(capcut_draft_dir),
        }

    if validate:
        validate_timeline_manifest(manifest, run_dir=run_dir, tolerance_sec=tolerance_sec)

    return manifest


def validate_timeline_manifest(
    manifest: dict[str, Any],
    *,
    run_dir: Optional[Path] = None,
    tolerance_sec: float = 1.0,
) -> None:
    """
    Strict validation. Raises ValueError on any mismatch.
    """
    if (manifest or {}).get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"Invalid manifest schema: {manifest.get('schema')}")
    root = Path(manifest.get("repo_root") or repo_root()).expanduser().resolve()
    src = manifest.get("source") or {}
    der = manifest.get("derived") or {}

    wav_p = Path(src.get("audio_wav", {}).get("path") or "")
    srt_p = Path(src.get("audio_srt", {}).get("path") or "")
    if not wav_p.is_absolute():
        wav_p = (root / wav_p).resolve()
    if not srt_p.is_absolute():
        srt_p = (root / srt_p).resolve()

    if not wav_p.exists():
        raise ValueError(f"audio_wav missing: {wav_p}")
    if not srt_p.exists():
        raise ValueError(f"audio_srt missing: {srt_p}")

    wav_dur = wav_duration_seconds(wav_p)
    srt_end = srt_end_seconds(srt_p)
    if abs(wav_dur - srt_end) > tolerance_sec:
        raise ValueError(f"audio/srt duration mismatch: wav={wav_dur:.3f}s srt_end={srt_end:.3f}s (tol={tolerance_sec}s)")

    if run_dir is None:
        run_dir = Path(der.get("run_dir") or "").expanduser()
        if not run_dir.is_absolute():
            run_dir = (root / run_dir).resolve()
    else:
        run_dir = run_dir.resolve()

    cues_rel = der.get("image_cues", {}).get("path") or "image_cues.json"
    cues_p = Path(cues_rel)
    cues_p = (run_dir / cues_p).resolve() if not cues_p.is_absolute() else cues_p
    if not cues_p.exists():
        raise ValueError(f"image_cues missing: {cues_p}")
    cues = json.loads(cues_p.read_text(encoding="utf-8"))
    cues_list = cues.get("cues") or []
    if not cues_list:
        raise ValueError("image_cues.cues is empty")
    cues_end = float(cues_list[-1].get("end_sec", 0.0))
    if abs(cues_end - srt_end) > tolerance_sec:
        raise ValueError(f"cues/srt end mismatch: cues_end={cues_end:.3f}s srt_end={srt_end:.3f}s (tol={tolerance_sec}s)")

    # Basic image existence check
    images_dir = run_dir / "images"
    if images_dir.exists():
        expected = int(len(cues_list))
        missing = []
        for i in range(1, expected + 1):
            p = images_dir / f"{i:04d}.png"
            if not p.exists():
                missing.append(p.name)
                if len(missing) >= 10:
                    break
        if missing:
            raise ValueError(f"Missing images for cues (first 10): {missing}")


def write_timeline_manifest(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    filename: str = MANIFEST_FILENAME,
) -> Path:
    out = run_dir.resolve() / filename
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out

