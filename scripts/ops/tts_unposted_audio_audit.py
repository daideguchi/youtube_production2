#!/usr/bin/env python3
"""
tts_unposted_audio_audit.py — audit unposted episodes that already have final audio.

Goal (LLM API 不使用):
- Find episodes where existing audio likely has reading issues (VOICEVOX mismatch vs MeCab baseline).
- For VOICEPEAK, auto-proof is impossible → provide risk indicators (unknown/ASCII/digits).
- Detect stale audio (A-text updated after audio was generated) to prioritize regeneration.
- Write aggregated reports (avoid per-episode log spam).

SSOT:
- Audio/TTS ops: ssot/ops/OPS_AUDIO_TTS.md
- Annotation flow (VOICEVOX/VOICEPEAK): ssot/ops/OPS_TTS_ANNOTATION_FLOW.md

Usage:
  ./scripts/with_ytm_env.sh python3 scripts/ops/tts_unposted_audio_audit.py --write-latest
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import (
    audio_final_dir,
    logs_root,
    planning_channels_dir,
    repo_root,
    status_path,
    video_root,
)

from audio_tts.tts.arbiter import get_mecab_reading
from audio_tts.tts.risk_utils import is_trivial_diff


KANJI_RE = re.compile(r"[\u4E00-\u9FFF]")
ASCII_RE = re.compile(r"[A-Za-z]")
DIGIT_RE = re.compile(r"\d")


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _parse_dt(ts: str | None) -> Optional[float]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None


def _is_unposted(progress: str) -> bool:
    p = (progress or "").strip()
    if not p:
        return True
    return "投稿済み" not in p


def _safe_video_no(raw: str) -> Optional[str]:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return None
    return f"{int(digits):03d}"


@dataclass(frozen=True)
class Target:
    channel: str
    video: str
    title: str
    progress: str
    final_dir: Path
    wav_path: Path
    a_text_path: Optional[Path]
    log_path: Optional[Path]
    engine: Optional[str]


def scan_targets() -> list[Target]:
    out: list[Target] = []
    for csv_path in sorted(planning_channels_dir().glob("CH*.csv")):
        channel = csv_path.stem
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not _is_unposted(row.get("進捗", "")):
                    continue
                video_no = _safe_video_no(row.get("動画番号") or "")
                if not video_no:
                    continue
                final_dir = audio_final_dir(channel, video_no)
                wav_path = final_dir / f"{channel}-{video_no}.wav"
                if not wav_path.exists():
                    continue
                a_text = final_dir / "a_text.txt"
                log_path = final_dir / "log.json"
                engine = None
                if log_path.exists():
                    obj = _read_json(log_path) or {}
                    if isinstance(obj.get("engine"), str):
                        engine = obj["engine"]
                out.append(
                    Target(
                        channel=channel,
                        video=video_no,
                        title=row.get("タイトル", ""),
                        progress=row.get("進捗", ""),
                        final_dir=final_dir,
                        wav_path=wav_path,
                        a_text_path=a_text if a_text.exists() else None,
                        log_path=log_path if log_path.exists() else None,
                        engine=engine,
                    )
                )
    return out


def audit_voicevox(target: Target) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not target.log_path:
        return {"error": "missing_log"}, []
    obj = _read_json(target.log_path)
    if not obj:
        return {"error": "log_parse_failed"}, []

    segments = obj.get("segments") or []
    mismatch_examples: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []

    token_source_counts: Counter[str] = Counter()
    local_surfaces: Counter[str] = Counter()

    ascii_unpatched = 0
    digit_unpatched = 0

    for seg_i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        vv = str(seg.get("voicevox") or "")
        if not vv:
            continue
        b_text = str(seg.get("reading") or seg.get("text") or "")
        mecab_now = get_mecab_reading(b_text)
        if not is_trivial_diff(mecab_now, vv):
            mismatches.append(
                {
                    "segment_index": seg_i,
                    "text": seg.get("text", ""),
                    "b_text": b_text,
                    "mecab_now": mecab_now,
                    "voicevox": vv,
                }
            )
            if len(mismatch_examples) < 3:
                mismatch_examples.append(mismatches[-1])

        for tok in seg.get("tokens") or []:
            if not isinstance(tok, dict):
                continue
            surface = str(tok.get("surface") or "")
            src = tok.get("final_reading_source")
            if isinstance(src, str) and src:
                token_source_counts[src] += 1
                if src == "local_dict":
                    local_surfaces[surface] += 1
            else:
                if ASCII_RE.search(surface):
                    ascii_unpatched += 1
                if DIGIT_RE.search(surface):
                    digit_unpatched += 1

    return (
        {
            "engine": obj.get("engine"),
            "timestamp": obj.get("timestamp"),
            "segments": len(segments),
            "mismatch_segments": len(mismatches),
            "mismatch_examples": mismatch_examples,
            "token_source_counts": dict(token_source_counts),
            "top_local_surfaces": [
                {"surface": s, "count": c} for s, c in local_surfaces.most_common(10)
            ],
            "ascii_unpatched_tokens": ascii_unpatched,
            "digit_unpatched_tokens": digit_unpatched,
        },
        mismatches,
    )


def audit_voicepeak(target: Target) -> dict[str, Any]:
    if not target.log_path:
        return {"error": "missing_log"}
    obj = _read_json(target.log_path)
    if not obj:
        return {"error": "log_parse_failed"}

    segments = obj.get("segments") or []
    unknown = 0
    ascii_ct = 0
    digit_ct = 0
    unknown_surfaces: Counter[str] = Counter()

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        for tok in seg.get("tokens") or []:
            if not isinstance(tok, dict):
                continue
            surface = str(tok.get("surface") or "")
            mecab_kana = str(tok.get("mecab_kana") or "")
            if ASCII_RE.search(surface):
                ascii_ct += 1
            if DIGIT_RE.search(surface):
                digit_ct += 1
            if mecab_kana and KANJI_RE.search(mecab_kana):
                unknown += 1
                if len(surface) >= 2:
                    unknown_surfaces[surface] += 1

    return {
        "engine": obj.get("engine"),
        "timestamp": obj.get("timestamp"),
        "segments": len(segments),
        "unknown_tokens": unknown,
        "ascii_tokens": ascii_ct,
        "digit_tokens": digit_ct,
        "top_unknown_surfaces": [
            {"surface": s, "count": c} for s, c in unknown_surfaces.most_common(10)
        ],
    }


def audit_stale(target: Target) -> dict[str, Any]:
    # A-text mtime vs audio completed_at. This is a conservative heuristic.
    st_path = status_path(target.channel, target.video)
    st = _read_json(st_path) or {}
    stages = st.get("stages") if isinstance(st.get("stages"), dict) else {}
    audio_stage = stages.get("audio_synthesis") if isinstance(stages, dict) else None
    details = audio_stage.get("details") if isinstance(audio_stage, dict) else None
    completed_at = _parse_dt(details.get("completed_at") if isinstance(details, dict) else None)

    # Fallback: audio_manifest.json (keeps audit independent from status correctness)
    if completed_at is None:
        mf = _read_json(target.final_dir / "audio_manifest.json") or {}
        completed_at = _parse_dt(mf.get("generated_at") if isinstance(mf.get("generated_at"), str) else None)

    # Resolve A-text path (prefer assembled_human)
    content_dir = video_root(target.channel, target.video) / "content"
    a_text = content_dir / "assembled_human.md"
    if not a_text.exists():
        a_text = content_dir / "assembled.md"

    if completed_at is None or not a_text.exists():
        return {"stale": False, "reason": "missing_time_or_a_text"}

    a_mtime = a_text.stat().st_mtime
    stale = a_mtime > completed_at + 1.0
    return {
        "stale": stale,
        "a_text_path": str(a_text.relative_to(repo_root())),
        "audio_completed_at": completed_at,
        "a_text_mtime": a_mtime,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-latest", action="store_true", help="Write stable *latest* filenames.")
    args = ap.parse_args()

    out_dir = logs_root() / "regression" / "tts_unposted_audio_audit"
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = scan_targets()

    # --- targets.json ---------------------------------------------------------
    by_channel: Counter[str] = Counter(t.channel for t in targets)
    by_engine: Counter[str] = Counter((t.engine or "unknown") for t in targets)
    targets_payload: dict[str, Any] = {
        "generated_at": _now_utc(),
        "count": len(targets),
        "by_channel": dict(sorted(by_channel.items())),
        "by_engine": dict(sorted(by_engine.items())),
        "targets": [
            {
                "channel": t.channel,
                "video": t.video,
                "title": t.title,
                "progress": t.progress,
                "final_dir": str(t.final_dir.relative_to(repo_root())),
                "wav": str(t.wav_path.relative_to(repo_root())),
                "a_text": str(t.a_text_path.relative_to(repo_root())) if t.a_text_path else None,
                "log": str(t.log_path.relative_to(repo_root())) if t.log_path else None,
                "engine": t.engine,
            }
            for t in targets
        ],
    }
    _write_json(out_dir / "targets_latest.json", targets_payload)

    # --- audits --------------------------------------------------------------
    vv_results: list[dict[str, Any]] = []
    vv_mismatch_episodes: list[dict[str, Any]] = []
    vp_results: list[dict[str, Any]] = []
    stale_list: list[dict[str, Any]] = []

    agg_local_surfaces: Counter[str] = Counter()
    mismatch_segments_total = 0

    for t in targets:
        if t.engine == "voicevox":
            audit, mismatches = audit_voicevox(t)
            vv_results.append(
                {
                    "channel": t.channel,
                    "video": t.video,
                    "title": t.title,
                    "progress": t.progress,
                    "final_dir": str(t.final_dir.relative_to(repo_root())),
                    "wav": str(t.wav_path.relative_to(repo_root())),
                    "a_text": str(t.a_text_path.relative_to(repo_root())) if t.a_text_path else None,
                    "log": str(t.log_path.relative_to(repo_root())) if t.log_path else None,
                    "engine": t.engine,
                    "audit": audit,
                }
            )
            if mismatches:
                mismatch_segments_total += len(mismatches)
                vv_mismatch_episodes.append(
                    {
                        "channel": t.channel,
                        "video": t.video,
                        "title": t.title,
                        "mismatch_segments": len(mismatches),
                        "mismatches": mismatches,
                    }
                )
            for item in audit.get("top_local_surfaces") or []:
                agg_local_surfaces[item["surface"]] += int(item["count"])

        elif t.engine == "voicepeak":
            vp_results.append(
                {
                    "channel": t.channel,
                    "video": t.video,
                    "title": t.title,
                    "final_dir": str(t.final_dir.relative_to(repo_root())),
                    "audit": audit_voicepeak(t),
                }
            )

        st = audit_stale(t)
        if st.get("stale") is True:
            stale_list.append({"channel": t.channel, "video": t.video, **st})

    vv_mismatch_episodes.sort(key=lambda x: (-x["mismatch_segments"], x["channel"], x["video"]))

    vv_summary = {
        "episodes_voicevox": sum(1 for t in targets if t.engine == "voicevox"),
        "episodes_with_mismatch": len(vv_mismatch_episodes),
        "mismatch_segments_total": mismatch_segments_total,
        "top_local_surfaces_total": [
            {"surface": s, "count": c} for s, c in agg_local_surfaces.most_common(30)
        ],
    }
    _write_json(out_dir / "voicevox_audit_latest.json", {"summary": vv_summary, "results": vv_results})
    _write_json(
        out_dir / "voicevox_mismatches_latest.json",
        {
            "count_episodes": len(vv_mismatch_episodes),
            "count_mismatch_segments_total": mismatch_segments_total,
            "episodes": vv_mismatch_episodes,
        },
    )

    vp_unknown_eps = sum(
        1 for r in vp_results if int((r.get("audit") or {}).get("unknown_tokens") or 0) > 0
    )
    vp_unknown_total = sum(int((r.get("audit") or {}).get("unknown_tokens") or 0) for r in vp_results)
    vp_summary = {
        "episodes_voicepeak": sum(1 for t in targets if t.engine == "voicepeak"),
        "episodes_with_unknown_tokens": vp_unknown_eps,
        "unknown_tokens_total": vp_unknown_total,
    }
    _write_json(out_dir / "voicepeak_audit_latest.json", {"summary": vp_summary, "results": vp_results})

    stale_list.sort(key=lambda x: (x["channel"], x["video"]))
    _write_json(
        out_dir / "stale_audit_latest.json",
        {"summary": {"targets": len(targets), "stale_count": len(stale_list)}, "stale": stale_list},
    )

    # --- Markdown ------------------------------------------------------------
    md: list[str] = []
    md.append("# 未投稿×既存音声 再監査レポート（latest）")
    md.append("")
    md.append(f"- Generated: {_now_utc()}")
    md.append(f"- Targets (unposted & already has final wav): {len(targets)}")
    md.append(f"  - by_channel: {dict(sorted(by_channel.items()))}")
    md.append(f"  - by_engine: {dict(sorted(by_engine.items()))}")
    md.append("")

    md.append("## VOICEVOX（自動監査）")
    md.append(f"- episodes_with_mismatch: {vv_summary['episodes_with_mismatch']} / {vv_summary['episodes_voicevox']}")
    md.append(f"- mismatch_segments_total: {vv_summary['mismatch_segments_total']}")
    md.append("")

    # Channel breakdown for mismatches
    by_ch = defaultdict(list)
    for ep in vv_mismatch_episodes:
        by_ch[ep["channel"]].append((ep["video"], ep["mismatch_segments"]))
    md.append("### mismatch（チャンネル別・全件）")
    for ch in sorted(by_ch.keys()):
        vids = ",".join([f"{v}({n})" for v, n in sorted(by_ch[ch], key=lambda x: (-x[1], x[0]))])
        md.append(f"- {ch}: {vids}")
    md.append("")

    md.append("### local_dict 反復サーフェス（昇格候補 Top20）")
    for item in vv_summary["top_local_surfaces_total"][:20]:
        md.append(f"- {item['surface']}: {item['count']}")
    md.append("")

    md.append("## VOICEPEAK（自動で誤読確定できないためリスク指標）")
    md.append(f"- episodes_with_unknown_tokens: {vp_summary['episodes_with_unknown_tokens']} / {vp_summary['episodes_voicepeak']}")
    md.append(f"- unknown_tokens_total: {vp_summary['unknown_tokens_total']}")
    md.append("")

    md.append("## STALE（Aテキスト更新後に音声が作られている＝再生成推奨）")
    md.append(f"- stale_count: {len(stale_list)} / {len(targets)}")
    if stale_list:
        by_st = defaultdict(list)
        for s in stale_list:
            by_st[s["channel"]].append(s["video"])
        md.append("")
        md.append("### stale（チャンネル別・全件）")
        for ch in sorted(by_st.keys()):
            md.append(f"- {ch}: {','.join(sorted(by_st[ch]))}")
    md.append("")

    md.append("## 出力ファイル")
    md.append("- targets: workspaces/logs/regression/tts_unposted_audio_audit/targets_latest.json")
    md.append("- voicevox audit: workspaces/logs/regression/tts_unposted_audio_audit/voicevox_audit_latest.json")
    md.append("- voicevox mismatches (full): workspaces/logs/regression/tts_unposted_audio_audit/voicevox_mismatches_latest.json")
    md.append("- voicepeak audit: workspaces/logs/regression/tts_unposted_audio_audit/voicepeak_audit_latest.json")
    md.append("- stale audit: workspaces/logs/regression/tts_unposted_audio_audit/stale_audit_latest.json")
    md.append("")

    (out_dir / "unposted_audio_audit_latest.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"[OK] wrote reports to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
