import pandas as pd
import re

def enhance_prompts(file_path):
    try:
        df = pd.read_csv(file_path)
    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
        return

    target_column = 'DALL-Eプロンプト（URL・テキスト指示込み）'
    
    if target_column not in df.columns:
        print(f"Error: Column '{target_column}' not found in CSV.")
        return

    # Enhanced style description based on reference images
    enhanced_style = """ドラマチックな照明効果と深い陰影。油絵の厚みのある筆致と、水彩画の流動的なにじみを融合させた独特の質感。強烈なコントラストと鮮やかな色彩対比。象徴的でメタファリカルな構図。映画ポスターのようなシネマティックで劇的な雰囲気。ビビッドなカラーとダークトーンの大胆な対比。光と影のドラマティックな演出。芸術的で感情を揺さぶる表現。16:9のワイドスクリーン構図。プロフェッショナルな仕上がり。"""

    def enhance_text(text):
        if not isinstance(text, str):
            return text
        
        # Remove the old style instructions if present
        old_patterns = [
            r'インパクト強く、水彩画と油絵を混ぜたような感じ。',
            r'文字は一切入れないでください。',
            r'アスペクト比16:9。'
        ]
        
        cleaned = text
        for pattern in old_patterns:
            cleaned = cleaned.replace(pattern, '')
        
        # Clean up extra spaces and periods
        cleaned = re.sub(r'。\s*。', '。', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        # Ensure it ends with a period
        if cleaned and not cleaned.endswith('。'):
            cleaned += '。'
        
        # Add the enhanced style
        result = f"{cleaned} {enhanced_style} テキストや文字は一切含めないでください。"
        
        return result.strip()

    # Apply enhancement
    df[target_column] = df[target_column].apply(enhance_text)

    # Save back to CSV
    df.to_csv(file_path, index=False)
    print(f"Successfully enhanced prompts in {file_path}")

    # Show a sample
    print("\n--- Sample of Enhanced Prompts ---")
    sample = df[target_column].dropna().head(2)
    for i, prompt in enumerate(sample.values, 1):
        print(f"\n[Sample {i}]")
        print(prompt[:300] + "..." if len(prompt) > 300 else prompt)

if __name__ == "__main__":
    file_path = 'workspaces/planning/channels/CH02.csv'
    enhance_prompts(file_path)
