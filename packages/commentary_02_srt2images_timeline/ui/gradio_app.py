#!/usr/bin/env python3
"""
Gradio UI for SRT2Images CapCut Draft Generation
シンプルな3ステップワークフロー
"""

import gradio as gr
import subprocess
import os
import json
import glob
import time
from pathlib import Path
from typing import List, Tuple, Optional, Dict
import sys

# プロジェクトルート
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.append(str(PROJECT_ROOT / "src"))

from config.template_registry import get_active_templates, resolve_template_path  # noqa: E402

_env_draft_root = os.getenv("CAPCUT_DRAFT_ROOT")
DRAFT_ROOT = (
    Path(_env_draft_root).expanduser()
    if _env_draft_root
    else Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
)
WHITELIST_PATH = PROJECT_ROOT / "config" / "track_whitelist.json"

def _load_image_templates() -> Dict[str, str]:
    active = get_active_templates()
    return {entry.label: entry.id for entry in active}


IMAGE_TEMPLATES = _load_image_templates()

CAPCUT_TEMPLATES = {
    "人生の道標_最新テンプレ": "人生の道標_最新テンプレ",
    "シニアの朗読テンプレ": "000_シニアの朗読テンプレ",
    "シニアの朗読_テンプレ改": "000_シニアの朗読_テンプレ改",
}

STYLE_PRESETS = {
    "人生の道標": "Warm Japanese illustration, calm storytelling",
    "シニア恋愛": "heartwarming senior love story, Japanese aesthetic",
    "シニア健康": "soft, warm, gentle, friendly, pastel palette",
    "ファンタジー清楚": "fantasy, elegant, clean, Japanese aesthetic",
}


def get_available_srt_files() -> List[str]:
    """input/ディレクトリのSRTファイル一覧を取得"""
    input_dir = PROJECT_ROOT / "input"
    if not input_dir.exists():
        return []

    srt_files = []
    for srt_path in input_dir.rglob("*.srt"):
        # プロジェクトルートからの相対パスで表示
        relative_path = str(srt_path.relative_to(PROJECT_ROOT))
        srt_files.append(relative_path)

    return sorted(srt_files)


def get_available_capcut_templates() -> List[str]:
    """利用可能なCapCutテンプレートを取得"""
    if not DRAFT_ROOT.exists():
        return list(CAPCUT_TEMPLATES.values())

    templates = []
    for name in DRAFT_ROOT.iterdir():
        if name.is_dir():
            templates.append(name.name)

    return sorted(templates)


def run_phase0(srt_dropdown, srt_file, target_sections: int, progress=gr.Progress()) -> Tuple[str, Optional[str]]:
    """Phase 0: 文脈セクション確認"""
    # ドロップダウンまたはアップロードファイルから選択
    if srt_dropdown:
        srt_path = str(PROJECT_ROOT / srt_dropdown)
    elif srt_file is not None:
        srt_path = srt_file.name
    else:
        return "❌ SRTファイルを選択またはアップロードしてください", None

    progress(0, desc="Phase 0: 文脈セクション分析中...")
    output_dir = PROJECT_ROOT / "output" / "latest"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    cmd = [
        "python3", "-m", "srt2images.cli",
        "--srt", srt_path,
        "--out", str(output_dir),
        "--engine", "none",
        "--nanobanana", "none",
        "--force"
    ]

    env = os.environ.copy()
    env["SRT2IMAGES_TARGET_SECTIONS"] = str(target_sections)
    # PYTHONPATHにsrc/を追加
    pythonpath = str(PROJECT_ROOT / "src")
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = f"{pythonpath}:{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = pythonpath
    
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        progress(1.0, desc="Phase 0: 完了")
        
        if result.returncode == 0:
            cues_file = output_dir / "image_cues.json"
            if cues_file.exists():
                with open(cues_file) as f:
                    cues = json.load(f)
                
                summary = f"""✅ Phase 0 完了！

📊 セクション分析結果:
- 総セクション数: {len(cues)}セグメント
- 設定ファイル: {cues_file}

次のステップ:
1. image_cues.json を確認
2. 問題なければ Phase 1 で画像生成
3. 手直しが必要なら編集後、Phase 0 再実行
"""
                return summary, str(cues_file)
            else:
                return "⚠️ image_cues.json が生成されませんでした", None
        else:
            return f"❌ エラー:\n{result.stderr}", None
    
    except subprocess.TimeoutExpired:
        return "❌ タイムアウト（5分以上）", None
    except Exception as e:
        return f"❌ エラー: {str(e)}", None


