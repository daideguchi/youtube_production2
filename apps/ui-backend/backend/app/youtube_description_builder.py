from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.app.channel_info_store import refresh_channel_info
from backend.app.episode_store import video_base_dir
from backend.app.json_store import load_json
from backend.app.path_utils import PROJECT_ROOT

def _extract_script_summary(channel_code: str, video_number: str) -> Optional[str]:
    """Assembled台本の冒頭から、説明文用の短い要約を作る。"""
    base_dir = video_base_dir(channel_code, video_number)
    candidates = [
        base_dir / "content" / "assembled_human.md",
        base_dir / "content" / "assembled.md",
    ]
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                raw_text = path.read_text(encoding="utf-8")
                if not raw_text:
                    continue
                text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
                paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
                paragraphs = [p for p in paragraphs if p.strip() != "---"]
                if not paragraphs:
                    continue

                def is_title_like(paragraph: str) -> bool:
                    candidate = paragraph.strip()
                    if "\n" in candidate:
                        return False
                    if len(candidate) > 30:
                        return False
                    if any(ch in candidate for ch in ("、", "！", "？", "!", "?", "「", "」")):
                        return False
                    return candidate.endswith("。") or candidate.endswith("…") or bool(re.match(r"^[#\s]+$", candidate))

                body: List[str] = []
                for paragraph in paragraphs:
                    if not body and is_title_like(paragraph):
                        continue
                    body.append(paragraph)
                    if len(body) >= 3 or sum(len(p) for p in body) >= 260:
                        break
                if not body:
                    body = paragraphs[:1]
                block = "\n".join(body).strip()
                if not block:
                    continue
                # 文の先頭2〜3文を抜粋
                sentences = [s for s in block.replace("！", "。").replace("？", "。").split("。") if s.strip()]
                summary = "。".join(sentences[:3]).strip()
                return (summary + "。").strip() if summary else block[:200]
        except Exception:
            continue
    return None


def _normalize_description_length(text: str, *, max_len: int = 900) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    # Prefer cutting at a block boundary first (copy-friendly).
    cut = text.rfind("\n", 0, max_len)
    if cut >= int(max_len * 0.6):
        return text[:cut].rstrip() + "\n…"
    # Fallback: cut by Japanese sentence boundary.
    sentences = [s for s in text.split("。") if s.strip()]
    trimmed = ""
    for s in sentences:
        candidate = (trimmed + s + "。").strip()
        if len(candidate) > max_len:
            break
        trimmed = candidate
    if trimmed:
        return trimmed + "…"
    return text[: max_len - 1].rstrip() + "…"


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_youtube_description_text(text: Optional[str]) -> Optional[str]:
    if not isinstance(text, str):
        return None
    value = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    value = _ANSI_ESCAPE_RE.sub("", value)
    value = _CONTROL_CHARS_RE.sub("", value)
    value = value.replace("\ufffd", "")  # Unicode replacement char (mojibake marker)
    # Normalize excessive blank lines (copy-friendly).
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value or None


def _normalize_description_field(text: Optional[str]) -> Optional[str]:
    value = _sanitize_youtube_description_text(text)
    if not value:
        return None
    # Planning fields sometimes contain HTML line breaks for UI; normalize to plain text.
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = value.replace("&nbsp;", " ")
    # Best-effort HTML tag removal (avoid copy/paste artifacts).
    value = re.sub(r"</?[^>]+>", "", value)
    return value.strip() or None


def _build_bullet_list(text: Optional[str]) -> Optional[str]:
    value = _normalize_description_field(text)
    if not value:
        return None
    raw_lines = [line.strip() for line in value.splitlines() if line.strip()]
    lines = [line.lstrip("・").lstrip("-").lstrip("•").strip() for line in raw_lines]
    lines = [line for line in lines if line]
    if not lines:
        return None
    return "・" + "\n・".join(lines)


def _get_channel_profile(channel_code: str) -> Dict[str, Any]:
    info_map = refresh_channel_info()
    info = info_map.get((channel_code or "").upper(), {})
    return info if isinstance(info, dict) else {}


