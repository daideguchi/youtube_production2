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

# CapCut API path
_CANDIDATE_API_PATHS = []
env_api_root = os.getenv("CAPCUT_API_ROOT")
if env_api_root:
    _CANDIDATE_API_PATHS.append(Path(env_api_root).expanduser())
_CANDIDATE_API_PATHS.extend([
    Path.home() / "capcut_api",
    Path(__file__).resolve().parents[2] / "50_tools" / "50_1_capcut_api",
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
        
        if not CAPCUT_AVAILABLE:
            self.log_warning("CapCut API利用不可 - SRTレイヤー検証をスキップ")
            return {"success": False, "reason": "CapCut API unavailable"}
        
        if not draft_dir.exists():
            self.log_error(f"CapCutドラフトディレクトリが見つかりません: {draft_dir}")
            return {"success": False, "reason": "Draft directory not found"}
        
        # SRTファイル解析
        srt_subtitles = self._parse_srt_file(srt_file) if srt_file.exists() else []
        expected_subtitle_count = len(srt_subtitles)
        
        try:
            # CapCutドラフト読み込み（新API対応）
            try:
                script = draft.Script_file(draft_dir / "draft_content.json", height=1080)
            except TypeError:
                # Fallback for older API
                script = draft.Script_file(draft_dir / "draft_content.json")
            
            # テキストトラック確認
            text_tracks = []
            subtitle_segments = []
            
            for track_name, track in script.tracks.items():
                if hasattr(track, 'type') and track.type == 'text':
                    text_tracks.append(track_name)
                    # セグメント数カウント
                    if hasattr(track, 'segments'):
                        subtitle_segments.extend(track.segments)
            
            actual_subtitle_count = len(subtitle_segments)
            
            result = {
                "success": len(text_tracks) > 0 and actual_subtitle_count > 0,
                "expected_subtitles": expected_subtitle_count,
                "actual_subtitles": actual_subtitle_count,
                "text_tracks": text_tracks,
                "has_srt_track": any("srt" in track.lower() for track in text_tracks)
            }
            
            if result["success"]:
                self.log_info(f"✅ SRTレイヤー挿入確認: {len(text_tracks)}個のテキストトラック, {actual_subtitle_count}個の字幕セグメント")
                if expected_subtitle_count > 0:
                    match_rate = actual_subtitle_count / expected_subtitle_count
                    if match_rate >= 0.9:  # 90%以上マッチ
                        self.log_info(f"✅ 字幕セグメント数マッチ率: {match_rate:.1%}")
                    else:
                        self.log_warning(f"⚠️ 字幕セグメント数不一致: 期待値{expected_subtitle_count}, 実際{actual_subtitle_count}")
            else:
                self.log_error("🚫 SRTレイヤーが正しく挿入されていません")
                
            return result
            
        except Exception as e:
            self.log_error(f"CapCutドラフト解析エラー: {e}")
            return {"success": False, "reason": f"Draft analysis error: {e}"}
    
    def validate_coordinate_positioning(self, draft_dir: Path, expected_tx: float = -0.163, expected_ty: float = 0.201, expected_scale: float = 0.59) -> Dict[str, Any]:
        """座標位置設定の確認"""
        print("\n=== 📍 座標位置設定検証 ===")
        
        if not CAPCUT_AVAILABLE:
            self.log_warning("CapCut API利用不可 - 座標検証をスキップ")
            return {"success": False, "reason": "CapCut API unavailable"}
        
        try:
            try:
                script = draft.Script_file(draft_dir / "draft_content.json", height=1080)
            except TypeError:
                # Fallback for older API
                script = draft.Script_file(draft_dir / "draft_content.json")
            tolerance = 0.05  # 5%の許容誤差
            
            positioned_segments = 0
            correct_positions = 0
            position_details = []
            
            for track_name, track in script.tracks.items():
                if hasattr(track, 'segments'):
                    for segment in track.segments:
                        if hasattr(segment, 'transform') or hasattr(segment, 'clip_settings'):
                            positioned_segments += 1
                            
                            # 座標取得
                            actual_tx = getattr(segment, 'transform_x', None)
                            actual_ty = getattr(segment, 'transform_y', None)
                            actual_scale = getattr(segment, 'scale', None)
                            
                            if actual_tx is not None and actual_ty is not None and actual_scale is not None:
                                tx_ok = abs(actual_tx - expected_tx) <= tolerance
                                ty_ok = abs(actual_ty - expected_ty) <= tolerance
                                scale_ok = abs(actual_scale - expected_scale) <= tolerance
                                
                                if tx_ok and ty_ok and scale_ok:
                                    correct_positions += 1
                                    self.log_info(f"✅ 正確な座標: TX={actual_tx:.3f}, TY={actual_ty:.3f}, Scale={actual_scale:.3f}")
                                else:
                                    self.log_warning(f"⚠️ 座標ずれ: TX={actual_tx:.3f} (期待{expected_tx:.3f}), TY={actual_ty:.3f} (期待{expected_ty:.3f}), Scale={actual_scale:.3f} (期待{expected_scale:.3f})")
                                
                                position_details.append({
                                    "track": track_name,
                                    "actual": {"tx": actual_tx, "ty": actual_ty, "scale": actual_scale},
                                    "expected": {"tx": expected_tx, "ty": expected_ty, "scale": expected_scale},
                                    "correct": tx_ok and ty_ok and scale_ok
                                })
            
            success_rate = correct_positions / positioned_segments if positioned_segments > 0 else 0
            
            result = {
                "success": success_rate >= 0.8,  # 80%以上正確
                "positioned_segments": positioned_segments,
                "correct_positions": correct_positions,
                "success_rate": success_rate,
                "details": position_details
            }
            
            if result["success"]:
                self.log_info(f"✅ 座標位置確認: {correct_positions}/{positioned_segments}個のセグメントが正確な位置")
            else:
                self.log_error(f"🚫 座標位置エラー: {positioned_segments - correct_positions}個のセグメントが不正確な位置")
            
            return result
            
        except Exception as e:
            self.log_error(f"座標検証エラー: {e}")
            return {"success": False, "reason": f"Coordinate validation error: {e}"}
    
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
  python3 comprehensive_validation.py --run ./output/auto_20250905_121136
  
  # 完全検証（CapCut + SRT含む）
  python3 comprehensive_validation.py \\
    --run ./output/auto_20250905_121136 \\
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