def run_phase1(
    srt_dropdown,
    srt_file,
    image_template: str,
    style: str,
    target_sections: int,
    progress=gr.Progress()
) -> Tuple[str, Optional[List[str]]]:
    """Phase 1: LLM分割 + 画像生成"""
    # ドロップダウンまたはアップロードファイルから選択
    if srt_dropdown:
        srt_path = str(PROJECT_ROOT / srt_dropdown)
    elif srt_file is not None:
        srt_path = srt_file.name
    else:
        return "❌ SRTファイルを選択またはアップロードしてください", None

    progress(0, desc="Phase 1: 画像生成準備中...")
    output_dir = PROJECT_ROOT / "output" / "latest"
    
    # テンプレートファイル名を取得
    template_file = IMAGE_TEMPLATES.get(image_template, list(IMAGE_TEMPLATES.values())[0])
    template_path = resolve_template_path(template_file)
    
    if not template_path.exists():
        return f"❌ テンプレートが見つかりません: {template_path}", None
    
    cmd = [
        "python3", "-m", "srt2images.cli",
        "--srt", srt_path,
        "--out", str(output_dir),
        "--engine", "capcut",
        "--prompt-template", str(template_path),
        "--style", style,
        "--nanobanana", "direct",
        "--concurrency", "1",
        "--size", "1920x1080",
        "--force",
        "--use-aspect-guide"
    ]

    env = os.environ.copy()
    env["SRT2IMAGES_TARGET_SECTIONS"] = str(target_sections)
    # PYTHONPATHにsrc/を追加
    pythonpath = str(PROJECT_ROOT / "src")
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = f"{pythonpath}:{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = pythonpath
    
    try:
        progress(0.1, desc="Phase 1: 画像生成中（数分かかります）...")
        
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=1800  # 30分
        )
        
        progress(1.0, desc="Phase 1: 完了")
        
        if result.returncode == 0:
            # 生成画像を取得
            images_dir = output_dir / "images"
            if images_dir.exists():
                images = sorted(glob.glob(str(images_dir / "*.png")))
                
                summary = f"""✅ Phase 1 完了！

🖼️ 画像生成結果:
- 生成画像数: {len(images)}枚
- 保存先: {images_dir}

次のステップ:
Phase 2 でCapCutドラフトを作成
"""
                return summary, images[:10]  # 最初の10枚をプレビュー
            else:
                return "⚠️ 画像が生成されませんでした", None
        else:
            return f"❌ エラー:\n{result.stderr}", None
    
    except subprocess.TimeoutExpired:
        return "❌ タイムアウト（30分以上）", None
    except Exception as e:
        return f"❌ エラー: {str(e)}", None


def run_phase2(
    srt_dropdown,
    srt_file,
    capcut_template: str,
    new_draft_name: str,
    progress=gr.Progress()
) -> str:
    """Phase 2: CapCutドラフト作成"""
    # ドロップダウンまたはアップロードファイルから選択
    if srt_dropdown:
        srt_path = str(PROJECT_ROOT / srt_dropdown)
    elif srt_file is not None:
        srt_path = srt_file.name
    else:
        return "❌ SRTファイルを選択またはアップロードしてください"

    if not new_draft_name:
        return "❌ 新規ドラフト名を入力してください"

    progress(0, desc="Phase 2: CapCutドラフト作成中...")
    output_dir = PROJECT_ROOT / "output" / "latest"
    
    cmd = [
        "python3", "tools/capcut_bulk_insert.py",
        "--run", str(output_dir),
        "--draft-root", str(DRAFT_ROOT),
        "--template", capcut_template,
        "--new", new_draft_name,
        "--srt-file", srt_path,  # ← 字幕デザイン適用！
        "--tx", "0.0",
        "--ty", "0.0",
        "--scale", "0.99"
    ]
    
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=300
        )
        
        progress(1.0, desc="Phase 2: 完了")
        
        if result.returncode == 0:
            draft_path = DRAFT_ROOT / new_draft_name
            return f"""✅ Phase 2 完了！

🎬 CapCutドラフト作成成功:
- ドラフト名: {new_draft_name}
- 保存先: {draft_path}
- 字幕デザイン: 人生の道標スタイル適用済み

CapCutで開いて確認してください！
"""
        else:
            return f"❌ エラー:\n{result.stderr}"
    
    except subprocess.TimeoutExpired:
        return "❌ タイムアウト（5分以上）"
    except Exception as e:
        return f"❌ エラー: {str(e)}"


