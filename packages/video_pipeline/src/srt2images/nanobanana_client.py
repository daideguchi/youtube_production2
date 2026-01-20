from __future__ import annotations
import base64
import hashlib
import math
import logging
import os
import re
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache

from factory_common.image_client import (
    ImageClient,
    ImageGenerationError,
    ImageTaskOptions,
    IMAGE_MODEL_KEY_BLOCKLIST,
    IMAGE_MODEL_KEY_BLOCKLIST_TASKS,
)
from factory_common.paths import repo_root
from factory_common.routing_lockdown import lockdown_active

from video_pipeline.src.core.config import config


# ==== 429 Resilient Pipeline: QuotaExhaustedError ====
class QuotaExhaustedError(Exception):
    """Gemini APIクォータ制限により処理継続不可"""
    def __init__(self, message: str, successful_count: int = 0, failed_count: int = 0):
        super().__init__(message)
        self.successful_count = successful_count
        self.failed_count = failed_count


# 連続429カウンター（モジュールレベル）
_CONSECUTIVE_429_COUNT = 0
_MAX_CONSECUTIVE_429 = 3  # 3回連続で諦める
_SUCCESSFUL_IMAGE_COUNT = 0  # 成功した画像数

# Legacy router fallback is disabled by default.
USE_LEGACY_IMAGE_ROUTER = False  # Legacy router disabled; ImageClient is the only path

# OpenRouter Gemini image API expects a canonical aspect ratio string (e.g. "16:9"),
# not a raw pixel ratio like "1920:1080". Keep it within the accepted set.
_OPENROUTER_ALLOWED_ASPECT_RATIOS = {
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
}


def _normalize_openrouter_aspect_ratio(width: int, height: int) -> str | None:
    try:
        w = int(width)
        h = int(height)
    except Exception:
        return None

    if w <= 0 or h <= 0:
        return None

    g = math.gcd(w, h)
    if g:
        rw = w // g
        rh = h // g
        reduced = f"{rw}:{rh}"
        if reduced in _OPENROUTER_ALLOWED_ASPECT_RATIOS:
            return reduced

    raw = w / h
    ratio_values = {
        "1:1": 1.0,
        "2:3": 2 / 3,
        "3:2": 3 / 2,
        "3:4": 3 / 4,
        "4:3": 4 / 3,
        "4:5": 4 / 5,
        "5:4": 5 / 4,
        "9:16": 9 / 16,
        "16:9": 16 / 9,
        "21:9": 21 / 9,
    }
    return min(ratio_values.keys(), key=lambda k: abs(ratio_values[k] - raw))

def _truncate_log(text: str, limit: int = 400) -> str:
    if text is None:
        return ""
    t = str(text)
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"

def _looks_like_429_quota(exc: Exception) -> bool:
    msg = str(exc)
    upper = msg.upper()
    # Be conservative: many providers emit 429 for transient rate limits.
    # Treat only explicit Google-style quota exhaustion as quota (prevents false aborts when
    # ImageClient mixes provider cooldown + a different provider's 429 in one error message).
    return ("429" in msg) and ("RESOURCE_EXHAUSTED" in upper)

def _extract_provider_cooldown_seconds(exc: Exception) -> int | None:
    try:
        m = re.search(r"cooldown for ~(\d+)s", str(exc))
        if not m:
            return None
        return int(m.group(1))
    except Exception:
        return None

def _looks_like_daily_quota(exc: Exception) -> bool:
    lower = str(exc).lower()
    return (
        "generate_requests_per_model_per_day" in lower
        or "generaterequestsperdayperprojectpermodel" in lower
    )

def _reset_429_counter():
    """429カウンターをリセット（成功時に呼ぶ）"""
    global _CONSECUTIVE_429_COUNT
    _CONSECUTIVE_429_COUNT = 0

def _increment_429_counter():
    """429カウンターをインクリメントし、閾値チェック"""
    global _CONSECUTIVE_429_COUNT, _SUCCESSFUL_IMAGE_COUNT, _MAX_CONSECUTIVE_429
    _CONSECUTIVE_429_COUNT += 1
    if _CONSECUTIVE_429_COUNT >= _MAX_CONSECUTIVE_429:
        raise QuotaExhaustedError(
            f"Gemini API 429エラーが{_MAX_CONSECUTIVE_429}回連続発生。APIクォータ制限と思われます。",
            successful_count=_SUCCESSFUL_IMAGE_COUNT,
            failed_count=_CONSECUTIVE_429_COUNT
        )

def _increment_success_counter():
    """成功カウンターをインクリメント"""
    global _SUCCESSFUL_IMAGE_COUNT
    _SUCCESSFUL_IMAGE_COUNT += 1
    _reset_429_counter()

def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _ensure_pillow():
    try:
        import PIL  # noqa
    except Exception:
        raise RuntimeError(
            "Pillow is required for placeholder image generation when external CLI is unavailable.\n"
            "Install with: pip install Pillow"
        )


def _make_placeholder_png(path: str, width: int, height: int, text: str):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (width, height), color=(32, 32, 48))
    draw = ImageDraw.Draw(img)

    # Try load a font; fallback to default
    try:
        font = ImageFont.truetype("Arial.ttf", size=max(24, min(width, height) // 20))
    except Exception:
        font = ImageFont.load_default()

    margin = 40
    wrapped = []
    words = text.split()
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) < (width - margin * 2):
            cur = test
        else:
            if cur:
                wrapped.append(cur)
            cur = w
    if cur:
        wrapped.append(cur)

    y = height // 2 - (len(wrapped) * (font.size + 6)) // 2
    for line in wrapped[:8]:
        w = draw.textlength(line, font=font)
        draw.text(((width - w) / 2, y), line, fill=(235, 235, 235), font=font)
        y += font.size + 6

    img.save(path)

@lru_cache(maxsize=64)
def _load_persona(run_dir: Path) -> str:
    """
    Load persona text from run_dir if available.
    Priority:
      1) persona.txt (plain text)
      2) persona.json (string or dict of fields)
    """
    try:
        txt_path = run_dir / "persona.txt"
        if txt_path.exists():
            content = txt_path.read_text(encoding="utf-8").strip()
            if content:
                return content
        json_path = run_dir / "persona.json"
        if json_path.exists():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, str):
                return data.strip()
            if isinstance(data, dict):
                parts = []
                for k, v in data.items():
                    if isinstance(v, (str, int, float)):
                        parts.append(f"{k}: {v}")
                    elif isinstance(v, list):
                        parts.append(f"{k}: " + ", ".join(map(str, v)))
                return "\n".join(parts).strip()
    except Exception as e:
        logging.warning("Failed to load persona: %s", e)
    return ""

