
import sys
import os
import json
import logging
from pathlib import Path
import shutil

from factory_common.paths import video_pkg_root, video_input_root, video_runs_root

# Add video package src to path for srt2images imports
pkg_src = video_pkg_root() / "src"
if str(pkg_src) not in sys.path:
    sys.path.insert(0, str(pkg_src))

from srt2images.generators import get_image_generator
from srt2images.engines.capcut_engine import build_capcut_draft
from srt2images.orchestration.utils import ensure_out_dirs, setup_logging, save_json, parse_size

def run_skip_llm():
    # 設定 - use paths SSOT
    srt_path = video_input_root() / "CH01_人生の道標" / "220.srt"
    src_out_dir = video_runs_root() / "jinsei220_resilient_v2"  # LLM済み
    dest_out_dir = video_runs_root() / "jinsei220_resilient_v3"  # 新規出力
    
    # 疑似引数オブジェクト
    class Args:
        nanobanana = "direct"
        nanobanana_bin = None
        nanobanana_timeout = 300
        nanobanana_config = None
        concurrency = 1
        force = False
        engine = "capcut"
        fps = 30
        crossfade = 1.0
        fit = "cover"
        margin = 0
        style = "jinsei_standard_v2" # 仮
        srt = srt_path
        channel = "CH01"
        
    args = Args()

    # 出力先準備
    if dest_out_dir.exists():
        shutil.rmtree(dest_out_dir)
    ensure_out_dirs(dest_out_dir)
    setup_logging(dest_out_dir)
    
    # image_cues.json のコピーと修正
    src_cues_path = src_out_dir / "image_cues.json"
    if not src_cues_path.exists():
        print(f"Error: {src_cues_path} not found.")
        return

    data = json.loads(src_cues_path.read_text(encoding="utf-8"))
    cues = data["cues"]
    size = data.get("size", {"width": 1920, "height": 1080})

    # 画像パスの書き換え
    for i, cue in enumerate(cues, start=1):
        # image_pathを新規生成
        filename = f"{i:04d}.png"
        cue["image_path"] = str(dest_out_dir / "images" / filename)
        # promptがない場合の保険
        if "prompt" not in cue:
            cue["prompt"] = cue.get("refined_prompt") or cue.get("summary") or "Scene"

    # 保存
    save_json(dest_out_dir / "image_cues.json", data)
    
    # 画像生成 (Gemini 2.5 Flash Image 強制)
    os.environ["SRT2IMAGES_IMAGE_MODEL"] = "gemini-2.5-flash-image"
    os.environ["SRT2IMAGES_IMAGE_MAX_PER_MINUTE"] = "5"
    os.environ["SRT2IMAGES_WAIT_BEFORE_IMAGES"] = "5"

    image_generator = get_image_generator(args)
    if image_generator:
        print("Starting image generation (Skipping LLM Analysis)...")
        try:
            image_generator.generate_batch(
                cues=cues,
                concurrency=args.concurrency,
                force=args.force,
                width=size["width"],
                height=size["height"],
            )
        except Exception as e:
            print(f"Error during generation: {e}")
            # 失敗してもドラフト作成へ進む（プレースホルダー対応）

    # CapCutドラフト作成
    print("Building CapCut draft...")
    draft_dir = build_capcut_draft(
        out_dir=dest_out_dir,
        cues=cues,
        fps=args.fps,
        crossfade=args.crossfade,
        size=size,
    )
    print(f"CapCut draft prepared at: {draft_dir}")

if __name__ == "__main__":
    run_skip_llm()