def run_swap_images(
    draft_path: str,
    run_dir: str,
    indices_text: str,
    custom_prompt: str,
    style_mode: str,
    apply_flag: bool,
    only_allow: str,
    validate_after: bool,
    rollback_on_fail: bool,
    progress=gr.Progress(),
) -> str:
    """safe_image_swap を Gradio から呼び出す簡易UI."""
    if not draft_path:
        return "❌ draft_path を入力してください"
    if not indices_text:
        return "❌ 差し替えインデックスを入力してください (例: 4,5,6)"

    try:
        indices_int = [int(x.strip()) for x in indices_text.split(",") if x.strip()]
    except ValueError:
        return "❌ indices の形式が不正です。カンマ区切りの数字で入力してください (例: 4,5,6)"
    if not indices_int:
        return "❌ 有効な indices がありません"
    # 重複・範囲チェック（0以下を禁止）
    if any(i <= 0 for i in indices_int):
        return "❌ indices は1以上の整数で指定してください"
    if len(indices_int) != len(set(indices_int)):
        return "❌ indices に重複があります。重複を削除してください"
    indices = [str(i) for i in indices_int]

    draft = Path(draft_path).expanduser().resolve()
    if not draft.exists():
        return f"❌ draft が存在しません: {draft}"
    run_dir_path = Path(run_dir).expanduser().resolve() if run_dir else (PROJECT_ROOT / "output" / "latest")
    if not run_dir_path.exists():
        return f"❌ run_dir が存在しません: {run_dir_path}"

    cmd = [
        "python3",
        str(PROJECT_ROOT / "tools" / "safe_image_swap.py"),
        "--run-dir",
        str(run_dir_path),
        "--draft",
        str(draft),
        "--indices",
        *indices,
        "--style-mode",
        style_mode,
        "--only-allow-draft-substring",
        only_allow or draft.name,
    ]
    if custom_prompt:
        cmd += ["--custom-prompt", custom_prompt]
    if apply_flag:
        cmd.append("--apply")
    else:
        cmd.append("--dry-run")
    if validate_after:
        cmd.append("--validate-after")
    if validate_after and rollback_on_fail:
        cmd.append("--rollback-on-validate-fail")

    env = os.environ.copy()
    progress(0, desc="safe_image_swap 実行中...")
    log_dir = PROJECT_ROOT / "logs" / "swap"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"swap_{ts}.log"
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=1200,
        )
        log_path.write_text((result.stdout or "") + "\n" + (result.stderr or ""), encoding="utf-8")
    except subprocess.TimeoutExpired:
        return "❌ タイムアウト（20分超過）"
    except Exception as e:
        return f"❌ エラー: {e}"

    progress(1.0, desc="完了")
    output = (result.stdout or "") + "\n" + (result.stderr or "") + f"\nログ: {log_path}"
    status_badge = "✅ バリデ未実行"
    if validate_after:
        if result.returncode == 0:
            status_badge = "✅ バリデ成功"
        else:
            status_badge = "❌ バリデ失敗"

    if result.returncode == 0:
        return status_badge + "\n" + "✅ 成功\n" + output
    else:
        return status_badge + "\n" + f"❌ 失敗 (exit={result.returncode})\n" + output


def load_whitelist() -> Dict[str, list]:
    if not WHITELIST_PATH.exists():
        return {"video": [], "audio": []}
    try:
        return json.loads(WHITELIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"video": [], "audio": []}


def save_whitelist(video_list: str, audio_list: str) -> str:
    try:
        v = [x.strip() for x in video_list.split(",") if x.strip()]
        a = [x.strip() for x in audio_list.split(",") if x.strip()]
        data = {"video": v, "audio": a}
        WHITELIST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return "✅ 保存しました"
    except Exception as e:
        return f"❌ 保存に失敗: {e}"


def list_swap_logs() -> List[str]:
    log_dir = PROJECT_ROOT / "logs" / "swap"
    if not log_dir.exists():
        return []
    files = sorted(log_dir.glob("swap_*.log"), reverse=True)
    return [f.name for f in files]


def is_fail_log(log_path: Path) -> bool:
    """Rough detection: look for fail/error/exit codes in the content."""
    if not log_path.exists() or not log_path.is_file():
        return False
    try:
        text = log_path.read_text(encoding="utf-8")
    except Exception:
        return False
    head = text[:8000].lower()
    return ("❌" in head) or ("error" in head) or ("fail" in head) or ("exit=1" in head) or ("exit=2" in head)


def read_swap_log(log_name: str) -> str:
    log_dir = PROJECT_ROOT / "logs" / "swap"
    log_path = log_dir / log_name
    if not log_path.exists():
        return "⚠️ ログが見つかりません"
    try:
        return log_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"❌ ログ読み込み失敗: {e}"