@lru_cache(maxsize=64)
def _load_persona_mode(run_dir: Path) -> str:
    """
    Optional persona_mode.txt with values:
      - \"auto\" (default): use persona when file exists
      - \"off\": disable persona even if persona file exists
      - \"on\": force persona when file exists
    """
    mode_file = run_dir / "persona_mode.txt"
    if mode_file.exists():
        try:
            mode = mode_file.read_text(encoding="utf-8").strip().lower()
            if mode in ("auto", "off", "on"):
                return mode
        except Exception:
            pass
    return "auto"


def _convert_to_16_9(path: str, target_width: int, target_height: int):
    """Convert generated image to 16:9 aspect ratio if needed"""
    _ensure_pillow()
    from PIL import Image
    
    try:
        with Image.open(path) as img:
            w, h = img.size
            if w <= 0 or h <= 0:
                logging.info("Skipping conversion for invalid size %s: %s", img.size, path)
                return

            current_ratio = w / h
            target_ratio = target_width / target_height

            # Fast path: already exact target.
            if abs(current_ratio - target_ratio) < 0.01 and (w, h) == (target_width, target_height):
                logging.info("Image already has correct aspect ratio and size: %s", path)
                return

            out = img

            # Center-crop to 16:9 if needed.
            if abs(current_ratio - target_ratio) >= 0.01:
                if current_ratio > target_ratio:
                    # Too wide → crop width.
                    new_w = int(round(h * target_ratio))
                    new_w = max(1, min(w, new_w))
                    left = (w - new_w) // 2
                    box = (left, 0, left + new_w, h)
                else:
                    # Too tall → crop height.
                    new_h = int(round(w / target_ratio))
                    new_h = max(1, min(h, new_h))
                    top = (h - new_h) // 2
                    box = (0, top, w, top + new_h)
                out = img.crop(box)

            # Resize to target dimensions (ensures CapCut-friendly consistency).
            if out.size != (target_width, target_height):
                out = out.resize((target_width, target_height), Image.LANCZOS)

            out.save(path)
            logging.info("Converted image to 16:9 (%dx%d): %s", target_width, target_height, path)
            
    except Exception as e:
        logging.warning("Failed to convert image to 16:9 aspect ratio %s: %s", path, e)


def _run_cli(prompt: str, output_path: str, width: int, height: int, bin_path: str | None, timeout_sec: int, input_images: list[str] | None = None, retry_count: int = 6) -> bool:
    # Support two styles:
    #  1) Generic: `nanobanana --prompt "..." --output out.png --size WxH`
    #  2) DD wrapper: `ddnanobanana generate "..." --output out.png --no-show`
    exe = bin_path or _which("nanobanana") or _which("ddnanobanana")
    if not exe:
        logging.warning("nanobanana CLI not found. Set --nanobanana-bin or $NANOBANANA_BIN.")
        return False
    exe_name = os.path.basename(exe)
    cmds = []
    if "ddnano" in exe_name or exe_name.endswith("nano_banana_cli.py"):
        # ddnanobanana doesn't support --size parameter - rely on prompt guidance for 16:9
        base = [exe, "generate", prompt, "--output", output_path, "--no-show"]
        if input_images:
            for img in input_images:
                base += ["--input", img]
        cmds.append(base[:])
    else:
        # Generic CLI with forced 16:9 dimensions (1920x1080)
        base_gen = [exe, "--prompt", prompt, "--output", output_path, "--size", f"{width}x{height}"]
        if input_images:
            for img in input_images:
                base_gen += ["--input", img]
        cmds.append(base_gen)

    env = os.environ.copy()
    # config is already loaded and env vars populated, no need to call _load_dotenv_env
    
    # Enhanced retry logic with exponential backoff and better error handling
    import time

    infinite = retry_count is None or retry_count <= 0
    attempt = 0
    while True:
        retry = attempt
        for cmd in cmds:
            try:
                result = subprocess.run(cmd, check=True, timeout=timeout_sec, env=env, 
                                      capture_output=True, text=True)
                logging.info("nanobanana CLI succeeded on attempt %d", retry + 1)
                return True
            except subprocess.TimeoutExpired:
                logging.warning("nanobanana CLI timeout on attempt %d (timeout: %ds)", retry + 1, timeout_sec)
                if infinite or retry < retry_count - 1:
                    wait_time = min(60, 2 ** retry * 5)  # Exponential backoff: 5s, 10s, 20s, max 60s
                    logging.info("Retrying in %ds...", wait_time)
                    time.sleep(wait_time)
                    break
            except subprocess.CalledProcessError as e:
                # Capture both stdout and stderr for comprehensive error analysis
                error_output = (e.stdout or "") + (e.stderr or "") + str(e)
                
                # Smart retry conditions - avoid meaningless retries
                should_retry = False
                wait_time = 0
                error_type = "Unknown Error"
                
                # Critical: 42% error rate from Google Console - scientific retry approach
                if "500 INTERNAL" in error_output:
                    # Success probability: (1-0.42)^5 = 92.4% with 5 attempts
                    # This accounts for the documented 42% error rate
                    if retry < 5:  # Allow up to 5 retries for statistical success
                        should_retry = True
                        # Optimized wait times for 42% error rate: 3s, 6s, 9s, 12s, 15s
                        wait_time = 3 + (retry * 3)  # Faster cycle for statistical success
                        error_type = f"Server Error (500) - Retry {retry + 1}/5 (targeting 92% success rate)"
                    else:
                        error_type = "Server Error (500) - Statistical retries exhausted"
                        
                elif "429" in error_output or "quota" in error_output.lower() or "rate limit" in error_output.lower():
                    # Rate limits: exponential backoff but cap at 2 retries
                    if retry < 2:
                        should_retry = True
                        wait_time = min(300, 60 * (2 ** retry))  # 60s, 120s, max 300s
                        error_type = f"Rate Limit (attempt {retry + 1})"
                    else:
                        error_type = "Rate Limit - Max Retries Exceeded"
                        
                elif "503" in error_output or "temporarily unavailable" in error_output.lower():
                    # Service unavailable: single retry with longer wait
                    if retry == 0:
                        should_retry = True
                        wait_time = 60
                        error_type = "Service Unavailable - Single Retry"
                    else:
                        error_type = "Service Unavailable - Persistent, No Retry"
                        
                elif "network" in error_output.lower() or "connection" in error_output.lower():
                    # Network issues: allow 2 retries with short waits
                    if retry < 2:
                        should_retry = True
                        wait_time = 10 * (retry + 1)  # 10s, 20s
                        error_type = f"Network Error (attempt {retry + 1})"
                    else:
                        error_type = "Network Error - Max Retries Exceeded"
                        
                elif "timeout" in error_output.lower():
                    # Timeout: single retry
                    if retry == 0:
                        should_retry = True
                        wait_time = 15
                        error_type = "Timeout - Single Retry"
                    else:
                        error_type = "Timeout - Persistent"
                else:
                    error_type = "Unknown Error - No Retry"
                
                if should_retry and (infinite or retry < retry_count - 1):
                    logging.warning("nanobanana CLI attempt %d failed with %s, retrying in %ds. Error: %s", 
                                  retry + 1, error_type, wait_time, error_output[:300])
                    time.sleep(wait_time)
                    break
                else:
                    logging.error("nanobanana CLI attempt %d failed with %s (final). Full error: %s", 
                                retry + 1, error_type, error_output[:500])
                    if infinite:
                        time.sleep(5)
                        break
                    else:
                        continue
            except Exception as e:
                logging.warning("nanobanana CLI attempt %d failed with unexpected error: %s", retry + 1, str(e))
                if infinite or retry < retry_count - 1:
                    wait_time = min(30, 2 ** retry * 5)  # Standard backoff for unexpected errors
                    logging.info("Retrying in %ds...", wait_time)
                    time.sleep(wait_time)
                    break
                else:
                    continue
        else:
            # No command succeeded and no early-break; advance attempt
            pass

        attempt += 1
        if not infinite and attempt >= retry_count:
            break

    logging.error("All nanobanana CLI attempts failed after %d retries", retry_count)
    return False


