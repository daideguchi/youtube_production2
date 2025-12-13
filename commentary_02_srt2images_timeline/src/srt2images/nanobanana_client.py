from __future__ import annotations
import logging
import os
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
from typing import Dict, List
from functools import lru_cache

from factory_common.image_client import (
    ImageClient,
    ImageGenerationError,
    ImageTaskOptions,
)
from factory_common.paths import repo_root

try:
    from commentary_02_srt2images_timeline.src.core.config import config
except ImportError:
    # Fallback to relative import if the package isn't properly installed
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root / "src"))
    from core.config import config


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

def _truncate_log(text: str, limit: int = 400) -> str:
    if text is None:
        return ""
    t = str(text)
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"

def _looks_like_429_quota(exc: Exception) -> bool:
    msg = str(exc)
    upper = msg.upper()
    lower = msg.lower()
    return ("429" in msg) and ("RESOURCE_EXHAUSTED" in upper or "quota" in lower)

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

# SSOT: UI保存時に書き出されるフェーズ別モデル定義
LLM_REGISTRY_PATH = repo_root() / "configs" / "llm_registry.json"


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
        img = Image.open(path)
        current_ratio = img.size[0] / img.size[1]
        target_ratio = target_width / target_height
        
        # If already correct ratio, skip
        if abs(current_ratio - target_ratio) < 0.01:
            logging.info("Image already has correct aspect ratio: %s", path)
            return
        
        # For 1024x1024 -> 1920x1080 conversion
        if img.size == (1024, 1024) and (target_width, target_height) == (1920, 1080):
            # Center crop to 16:9 then resize
            crop_height = int(1024 * 9 / 16)  # 576
            crop_y = (1024 - crop_height) // 2  # 224
            
            # Crop to 16:9 aspect ratio
            cropped = img.crop((0, crop_y, 1024, crop_y + crop_height))
            
            # Resize to target dimensions
            resized = cropped.resize((target_width, target_height), Image.LANCZOS)
            
            # Save back to original path
            resized.save(path)
            logging.info("Converted image to 16:9 (%dx%d): %s", target_width, target_height, path)
        else:
            logging.info("Skipping conversion for size %s to %dx%d: %s", img.size, target_width, target_height, path)
            
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


def _run_direct(prompt: str, output_path: str, width: int, height: int, config_path: str | None, timeout_sec: int, input_images: list[str] | None = None) -> bool:
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
    
    # Retry configuration (削減: 5→3)
    max_retries = 3
    
    messages = [
        {"role": "user", "content": prompt}
    ]

    aspect_ratio = f"{width}:{height}" if width and height else None

    for attempt in range(max_retries + 1):
        try:
            if image_client is not None:
                options = ImageTaskOptions(
                    task="visual_image_gen",
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    n=1,
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
            image_client = None
        except Exception as exc:
            logging.error("Unexpected error from ImageClient: %s", exc)
            image_client = None

        # Legacy router path removed; if ImageClient failed, retry loop continues

    return False


def _gen_one(cue: Dict, mode: str, force: bool, width: int, height: int, bin_path: str | None, timeout_sec: int, config_path: str | None,
             retry_until_success: bool = False, max_retries: int = 6, placeholder_text: str | None = None):
    import glob
    import os
    import shutil
    
    out_path = cue["image_path"]
    prompt = cue.get("prompt", cue.get("summary", ""))
    try:
        # Prepend persona if available in run_dir (output/<run>/persona.*)
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
        logging.info("Skip existing %s", out_path)
        return

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Normalize mode: allow only direct (ImageClient) or none (skip)
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
    ok = _run_direct(prompt, out_path, width, height, config_path, timeout_sec, input_images=input_images)
        
    # Log image generation status for direct mode
    if ok:
        run_dir_name = Path(out_path).parent.parent.name  # Get run_dir name for logging
        # Count how many PNG files are in the images directory
        images_dir = Path(out_path).parent
        png_count = len([f for f in images_dir.glob("*.png") if f.is_file()])
        logging.info(f"[{run_dir_name}][image_gen][OK] engine=gemini_2_5_flash_image images={png_count} dir={images_dir}")

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

    # ★ここが「完全直列」のポイント★
    # ThreadPoolExecutor や asyncio.gather 等は一切使わず、
    # 1プロンプトずつ順番に叩いていく
    for cue in cues:
        _rate_limited_gen_one(
            cue, mode, force, width, height, bin_path, timeout_sec, config_path,
            retry_until_success, max_retries, placeholder_text, max_per_minute
        )
    
    # Log a summary of the image generation results
    if len(cues) > 0:
        run_dir = Path(cues[0]['image_path']).parent.parent
        run_dir_name = run_dir.name
        images_dir = run_dir / "images"
        expected = len(cues)
        png_names = []
        if images_dir.exists():
            png_names = [f.name for f in images_dir.glob("*.png") if f.is_file()]
            png_count = len(png_names)
        else:
            png_count = 0

        if mode == "direct":
            logging.info(f"[{run_dir_name}][image_gen][OK] engine=gemini_2_5_flash_image images={png_count} dir={images_dir}")
        elif mode == "none":
            logging.info(f"[{run_dir_name}][image_gen][SKIP] reason='mode none' images=0")
        else:
            logging.info(f"[{run_dir_name}][image_gen][BATCH_COMPLETE] mode={mode} total_images={png_count}")

        if png_count != expected:
            # Detect missing frames early so downstream (CapCut) doesn't fail silently
            missing = sorted(
                {f"{i:04d}.png" for i in range(1, expected + 1)} - set(png_names)
            )
            logging.warning(
                "[%s][image_gen][MISMATCH] expected=%d got=%d missing=%s",
                run_dir_name,
                expected,
                png_count,
                ",".join(missing[:10]) + ("..." if len(missing) > 10 else ""),
            )
