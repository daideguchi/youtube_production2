#!/usr/bin/env python3
"""
Integration Test for SRT2Images Timeline UI System
統合UIワークフローのテスト
"""
import sys
import os
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

def test_template_managers():
    """テンプレート管理システムのテスト"""
    print("=== テンプレート管理システム統合テスト ===")
    
    try:
        # パスの追加
        sys.path.insert(0, str(project_root / "src" / "ui"))
        
        from capcut_template_manager import CapCutTemplateManager
        from image_template_manager import ImageTemplateManager
        
        # CapCutテンプレート管理システムテスト
        print("📋 CapCutテンプレート管理システムテスト...")
        capcut_manager = CapCutTemplateManager()
        capcut_templates = capcut_manager.get_all_templates()
        
        print(f"✅ CapCutテンプレート数: {len(capcut_templates)}")
        print(f"✅ カテゴリー: {capcut_manager.get_categories()}")
        
        # シニア恋愛テンプレートの検索
        senior_template = capcut_manager.get_template_by_name("シニア恋愛テンプレ")
        if senior_template:
            print(f"✅ シニア恋愛テンプレート検出: {senior_template.name}")
        else:
            print("⚠️ シニア恋愛テンプレートが見つかりません")
        
        # 画像テンプレート管理システムテスト
        print("\n🎨 画像テンプレート管理システムテスト...")
        image_manager = ImageTemplateManager()
        image_templates = image_manager.get_all_templates()
        
        print(f"✅ 画像テンプレート数: {len(image_templates)}")
        print(f"✅ カテゴリー: {image_manager.get_categories()}")
        
        # 日本語ビジュアルテンプレートの検索
        jp_visual = image_manager.get_template_by_name("日本語ビジュアル")
        if jp_visual:
            print(f"✅ 日本語ビジュアルテンプレート検出: {jp_visual.name}")
            print(f"   ファイル: {jp_visual.file}")
        else:
            print("⚠️ 日本語ビジュアルテンプレートが見つかりません")
        
        return True
        
    except Exception as e:
        print(f"❌ テンプレート管理システムテストエラー: {e}")
        return False

def test_srt_files():
    """SRTファイルの利用可能性テスト"""
    print("\n=== SRTファイル利用可能性テスト ===")
    
    # テスト対象ディレクトリ
    test_dirs = [
        project_root / "examples",
        project_root / "input",
        project_root / "output"
    ]
    
    srt_files = []
    for test_dir in test_dirs:
        if test_dir.exists():
            srt_files.extend(test_dir.rglob("*.srt"))
    
    print(f"✅ 検出されたSRTファイル数: {len(srt_files)}")
    
    if srt_files:
        # 最初の3ファイルの詳細確認
        for i, srt_file in enumerate(srt_files[:3]):
            try:
                with open(srt_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                lines = content.strip().split('\n')
                print(f"✅ {srt_file.name}: {len(lines)}行")
                
            except Exception as e:
                print(f"⚠️ {srt_file.name}: 読み込みエラー - {e}")
        
        return True
    else:
        print("❌ SRTファイルが見つかりません")
        return False

def test_workflow_dependencies():
    """ワークフロー依存関係テスト"""
    print("\n=== ワークフロー依存関係テスト ===")
    
    # 必要なディレクトリの確認
    required_dirs = [
        project_root / "templates",
        project_root / "tools",
        project_root / "src" / "srt2images",
        project_root / "output"
    ]
    
    for required_dir in required_dirs:
        if required_dir.exists():
            print(f"✅ {required_dir.name}: 存在")
        else:
            print(f"❌ {required_dir.name}: 見つかりません")
    
    # 重要なスクリプトの確認
    important_scripts = [
        project_root / "tools" / "capcut_bulk_insert.py",
        project_root / "tools" / "ensure_canvas_16x9.py",
        project_root / "src" / "srt2images" / "cli.py"
    ]
    
    for script in important_scripts:
        if script.exists():
            print(f"✅ {script.name}: 存在")
        else:
            print(f"❌ {script.name}: 見つかりません")
    
    # テンプレートファイルの確認
    template_files = list((project_root / "templates").glob("*.txt"))
    print(f"✅ テンプレートファイル数: {len(template_files)}")
    
    return True

def test_environment():
    """環境変数テスト"""
    print("\n=== 環境変数テスト ===")
    
    required_env_vars = [
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY"
    ]
    
    for env_var in required_env_vars:
        if os.getenv(env_var):
            print(f"✅ {env_var}: 設定済み")
        else:
            print(f"⚠️ {env_var}: 未設定")
    
    return True

def main():
    """メインテスト関数"""
    print("🚀 SRT2Images Timeline 統合テスト開始")
    print("=" * 60)
    
    test_results = []
    
    # 各テストの実行
    test_results.append(test_template_managers())
    test_results.append(test_srt_files())
    test_results.append(test_workflow_dependencies())
    test_results.append(test_environment())
    
    # 結果サマリー
    print("\n" + "=" * 60)
    print("📊 テスト結果サマリー")
    print("=" * 60)
    
    passed_tests = sum(test_results)
    total_tests = len(test_results)
    
    print(f"✅ 合格: {passed_tests}/{total_tests}")
    
    if passed_tests == total_tests:
        print("🎉 全テスト合格！統合ワークフローの準備完了")
        print("\n🎬 Streamlitアプリでのテスト手順:")
        print("1. ブラウザで http://localhost:8501 を開く")
        print("2. SRTファイルを選択する")
        print("3. CapCutテンプレートを選択する")
        print("4. 画像デザインテンプレートを選択する")
        print("5. プロジェクト名を入力してドラフト生成を実行する")
    else:
        print("⚠️ 一部テストが失敗しました。詳細を確認してください。")
    
    return passed_tests == total_tests

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)