def _run_mcp(prompt: str, output_path: str, width: int, height: int) -> bool:
    # Placeholder for MCP integration. For now, return False to use placeholder image.
    logging.info("MCP mode not configured; falling back to placeholder image for %s", output_path)
    return False


def _run_direct(
    prompt: str,
    output_path: str,
    width: int,
    height: int,
    config_path: str | None,
    timeout_sec: int,
    input_images: list[str] | None = None,
    model_key: str | None = None,
    seed: int | None = None,
    *,
    max_retries: int = 3,
) -> bool:
    image_client: ImageClient | None = None
    router = None

    if not USE_LEGACY_IMAGE_ROUTER:
        try:
            image_client = ImageClient()
        except Exception as exc:
            logging.error("ImageClient initialization failed: %s", exc)

    if image_client is None and USE_LEGACY_IMAGE_ROUTER:
        from factory_common.llm_router import get_router

        router = get_router()
    
    # Legacy chat payload removed; ImageClient handles prompt-only image generation.

    aspect_ratio = _normalize_openrouter_aspect_ratio(width, height)

    max_retries = max(1, int(max_retries))
    # Cooldown-aware retries: provider cooldown may be minutes; do not burn retry budget immediately.
    try:
        cooldown_max_total = int(os.getenv("SRT2IMAGES_IMAGE_COOLDOWN_MAX_TOTAL_SEC", "1800"))
    except ValueError:
        cooldown_max_total = 1800
    try:
        cooldown_sleep_cap = int(os.getenv("SRT2IMAGES_IMAGE_COOLDOWN_SLEEP_CAP_SEC", "60"))
    except ValueError:
        cooldown_sleep_cap = 60
    cooldown_slept = 0

    attempt = 0
    while attempt < max_retries:
        try:
            if image_client is not None:
                extra: dict = {"timeout_sec": int(timeout_sec)} if timeout_sec else {}
                raw_af = os.getenv("SRT2IMAGES_IMAGE_ALLOW_FALLBACK")
                if raw_af is not None and raw_af.strip() != "":
                    extra["allow_fallback"] = raw_af.strip().lower() not in ("0", "false", "no", "off")
                mk = (str(model_key).strip() if model_key else "")
                if mk:
                    extra["model_key"] = mk
                options = ImageTaskOptions(
                    task="visual_image_gen",
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    n=1,
                    seed=seed,
                    input_images=input_images,
                    extra=extra,
                )
                result = image_client.generate(options)
                image_data = result.images[0] if result.images else None

                if not image_data:
                    raise ImageGenerationError("No image bytes returned from ImageClient")

                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, 'wb') as f:
                    f.write(image_data)

                run_dir_name = Path(output_path).parent.parent.name
                logging.info(
                    f"[{run_dir_name}][image_gen][OK] engine={result.model} output={output_path}"
                )
                _increment_success_counter()
                return True

        except ImageGenerationError as exc:
            cooldown_sec = _extract_provider_cooldown_seconds(exc)
            if cooldown_sec is not None and cooldown_sec > 0 and cooldown_slept < cooldown_max_total:
                sleep_for = min(cooldown_sec, cooldown_sleep_cap, max(1, cooldown_max_total - cooldown_slept))
                logging.warning(
                    "ImageClient provider cooldown detected; sleeping %ds (slept %d/%ds total)",
                    sleep_for,
                    cooldown_slept,
                    cooldown_max_total,
                )
                time.sleep(float(sleep_for))
                cooldown_slept += int(sleep_for)
                # Do not count cooldown waits against retry budget.
                continue

            if _looks_like_429_quota(exc):
                # If it's clearly a per-day quota, stop immediately (waiting won't help).
                if _looks_like_daily_quota(exc):
                    raise QuotaExhaustedError(
                        "Gemini API daily quota exhausted (429 RESOURCE_EXHAUSTED): "
                        + _truncate_log(exc),
                        successful_count=_SUCCESSFUL_IMAGE_COUNT,
                        failed_count=_CONSECUTIVE_429_COUNT + 1,
                    )
                # Otherwise treat as transient 429; stop after consecutive threshold.
                _increment_429_counter()
            logging.warning(
                "ImageClient generation failed (Attempt %d/%d): %s",
                attempt + 1,
                max_retries,
                exc,
            )
        except Exception as exc:
            logging.error("Unexpected error from ImageClient: %s", exc)

        # Backoff before retrying (counts toward retry budget)
        attempt += 1
        if attempt < max_retries:
            time.sleep(min(10.0, 0.5 * (2 ** (attempt - 1))))

    return False


