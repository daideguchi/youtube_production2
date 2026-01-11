#!/usr/bin/env python3
"""
Regenerate the final SRT from strict TTS log.json (without re-synthesizing audio).

Why:
  - Fine-tune subtitle cue splitting / timing allocation while keeping audio as-is.
  - Uses the measured per-segment durations in final/log.json as the timing base (SoT).

Usage examples:
  python3 scripts/regenerate_srt_from_log.py --channel CH22 --from 23 --to 30 --apply
  python3 scripts/regenerate_srt_from_log.py --channel CH22 --videos 023 024 --apply
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from _bootstrap import bootstrap

bootstrap()

from factory_common.paths import audio_final_dir  # noqa: E402
from factory_common.timeline_manifest import sha1_file, srt_end_seconds, srt_entry_count, wav_duration_seconds  # noqa: E402

from audio_tts.tts.strict_structure import AudioSegment  # noqa: E402
from audio_tts.tts.strict_synthesizer import generate_srt  # noqa: E402
from audio_tts.tts.routing import load_default_voice_config, load_routing_config, resolve_voicevox_speaker_id  # noqa: E402
from audio_tts.tts.voicevox_api import VoicevoxClient  # noqa: E402


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_video_tokens(
    *, videos: Optional[List[str]], from_: int, to: int
) -> List[str]:
    if videos:
        out: List[str] = []
        for v in videos:
            digits = "".join(ch for ch in str(v) if ch.isdigit())
            if not digits:
                raise ValueError(f"invalid video token: {v}")
            out.append(f"{int(digits):03d}")
        return out
    if from_ > to:
        return []
    return [f"{i:03d}" for i in range(int(from_), int(to) + 1)]


def _build_segments_from_log(log_payload: Dict[str, Any]) -> List[AudioSegment]:
    segs = log_payload.get("segments") or []
    if not isinstance(segs, list) or not segs:
        raise ValueError("log.json: segments is empty/invalid")
    out: List[AudioSegment] = []
    for idx, s in enumerate(segs):
        if not isinstance(s, dict):
            continue
        out.append(
            AudioSegment(
                text=str(s.get("text") or ""),
                reading=(str(s.get("reading")) if s.get("reading") is not None else None),
                pre_pause_sec=float(s.get("pre") or 0.0),
                post_pause_sec=float(s.get("post") or 0.0),
                is_heading=bool(s.get("heading")),
                heading_level=0,
                original_line_index=int(s.get("section_id", idx)),
                duration_sec=float(s.get("duration") or 0.0),
                mecab_reading=str(s.get("mecab") or ""),
                voicevox_reading=str(s.get("voicevox") or ""),
                arbiter_verdict=str(s.get("verdict") or ""),
            )
        )
    if not out:
        raise ValueError("log.json: no valid segments parsed")
    return out


def _resolve_voicevox(channel: str) -> Tuple[Optional[VoicevoxClient], int, Optional[Dict[str, Any]]]:
    voice_cfg = load_default_voice_config(channel) or {}
    speaker_id = 0
    if isinstance(voice_cfg, dict) and voice_cfg.get("voicevox_speaker_id") is not None:
        speaker_id = int(voice_cfg["voicevox_speaker_id"])
    else:
        speaker_id = resolve_voicevox_speaker_id(channel)
    routing = load_routing_config()
    client = VoicevoxClient(engine_url=routing.voicevox_url)
    return client, speaker_id, (voice_cfg if isinstance(voice_cfg, dict) else None)


def _maybe_update_audio_manifest(final_dir: Path, *, srt_path: Path) -> None:
    manifest_path = final_dir / "audio_manifest.json"
    if not manifest_path.exists():
        return
    try:
        manifest = _read_json(manifest_path)
    except Exception:
        return

    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifacts, dict):
        return
    srt_meta = artifacts.get("srt") if isinstance(artifacts.get("srt"), dict) else None
    if srt_meta is None:
        return

    srt_meta["sha1"] = sha1_file(srt_path)
    srt_meta["end_sec"] = round(float(srt_end_seconds(srt_path)), 3)
    srt_meta["entries"] = int(srt_entry_count(srt_path))
    manifest["artifacts"] = artifacts
    # Keep original generated_at, but record a separate stamp for SRT regeneration.
    manifest["srt_regenerated_at"] = _utc_now_iso()
    _write_json(manifest_path, manifest)


def _assert_srt_clean(srt_text: str) -> None:
    """
    Guardrail: reject subtitle cue boundaries that look like mid-phrase cuts.
    """
    blocks = [b.strip() for b in re.split(r"\r?\n\r?\n", str(srt_text or "")) if b.strip()]

    def _body(block: str) -> str:
        lines = str(block).splitlines()
        if len(lines) < 3:
            return ""
        return "\n".join(lines[2:]).strip()

    texts = [_body(b) for b in blocks]

    def _is_kanji(ch: str) -> bool:
        return "\u4e00" <= ch <= "\u9fff"

    def _is_hiragana(ch: str) -> bool:
        return "\u3041" <= ch <= "\u309f"

    _OPEN = set("（(「『【〈《[")
    _CLOSE = set("）)」』】〉》]")
    _PUNCT = set("、，,;；:：。．.!！?？…")
    _SMALL = set("ぁぃぅぇぉゃゅょっァィゥェォャュョッー")
    _SENTENCE_END = set("。．.!！?？…")

    def _compact(s: str) -> str:
        return str(s or "").replace("\r", "").replace("\n", "")

    def _effective_last_char(s: str) -> str:
        t = _compact(s).rstrip()
        while t and t[-1] in _CLOSE:
            t = t[:-1].rstrip()
        return t[-1] if t else ""

    try:
        from audio_tts.tts.mecab_tokenizer import tokenize_with_mecab  # type: ignore
    except Exception:
        tokenize_with_mecab = None  # type: ignore

    bad: list[tuple[str, int, str, str]] = []

    # In-cue line breaks must not split words (e.g., 話\nす, 思\nい).
    for cue_idx, t in enumerate(texts, start=1):
        if "\n" not in t:
            continue
        for j, ch in enumerate(t):
            if ch != "\n":
                continue
            if j <= 0 or j + 1 >= len(t):
                continue
            prev = t[j - 1]
            nxt = t[j + 1]
            if not prev or not nxt:
                continue
            if nxt in _PUNCT or nxt in _CLOSE or nxt in _SMALL:
                bad.append(("in", cue_idx, _compact(t[max(0, j - 14) : j]), _compact(t[j + 1 : j + 15])))
                break
            if _is_kanji(prev) and nxt and (_is_hiragana(nxt) or _is_kanji(nxt)):
                if prev not in _PUNCT and prev not in _CLOSE:
                    bad.append(("in", cue_idx, _compact(t[max(0, j - 14) : j]), _compact(t[j + 1 : j + 15])))
                    break
    for i in range(len(texts) - 1):
        a = texts[i]
        b = texts[i + 1]
        if not a or not b:
            continue

        last = a[-1]
        b_compact = _compact(b)
        first = b_compact[0] if b_compact else ""
        last_effective = _effective_last_char(a)

        # Mid-word/compound split: Kanji + (Hiragana/Kanji) across cue boundary.
        if _is_kanji(last) and first and (_is_hiragana(first) or _is_kanji(first)):
            if last not in _PUNCT and last not in _CLOSE:
                bad.append(("after", i + 1, _compact(a[-14:]), b_compact[:14]))
                continue

        # Hard bad starts by character.
        if first in _PUNCT or first in _CLOSE or first in _SMALL:
            bad.append(("after", i + 1, _compact(a[-14:]), b_compact[:14]))
            continue

        # Token-based bad starts (particles/auxiliaries).
        if tokenize_with_mecab is not None:
            try:
                toks = tokenize_with_mecab(b_compact)
                if toks:
                    pos0 = str(toks[0].get("pos") or "")
                    surf0 = str(toks[0].get("surface") or "")
                    if pos0 in {"助詞", "助動詞"}:
                        allow = False
                        # Allow some discourse-y starts only when the previous cue ends with punctuation.
                        # e.g., "...より深く、 | より正確に..." / "...ある。 | だったら..."
                        if last_effective in _PUNCT or last in _CLOSE:
                            if pos0 == "助詞" and surf0 in {"より", "という", "と"}:
                                allow = True
                            # Discourse connector: "かといって" can be tokenized as a leading particle "か".
                            # Allow only when the previous cue ends a sentence (prevents mid-phrase breaks).
                            elif (
                                pos0 == "助詞"
                                and surf0 == "か"
                                and last_effective in _SENTENCE_END
                                and (b_compact.startswith("かといって") or b_compact.startswith("かと言って"))
                            ):
                                allow = True
                            elif pos0 == "助動詞" and b_compact.startswith("だったら") and last_effective in _SENTENCE_END:
                                allow = True
                            elif pos0 == "助動詞" and b_compact.startswith("だとしたら") and last_effective in _SENTENCE_END:
                                allow = True
                            elif pos0 == "助動詞" and b_compact.startswith("べき") and last_effective in _SENTENCE_END:
                                allow = True
                            elif pos0 == "助動詞" and b_compact.startswith("なのに") and last_effective in _SENTENCE_END:
                                allow = True
                        if not allow:
                            bad.append(("after", i + 1, _compact(a[-14:]), b_compact[:14]))
                            continue
                    if pos0 == "記号" and surf0 and surf0[0] not in _OPEN:
                        bad.append(("after", i + 1, _compact(a[-14:]), b_compact[:14]))
                        continue
            except Exception:
                pass

    if bad:
        head = bad[:8]
        msg_lines = ["SRT cue boundary validation failed (examples):"]
        for kind, idx, tail, head_txt in head:
            if kind == "in":
                msg_lines.append(f"  in cue {idx}: ...{tail} | {head_txt}...")
            else:
                msg_lines.append(f"  after cue {idx}: ...{tail} | {head_txt}...")
        raise SystemExit("\n".join(msg_lines))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", required=True, help="Channel id (e.g., CH22)")
    ap.add_argument("--videos", nargs="*", help="Video numbers (e.g., 023 024). If omitted, uses --from/--to range.")
    ap.add_argument("--from", dest="from_", type=int, default=1, help="Start video number (default: 1)")
    ap.add_argument("--to", type=int, default=999, help="End video number (inclusive)")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    ap.add_argument("--tolerance-sec", type=float, default=1.0, help="Allowed wav/srt end mismatch (default: 1.0s)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    channel = str(args.channel).upper().strip()
    tokens = _parse_video_tokens(videos=list(args.videos) if args.videos else None, from_=int(args.from_), to=int(args.to))
    if not tokens:
        return 0

    for token in tokens:
        final_dir = audio_final_dir(channel, token)
        log_path = final_dir / "log.json"
        wav_path = final_dir / f"{channel}-{token}.wav"
        srt_path = final_dir / f"{channel}-{token}.srt"
        if not log_path.exists():
            print(f"[SKIP] {channel}-{token}: missing log.json ({log_path})")
            continue
        if not wav_path.exists():
            print(f"[SKIP] {channel}-{token}: missing wav ({wav_path})")
            continue
        if not srt_path.exists():
            print(f"[SKIP] {channel}-{token}: missing srt ({srt_path})")
            continue

        log_payload = _read_json(log_path)
        engine = str(log_payload.get("engine") or "").lower().strip()
        segments = _build_segments_from_log(log_payload)

        vv_client: Optional[VoicevoxClient] = None
        speaker_id = 0
        voice_cfg: Optional[Dict[str, Any]] = None
        if engine == "voicevox":
            vv_client, speaker_id, voice_cfg = _resolve_voicevox(channel)

        tmp = srt_path.with_suffix(f".new_{_now_tag()}.srt")
        generate_srt(
            segments,
            tmp,
            channel=channel,
            video_no=token,
            engine=engine,
            voice_config=voice_cfg,
            voicevox_client=vv_client,
            speaker_id=speaker_id,
        )

        new_text = tmp.read_text(encoding="utf-8")
        _assert_srt_clean(new_text)
        old_text = srt_path.read_text(encoding="utf-8")
        if new_text == old_text:
            tmp.unlink(missing_ok=True)
            print(f"[OK] {channel}-{token}: no change")
            continue

        wav_dur = wav_duration_seconds(wav_path)
        new_end = srt_end_seconds(tmp)
        if abs(wav_dur - new_end) > float(args.tolerance_sec):
            raise SystemExit(
                f"[FAIL] {channel}-{token}: wav/srt mismatch wav={wav_dur:.3f}s srt_end={new_end:.3f}s tol={float(args.tolerance_sec):.3f}s"
            )

        backup = srt_path.with_name(f"{srt_path.stem}.legacy.{_now_tag()}.srt")
        print(f"[PLAN] {channel}-{token}: {srt_path.name} -> {backup.name} -> regenerated")
        if args.apply:
            backup.write_text(old_text, encoding="utf-8")
            srt_path.write_text(new_text, encoding="utf-8")
            tmp.unlink(missing_ok=True)
            _maybe_update_audio_manifest(final_dir, srt_path=srt_path)
            print(f"[APPLY] {channel}-{token}: entries={srt_entry_count(srt_path)} end_sec={srt_end_seconds(srt_path):.3f}")
        else:
            # keep tmp for inspection in dry-run
            print(f"[DRY] {channel}-{token}: wrote preview {tmp.name} (not applied)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
