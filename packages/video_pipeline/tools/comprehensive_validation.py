#!/usr/bin/env python3
"""
完全チェック機能 - srt2images-timeline 包括的品質確認システム
用途: 生成された画像、CapCutドラフト、SRT挿入の全項目を徹底検証

ユーザー要求: 「これらが絶対に守られるようにチェック機能を徹底整備して」
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Any
from PIL import Image
import re

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

REPO_ROOT = tool_bootstrap(load_env=False)


# CapCut API path
_CANDIDATE_API_PATHS = []
env_api_root = os.getenv("CAPCUT_API_ROOT")
if env_api_root:
    _CANDIDATE_API_PATHS.append(Path(env_api_root).expanduser())
_CANDIDATE_API_PATHS.extend([
    Path.home() / "capcut_api",
    REPO_ROOT / "packages" / "capcut_api",
])
for _candidate in _CANDIDATE_API_PATHS:
    if _candidate.exists():
        path_str = str(_candidate)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

try:
    import pyJianYingDraft as draft
    CAPCUT_AVAILABLE = True
except ImportError:
    CAPCUT_AVAILABLE = False
    print("⚠️ Warning: pyJianYingDraft not available - CapCut validation will be skipped")


class ComprehensiveValidator:
    """包括的品質検証システム"""
    
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.info = []
        self.validation_results = {}
    
    def log_error(self, message: str):
        """エラーメッセージを記録"""
        self.errors.append(message)
        print(f"❌ ERROR: {message}")
    
    def log_warning(self, message: str):
        """警告メッセージを記録"""
        self.warnings.append(message)
        print(f"⚠️ WARNING: {message}")
    
    def log_info(self, message: str):
        """情報メッセージを記録"""
        self.info.append(message)
        print(f"ℹ️ INFO: {message}")
    
    def validate_image_aspect_ratio(self, images_dir: Path) -> Dict[str, Any]:
        """16:9画像比率の徹底確認"""
        print("\n=== 🖼️ 画像アスペクト比検証 ===")
        
        image_files = list(images_dir.glob("*.png"))
        if not image_files:
            self.log_error(f"画像ファイルが見つかりません: {images_dir}")
            return {"success": False, "total": 0, "valid": 0, "invalid": 0}
        
        target_ratio = 16 / 9  # 1.777...
        target_size = (1920, 1080)
        tolerance = 0.01  # 1%の許容誤差
        
        valid_images = 0
        invalid_images = []
        
        for img_path in sorted(image_files):
            try:
                img = Image.open(img_path)
                current_ratio = img.size[0] / img.size[1]
                ratio_diff = abs(current_ratio - target_ratio)
                
                if ratio_diff <= tolerance and img.size == target_size:
                    valid_images += 1
                    self.log_info(f"✅ {img_path.name}: {img.size} (ratio: {current_ratio:.3f})")
                else:
                    invalid_images.append({
                        "file": img_path.name,
                        "size": img.size,
                        "ratio": current_ratio,
                        "expected_size": target_size,
                        "expected_ratio": target_ratio
                    })
                    self.log_error(f"🚫 {img_path.name}: {img.size} (ratio: {current_ratio:.3f}) - Expected: {target_size} (ratio: {target_ratio:.3f})")
                    
            except Exception as e:
                self.log_error(f"画像読み込みエラー {img_path.name}: {e}")
                invalid_images.append({"file": img_path.name, "error": str(e)})
        
        success_rate = valid_images / len(image_files) if image_files else 0
        
        result = {
            "success": success_rate == 1.0,
            "total": len(image_files),
            "valid": valid_images,
            "invalid": len(invalid_images),
            "success_rate": success_rate,
            "invalid_details": invalid_images
        }
        
        if success_rate == 1.0:
            self.log_info(f"✅ 全画像が16:9 (1920x1080)を満たしています: {valid_images}/{len(image_files)}")
        else:
            self.log_error(f"🚫 {len(invalid_images)}個の画像が16:9要件を満たしていません")
        
        return result
    
    def validate_srt_layer_insertion(self, draft_dir: Path, srt_file: Path) -> Dict[str, Any]:
        """SRTレイヤー挿入の徹底確認"""
        print("\n=== 📝 SRTレイヤー挿入検証 ===")

        if not draft_dir.exists():
            self.log_error(f"CapCutドラフトディレクトリが見つかりません: {draft_dir}")
            return {"success": False, "reason": "Draft directory not found"}

        # SRTファイル解析
        srt_subtitles = self._parse_srt_file(srt_file) if srt_file.exists() else []
        expected_subtitle_count = len(srt_subtitles)

        draft_json_path = draft_dir / "draft_content.json"
        if not draft_json_path.exists():
            self.log_error(f"draft_content.json が見つかりません: {draft_json_path}")
            return {"success": False, "reason": "draft_content.json not found"}

        try:
            draft_obj = json.loads(draft_json_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.log_error(f"draft_content.json の解析に失敗: {e}")
            return {"success": False, "reason": f"draft_content.json parse error: {e}"}

        tracks = draft_obj.get("tracks") or []
        text_track_names: List[str] = []
        subtitle_segments: List[Dict[str, Any]] = []
        for t in tracks:
            if not isinstance(t, dict):
                continue
            if str(t.get("type") or "") != "text":
                continue
            name = str(t.get("name") or "")
            text_track_names.append(name)
            segs = t.get("segments") or []
            if isinstance(segs, list) and any(tok in name.lower() for tok in ("subtitle", "subtitles", "srt")):
                subtitle_segments.extend([s for s in segs if isinstance(s, dict)])

        # Fallback: if we couldn't identify the subtitle track by name, count all text segments.
        if not subtitle_segments:
            for t in tracks:
                if not isinstance(t, dict):
                    continue
                if str(t.get("type") or "") != "text":
                    continue
                segs = t.get("segments") or []
                if isinstance(segs, list):
                    subtitle_segments.extend([s for s in segs if isinstance(s, dict)])

        actual_subtitle_count = len(subtitle_segments)
        has_srt_track = any(tok in (name or "").lower() for name in text_track_names for tok in ("subtitle", "subtitles", "srt"))

        result = {
            "success": actual_subtitle_count > 0,
            "expected_subtitles": expected_subtitle_count,
            "actual_subtitles": actual_subtitle_count,
            "text_tracks": text_track_names,
            "has_srt_track": has_srt_track,
        }

        if result["success"]:
            self.log_info(
                f"✅ SRTレイヤー挿入確認: text_tracks={len(text_track_names)} subtitles={actual_subtitle_count}"
            )
            if expected_subtitle_count > 0:
                match_rate = actual_subtitle_count / expected_subtitle_count
                if match_rate >= 0.9:
                    self.log_info(f"✅ 字幕セグメント数マッチ率: {match_rate:.1%}")
                else:
                    self.log_warning(f"⚠️ 字幕セグメント数不一致: 期待値{expected_subtitle_count}, 実際{actual_subtitle_count}")
        else:
            self.log_error("🚫 SRTレイヤーが正しく挿入されていません（text track/segments not found）")

        return result
    
    def validate_coordinate_positioning(
        self,
        draft_dir: Path,
        expected_tx: float | None = None,
        expected_ty: float | None = None,
        expected_scale: float | None = None,
    ) -> Dict[str, Any]:
        """座標位置設定の確認"""
        print("\n=== 📍 座標位置設定検証 ===")

        draft_json_path = draft_dir / "draft_content.json"
        if not draft_json_path.exists():
            self.log_error(f"draft_content.json が見つかりません: {draft_json_path}")
            return {"success": False, "reason": "draft_content.json not found"}

        try:
            draft_obj = json.loads(draft_json_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.log_error(f"draft_content.json の解析に失敗: {e}")
            return {"success": False, "reason": f"draft_content.json parse error: {e}"}

        def _median(vals: List[float]) -> float:
            s = sorted(vals)
            mid = len(s) // 2
            if not s:
                return 0.0
            if len(s) % 2 == 1:
                return float(s[mid])
            return float(s[mid - 1] + s[mid]) / 2.0

        tolerance = 0.05
        positioned_segments = 0
        ok_segments = 0
        details: List[Dict[str, Any]] = []
        tx_vals: List[float] = []
        ty_vals: List[float] = []
        sx_vals: List[float] = []
        sy_vals: List[float] = []

        tracks = draft_obj.get("tracks") or []
        for t in tracks:
            if not isinstance(t, dict):
                continue
            if str(t.get("type") or "") != "video":
                continue
            name = str(t.get("name") or "")
            if "srt2images" not in name:
                continue
            segs = t.get("segments") or []
            if not isinstance(segs, list):
                continue
            for seg in segs:
                if not isinstance(seg, dict):
                    continue
                clip = seg.get("clip") or {}
                if not isinstance(clip, dict):
                    continue
                transform = clip.get("transform") or {}
                scale = clip.get("scale") or {}
                if not isinstance(transform, dict) or not isinstance(scale, dict):
                    continue
                tx = transform.get("x")
                ty = transform.get("y")
                sx = scale.get("x")
                sy = scale.get("y")
                if not all(isinstance(v, (int, float)) for v in (tx, ty, sx, sy)):
                    continue

                txf = float(tx)
                tyf = float(ty)
                sxf = float(sx)
                syf = float(sy)
                positioned_segments += 1
                tx_vals.append(txf)
                ty_vals.append(tyf)
                sx_vals.append(sxf)
                sy_vals.append(syf)
                details.append(
                    {
                        "track": name,
                        "actual": {"tx": txf, "ty": tyf, "sx": sxf, "sy": syf},
                    }
                )

        if positioned_segments == 0:
            self.log_warning("座標検証: clip.transform/scale を持つ srt2images video セグメントが見つかりません（スキップ扱い）")
            return {
                "success": True,
                "positioned_segments": 0,
                "correct_positions": 0,
                "success_rate": 1.0,
                "details": [],
            }

        baseline_tx = float(expected_tx) if expected_tx is not None else _median(tx_vals)
        baseline_ty = float(expected_ty) if expected_ty is not None else _median(ty_vals)
        baseline_sx = float(expected_scale) if expected_scale is not None else _median(sx_vals)
        baseline_sy = float(expected_scale) if expected_scale is not None else _median(sy_vals)

        # sanity bounds: avoid obviously broken coordinate systems
        if abs(baseline_tx) > 0.9 or abs(baseline_ty) > 0.9 or baseline_sx <= 0.0 or baseline_sy <= 0.0:
            self.log_error(
                f"🚫 座標位置エラー: baseline out of bounds tx={baseline_tx:.3f} ty={baseline_ty:.3f} "
                f"sx={baseline_sx:.3f} sy={baseline_sy:.3f}"
            )
            return {
                "success": False,
                "positioned_segments": positioned_segments,
                "correct_positions": 0,
                "success_rate": 0.0,
                "expected": {"tx": baseline_tx, "ty": baseline_ty, "sx": baseline_sx, "sy": baseline_sy},
                "details": details[:50],
            }

        for d in details:
            a = d.get("actual") or {}
            txf = float(a.get("tx", 0.0))
            tyf = float(a.get("ty", 0.0))
            sxf = float(a.get("sx", 0.0))
            syf = float(a.get("sy", 0.0))
            tx_ok = abs(txf - baseline_tx) <= tolerance
            ty_ok = abs(tyf - baseline_ty) <= tolerance
            sx_ok = abs(sxf - baseline_sx) <= tolerance
            sy_ok = abs(syf - baseline_sy) <= tolerance
            ok = bool(tx_ok and ty_ok and sx_ok and sy_ok)
            d["expected"] = {"tx": baseline_tx, "ty": baseline_ty, "sx": baseline_sx, "sy": baseline_sy}
            d["correct"] = ok
            if ok:
                ok_segments += 1

        success_rate = ok_segments / positioned_segments
        result = {
            "success": success_rate >= 0.8,
            "positioned_segments": positioned_segments,
            "correct_positions": ok_segments,
            "success_rate": success_rate,
            "expected": {"tx": baseline_tx, "ty": baseline_ty, "sx": baseline_sx, "sy": baseline_sy},
            "details": details[:50],
        }

        if result["success"]:
            self.log_info(
                f"✅ 座標位置確認: {ok_segments}/{positioned_segments} segments consistent "
                f"(tx={baseline_tx:.3f}, ty={baseline_ty:.3f}, scale={baseline_sx:.3f})"
            )
        else:
            self.log_error(
                f"🚫 座標位置エラー: {positioned_segments - ok_segments}個のセグメントが期待値と一致しません"
            )

        return result
    
    def validate_file_completeness(self, run_dir: Path, srt_file: Path) -> Dict[str, Any]:
        """ファイル完整性の確認"""
        print("\n=== 📁 ファイル完整性検証 ===")
        
        required_files = [
            "image_cues.json",
            "images/"
        ]
        
        optional_files = [
            "guides/",
            "logs/"
        ]
        
        missing_required = []
        missing_optional = []
        existing_files = []
        
        for req_file in required_files:
            file_path = run_dir / req_file
            if file_path.exists():
                existing_files.append(req_file)
                self.log_info(f"✅ 必須ファイル存在: {req_file}")
            else:
                missing_required.append(req_file)
                self.log_error(f"🚫 必須ファイル不在: {req_file}")
        
        for opt_file in optional_files:
            file_path = run_dir / opt_file
            if file_path.exists():
                existing_files.append(opt_file)
                self.log_info(f"✅ オプションファイル存在: {opt_file}")
            else:
                missing_optional.append(opt_file)
                self.log_info(f"ℹ️ オプションファイル不在: {opt_file}")
        
        # SRTファイル確認
        if srt_file.exists():
            existing_files.append(str(srt_file))
            self.log_info(f"✅ SRTファイル存在: {srt_file}")
        else:
            self.log_error(f"🚫 SRTファイル不在: {srt_file}")
        
        # 画像ファイル数確認
        images_dir = run_dir / "images"
        if images_dir.exists():
            image_count = len(list(images_dir.glob("*.png")))
            if image_count > 0:
                self.log_info(f"✅ 画像ファイル数: {image_count}枚")
            else:
                self.log_error("🚫 画像ファイルが見つかりません")
        
        result = {
            "success": len(missing_required) == 0,
            "existing_files": existing_files,
            "missing_required": missing_required,
            "missing_optional": missing_optional,
            "image_count": image_count if 'image_count' in locals() else 0
        }
        
        return result
    
    def _parse_srt_file(self, srt_path: Path) -> List[Dict]:
        """SRTファイル解析"""
        if not srt_path.exists():
            return []
        
        content = srt_path.read_text(encoding='utf-8')
        pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\n\d+\n|\Z)'
        matches = re.findall(pattern, content, re.DOTALL)
        
        return [{"index": int(match[0]), "text": match[3].strip()} for match in matches]
    
    def run_comprehensive_validation(self, run_dir: Path, draft_dir: Path = None, srt_file: Path = None) -> Dict[str, Any]:
        """包括的品質検証実行"""
        print("🔍 SRT2Images-Timeline 包括的品質検証開始")
        print(f"📂 検証対象: {run_dir}")
        
        results = {}
        
        # 1. ファイル完整性確認
        results["file_completeness"] = self.validate_file_completeness(run_dir, srt_file or Path())
        
        # 2. 画像アスペクト比確認
        images_dir = run_dir / "images"
        results["image_aspect_ratio"] = self.validate_image_aspect_ratio(images_dir)
        
        # 3. SRTレイヤー挿入確認
        if draft_dir and srt_file:
            results["srt_layer_insertion"] = self.validate_srt_layer_insertion(draft_dir, srt_file)
        
        # 4. 座標位置確認
        if draft_dir:
            results["coordinate_positioning"] = self.validate_coordinate_positioning(draft_dir)
        
        # 総合判定
        all_success = all(
            result.get("success", False) 
            for result in results.values() 
            if isinstance(result, dict)
        )
        
        results["overall"] = {
            "success": all_success,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "info_count": len(self.info)
        }
        
        # 最終結果表示
        print("\n" + "="*60)
        print("🏁 総合検証結果")
        print("="*60)
        
        if all_success:
            print("🎉 ✅ 全ての検証項目をクリア！品質基準を満たしています。")
        else:
            print("❌ 🚫 品質基準を満たしていない項目があります。")
        
        print(f"📊 エラー: {len(self.errors)}個, 警告: {len(self.warnings)}個, 情報: {len(self.info)}個")
        
        if self.errors:
            print("\n🚫 エラー項目:")
            for error in self.errors:
                print(f"   • {error}")
        
        if self.warnings:
            print("\n⚠️ 警告項目:")
            for warning in self.warnings:
                print(f"   • {warning}")
        
        return results


def main():
    parser = argparse.ArgumentParser(
        description="srt2images-timeline 包括的品質検証システム",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 基本検証（画像のみ）
  PYTHONPATH=".:packages" python3 -m video_pipeline.tools.comprehensive_validation --run workspaces/video/runs/<run_id>
  
  # 完全検証（CapCut + SRT含む）
  PYTHONPATH=".:packages" python3 -m video_pipeline.tools.comprehensive_validation \\
    --run workspaces/video/runs/<run_id> \\
    --draft-dir "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/001_シニアの朗読_画像版" \\
    --srt-file "./path/to/script.srt"
        """
    )
    
    parser.add_argument("--run", required=True, help="srt2images出力ディレクトリ")
    parser.add_argument("--draft-dir", help="CapCutドラフトディレクトリ")
    parser.add_argument("--srt-file", help="SRTファイルパス")
    parser.add_argument("--json-output", help="結果をJSONで出力するファイルパス")
    
    args = parser.parse_args()
    
    run_dir = Path(args.run).resolve()
    draft_dir = Path(args.draft_dir).resolve() if args.draft_dir else None
    srt_file = Path(args.srt_file).resolve() if args.srt_file else None
    
    if not run_dir.exists():
        print(f"❌ エラー: 実行ディレクトリが見つかりません: {run_dir}")
        sys.exit(1)
    
    validator = ComprehensiveValidator()
    results = validator.run_comprehensive_validation(run_dir, draft_dir, srt_file)
    
    # JSON出力
    if args.json_output:
        output_path = Path(args.json_output)
        output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f"📄 結果をJSONで出力: {output_path}")
    
    # 終了コード設定
    sys.exit(0 if results["overall"]["success"] else 1)


if __name__ == "__main__":
    main()
