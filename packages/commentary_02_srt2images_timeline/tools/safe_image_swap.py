#!/usr/bin/env python3
"""
Safe image swapper for existing CapCut drafts.

目的:
- 手動調整済みのドラフトを壊さずに「指定カットの画像だけ」差し替える。
- regenerate_and_swap_v2 の ID スワップでキャッシュを無効化し、差し替えを確実に反映。
- 差し替え後に draft_content / draft_info を同期し、CapCut で認識させる。
- 実行前にドラフト全体をバックアップ（タイムスタンプ付）しておく。

使い方:
  # まず --dry-run（デフォルト）で計画確認
  GEMINI_API_KEY=... python3 tools/safe_image_swap.py \
      --run-dir output/jinsei195_v1 \
      --draft "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/195_draft" \
      --indices 4 5 6 \
      --style-mode illustration \
      --custom-prompt "Persona指示をここに"

  # 実行する場合は --apply を必ず付ける（バックアップ→差し替え→同期）
  GEMINI_API_KEY=... python3 tools/safe_image_swap.py ... --apply

前提:
- run_dir に新しい画像を生成できること（Geminiキーが必要）。
- draft は既存を上書きせず、指定インデックスの素材のみ置換する。
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run(cmd, cwd=None, env=None):
    print("▶", " ".join(cmd))
    res = subprocess.run(cmd, cwd=cwd or PROJECT_ROOT, env=env)
    if res.returncode != 0:
        sys.exit(res.returncode)


def main():
    ap = argparse.ArgumentParser(description="Safe image swap for existing CapCut draft (with backup + sync)")
    ap.add_argument("--run-dir", required=True, help="runディレクトリ（images/ がある）")
    ap.add_argument("--draft", required=True, help="CapCut draft のパス（既存ドラフトを壊さず差し替え）")
    ap.add_argument("--indices", type=int, nargs="+", required=True, help="1-based 画像番号のリスト")
    ap.add_argument("--style-mode", choices=["illustration", "realistic", "keep"], default="illustration")
    ap.add_argument("--custom-prompt", default="", help="persona など追加指示（任意）")
    ap.add_argument("--only-allow-draft-substring", required=True, help="必須: 指定文字列をドラフトパスに含まない場合は即エラー")
    ap.add_argument("--skip-full-sync", action="store_true", help="(非推奨/無視されます) draft_info のトラックを上書きしない")
    ap.add_argument("--dry-run", action="store_true", help="変更を加えず計画のみ表示（必須ステップと位置づけ）")
    ap.add_argument("--apply", action="store_true", help="本実行フラグ。指定しない場合は計画表示のみで終了。")
    ap.add_argument("--validate-after", action="store_true", help="差し替え後に validate_srt2images_state.py を実行")
    ap.add_argument("--rollback-on-validate-fail", action="store_true", help="validate失敗時にバックアップへ即ロールバック（--validate-after と併用）")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    draft_path = Path(args.draft).resolve()

    if not run_dir.exists():
        print(f"❌ run-dir not found: {run_dir}")
        sys.exit(1)
    if not draft_path.exists():
        print(f"❌ draft not found: {draft_path}")
        sys.exit(1)
    if args.only_allow_draft_substring not in draft_path.name:
        print(f"❌ draft path '{draft_path}' does not contain required substring '{args.only_allow_draft_substring}'. Aborting to protect manual edits.")
        sys.exit(1)

    # 1) Pre-check tracks before any write/backup
    from pathlib import Path as _Path
    import json as _json

    # load cues length for better detection
    cues_len = None
    try:
        cues_json = json.loads((run_dir / "image_cues.json").read_text(encoding="utf-8"))
        cues_len = len(cues_json.get("cues", []))
    except Exception:
        pass

    info_probe = _json.loads((draft_path / "draft_info.json").read_text(encoding="utf-8"))
    content_probe = _json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
    tracks_info = info_probe.get("tracks") or info_probe.get("script", {}).get("tracks") or []
    tracks_content = content_probe.get("tracks") or content_probe.get("script", {}).get("tracks") or []

    # Whitelist: 背景/BGMトラックIDを config/track_whitelist.json から読み込み（存在しない場合は警告のみ）
    whitelist_path = PROJECT_ROOT / "config" / "track_whitelist.json"
    whitelist = {"video": [], "audio": []}
    if whitelist_path.exists():
        try:
            whitelist = json.loads(whitelist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"❌ whitelist JSON が壊れています: {whitelist_path}")
            sys.exit(1)
    whitelist_video = set(whitelist.get("video") or [])
    whitelist_audio = set(whitelist.get("audio") or [])

    def _warn_non_srt2images(tracks):
        for t in tracks:
            name_raw = t.get("name") or t.get("id") or ""
            if t.get("type") in ("video", "audio") and not (name_raw or "").startswith("srt2images_"):
                allow = (name_raw in whitelist_video) if t.get("type") == "video" else (name_raw in whitelist_audio)
                if not allow and name_raw:
                    print(f"⚠️  Non-srt2images {t.get('type')} track detected ('{name_raw}') — not aborting, but keep in mind during swap.")

    _warn_non_srt2images(tracks_info)

    def find_track(data, prefer_len=None):
        tr = data.get("tracks") or data.get("script", {}).get("tracks") or []
        # 1) named srt2images
        for _t in tr:
            nm = (_t.get("name") or _t.get("id") or "").lower()
            if nm.startswith("srt2images_"):
                return _t
        # 2) by segment length match
        if prefer_len:
            cand = [_t for _t in tr if _t.get("type") == "video" and len(_t.get("segments") or []) == prefer_len]
            if cand:
                return cand[0]
        # 3) longest video track
        vids = [_t for _t in tr if _t.get("type") == "video"]
        if vids:
            vids = sorted(vids, key=lambda x: len(x.get("segments") or []), reverse=True)
            return vids[0]
        return None

    ct = find_track(content_probe, cues_len)
    it = find_track(info_probe, cues_len)
    if not ct or not it:
        print("❌ srt2images 対象トラックを特定できませんでした（名前なし＋セグメント数も合致せず）。手動でドラフトを整備してください。")
        sys.exit(1)
    csegs = ct.get("segments") or []
    isegs = it.get("segments") or []
    if not csegs or not isegs:
        print("❌ srt2images トラックにセグメントがありません。")
        sys.exit(1)
    if cues_len and abs(len(csegs) - cues_len) > 1:
        print(f"⚠️  注意: draft segments({len(csegs)}) と image_cues({cues_len}) がズレています。差し替えは行うが手動調整に注意。")

    if args.skip_full_sync:
        print("⚠️  --skip-full-sync は非推奨かつ無視されます（常に srt2images 材料同期を実施）。")

    # 2) Regenerate & swap (ID swap) for specified indices
    regen_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "regenerate_and_swap_v2.py"),
        "--run-dir",
        str(run_dir),
        "--draft-path",
        str(draft_path),
        "--indices",
        *[str(i) for i in args.indices],
        "--style-mode",
        args.style_mode,
    ]
    if args.custom_prompt:
        regen_cmd += ["--custom-prompt", args.custom_prompt]

    ts = time.strftime("%Y%m%d_%H%M%S")
    planned_backup_dir = draft_path.parent / f"{draft_path.name}_bak_{ts}"

    if args.dry_run or not args.apply:
        print("🔍 DRY-RUN / PREVIEW MODE (use --apply to actually run)")
        print("🔍 Would backup draft ->", planned_backup_dir)
        print("🔍 Would execute:", " ".join(regen_cmd))
        print("🔍 Would sync srt2images material_ids only (tracks untouched)")
        return

    # 3) Backup draft
    backup_dir = planned_backup_dir
    print(f"🛡️  Backup draft -> {backup_dir}")
    shutil.copytree(draft_path, backup_dir)

    # 4) Regenerate & swap with guard env
    regen_env = os.environ.copy()
    regen_env["SAFE_IMAGE_SWAP_ALLOW"] = "1"
    run(regen_cmd, cwd=PROJECT_ROOT, env=regen_env)

    # 5) Sync srt2images material_ids only (no track/timerange change)
    sync_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "sync_srt2images_materials.py"),
        "--draft",
        str(draft_path),
    ]
    run(sync_cmd, cwd=PROJECT_ROOT)

    # 6) Optional validation
    if args.validate_after:
        print("🔍 validate_srt2images_state.py を実行中...")
        validate_cmd = [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "validate_srt2images_state.py"),
            "--draft",
            str(draft_path),
        ]
        vres = subprocess.run(validate_cmd, cwd=PROJECT_ROOT)
        if vres.returncode != 0:
            print(f"❌ バリデーション失敗 (exit={vres.returncode})")
            if args.rollback_on_validate_fail:
                failed_dir = draft_path.parent / f"{draft_path.name}_failed_{ts}"
                print(f"↩️  ロールバック: 現在のドラフトを {failed_dir} に移動し、バックアップを元に戻します")
                if failed_dir.exists():
                    shutil.rmtree(failed_dir)
                draft_path.rename(failed_dir)
                shutil.copytree(backup_dir, draft_path)
                print("✅ ロールバック完了")
            sys.exit(vres.returncode)
        else:
            print("✅ バリデーション成功")

    print("✅ 完了: 画像のみ差し替え＋同期。CapCutを開き直して反映を確認してください。")
    print(f"🗂️ バックアップ: {backup_dir}")


if __name__ == "__main__":
    main()
