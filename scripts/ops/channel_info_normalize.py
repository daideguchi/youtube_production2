#!/usr/bin/env python3
"""
Normalize per-channel metadata (SoT):
- packages/script_pipeline/channels/CHxx-*/channel_info.json

Goals (memo6 + UI operational needs):
- Ensure UI-editable fields are consistently present:
  - youtube_handle / youtube_title
  - youtube_description (video description fixed template; avoid falling back to YouTube channel bio)
  - default_tags
  - benchmarks (schema v1)
- Seed minimal benchmark pointers (script_samples) from workspaces/research when empty.
- Ensure VOICEVOX voice_config exists for all channels.
- Rebuild channels_info.json catalog after applying changes.

Conservative policy:
- Do not overwrite non-empty values unless they look like placeholders.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from _bootstrap import bootstrap

bootstrap()

from factory_common.paths import planning_root, repo_root, research_root, script_pkg_root, video_pkg_root
from factory_common.youtube_handle import normalize_youtube_handle

CHANNEL_CODE_RE = re.compile(r"^CH\d{2}$", re.IGNORECASE)


def _utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _to_repo_rel(path: Path) -> str:
    return path.relative_to(repo_root()).as_posix()


def _canonical_persona_path(channel_code: str) -> str:
    persona = planning_root() / "personas" / f"{channel_code}_PERSONA.md"
    return _to_repo_rel(persona)


def _canonical_template_path(channel_dir: Path) -> Optional[str]:
    candidate = channel_dir / "script_prompt.txt"
    if not candidate.exists():
        return None
    return _to_repo_rel(candidate)


def _canonicalize_thumbnail_asset_path(value: str) -> str:
    """
    Normalize repo-relative paths that rely on compat symlinks.

    Policy:
    - keep repo-relative
    - prefer `workspaces/thumbnails/...` over `thumbnails/...` (symlink)
    """
    raw = str(value or "").strip().replace("\\", "/")
    if raw.startswith("thumbnails/"):
        return "workspaces/thumbnails/" + raw[len("thumbnails/") :]
    return raw


def _channels_root() -> Path:
    return script_pkg_root() / "channels"


def _iter_channel_dirs() -> Iterable[Path]:
    root = _channels_root()
    if not root.exists():
        return
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if not entry.name.upper().startswith("CH"):
            continue
        if (entry / "channel_info.json").exists():
            yield entry


def _infer_channel_code(channel_dir: Path, payload: Dict[str, Any]) -> str:
    raw = str(payload.get("channel_id") or "").strip().upper()
    if raw and CHANNEL_CODE_RE.match(raw):
        return raw
    prefix = channel_dir.name.split("-", 1)[0].strip().upper()
    if CHANNEL_CODE_RE.match(prefix):
        return prefix
    raise ValueError(f"channel_id not found: {channel_dir}")


def _safe_normalize_handle(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return normalize_youtube_handle(text)
    except Exception:
        return text


def _infer_handle(payload: Dict[str, Any]) -> Optional[str]:
    candidates = [
        payload.get("youtube_handle"),
        (payload.get("youtube") or {}).get("handle"),
        (payload.get("youtube") or {}).get("custom_url"),
        (payload.get("branding") or {}).get("handle"),
        (payload.get("branding") or {}).get("custom_url"),
    ]
    for c in candidates:
        h = _safe_normalize_handle(c)
        if h:
            return h
    return None


def _infer_youtube_title(payload: Dict[str, Any]) -> Optional[str]:
    candidates = [
        payload.get("youtube_title"),
        (payload.get("youtube") or {}).get("title"),
        (payload.get("branding") or {}).get("title"),
        payload.get("name"),
    ]
    for c in candidates:
        t = str(c or "").strip()
        if t:
            return t
    return None


def _dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        t = str(item or "").strip()
        if not t:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _needs_video_description_template(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if text in {"新しいテンプレ", "テンプレ", "新テンプレ"}:
        return True
    # Enforce explicit video template; otherwise UI falls back to channel bio.
    if "【音声】" not in text:
        return True
    return False


def _extract_persona_one_liner(channel_code: str) -> Optional[str]:
    persona = planning_root() / "personas" / f"{channel_code}_PERSONA.md"
    if not persona.exists():
        return None
    try:
        lines = persona.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    for line in lines:
        raw = line.strip()
        if raw.startswith(">"):
            val = raw.lstrip(">").strip()
            if val:
                return val
    return None


def _normalize_voice_name_for_display(name: str) -> str:
    """
    Normalize voice character names for UI/YouTube template display.
    Keep legacy-friendly spelling where needed.
    """
    if name == "青山龍星":
        return "青山流星"
    return name


def _infer_voice_meta(channel_code: str) -> Tuple[str, str]:
    """
    Infer (engine_tag, voice_name) from:
      packages/script_pipeline/audio/channels/<CHxx>/voice_config.json
    """
    cfg_path = script_pkg_root() / "audio" / "channels" / channel_code / "voice_config.json"
    if not cfg_path.exists():
        return ("VOICEVOX", "青山流星")

    try:
        data = _read_json(cfg_path)
        key = str(data.get("default_voice_key") or "").strip()
        voices = data.get("voices") if isinstance(data.get("voices"), dict) else {}
        voice = voices.get(key) if isinstance(voices, dict) else None
        if not isinstance(voice, dict):
            return ("VOICEVOX", "青山流星")

        engine = str(voice.get("engine") or "").strip().lower()
        character = str(voice.get("character") or "").strip()
        if engine == "voicepeak" and not character:
            opts = voice.get("engine_options")
            if isinstance(opts, dict):
                character = str(opts.get("narrator") or "").strip()

        character = _normalize_voice_name_for_display(character)

        if engine == "voicepeak":
            return ("VOICEPEAK", character or "女性1")
        if engine == "voicevox":
            return ("VOICEVOX", character or "青山流星")

        return ("VOICEVOX", "青山流星")
    except Exception:
        return ("VOICEVOX", "青山流星")


_VOICE_TAG_HINTS = {
    "VOICEVOX",
    "VOICEPEAK",
    "青山流星",
    "青山龍星",
    "波音リツ",
    "男性1",
    "男性2",
    "男性3",
    "女性1",
    "女性2",
    "女性3",
}


def _apply_voice_tags(tags: List[str], *, engine_tag: str, voice_name: str) -> List[str]:
    cleaned = [t for t in tags if str(t or "").strip() not in _VOICE_TAG_HINTS]
    return _dedupe_preserve_order([*cleaned, engine_tag, voice_name])[:30]


def _infer_tags(channel_name: str, description: str) -> List[str]:
    text = f"{channel_name}\n{description}"
    tags: List[str] = []

    def has(token: str) -> bool:
        return token in text

    if any(
        has(t)
        for t in (
            "仏教",
            "ブッダ",
            "法話",
            "禅",
            "寺",
            "僧",
            "空海",
            "親鸞",
            "道元",
            "弘法大師",
        )
    ):
        tags += ["仏教", "法話", "禅"]
    if any(has(t) for t in ("偉人", "名言")):
        tags += ["偉人", "名言", "教養"]
    if any(has(t) for t in ("雑学", "豆知識", "トリビア")):
        tags += ["雑学", "豆知識", "トリビア"]
    if any(has(t) for t in ("昔ばなし", "童話", "民話", "朗読", "図書館")):
        tags += ["朗読", "昔ばなし", "童話"]
    if any(has(t) for t in ("旅", "旅行", "紀行", "散歩")):
        tags += ["旅", "旅行", "紀行"]
    if any(has(t) for t in ("心理", "メンタル", "哲学", "内省")):
        tags += ["心理学", "哲学", "内省"]
    if any(has(t) for t in ("老後", "シニア")):
        tags += ["老後", "シニア"]
    if any(has(t) for t in ("夫婦", "熟年")):
        tags += ["夫婦", "熟年夫婦"]
    if any(has(t) for t in ("友人", "近所")):
        tags += ["友人関係", "人間関係"]
    if any(has(t) for t in ("恋愛", "再婚")):
        tags += ["恋愛", "シニア恋愛"]
    if any(has(t) for t in ("都市伝説", "ミステリー", "怪談")):
        tags += ["都市伝説", "ミステリー", "考察"]

    tags += ["寝落ち", "睡眠用", "作業用BGM", "勉強用BGM", "長時間", "聞き流し"]
    return _dedupe_preserve_order(tags)[:30]


def _render_video_description_template(channel_code: str, *, name: str, lead: str, voice_label: str) -> str:
    fiction_notice = "" if channel_code not in {"CH22", "CH23"} else "※本チャンネルの物語はフィクションです。\n\n"

    hashtags: List[str] = []
    if any(tok in name for tok in ("仏教", "ブッダ", "法話", "禅")):
        hashtags.append("#仏教")
    if "偉人" in name:
        hashtags.append("#偉人")
    if "雑学" in name:
        hashtags.append("#雑学")
    if any(tok in name for tok in ("旅", "旅行", "紀行")):
        hashtags.append("#旅")
    if any(tok in name for tok in ("昔ばなし", "朗読")):
        hashtags.append("#朗読")
    hashtags.append("#寝落ち")
    hashtags = _dedupe_preserve_order(hashtags)[:3]

    lines = [
        fiction_notice.strip("\n"),
        f"この動画は、「{name}」の長時間コンテンツです。",
        lead.strip() or name.strip(),
        "",
        "寝落ち・作業・勉強中の“聞き流し”としてお使いください。",
        "",
        f"【音声】{voice_label}",
        "【視聴スタイル】画面は見なくてOK／音量は小さめ推奨",
        "【ご注意】運転中・危険作業中の視聴はお控えください。",
        "",
        " ".join(hashtags),
    ]
    text = "\n".join([line for line in lines if line != ""]).strip()
    return text + "\n"


def _default_benchmark_sample(channel_code: str) -> Optional[Dict[str, Any]]:
    mapping: Dict[str, str] = {
        "CH01": "ブッダ系/人生の道標1",
        "CH02": "心理学自己啓発系/曖昧な思考1",
        "CH03": "シニア体験談/台本構成の参考",
        "CH04": "心理学スピリチュアル系/秘密の図書館01",
        "CH05": "シニアのストーリー/シニアの恋愛1",
        "CH06": "都市伝説系/都市伝説ラボ1",
        "CH07": "benchmarks_ch07_ch08.md",
        "CH08": "心理学スピリチュアル系/秘密の図書館01",
        "CH09": "心理学自己啓発系/曖昧な思考2",
        "CH10": "偉人名言系のベンチマーク台本（参考）/ch10ベンチーく偉人",
        "CH11": "ブッダ系/ブッダ人生これから1",
        "CH12": "ブッダ系/バズ台本構造分析.md",
        "CH13": "ブッダ系/台本構造の参考",
        "CH14": "ブッダ系/台本構造の参考",
        "CH15": "ブッダ系/台本構造の参考",
        "CH16": "ブッダ系/台本構造の参考",
        "CH17": "ブッダ系/台本構造の参考",
        "CH18": "空海系/ベンチマーク情報",
        "CH19": "シニア体験談/台本構成の参考",
        "CH20": "心理学自己啓発系/曖昧な思考1",
        "CH21": "シニア体験談/体験談1",
        "CH22": "シニア体験談/体験談2",
        "CH23": "シニア体験談/体験談3",
        "CH24": "空海系/バズ台本の構成",
    }
    rel = mapping.get(channel_code)
    if not rel:
        return None
    base = research_root()
    if not (base / rel).exists():
        return None
    return {
        "base": "research",
        "path": rel,
        "label": "参考台本/メモ",
        "note": "構成/テンポ/表現を参考（表現は安全側へ調整）",
    }


def _extract_benchmark_channels_from_persona(channel_code: str) -> List[Dict[str, Any]]:
    persona = planning_root() / "personas" / f"{channel_code}_PERSONA.md"
    if not persona.exists():
        return []
    try:
        lines = persona.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines:
        if "YouTube:" not in line:
            continue
        m = re.search(r"`(@[^`]+)`", line)
        if not m:
            continue
        handle = _safe_normalize_handle(m.group(1))
        out.append(
            {
                "handle": handle,
                "name": None,
                "url": f"https://www.youtube.com/{handle}" if handle else None,
                "note": line.strip() or None,
            }
        )

    uniq: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in out:
        h = str(item.get("handle") or "").strip()
        if not h or h in seen:
            continue
        seen.add(h)
        uniq.append(item)
    return uniq


def _load_capcut_channel_presets() -> Dict[str, Any]:
    path = video_pkg_root() / "config" / "channel_presets.json"
    if not path.exists():
        return {}
    try:
        return _read_json(path)
    except Exception:
        return {}


def _load_thumbnail_templates() -> Dict[str, Any]:
    path = repo_root() / "workspaces" / "thumbnails" / "templates.json"
    if not path.exists():
        return {}
    try:
        return _read_json(path)
    except Exception:
        return {}


def _build_production_sources(channel_code: str, *, capcut_presets: Dict[str, Any], thumb_templates: Dict[str, Any]) -> Dict[str, Any]:
    production: Dict[str, Any] = {
        "voice_config_path": f"packages/script_pipeline/audio/channels/{channel_code}/voice_config.json",
        "thumbnails_templates_sot": "workspaces/thumbnails/templates.json",
        "thumbnails_default_template_id": None,
        "capcut_channel_preset_sot": "packages/video_pipeline/config/channel_presets.json",
        "capcut_preset": None,
    }

    channels = (thumb_templates.get("channels") or {}) if isinstance(thumb_templates, dict) else {}
    if isinstance(channels, dict):
        ch_obj = channels.get(channel_code)
        if isinstance(ch_obj, dict):
            production["thumbnails_default_template_id"] = ch_obj.get("default_template_id")

    presets_channels = (capcut_presets.get("channels") or {}) if isinstance(capcut_presets, dict) else {}
    if isinstance(presets_channels, dict):
        preset = presets_channels.get(channel_code)
        if isinstance(preset, dict):
            production["capcut_preset"] = {
                "capcut_template": preset.get("capcut_template"),
                "video_style_id": preset.get("video_style_id"),
                "prompt_template": preset.get("prompt_template"),
            }
    return production


def _ensure_voice_config(channel_code: str, channel_name: str, *, apply: bool) -> Tuple[bool, Path]:
    cfg_path = script_pkg_root() / "audio" / "channels" / channel_code / "voice_config.json"
    if cfg_path.exists():
        return False, cfg_path

    payload = {
        "channel_code": channel_code,
        "channel_name": channel_name,
        "default_voice_key": "voicevox_aoyama",
        "output": {
            "final_root": f"data/{channel_code}/_audio_workspace/final",
            "temp_root": f"data/{channel_code}/_audio_workspace/temp",
        },
        "voices": {
            "voicevox_aoyama": {
                "engine": "voicevox",
                "character": "青山龍星",
                "style": "ノーマル",
                "voicevox_speaker_id": 13,
                "speed_scale": 0.96,
                "pitch_scale": -0.02,
                "intonation_scale": 1.0,
                "volume_scale": 1.0,
                "pre_phoneme_length": 0.11,
                "post_phoneme_length": 0.13,
            },
            "pyopenjtalk_basic": {
                "engine": "pyopenjtalk",
                "character": "default",
                "style": "standard",
                "speed_scale": 1.0,
                "pitch_scale": 0.0,
                "intonation_scale": 1.0,
                "volume_scale": 1.0,
                "pre_phoneme_length": 0.0,
                "post_phoneme_length": 0.0,
                "engine_options": {"speed": 1.0},
            },
        },
        "section_voice_rules": {},
    }

    if apply:
        _write_json(cfg_path, payload)
    return True, cfg_path


def normalize_channel_info(
    path: Path, *, apply: bool, capcut_presets: Dict[str, Any], thumb_templates: Dict[str, Any]
) -> Tuple[bool, List[str]]:
    payload = _read_json(path)
    channel_code = _infer_channel_code(path.parent, payload)
    voice_engine_tag, voice_name = _infer_voice_meta(channel_code)
    voice_label = f"{voice_engine_tag}：{voice_name}" if voice_name else voice_engine_tag
    changed = False
    notes: List[str] = []

    if payload.get("channel_id") != channel_code:
        payload["channel_id"] = channel_code
        changed = True
        notes.append("fix channel_id")

    name = str(payload.get("name") or "").strip()
    if not name:
        inferred_title = _infer_youtube_title(payload) or channel_code
        payload["name"] = inferred_title
        name = inferred_title
        changed = True
        notes.append("fill name")

    if not str(payload.get("youtube_title") or "").strip():
        inferred = _infer_youtube_title(payload)
        if inferred:
            payload["youtube_title"] = inferred
            changed = True
            notes.append("fill youtube_title")

    if not str(payload.get("youtube_handle") or "").strip():
        inferred = _infer_handle(payload)
        if inferred:
            payload["youtube_handle"] = inferred
            changed = True
            notes.append("fill youtube_handle")

    canonical_persona = _canonical_persona_path(channel_code)
    if payload.get("persona_path") != canonical_persona:
        payload["persona_path"] = canonical_persona
        changed = True
        notes.append("set persona_path")

    canonical_template = _canonical_template_path(path.parent)
    if canonical_template and payload.get("template_path") != canonical_template:
        payload["template_path"] = canonical_template
        changed = True
        notes.append("set template_path")

    thumb_spec = payload.get("thumbnail_text_layer_spec_path")
    if isinstance(thumb_spec, str) and thumb_spec.strip():
        canonical_thumb = _canonicalize_thumbnail_asset_path(thumb_spec)
        if canonical_thumb != thumb_spec:
            payload["thumbnail_text_layer_spec_path"] = canonical_thumb
            changed = True
            notes.append("canonicalize thumbnail_text_layer_spec_path")

    if not str(payload.get("audience_profile") or "").strip():
        one_liner = _extract_persona_one_liner(channel_code)
        if one_liner:
            payload["audience_profile"] = one_liner
            changed = True
            notes.append("fill audience_profile")

    existing_tags = payload.get("default_tags")
    if not isinstance(existing_tags, list):
        existing_tags = []
    desc = str(payload.get("description") or "").strip()
    inferred_tags = _infer_tags(name, desc)
    merged_tags = _dedupe_preserve_order([*existing_tags, *inferred_tags])
    merged_tags = _apply_voice_tags(merged_tags, engine_tag=voice_engine_tag, voice_name=voice_name)
    if merged_tags != existing_tags:
        payload["default_tags"] = merged_tags
        changed = True
        notes.append("normalize default_tags")

    if _needs_video_description_template(payload.get("youtube_description")):
        lead = desc or name
        payload["youtube_description"] = _render_video_description_template(
            channel_code, name=name, lead=lead, voice_label=voice_label
        )
        changed = True
        notes.append("fill youtube_description")

    b = payload.get("benchmarks")
    if not isinstance(b, dict) or b.get("version") is None:
        b = {
            "version": 1,
            "updated_at": _utc_date(),
            "channels": [],
            "script_samples": [],
            "notes": "ベンチマークはUIから追加してください。",
        }
        payload["benchmarks"] = b
        changed = True
        notes.append("init benchmarks")
    else:
        b.setdefault("version", 1)
        b.setdefault("updated_at", b.get("updated_at") or _utc_date())
        b.setdefault("channels", [])
        b.setdefault("script_samples", [])
        b.setdefault("notes", b.get("notes") or "")

    b2 = payload.get("benchmarks")
    if isinstance(b2, dict):
        if isinstance(b2.get("script_samples"), list) and len(b2.get("script_samples")) == 0:
            sample = _default_benchmark_sample(channel_code)
            if sample:
                b2["script_samples"] = [sample]
                changed = True
                notes.append("seed benchmark script_samples")
        if isinstance(b2.get("channels"), list) and len(b2.get("channels")) == 0:
            seeded = _extract_benchmark_channels_from_persona(channel_code)
            if seeded:
                b2["channels"] = seeded
                changed = True
                notes.append("seed benchmark channels")

    computed = _build_production_sources(channel_code, capcut_presets=capcut_presets, thumb_templates=thumb_templates)
    if payload.get("production_sources") != computed:
        payload["production_sources"] = computed
        changed = True
        notes.append("set production_sources")

    voice_missing, voice_path = _ensure_voice_config(channel_code, name, apply=apply)
    if voice_missing:
        changed = True
        notes.append(f"ensure voice_config ({voice_path.relative_to(repo_root())})")

    if apply and changed:
        _write_json(path, payload)
    return changed, notes


def rebuild_channels_info(*, apply: bool) -> Path:
    out_path = _channels_root() / "channels_info.json"
    if not apply:
        return out_path
    items: List[Dict[str, Any]] = []
    for d in _iter_channel_dirs():
        try:
            items.append(_read_json(d / "channel_info.json"))
        except Exception:
            continue
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write changes to disk")
    ap.add_argument("--channel", help="Only normalize one channel (e.g., CH07)")
    args = ap.parse_args()

    only = args.channel.strip().upper() if args.channel else None
    if only and not CHANNEL_CODE_RE.match(only):
        raise SystemExit(f"invalid --channel: {args.channel}")

    capcut_presets = _load_capcut_channel_presets()
    thumb_templates = _load_thumbnail_templates()

    total = 0
    changed_count = 0
    for d in _iter_channel_dirs():
        path = d / "channel_info.json"
        payload = _read_json(path)
        ch = _infer_channel_code(d, payload)
        if only and ch != only:
            continue
        total += 1
        changed, notes = normalize_channel_info(path, apply=args.apply, capcut_presets=capcut_presets, thumb_templates=thumb_templates)
        if changed:
            changed_count += 1
            print(f"✅ {ch}: {', '.join(notes) if notes else 'updated'}")
        else:
            print(f"- {ch}: ok")

    out = rebuild_channels_info(apply=args.apply)
    if args.apply:
        print(f"\nRebuilt: {out}")
    print(f"\nDone. channels={total}, changed={changed_count}, apply={bool(args.apply)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
