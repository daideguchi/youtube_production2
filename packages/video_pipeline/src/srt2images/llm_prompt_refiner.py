"""
LLM-based prompt refiner: use surrounding cue context to craft a scene-ready prompt snippet.
If LLM call fails, original cues are returned untouched.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

def _truncate_text(text: str, limit: int) -> str:
    t = " ".join((text or "").split())
    if limit <= 0:
        return ""
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"


def _env_flag(name: str, default: bool = True) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() not in ("0", "false", "no", "off")

def _llm_mode() -> str:
    return (os.getenv("LLM_MODE") or "api").strip().lower()


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    # Strip common code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    # Best-effort: extract the first {...} block
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        snippet = raw[start : end + 1]
        try:
            parsed = json.loads(snippet)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


class PromptRefiner:
    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        # NOTE: Prompt refinement is routed via factory_common.llm_router (LLM_MODE aware).
        # Keep constructor args for backward compatibility, but routing is task-based.
        self.model = model or os.getenv("SRT2IMAGES_PROMPT_MODEL") or ""
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or ""
        self.enabled = _env_flag("SRT2IMAGES_REFINE_PROMPTS", False)
        if self.enabled:
            logger.info("PromptRefiner is ENABLED. Using task: visual_prompt_refine")
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

        # Per-cue refinement + agent/think mode would create many pending tasks (operator pain).
        # Keep refinement API-only; cues_plan already provides a single-task planning route.
        if _llm_mode() in {"agent", "think"}:
            logger.info("PromptRefiner: LLM_MODE=%s; skipping per-cue refine.", _llm_mode())
            return cues

        try:
            from factory_common.llm_router import get_router
        except Exception as exc:  # pragma: no cover
            logger.warning("PromptRefiner: failed to import LLMRouter (%s); skipping refine.", exc)
            return cues

        router = get_router()
        # CH01は文脈のズレを抑えるため広めの窓＋環境変数で上書き可
        win_env = os.getenv("SRT2IMAGES_REFINE_WINDOW")
        if win_env:
            try:
                window = max(1, int(win_env))
            except ValueError:
                logger.warning("PromptRefiner: invalid SRT2IMAGES_REFINE_WINDOW=%s", win_env)
        elif (channel_id or "").upper() == "CH01":
            window = max(window, 2)

        # Keep per-cue prompts compact to avoid provider truncation / slowdowns.
        # NOTE: Final image prompts still include full style/persona; this refiner only needs
        # enough context to craft a concrete scene description.
        try:
            style_max = int(os.getenv("SRT2IMAGES_REFINE_STYLE_MAX_CHARS", "600"))
        except ValueError:
            style_max = 600
        try:
            persona_max = int(os.getenv("SRT2IMAGES_REFINE_PERSONA_MAX_CHARS", "900"))
        except ValueError:
            persona_max = 900
        try:
            ctx_line_max = int(os.getenv("SRT2IMAGES_REFINE_CTX_LINE_MAX_CHARS", "420"))
        except ValueError:
            ctx_line_max = 420

        common_style = _truncate_text(common_style, style_max)
        persona = _truncate_text(persona, persona_max)

        refined_any = False
        for idx, cue in enumerate(cues):
            ctx_chunks = []
            for offset in range(-window, window + 1):
                j = idx + offset
                if j < 0 or j >= len(cues):
                    continue
                c = cues[j]
                prefix = "current" if offset == 0 else ("prev" if offset < 0 else "next")
                ctx_payload = c.get("text", "") or c.get("summary", "")
                ctx_payload = _truncate_text(str(ctx_payload), ctx_line_max)
                ctx_chunks.append(
                    (
                        f"[{prefix} #{c.get('index')} {c.get('start_sec')}–{c.get('end_sec')}s "
                        f"role={c.get('role_tag') or '-'} type={c.get('section_type') or '-'} "
                        f"tone={c.get('emotional_tone') or '-'}] "
                        f"text: {ctx_payload}"
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
                result = router.call_with_raw(
                    task="visual_prompt_refine",
                    messages=[{"role": "user", "content": prompt}],
                    response_format="json_object",
                )
                parsed = _parse_json_object(str(result.get("content", "") or ""))
                refined = parsed.get("refined_prompt") if parsed else None
                if refined:
                    cue["refined_prompt"] = refined.strip()
                    refined_any = True
            except Exception as exc:
                logger.warning("Prompt refine failed for cue %s: %s", cue.get("index"), exc)
                continue

        if refined_any:
            logger.info("PromptRefiner: refined prompts for %d cue(s)", len([c for c in cues if c.get("refined_prompt")]))
        return cues