def _gen_one(cue: Dict, mode: str, force: bool, width: int, height: int, bin_path: str | None, timeout_sec: int, config_path: str | None,
             retry_until_success: bool = False, max_retries: int = 6, placeholder_text: str | None = None):
    import glob
    import os
    import shutil
    
    out_path = cue["image_path"]

    # If this cue already has an injected asset (e.g., stock b-roll mp4), skip image generation.
    # CapCut insertion prefers `asset_relpath` over `images/*.png`.
    rel = ""
    try:
        rel = (cue.get("asset_relpath") or "").strip()
    except Exception:
        rel = ""
    if rel:
        try:
            run_dir = Path(out_path).resolve().parent.parent  # images/.. -> run_dir
            asset_path = (run_dir / rel).resolve()
            if asset_path.exists():
                logging.info("Skip image generation (asset_relpath=%s): %s", rel, out_path)
                return
            logging.warning("asset_relpath set but missing (%s); generating image fallback: %s", asset_path, out_path)
        except Exception:
            # If path resolution fails, fall back to image generation.
            pass

    prompt = cue.get("prompt", cue.get("summary", ""))
    try:
        # Prepend persona if available in run_dir (workspaces/video/runs/<run_id>/persona.*)
        run_dir = Path(out_path).resolve().parent.parent  # images/.. -> run_dir
        persona_mode = _load_persona_mode(run_dir)
        persona = _load_persona(run_dir)
        use_persona_flag = cue.get("use_persona")
        # persona_mode logic:
        # auto: use persona file if exists
        # off: never use persona
        # on: use persona file if exists
        # cue.use_persona overrides: True/False
        enabled = False
        if persona_mode == "off":
            enabled = False
        elif persona_mode in ("auto", "on"):
            enabled = bool(persona)
        if use_persona_flag is True:
            enabled = bool(persona)
        elif use_persona_flag is False:
            enabled = False
        if enabled and persona:
            prompt = f"{persona}\n\n{prompt}"
    except Exception as e:
        logging.warning("Persona load skipped: %s", e)

    if os.path.exists(out_path) and not force:
        # Legacy runs may contain non-16:9 images (e.g. 1:1 1024x1024) from older pipelines.
        # Normalize in-place so resume/draft rebuild always uses 1920x1080 assets.
        try:
            _convert_to_16_9(out_path, width, height)
        except Exception:
            pass
        logging.info("Skip existing %s", out_path)
        return

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Normalize mode for the per-image generator: allow only direct (ImageClient) or none (skip).
    # NOTE: mode=batch is handled at generate_image_batch() level (submit→poll→fetch).
    mode_norm = mode
    if mode_norm not in ("direct", "none"):
        logging.warning("nanobanana mode=%s is deprecated; forcing direct", mode_norm)
        mode_norm = "direct"

    ok = False
    input_images = []
    if isinstance(cue.get("input_images"), list):
        input_images = [str(x) for x in cue["input_images"]]
    if mode_norm == "none":
        logging.info("nanobanana mode=none: skipping image generation for %s", out_path)
        return

    # Only direct path (ImageClient)
    model_key = None
    try:
        mk = cue.get("image_model_key")
        if isinstance(mk, str) and mk.strip():
            model_key = mk.strip()
    except Exception:
        model_key = None
    ok = _run_direct(
        prompt,
        out_path,
        width,
        height,
        config_path,
        timeout_sec,
        input_images=input_images,
        model_key=model_key,
        seed=(int(cue.get("seed")) if str(cue.get("seed") or "").strip().isdigit() else None),
        max_retries=max_retries,
    )
        
    # Log image generation status for direct mode
    if ok:
        run_dir_name = Path(out_path).parent.parent.name  # Get run_dir name for logging
        # Count how many PNG files are in the images directory
        images_dir = Path(out_path).parent
        png_count = len([f for f in images_dir.glob("*.png") if f.is_file()])
        logging.info(f"[{run_dir_name}][image_gen][OK] engine=image_client images={png_count} dir={images_dir}")

    if ok:
        # Convert to 16:9 aspect ratio if needed - handle multiple generated images
        base_path = os.path.splitext(out_path)[0]
        pattern = f"{base_path}*.png"
        generated_files = glob.glob(pattern)
        
        if generated_files:
            # Convert all generated images to 16:9
            for img_file in generated_files:
                _convert_to_16_9(img_file, width, height)
            
            # If multiple images, keep the first one as the main output
            if len(generated_files) > 1:
                main_file = generated_files[0]
                if main_file != out_path:
                    shutil.move(main_file, out_path)
                    logging.info("Selected primary image from %d generated: %s", len(generated_files), out_path)
        else:
            # Fallback to original conversion logic
            _convert_to_16_9(out_path, width, height)
    else:
        if not retry_until_success:
            _ensure_pillow()
            ph_text = placeholder_text if (placeholder_text is not None and len(placeholder_text) > 0) else cue.get("summary", "")
            _make_placeholder_png(out_path, width, height, ph_text)
            run_dir_name = Path(out_path).parent.parent.name  # Get run_dir name for logging
            images_dir = Path(out_path).parent
            png_count = len([f for f in images_dir.glob("*.png") if f.is_file()])
            logging.info(f"[{run_dir_name}][image_gen][FALLBACK] reason='placeholder' placeholders={png_count}")


