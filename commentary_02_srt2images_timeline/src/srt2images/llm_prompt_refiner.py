"""
LLM-based prompt refiner: use surrounding cue context to craft a scene-ready prompt snippet.
If LLM call fails, original cues are returned untouched.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = True) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() not in ("0", "false", "no", "off")


class PromptRefiner:
    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        # デフォルトモデルを gemini-2.5-pro に変更 (gemini-3-pro-preview は429頻発のため回避)
        self.model = model or os.getenv("SRT2IMAGES_PROMPT_MODEL") or "gemini-2.5-pro"
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.enabled = _env_flag("SRT2IMAGES_REFINE_PROMPTS", False)
        if self.enabled:
            logger.info("PromptRefiner is ENABLED. Using model: %s", self.model)
        else:
            logger.info("PromptRefiner is DISABLED.")
        
        # ロールタグごとのヒント（人物追加禁止/視聴者アドレスの振る舞いなど）
        self.role_hints: Dict[str, str] = {
            "viewer_address": "Talk directly to viewer; do NOT insert statues/monks/extra people. Use gestures toward audience, eye contact, simple setting.",
            "explanation": "Focus on concept or object, minimal people; show the idea visually.",
            "story": "If a character already exists, keep same face/clothes; show concrete action, place, props.",
            "list_item": "Show the specific item or example, single clear subject.",
            "metaphor": "Depict the metaphor literally but do not add unrelated characters.",
            "hook": "High-contrast, cinematic hook; no random monks or sitting unless stated.",
        }

    def refine(self, cues: List[Dict[str, Any]], channel_id: Optional[str] = None, window: int = 1, common_style: str = "", persona: str = "") -> List[Dict[str, Any]]:
        if not self.enabled:
            return cues
        if not self.api_key:
            logger.warning("PromptRefiner: GEMINI_API_KEY missing; skipping refine.")
            return cues
        try:
            from google import genai  # type: ignore
        except Exception as exc:  # pragma: no cover
            logger.warning("PromptRefiner: google-genai not installed (%s); skipping refine.", exc)
            return cues

        client = genai.Client(api_key=self.api_key)
        # CH01は文脈のズレを抑えるため広めの窓＋環境変数で上書き可
        win_env = os.getenv("SRT2IMAGES_REFINE_WINDOW")
        if win_env:
            try:
                window = max(1, int(win_env))
            except ValueError:
                logger.warning("PromptRefiner: invalid SRT2IMAGES_REFINE_WINDOW=%s", win_env)
        elif (channel_id or "").upper() == "CH01":
            window = max(window, 2)

        refined_any = False
        for idx, cue in enumerate(cues):
            ctx_chunks = []
            for offset in range(-window, window + 1):
                j = idx + offset
                if j < 0 or j >= len(cues):
                    continue
                c = cues[j]
                prefix = "current" if offset == 0 else ("prev" if offset < 0 else "next")
                ctx_chunks.append(
                    (
                        f"[{prefix} #{c.get('index')} {c.get('start_sec')}–{c.get('end_sec')}s "
                        f"role={c.get('role_tag') or '-'} type={c.get('section_type') or '-'} "
                        f"tone={c.get('emotional_tone') or '-'}] "
                        f"text: {c.get('text','') or c.get('summary','')}"
                    )
                )
            ctx_text = "\n".join(ctx_chunks)
            role_hint = ""
            rt = (cue.get("role_tag") or "").lower()
            if rt in self.role_hints:
                role_hint = f"Role Guidance ({rt}): {self.role_hints[rt]}"

            prompt = f"""You are crafting a concise visual brief for one illustration frame. Base it on the current line; use neighbors only to avoid repetition and to keep continuity (change angle/pose/foreground from adjacent frames).
- Describe concrete action, pose, setting, props, lighting, and atmosphere. Make it camera-ready.
- If the script has no people, avoid inventing any. Keep faces/clothes consistent when the same person recurs; do not add new characters.
- Never default to monks/meditation/正座 unless clearly stated. For viewer_address, keep it direct to the viewer without inventing a Buddha statue.
- Vary stance and camera vs. adjacent frames (closer, wider, side/back view, hands in action, walking/standing/handling objects instead of sitting).
{role_hint}

Global Style & Tone:
{common_style}

Persona / Character Guide:
{persona}

Context:
{ctx_text}

Return only JSON on one line:
{{"refined_prompt": "<=320 chars with action+pose+setting+props+lighting+camera (no text in scene, no markdown)"}}
"""
            try:
                resp = client.models.generate_content(
                    model=self.model,
                    contents=[prompt],
                    config={"response_mime_type": "application/json"},
                )
                text = getattr(resp, "text", "") or ""
                if not text:
                    continue
                parsed = json.loads(text)
                refined = parsed.get("refined_prompt") if isinstance(parsed, dict) else None
                if refined:
                    cue["refined_prompt"] = refined.strip()
                    refined_any = True
            except Exception as exc:
                logger.warning("Prompt refine failed for cue %s: %s", cue.get("index"), exc)
                continue

        if refined_any:
            logger.info("PromptRefiner: refined prompts for %d cue(s)", len([c for c in cues if c.get("refined_prompt")]))
        return cues
