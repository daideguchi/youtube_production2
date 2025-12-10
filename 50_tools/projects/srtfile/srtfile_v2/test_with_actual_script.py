#!/usr/bin/env python3
"""
Test script to verify the standardized file naming functionality with actual script content
"""
import tempfile
from pathlib import Path
from tools.file_naming_utils import generate_standard_filename

def test_with_actual_script():
    """Test with actual script content"""
    print("Testing with actual script content...")
    
    # A sample script content similar to what would be generated
    sample_script = """AI医師の桜庭です。
高齢者の皆さまへ、毎日の生活の中で気をつけていただきたい、
健康維持のための基本的なポイントについてお話しします。
第一に、水分補給です。年齢とともに体内の水分量は減少するため、
意識的に水分を取ることが重要です。
第二に、適度な運動です。無理のない範囲で、毎日少しのウォーキングを
続けることが心身の健康に良い影響を与えます。
第三に、バランスの取れた食事です。特にタンパク質と食物繊維の摂取を
意識することが大切です。これらの習慣を続けることで、
健康で長生きな生活を送ることができます。
"""
    
    filename = generate_standard_filename("CH01", "018", sample_script)
    print(f"Generated filename: '{filename}'")
    
    # Check if it follows the expected format
    expected_pattern = "CH01-018_"
    if filename.startswith(expected_pattern) and filename.endswith("_原文.txt"):
        print("✅ Filename format is correct")
    else:
        print("❌ Filename format is incorrect")
    
    # Verify the title part is meaningful
    parts = filename.split('_')
    if len(parts) >= 2:
        title_part = parts[1] if parts[1] != "原文.txt" else parts[0].replace("CH01-018_", "")
        if "高齢者" in title_part or "健康" in title_part:
            print(f"✅ Title contains relevant keywords: '{title_part}'")
        else:
            print(f"⚠️  Title might not contain relevant keywords: '{title_part}'")
    else:
        print("❌ Could not extract title part from filename")


if __name__ == "__main__":
    test_with_actual_script()