# 単純なトークンバケット的なレートリミット用のキュー
# 「直近60秒間に何回叩いたか」を見るためのもの
_REQUEST_TIMES: deque[float] = deque()


def _rate_limited_gen_one(cue: Dict, mode: str, force: bool, width: int, height: int, bin_path: str | None, timeout_sec: int, config_path: str | None,
                         retry_until_success: bool, max_retries: int, placeholder_text: str | None, max_per_minute: int):
    """
    `_gen_one` を呼ぶ前に、「1分あたりの最大リクエスト数」を超えないように待機する。
    - max_per_minute: 1分あたりに許可する最大リクエスト数
    """
    window = 60.0  # 秒

    # 古いタイムスタンプ（60秒より前）は捨てる
    now = time.monotonic()
    while _REQUEST_TIMES and (now - _REQUEST_TIMES[0]) >= window:
        _REQUEST_TIMES.popleft()

    # 既に上限個数記録されている場合は、古いものがウィンドウから抜けるまで sleep
    if len(_REQUEST_TIMES) >= max_per_minute:
        oldest = _REQUEST_TIMES[0]
        sleep_for = window - (now - oldest)
        if sleep_for > 0:
            time.sleep(sleep_for)
        # sleep 後に再度古いものを掃除
        now = time.monotonic()
        while _REQUEST_TIMES and (now - _REQUEST_TIMES[0]) >= window:
            _REQUEST_TIMES.popleft()

    # ここまで来たら「呼んでOK」
    _REQUEST_TIMES.append(time.monotonic())

    # 実際に画像生成処理を呼び出す
    _gen_one(cue, mode, force, width, height, bin_path, timeout_sec, config_path,
             retry_until_success, max_retries, placeholder_text)


# ==== Gemini Batch (Developer API Batch) ====
_GEMINI_BATCH_SCHEMA = "ytm.gemini_batch_images.v1"