# Gradio UI構築
with gr.Blocks(title="🎬 SRT2Images CapCut自動生成", theme=gr.themes.Soft()) as demo:
    gr.Markdown("""
    # 🎬 SRT2Images + CapCut ドラフト自動生成システム
    
    **3ステップワークフロー**:
    1. **Phase 0**: 文脈セクション確認（LLM分析）
    2. **Phase 1**: 画像生成（Gemini API）
    3. **Phase 2**: CapCutドラフト作成
    """)
    
    with gr.Row():
        with gr.Column(scale=2):
            # Step 1: ファイル選択
            gr.Markdown("## 📁 Step 1: SRTファイル選択")

            srt_dropdown = gr.Dropdown(
                choices=get_available_srt_files(),
                label="📂 input/ディレクトリから選択",
                allow_custom_value=False
            )

            gr.Markdown("**または**")

            srt_file = gr.File(
                label="🔼 ファイルをアップロード",
                file_types=[".srt"]
            )

            # Step 2: 設定
            gr.Markdown("## ⚙️ Step 2: 設定")
            
            with gr.Row():
                image_template = gr.Dropdown(
                    choices=list(IMAGE_TEMPLATES.keys()),
                    value="人生の道標（文脈多様版）",
                    label="画像プロンプトテンプレート"
                )
                
                capcut_template = gr.Dropdown(
                    choices=get_available_capcut_templates(),
                    value="人生の道標_最新テンプレ",
                    label="CapCutテンプレート"
                )
            
            target_sections = gr.Slider(
                minimum=20,
                maximum=50,
                value=30,
                step=1,
                label="目標セクション数"
            )
            
            style = gr.Dropdown(
                choices=list(STYLE_PRESETS.values()),
                value=STYLE_PRESETS["人生の道標"],
                label="スタイル設定",
                allow_custom_value=True
            )
            
            new_draft_name = gr.Textbox(
                label="新規ドラフト名",
                placeholder="例: 177_仏教の教え_テスト版",
                value="新規ドラフト_テスト"
            )
        
        with gr.Column(scale=1):
            gr.Markdown("## 📊 プリセット選択")
            
            preset_buttons = gr.Radio(
                choices=["人生の道標", "シニア恋愛", "シニア健康", "ファンタジー清楚"],
                label="プリセット",
                value="人生の道標"
            )
            
            def update_from_preset(preset):
                if preset == "人生の道標":
                    return (
                        "人生の道標（文脈多様版）",
                        "人生の道標_最新テンプレ",
                        STYLE_PRESETS["人生の道標"]
                    )
                elif preset == "シニア恋愛":
                    return (
                        "日本人向け恋愛（ウルトラソフト）",
                        "シニアの朗読テンプレ",
                        STYLE_PRESETS["シニア恋愛"]
                    )
                elif preset == "シニア健康":
                    return (
                        "シニア健康系",
                        "シニアの朗読テンプレ",
                        STYLE_PRESETS["シニア健康"]
                    )
                else:
                    return (
                        "ファンタジー（油絵アカシック）",
                        "人生の道標_最新テンプレ",
                        STYLE_PRESETS["ファンタジー清楚"]
                    )
            
            preset_buttons.change(
                update_from_preset,
                inputs=[preset_buttons],
                outputs=[image_template, capcut_template, style]
            )
    
    gr.Markdown("---")
    gr.Markdown("## 🚀 Step 3: 実行")
    
    with gr.Row():
        btn_phase0 = gr.Button("Phase 0: 文脈確認", variant="secondary", size="lg")
        btn_phase1 = gr.Button("Phase 1: 画像生成", variant="primary", size="lg")
        btn_phase2 = gr.Button("Phase 2: ドラフト作成", variant="primary", size="lg")
    
    gr.Markdown("### 🔄 画像差し替え (safe_image_swap)")
    with gr.Row():
        with gr.Column():
            swap_draft_path = gr.Textbox(
                label="CapCutドラフトパス",
                value=str(DRAFT_ROOT / "195_draft-【手動調整後4】"),
            )
            swap_run_dir = gr.Textbox(
                label="run_dir (images/ があるディレクトリ)",
                value=str(PROJECT_ROOT / "output" / "jinsei195_v1"),
            )
            swap_indices = gr.Textbox(
                label="差し替えインデックス（カンマ区切り例: 4,5,6）",
                value="19",
            )
            swap_custom_prompt = gr.Textbox(
                label="custom_prompt (任意)",
                value="カンタ（若い日本人男性、感情豊かながまん強い青年）。泣きながらも前を向く。僧侶ではない。顔アップ禁止。胸から上の中距離、背景は物語に合う和風・穏やかな夕景。手描きイラスト調。",
                lines=3,
            )
        with gr.Column():
            swap_style_mode = gr.Dropdown(
                choices=["illustration", "realistic", "keep"],
                value="illustration",
                label="style_mode",
            )
            swap_only_allow = gr.Textbox(
                label="only_allow_draft_substring (未入力ならドラフト名)",
                value="195_draft-【手動調整後4】",
            )
            swap_apply = gr.Checkbox(label="apply（未チェックならdry-run）", value=False)
            swap_validate = gr.Checkbox(label="差し替え後にバリデーションを実行", value=True)
            swap_rollback = gr.Checkbox(label="バリデーション失敗ならロールバック", value=True)
            btn_swap = gr.Button("差し替え実行 (safe_image_swap)", variant="primary")
            swap_log = gr.Textbox(label="safe_image_swap ログ", lines=12)

    gr.Markdown("### 📝 ホワイトリスト編集 (背景/BGMトラックID)")
    whitelist = load_whitelist()
    wl_video = gr.Textbox(label="video トラックID（カンマ区切り）", value=",".join(whitelist.get("video", [])))
    wl_audio = gr.Textbox(label="audio トラックID（カンマ区切り）", value=",".join(whitelist.get("audio", [])))
    btn_save_wl = gr.Button("ホワイトリスト保存", variant="secondary")
    wl_status = gr.Textbox(label="ホワイトリスト保存結果", lines=2)

    gr.Markdown("### 📑 差し替えログ")
    log_filter = gr.Radio(
        choices=["all", "fail_only"],
        value="all",
        label="フィルタ",
        info="fail_only はエラー/バリデ失敗っぽいログのみ（簡易フィルタ）",
    )
    log_count = gr.Slider(5, 100, value=30, step=5, label="最大件数")
    log_list = gr.Dropdown(choices=list_swap_logs(), label="ログファイル", value=None)
    btn_reload_logs = gr.Button("ログ一覧再読込", variant="secondary")
    log_view = gr.Textbox(label="ログ内容", lines=12)
    
    # 結果表示
    with gr.Row():
        with gr.Column():
            output = gr.Textbox(
                label="📋 実行ログ",
                lines=10,
                max_lines=20
            )
            cues_file = gr.Textbox(label="image_cues.json パス", visible=False)
        
        with gr.Column():
            gallery = gr.Gallery(
                label="🖼️ 生成画像プレビュー",
                columns=3,
                height=400
            )
    
    # イベントハンドラー
    btn_phase0.click(
        run_phase0,
        inputs=[srt_dropdown, srt_file, target_sections],
        outputs=[output, cues_file]
    )

    btn_phase1.click(
        run_phase1,
        inputs=[srt_dropdown, srt_file, image_template, style, target_sections],
        outputs=[output, gallery]
    )

    btn_phase2.click(
        run_phase2,
        inputs=[srt_dropdown, srt_file, capcut_template, new_draft_name],
        outputs=[output]
    )
    
    btn_swap.click(
        run_swap_images,
        inputs=[
            swap_draft_path,
            swap_run_dir,
            swap_indices,
            swap_custom_prompt,
            swap_style_mode,
            swap_apply,
            swap_only_allow,
            swap_validate,
            swap_rollback,
        ],
        outputs=[swap_log],
    )

    btn_save_wl.click(
        save_whitelist,
        inputs=[wl_video, wl_audio],
        outputs=[wl_status],
    )

    def _reload_logs(filter_mode, limit):
        logs = list_swap_logs()
        if filter_mode == "fail_only":
            filtered = []
            for ln in logs:
                lp = (PROJECT_ROOT / "logs" / "swap" / ln)
                if is_fail_log(lp):
                    filtered.append(ln)
            logs = filtered
        return gr.update(choices=logs[: int(limit)], value=None)

    btn_reload_logs.click(
        _reload_logs,
        inputs=[log_filter, log_count],
        outputs=[log_list],
    )

    log_list.change(
        read_swap_log,
        inputs=[log_list],
        outputs=[log_view],
    )
    
    gr.Markdown("""
    ---
    ## 💡 使い方のヒント

    - **SRTファイル選択**: `input/` ディレクトリから選択、またはファイルをアップロード
    - **Phase 0**: まず文脈セクション分析を実行し、`image_cues.json` を確認
    - **Phase 1**: 画像生成は数分かかります（約5-10分）
    - **Phase 2**: ドラフト作成は数十秒で完了
    - **字幕デザイン**: Phase 2で自動的に「人生の道標スタイル」が適用されます
    """)


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False  # True にすると外部公開URLを生成
    )