def _channel_subscribe_url(channel_info: Dict[str, Any]) -> Optional[str]:
    if not isinstance(channel_info, dict):
        return None
    # Prefer handle/custom URL for copy friendliness; fall back to channel URL.
    youtube_meta = channel_info.get("youtube")
    if isinstance(youtube_meta, dict):
        handle = youtube_meta.get("handle") or youtube_meta.get("custom_url") or channel_info.get("youtube_handle")
        if isinstance(handle, str) and handle.strip():
            handle = handle.strip()
            if handle.startswith("@"):
                return f"https://www.youtube.com/{handle}"
            return handle
        url = youtube_meta.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    handle = channel_info.get("youtube_handle")
    if isinstance(handle, str) and handle.strip():
        handle = handle.strip()
        if handle.startswith("@"):
            return f"https://www.youtube.com/{handle}"
        return handle
    return None


def _voice_credit_line(channel_info: Dict[str, Any]) -> Optional[str]:
    prod = channel_info.get("production_sources") if isinstance(channel_info, dict) else None
    voice_config_path = prod.get("voice_config_path") if isinstance(prod, dict) else None
    if not isinstance(voice_config_path, str) or not voice_config_path.strip():
        return None
    try:
        voice_cfg = load_json(PROJECT_ROOT / voice_config_path)
    except Exception:
        return None
    if not isinstance(voice_cfg, dict):
        return None
    default_key = voice_cfg.get("default_voice_key")
    voices = voice_cfg.get("voices")
    if not isinstance(default_key, str) or not isinstance(voices, dict):
        return None
    voice = voices.get(default_key, {})
    if not isinstance(voice, dict):
        return None
    character = voice.get("character")
    engine = voice.get("engine")
    if not isinstance(character, str) or not character.strip():
        return None
    character = character.strip()
    if str(engine).lower() == "voicevox":
        return f"VOICEVOX:{character}"
    return f"音声:{character}"