def _utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _sha256(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_gemini_api_key() -> str:
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise ImageGenerationError(
            "GEMINI_API_KEY is not set.\n"
            "- Recommended: run via ./scripts/with_ytm_env.sh ...\n"
            "- Or export GEMINI_API_KEY in your shell."
        )
    return key


def _infer_run_dir_from_cues(cues: List[Dict[str, Any]]) -> Optional[Path]:
    if not cues:
        return None
    try:
        out_path = str(cues[0].get("image_path") or "").strip()
        if not out_path:
            return None
        p = Path(out_path).expanduser().resolve()
        # images/.. -> run_dir
        return p.parent.parent
    except Exception:
        return None


def _infer_channel_from_run_dir(run_dir: Path) -> str:
    m = re.match(r"^(CH\\d{2})\\b", str(run_dir.name or ""), flags=re.IGNORECASE)
    return m.group(1).upper() if m else ""


def _resolve_model_conf_for_task(*, task: str, selector: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    """
    Resolve a model selector (slot code or model_key) to a concrete model_conf in ImageClient config,
    without making any network calls.
    """
    client = ImageClient()
    task_conf = (client._config.get("tasks", {}) or {}).get(task)  # type: ignore[attr-defined]
    if not isinstance(task_conf, dict):
        raise ImageGenerationError(f"Task '{task}' not found in image model configuration")

    tier_name = str(task_conf.get("tier") or "").strip()
    if not tier_name:
        raise ImageGenerationError(f"Tier is not defined for task '{task}'")

    candidates = (client._config.get("tiers", {}) or {}).get(tier_name)  # type: ignore[attr-defined]
    if not isinstance(candidates, list) or not candidates:
        raise ImageGenerationError(f"No tier candidates found for tier '{tier_name}'")

    model_key: Optional[str] = None
    selector_norm = (str(selector or "").strip() or None)
    if selector_norm:
        model_key = client._resolve_model_key_selector(task=task, selector=selector_norm) or selector_norm  # type: ignore[attr-defined]
    else:
        first = candidates[0]
        model_key = str(first).strip() if isinstance(first, str) and str(first).strip() else None

    if not model_key:
        raise ImageGenerationError(f"Failed to resolve model_key for task '{task}' (selector={selector_norm!r})")

    if (
        lockdown_active()
        and str(task or "").strip() in IMAGE_MODEL_KEY_BLOCKLIST_TASKS
        and model_key in IMAGE_MODEL_KEY_BLOCKLIST
    ):
        raise ImageGenerationError(
            "\n".join(
                [
                    "[LOCKDOWN] Forbidden image model key detected for video images (Gemini 3 image models are not allowed for visual_image_gen).",
                    f"- task: {task}",
                    f"- selector: {selector_norm or '(tier default)'}",
                    f"- resolved_model_key: {model_key}",
                    "- policy: Gemini 3 系の画像モデルは動画内画像では使用禁止です（サムネは許可）。",
                    "- fix: use slot/codes like img-gemini-flash-1 (g-1).",
                ]
            )
        )

    model_conf = (client._config.get("models", {}) or {}).get(model_key)  # type: ignore[attr-defined]
    if not isinstance(model_conf, dict):
        raise ImageGenerationError(f"Model '{model_key}' not found in image model configuration")
    return model_key, model_conf


def _extract_image_b64_parts_from_response_dict(resp: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    candidates = resp.get("candidates") or []
    if not isinstance(candidates, list):
        return out
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        content = cand.get("content") or {}
        if not isinstance(content, dict):
            continue
        parts = content.get("parts") or []
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if not isinstance(inline, dict):
                continue
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            data = inline.get("data") or ""
            if isinstance(mime, str) and isinstance(data, str) and mime.startswith("image/") and data:
                out.append((mime, data))
    return out


def _compute_batch_indices(
    *,
    cues: List[Dict[str, Any]],
    force: bool,
    min_bytes: int,
) -> List[int]:
    """
    Select cue indices to generate via batch.
    - Skip cues with existing injected assets (asset_relpath exists).
    - When not forcing: treat missing or too-small PNGs as targets (placeholder detection).
    """
    out: List[int] = []
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        try:
            idx = int(cue.get("index") or 0)
        except Exception:
            idx = 0
        if idx <= 0:
            continue

        # Skip cues that are backed by an injected asset (e.g., b-roll mp4).
        rel = ""
        try:
            rel = (cue.get("asset_relpath") or "").strip()
        except Exception:
            rel = ""
        if rel:
            try:
                out_path = Path(str(cue.get("image_path") or "")).expanduser().resolve()
                run_dir = out_path.parent.parent
                asset_path = (run_dir / rel).resolve()
                if asset_path.exists():
                    continue
            except Exception:
                pass

        if force:
            out.append(idx)
            continue

        out_path_raw = cue.get("image_path")
        if not isinstance(out_path_raw, str) or not out_path_raw.strip():
            out.append(idx)
            continue
        out_path = Path(out_path_raw).expanduser()
        try:
            if out_path.exists() and out_path.is_file():
                size = int(out_path.stat().st_size)
                if size >= max(0, int(min_bytes)):
                    continue
        except Exception:
            pass
        out.append(idx)
    return sorted(set(out))


def _generate_images_via_gemini_batch(
    *,
    cues: List[Dict[str, Any]],
    force: bool,
    width: int,
    height: int,
    retry_until_success: bool,
    placeholder_text: Optional[str],
    max_retries: int,
) -> None:
    run_dir = _infer_run_dir_from_cues(cues)
    if run_dir is None:
        raise ImageGenerationError("batch mode requires cues[*].image_path to infer run_dir")

    channel = _infer_channel_from_run_dir(run_dir)

    # Prefer a single per-cue selector when present; otherwise fall back to the run-level env override.
    selectors: set[str] = set()
    for cue in cues:
        mk = cue.get("image_model_key") if isinstance(cue, dict) else None
        if isinstance(mk, str) and mk.strip():
            selectors.add(mk.strip())
    selector: Optional[str] = None
    if len(selectors) == 1:
        selector = next(iter(selectors))
    elif len(selectors) > 1:
        raise ImageGenerationError(
            "\n".join(
                [
                    "nanobanana mode=batch requires a single image_model_key.",
                    f"- detected: {', '.join(sorted(selectors))}",
                    "- policy: batch→direct の自動フォールバックは禁止（明示運用）。",
                    "- action: rerun with `--nanobanana direct` (or unify image_model_key).",
                ]
            )
        )

    env_selector = (os.getenv("IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN") or "").strip() or None
    selector = selector or env_selector

    task = "visual_image_gen"
    model_key, model_conf = _resolve_model_conf_for_task(task=task, selector=selector)
    provider = str(model_conf.get("provider") or "").strip().lower()
    model_name = str(model_conf.get("model_name") or "").strip()

    # Only Gemini (generateContent image models) are batchable via this interface.
    if provider != "gemini" or not model_name or model_name.startswith("imagen"):
        raise ImageGenerationError(
            "\n".join(
                [
                    "nanobanana mode=batch: provider/model not batchable.",
                    f"- provider: {provider or '?'}",
                    f"- model: {model_name or '?'}",
                    f"- model_key: {model_key}",
                    "- policy: batch→direct の自動フォールバックは禁止（明示運用）。",
                    "- action: rerun with `--nanobanana direct` (or choose a batchable Gemini image model).",
                ]
            )
        )

    # Placeholder detection threshold (default: 60KB). Keep consistent with UI validation.
    try:
        min_bytes = int(os.getenv("SRT2IMAGES_MIN_IMAGE_BYTES", "60000"))
    except Exception:
        min_bytes = 60000

    indices = _compute_batch_indices(cues=cues, force=force, min_bytes=min_bytes)
    if not indices:
        logging.info("nanobanana mode=batch: nothing to do (all images present).")
        return

    batch_dir = run_dir / "_gemini_batch"
    batch_dir.mkdir(parents=True, exist_ok=True)
    input_jsonl = batch_dir / "batch_input.jsonl"
    manifest_path = batch_dir / "manifest.json"

    # Build JSONL + manifest items.
    id_to_item: Dict[str, Dict[str, Any]] = {}
    lines: List[str] = []
    items: List[Dict[str, Any]] = []
    idx_to_cue: Dict[int, Dict[str, Any]] = {}
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        try:
            idx = int(cue.get("index") or 0)
        except Exception:
            continue
        if idx > 0:
            idx_to_cue[idx] = cue

    for idx in indices:
        cue = idx_to_cue.get(idx) or {}
        prompt = str(cue.get("prompt") or cue.get("summary") or "").strip()
        if not prompt:
            prompt = "Scene illustration. No text."
        out_path = Path(str(cue.get("image_path") or (run_dir / "images" / f"{idx:04d}.png"))).expanduser()
        if not out_path.is_absolute():
            out_path = (repo_root() / out_path).resolve()
        req_id = f"{run_dir.name}#{idx:04d}"
        line = {
            "request": {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt}],
                    }
                ]
            },
            "metadata": {"id": req_id},
        }
        lines.append(json.dumps(line, ensure_ascii=False))
        item = {
            "id": req_id,
            "run_dir": str(run_dir),
            "cue_index": int(idx),
            "output_path": str(out_path),
            "prompt_sha256": _sha256(prompt),
        }
        items.append(item)
        id_to_item[req_id] = item

    input_jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")

    api_key = _resolve_gemini_api_key()
    try:
        import google.genai as genai  # type: ignore
        import google.genai.types as genai_types  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ImageGenerationError(
            "google-genai is required for Gemini Batch. Install: pip install google-genai\n"
            f"Import error: {exc}"
        ) from exc

    client = genai.Client(api_key=api_key)
    uploaded = client.files.upload(
        file=str(input_jsonl),
        config=genai_types.UploadFileConfig(mime_type="application/json"),
    )
    job = client.batches.create(model=model_name, src=str(uploaded.name))
    job_name = str(getattr(job, "name", "") or "")

    manifest = {
        "schema": _GEMINI_BATCH_SCHEMA,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "channel": channel or None,
        "task": task,
        "selector": selector or None,
        "resolved_model_key": model_key,
        "model": model_name,
        "input": {
            "path": str(input_jsonl),
            "uploaded_file": str(getattr(uploaded, "name", "") or ""),
            "count": len(items),
        },
        "job": {
            "name": job_name,
            "state": str(getattr(job, "state", "") or ""),
        },
        "items": items,
    }
    _write_json(manifest_path, manifest)
    logging.info("nanobanana mode=batch: submitted job=%s items=%d out=%s", job_name, len(items), manifest_path)

    # Poll until finished (batch can be slow; waiting is the default behavior for the production pipeline).
    poll_sec = int(os.getenv("SRT2IMAGES_GEMINI_BATCH_POLL_SEC", "30") or 30)
    while True:
        j = client.batches.get(name=job_name)
        state = str(getattr(j, "state", "") or "")
        if "SUCCEEDED" in state or "JOB_STATE_SUCCEEDED" in state:
            break
        if "FAILED" in state or "CANCELLED" in state:
            raise ImageGenerationError(f"Gemini Batch job failed: {job_name} state={state}")
        logging.info("nanobanana mode=batch: waiting job=%s state=%s", job_name, state)
        time.sleep(max(5, poll_sec))

    # Fetch results (inline or file download).
    j = client.batches.get(name=job_name)
    dest = getattr(j, "dest", None)
    if dest is None:
        raise ImageGenerationError("Gemini Batch job has no destination")

    decoded: Dict[str, bytes] = {}
    errors: List[str] = []

    inlined = getattr(dest, "inlined_responses", None)
    if isinstance(inlined, list) and inlined:
        # Inline responses: order matches input request order; metadata may be absent.
        if len(inlined) != len(items):
            logging.warning("inlined_responses count mismatch: dest=%d items=%d", len(inlined), len(items))
        for i, item in enumerate(items):
            if i >= len(inlined):
                break
            resp_obj = inlined[i]
            err = getattr(resp_obj, "error", None)
            if err:
                errors.append(f"{item['id']}: {err}")
                continue
            resp = getattr(resp_obj, "response", None)
            try:
                resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else {}
            except Exception:
                resp_dict = {}
            parts = _extract_image_b64_parts_from_response_dict(resp_dict)
            if not parts:
                errors.append(f"{item['id']}: no inline image parts")
                continue
            _mime, b64_data = parts[0]
            try:
                decoded[item["id"]] = base64.b64decode(b64_data)
            except Exception:
                errors.append(f"{item['id']}: base64 decode failed")
        # proceed to write
    else:
        file_name = getattr(dest, "file_name", None)
        if not (isinstance(file_name, str) and file_name.strip()):
            raise ImageGenerationError("No results found in batch destination")

        raw_name = str(file_name).strip()
        name = raw_name.split("files/", 1)[1] if raw_name.startswith("files/") else raw_name
        url = f"https://generativelanguage.googleapis.com/v1beta/files/{name}:download"
        headers = {"x-goog-api-key": api_key}
        params = {"alt": "media"}

        import requests  # local import; optional dependency already used in repo

        with requests.get(url, headers=headers, params=params, stream=True, timeout=600) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                obj = json.loads(line)
                meta = obj.get("metadata") or {}
                if not isinstance(meta, dict):
                    continue
                rid = str(meta.get("id") or "").strip()
                if not rid:
                    continue
                if rid not in id_to_item:
                    continue
                if obj.get("error"):
                    errors.append(f"{rid}: {obj.get('error')}")
                    continue
                resp = obj.get("response") or {}
                if not isinstance(resp, dict):
                    errors.append(f"{rid}: missing response")
                    continue
                parts = _extract_image_b64_parts_from_response_dict(resp)
                if not parts:
                    errors.append(f"{rid}: no inline image parts")
                    continue
                _mime, b64_data = parts[0]
                try:
                    decoded[rid] = base64.b64decode(b64_data)
                except Exception:
                    errors.append(f"{rid}: base64 decode failed")

    # Write images (+ convert to 16:9).
    failed_indices: List[int] = []
    for item in items:
        rid = str(item.get("id") or "")
        out_path = Path(str(item.get("output_path") or "")).expanduser()
        if not out_path.is_absolute():
            out_path = (repo_root() / out_path).resolve()
        img_bytes = decoded.get(rid)
        if not img_bytes:
            failed_indices.append(int(item.get("cue_index") or 0))
            continue
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if force and out_path.exists() and out_path.is_file():
                bdir = run_dir / "images" / f"_backup_{_utc_stamp()}"
                bdir.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(out_path, bdir / out_path.name)
                except Exception:
                    pass
            out_path.write_bytes(img_bytes)
            _convert_to_16_9(str(out_path), width, height)
        except Exception as exc:
            errors.append(f"{rid}: write failed: {exc}")
            failed_indices.append(int(item.get("cue_index") or 0))

    if failed_indices or errors:
        msg = f"nanobanana mode=batch: completed with errors (failed_indices={sorted(set([i for i in failed_indices if i>0]))[:10]}... total_failed={len(set([i for i in failed_indices if i>0]))} errors={len(errors)})"
        if retry_until_success:
            raise ImageGenerationError(msg + " (retry_until_success=true)")

        _ensure_pillow()
        for idx in sorted(set([i for i in failed_indices if i > 0])):
            out_path = run_dir / "images" / f"{idx:04d}.png"
            try:
                _make_placeholder_png(
                    str(out_path),
                    width,
                    height,
                    placeholder_text or "画像生成に失敗しました（batch）。後で再生成してください。",
                )
            except Exception:
                pass
        logging.warning(msg)

    # Summary log (keep consistent with direct mode summary).
    images_dir = run_dir / "images"
    requested = len(indices)
    ok = 0
    for idx in indices:
        p = images_dir / f"{idx:04d}.png"
        try:
            if p.exists() and p.is_file() and p.stat().st_size > 0:
                ok += 1
        except Exception:
            pass
    try:
        dir_total = len([f for f in images_dir.glob("*.png") if f.is_file()]) if images_dir.exists() else 0
    except Exception:
        dir_total = 0
    run_dir_name = run_dir.name
    logging.info(
        f"[{run_dir_name}][image_gen][OK] mode=batch requested={requested} ok={ok} dir_total={dir_total} dir={images_dir}"
    )


