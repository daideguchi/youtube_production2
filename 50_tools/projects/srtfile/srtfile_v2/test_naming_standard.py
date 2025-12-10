#!/usr/bin/env python3
"""
Test script to verify the standardized file naming functionality
"""
import tempfile
from pathlib import Path
from tools.file_naming_utils import generate_standard_filename, generate_standard_final_filename, extract_title_for_filename

def test_title_extraction():
    """Test the title extraction function"""
    print("Testing title extraction...")
    
    # Test with a script that has various content
    content1 = """AI医師の桜庭です。
今日は高齢者の健康維持に重要な食事のとり方についてお話しします。
バランスの取れた食事は、健康寿命を延ばす鍵となります。
特に、タンパク質と食物繊維の摂取が重要です。
"""
    
    title1 = extract_title_for_filename(content1)
    print(f"Extracted title 1: '{title1}'")
    
    # Test with another script
    content2 = """心の平安について。
現代社会ではストレスが避けられないものとなっています。
しかし、心の平安を保つための方法はいくつもあります。
呼吸法や瞑想、自然とのふれあいなどが効果的です。
"""
    
    title2 = extract_title_for_filename(content2)
    print(f"Extracted title 2: '{title2}'")
    

def test_standard_filename_generation():
    """Test the standard filename generation"""
    print("\nTesting standard filename generation...")
    
    content = """Summary
高齢者の健康維持の秘訣について

AI医師の桜庭です。
今日は高齢者の皆様に向けた健康維持の秘訣についてお話しします。
"""
    
    filename = generate_standard_filename("CH01", "016", content)
    print(f"Generated standard filename: '{filename}'")
    
    # Verify the format is correct
    expected_pattern = "CH01-016_"
    if filename.startswith(expected_pattern) and filename.endswith("_原文.txt"):
        print("✅ Filename format is correct")
    else:
        print("❌ Filename format is incorrect")
    

def test_final_filename_generation():
    """Test the final script filename generation"""
    print("\nTesting final script filename generation...")
    
    final_filename = generate_standard_final_filename("CH01", "016", "シニア健康", "高齢者の健康維持の秘訣")
    print(f"Generated final filename: '{final_filename}'")
    
    expected = "CH01-016_高齢者の健康維持の秘訣台本.txt"
    if final_filename == expected:
        print("✅ Final filename is correct")
    else:
        print(f"❌ Final filename mismatch. Expected: {expected}, Got: {final_filename}")
    

def test_file_creation():
    """Test creating a file with the standardized naming"""
    print("\nTesting file creation with standardized naming...")
    
    content = """Summary
テストのタイトル
    
これはテスト用のスクリプトです。
ファイル名が正しく生成されるか確認します。
"""
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Generate the standardized filename
        filename = generate_standard_filename("CH99", "999", content)
        file_path = Path(temp_dir) / filename
        
        # Write content to the file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"Created file: {file_path}")
        
        # Verify the file was created with the correct name
        if file_path.exists():
            print("✅ File was created successfully")
            
            # Verify the filename pattern
            if str(file_path).endswith("_原文.txt") and "CH99-999_" in str(file_path):
                print("✅ File name follows the correct pattern")
            else:
                print("❌ File name does not follow the correct pattern")
        else:
            print("❌ File was not created")


if __name__ == "__main__":
    print("Running standardized file naming tests...\n")
    
    test_title_extraction()
    test_standard_filename_generation()
    test_final_filename_generation()
    test_file_creation()
    
    print("\nAll tests completed!")