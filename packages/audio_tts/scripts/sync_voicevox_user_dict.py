#!/usr/bin/env python3
"""
Sync factory_commentary reading dictionaries into VOICEVOX ENGINE official user dictionary.

Official endpoints (VOICEVOX/voicevox_engine README):
  GET    /user_dict
  POST   /user_dict_word?surface=...&pronunciation=...&accent_type=...
  PUT    /user_dict_word/{uuid}?surface=...&pronunciation=...&accent_type=...
  DELETE /user_dict_word/{uuid}

This script treats repo dictionaries as the source of truth and pushes a safe subset
to the engine user dictionary for local interactive use.

Usage:
  # From repo root
  PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --channel CH05
  PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --all

Options:
  --base-url http://127.0.0.1:50021  (defaults to routing.json voicevox.url)
  --overwrite  (update existing surface entries when pronunciation differs)
  --dry-run    (print actions only)
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple

from audio_tts.tts.reading_dict import (
    load_channel_reading_dict,
    normalize_reading_kana,
    is_safe_reading,
)
from factory_common.paths import audio_pkg_root
from audio_tts.tts.routing import load_routing_config
from audio_tts.tts.voicevox_api import VoicevoxClient
from audio_tts.tts.voicevox_user_dict import VoicevoxUserDictClient


def _norm_surface(surface: str) -> str:
    """
    Normalize surfaces for matching against VOICEVOX user-dict entries.

    VOICEVOX may store ASCII surfaces as fullwidth forms; using NFKC on both sides
    keeps sync idempotent (prevents duplicate entries across runs).
    """

    return unicodedata.normalize("NFKC", str(surface or "")).strip()


def _channel_speaker_id(channel: str) -> int | None:
    from factory_common.paths import script_pkg_root

    cfg_path = script_pkg_root() / "audio" / "channels" / channel / "voice_config.json"
    if not cfg_path.exists():
        return None
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        key = data.get("default_voice_key")
        voice = (data.get("voices") or {}).get(key) or {}
        sid = voice.get("voicevox_speaker_id")
        return int(sid) if sid is not None else None
    except Exception:
        return None


def _infer_accent_type(vv_client: VoicevoxClient, pronunciation: str, speaker_id: int) -> int:
    try:
        q = vv_client.audio_query(pronunciation, speaker_id)
        ap = q.get("accent_phrases") if isinstance(q, dict) else None
        if isinstance(ap, list) and ap and isinstance(ap[0], dict):
            acc = ap[0].get("accent")
            if isinstance(acc, (int, float)):
                acc_i = int(acc)
                if acc_i >= 0:
                    return acc_i
    except Exception:
        pass
    return 1  # safest flat-ish default


def _discover_channels() -> List[str]:
    root = audio_pkg_root() / "data" / "reading_dict"
    if not root.exists():
        return []
    return sorted(p.stem for p in root.glob("*.yaml"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", help="Channel to sync (e.g. CH05)")
    ap.add_argument("--all", action="store_true", help="Sync all reading_dict/*.yaml (conflicts skipped)")
    ap.add_argument("--base-url", help="VOICEVOX engine URL (default from routing.json)")
    ap.add_argument("--overwrite", action="store_true", help="Update existing entries when different")
    ap.add_argument("--dry-run", action="store_true", help="Do not modify engine; print actions")
    args = ap.parse_args()

    if not args.channel and not args.all:
        ap.error("Specify --channel or --all")

    cfg = load_routing_config()
    base_url = args.base_url or cfg.voicevox_url

    user_client = VoicevoxUserDictClient(base_url)
    vv_client = VoicevoxClient(base_url)

    existing = user_client.list_words()
    existing_by_surface: Dict[str, str] = {}
    for uuid, w in existing.items():
        surface = w.get("surface")
        if isinstance(surface, str) and surface:
            existing_by_surface[_norm_surface(surface)] = uuid

    channels = [args.channel] if args.channel else _discover_channels()

    # Collect candidate entries with conflict detection across channels.
    entries: List[Tuple[str, str, str, Dict[str, object]]] = []
    for ch in channels:
        for surface, meta in load_channel_reading_dict(ch).items():
            reading = meta.get("reading_kana") or meta.get("reading_hira") or ""
            if not isinstance(reading, str) or not reading.strip():
                continue
            pronunciation = normalize_reading_kana(reading)
            if not pronunciation or not is_safe_reading(pronunciation):
                continue
            entries.append((ch, surface, pronunciation, meta))

    surface_map: Dict[str, Tuple[str, str, str, Dict[str, object]]] = {}
    conflicts: List[Tuple[str, str, str, str]] = []
    for ch, surface, pronunciation, meta in entries:
        key = _norm_surface(surface)
        if key in surface_map and surface_map[key][2] != pronunciation:
            conflicts.append((surface, surface_map[key][2], pronunciation, ch))
            continue
        surface_map[key] = (ch, surface, pronunciation, meta)

    added = updated = skipped = 0

    for _, (ch, surface, pronunciation, meta) in sorted(surface_map.items()):
        sid = _channel_speaker_id(ch) or 1
        accent_type = meta.get("accent_type")
        if not isinstance(accent_type, int):
            accent_type = _infer_accent_type(vv_client, pronunciation, sid)

        surface_key = _norm_surface(surface)
        if surface_key in existing_by_surface:
            uuid = existing_by_surface[surface_key]
            prev = existing.get(uuid, {})
            prev_pron = prev.get("pronunciation")
            prev_acc = prev.get("accent_type")
            if str(prev_pron) == pronunciation and int(prev_acc or accent_type) == accent_type:
                skipped += 1
                continue
            if args.overwrite:
                if not args.dry_run:
                    user_client.update_word(uuid, surface, pronunciation, accent_type)
                updated += 1
            else:
                skipped += 1
            continue

        if not args.dry_run:
            user_client.add_word(surface, pronunciation, accent_type)
        added += 1

    print(f"[VOICEVOX_USER_DICT] base_url={base_url}")
    print(f"[VOICEVOX_USER_DICT] channels={channels}")
    print(f"[VOICEVOX_USER_DICT] added={added} updated={updated} skipped={skipped}")
    if conflicts:
        print(f"[VOICEVOX_USER_DICT] conflicts_skipped={len(conflicts)}")
        for surface, a, b, ch in conflicts[:20]:
            print(f"  - {surface}: {a} vs {b} (from {ch})")


if __name__ == "__main__":
    main()