def _hashtags_line(*tags: Optional[str], max_tags: int = 12) -> Optional[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in tags:
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value:
            continue
        value = value.lstrip("#").strip()
        if not value or any(ch.isspace() for ch in value):
            continue
        tag = f"#{value}"
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= max_tags:
            break
    return " ".join(out) if out else None


def _build_youtube_description(channel_code: str, video_number: str, metadata: Dict[str, Any], title: Optional[str]) -> str:
    """Generate a richer YouTube description from planning + 台本本文。"""

    planning = metadata.get("planning", {}) if isinstance(metadata, dict) else {}

    def pget(key: str) -> Optional[str]:
        value = planning.get(key) if isinstance(planning, dict) else None
        if not value and isinstance(metadata, dict):
            value = metadata.get(key)
        if isinstance(value, str):
            value = value.strip()
        return value or None

    channel_code = (channel_code or "").upper()

    lead = _normalize_description_field(pget("description_lead"))
    takeaways = _normalize_description_field(pget("description_takeaways"))
    audience = pget("target_audience")
    main_tag = pget("primary_pain_tag")
    sub_tag = pget("secondary_pain_tag")
    life_scene = pget("life_scene")

    title_text = title or pget("sheet_title") or pget("title") or ""

    takeaways_block = _build_bullet_list(takeaways)

    script_summary = _extract_script_summary(channel_code, video_number)
    summary_line = _normalize_description_field(script_summary) or (lead if lead and "フィクション" not in lead else None)

    def fmt(blocks: List[Optional[str]], *, max_len: int = 4500) -> str:
        text = "\n\n".join(filter(None, blocks))
        text = _sanitize_youtube_description_text(text) or ""
        return _normalize_description_length(text, max_len=max_len)

    channel_info = _get_channel_profile(channel_code)
    subscribe_url = _channel_subscribe_url(channel_info)
    subscribe_block = f"🔔チャンネル登録はこちら\n{subscribe_url}" if subscribe_url else None
    voice_line = _voice_credit_line(channel_info)

    # CH22: senior friendship/community story channel (benchmark-aligned, copy-friendly)
    if channel_code == "CH22":
        takeaways_section = f"▼この動画でわかること\n{takeaways_block}" if takeaways_block else None
        teaser = (
            summary_line
            or _normalize_description_field(pget("content_summary"))
            or (f"今日の物語：{title_text}" if title_text else None)
            or "老後の友人関係を、物語で整える回です。"
        )
        question = (
            "皆さんは、友人関係で「この人とは合わないかも」と感じた経験はありますか？\n"
            "もし同じような経験や、人間関係で気をつけていることがあれば、ぜひコメント欄で教えてください。"
        )
        fiction = (
            "この物語はフィクションです。\n"
            "登場する人物・団体・名称等は架空であり、実在のものとは関係ありません。"
        )
        hashtags = _hashtags_line(
            "老後",
            "朗読",
            "シニア",
            "友人関係",
            "人間関係",
            life_scene,
            main_tag,
            sub_tag,
        )
        return fmt([teaser, takeaways_section, question, subscribe_block, fiction, hashtags, voice_line])

    if channel_code in {"CH01", "CH07", "CH11"}:
        opener = f"この動画では「{title_text}」を仏教の視点でやさしく解き明かします。"
        body = summary_line or "心が折れそうなときに使える“たった一言”をお届け。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：一呼吸おいて距離をとる / 優しさと境界線を両立する"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#仏教 #心を整える #人間関係"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH02", "CH10"}:
        opener = f"{title_text} を哲学・心理と偉人の言葉で分解し、静かな思考法に落とし込みます。"
        body = summary_line or "考えすぎる夜に“考えない時間”をつくるための小さなステップを紹介。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：深呼吸・メモ・短い無思考タイムを挟む"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#思考法 #哲学 #夜のラジオ"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH04"}:
        opener = f"{title_text} の“違和感/謎”を心理・脳科学・物語で探究し、日常に使える視点に翻訳します。"
        body = summary_line or "静かな語りで“なるほど”を届ける知的エンタメ回です。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：気づいた違和感をメモし、1日1つ観察してみる"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#心理学 #脳科学 #好奇心 #知的エンタメ"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH03"}:
        opener = f"{title_text} を“病院任せにしない”日常習慣で整える方法をまとめました。"
        body = summary_line or "50〜70代の体と心をやさしくケアするシンプルなステップ。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：寝る前の呼吸・短いストレッチ・水分補給"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#シニア健康 #習慣化 #ウェルネス"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH05"}:
        opener = f"{title_text} を安心とユーモアで解説。距離の取り方・伝え方・再出発のヒントを紹介。"
        body = summary_line or "シニア世代の恋愛・パートナーシップを穏やかに進めるための道しるべ。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：短い挨拶・連絡頻度の合意・1つの共通体験を増やす"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#シニア恋愛 #コミュ力 #第二の人生"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH06"}:
        opener = f"{title_text} の“噂”と“根拠”を切り分け、考察で本当かもしれないを探ります。"
        body = summary_line or "ワクワクしつつ冷静に検証する安全運転の都市伝説ガイド。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：複数ソースを照合・仮説と事実を分けてメモ"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#都市伝説 #考察 #検証"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH08"}:
        opener = f"{title_text} を“悪用厳禁”の視点で安全に扱う方法を解説します。"
        body = summary_line or "波動・カルマ・反応しない力を、心理とミニ実験付きで紹介。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"⚠️ 注意：\n{takeaways_block}" if takeaways_block else "⚠️ 注意：無理をせず、体調や人間関係を優先して試してください。"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#スピリチュアル #波動 #自己浄化"
        return fmt([opener, body, audience_line, take_line, hash_line])

    if channel_code in {"CH09"}:
        opener = f"{title_text} を“危険人物/言ってはいけない言葉”の視点で整理し、線引きのチェックリストを提供。"
        body = summary_line or "舐められない距離感と、今日からできる自己防衛の一言。"
        audience_line = f"💡 こんな方に：{audience}" if audience else None
        take_line = f"🧭 今日からできる一歩：\n{takeaways_block}" if takeaways_block else "🧭 今日からできる一歩：言わないリストを作る / 距離を置くサインを1つ決める"
        hash_line = _hashtags_line(main_tag, sub_tag) or "#人間関係 #自己防衛 #線引き"
        return fmt([opener, body, audience_line, take_line, hash_line])

    # Common fallback (all channels): dynamic header + channel template as footer.
    template = _normalize_description_field(
        channel_info.get("youtube_description") if isinstance(channel_info, dict) else None
    )
    takeaways_section = f"▼この動画でわかること\n{takeaways_block}" if takeaways_block else None
    hash_line = _hashtags_line(main_tag, sub_tag, life_scene)
    return fmt(
        [
            f"{title_text} の要点を短くまとめました。" if title_text else None,
            summary_line,
            takeaways_section,
            subscribe_block,
            template,
            hash_line,
            voice_line,
        ]
    )