def generate_image_batch(cues: List[Dict], mode: str, concurrency: int, force: bool, width: int, height: int, bin_path: str | None = None, timeout_sec: int = 300, config_path: str | None = None,
                         retry_until_success: bool = False, max_retries: int = 6, placeholder_text: str | None = None):
    """
    複数のプロンプトから画像を生成するが、
    - **完全に直列処理（並列禁止）**
    - かつ 1分あたりのリクエスト数を `max_per_minute` 以下に制御する

    max_per_minute が None の場合は、環境変数 SRT2IMAGES_IMAGE_MAX_PER_MINUTE
    （未設定なら 30）を上限として使う。
    """
    # 環境変数から1分あたりの最大リクエスト数を取得 (デフォルト: 10 - 429対策で厳しく)
    env_value = os.getenv("SRT2IMAGES_IMAGE_MAX_PER_MINUTE", "10")
    try:
        max_per_minute = int(env_value)
    except ValueError:
        max_per_minute = 10  # 万一おかしな値が来ても安全側に倒す

    mode_norm = (mode or "").strip().lower()
    if mode_norm == "batch":
        _generate_images_via_gemini_batch(
            cues=cues,
            force=force,
            width=width,
            height=height,
            retry_until_success=retry_until_success,
            placeholder_text=placeholder_text,
            max_retries=max_retries,
        )
        return

    # ★ここが「完全直列」のポイント★
    # ThreadPoolExecutor や asyncio.gather 等は一切使わず、
    # 1プロンプトずつ順番に叩いていく
    previous_image_path: str | None = None
    for cue in cues:
        # If persona/character consistency is required, feed the previous generated frame
        # as an additional reference image (guide + prev). This reduces identity drift.
        try:
            if previous_image_path and cue.get("use_persona") is True:
                cur_inputs = cue.get("input_images")
                if not isinstance(cur_inputs, list):
                    cur_inputs = []
                # Preserve order (guide first), avoid duplicates.
                merged_inputs: list[str] = []
                for item in cur_inputs:
                    s = str(item).strip()
                    if s and s not in merged_inputs:
                        merged_inputs.append(s)
                if previous_image_path not in merged_inputs:
                    merged_inputs.append(previous_image_path)
                cue["input_images"] = merged_inputs
        except Exception:
            pass

        _rate_limited_gen_one(
            cue, mode, force, width, height, bin_path, timeout_sec, config_path,
            retry_until_success, max_retries, placeholder_text, max_per_minute
        )
        try:
            out_path = cue.get("image_path")
            if isinstance(out_path, str) and out_path:
                p = Path(out_path)
                if p.exists() and p.is_file():
                    previous_image_path = str(p)
        except Exception:
            pass
    
    # Log a summary of the image generation results
    if len(cues) > 0:
        run_dir = Path(cues[0]['image_path']).parent.parent
        run_dir_name = run_dir.name
        images_dir = run_dir / "images"
        expected = len(cues)

        # When regenerating a subset (e.g. only cue 28/30/36), images_dir may already contain
        # many PNGs from the full run. We must validate *requested* outputs, not directory totals.
        requested: list[str] = []
        existing: list[str] = []
        for cue in cues:
            try:
                out_path = cue.get("image_path")
                if not isinstance(out_path, str) or not out_path.strip():
                    continue
                p = Path(out_path).resolve()
                requested.append(p.name)
                if p.exists() and p.is_file() and p.stat().st_size > 0:
                    existing.append(p.name)
            except Exception:
                continue

        got = len(existing)
        try:
            dir_total = len([f for f in images_dir.glob("*.png") if f.is_file()]) if images_dir.exists() else 0
        except Exception:
            dir_total = 0

        if mode == "direct":
            logging.info(
                f"[{run_dir_name}][image_gen][OK] mode=direct requested={expected} ok={got} dir_total={dir_total} dir={images_dir}"
            )
        elif mode == "none":
            logging.info(f"[{run_dir_name}][image_gen][SKIP] reason='mode none' requested={expected}")
        else:
            logging.info(
                f"[{run_dir_name}][image_gen][BATCH_COMPLETE] mode={mode} requested={expected} ok={got} dir_total={dir_total}"
            )

        if got != expected:
            missing = sorted(set(requested) - set(existing))
            logging.warning(
                "[%s][image_gen][MISMATCH] expected=%d got=%d missing=%s",
                run_dir_name,
                expected,
                got,
                ",".join(missing[:10]) + ("..." if len(missing) > 10 else ""),
            )
