import pandas as pd
import re
import sys

def clean_prompts(file_path):
    try:
        df = pd.read_csv(file_path)
    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
        return

    target_column = 'DALL-Eプロンプト（URL・テキスト指示込み）'
    
    if target_column not in df.columns:
        print(f"Error: Column '{target_column}' not found in CSV.")
        return

    # Keywords that indicate a sentence is about text/font instructions
    # If a sentence contains any of these, it will be removed.
    ban_keywords = [
        'テキスト', 'フォント', 'ゴシック', '明朝', 'セリフ体', 
        '筆文字', '文字を', '文字が', '文字の'
    ]

    def clean_text(text):
        if not isinstance(text, str):
            return text
        
        # Split into sentences based on Japanese period
        sentences = text.split('。')
        cleaned_sentences = []
        
        for s in sentences:
            s = s.strip()
            if not s:
                continue
                
            # Check if sentence contains any ban keywords
            should_remove = False
            for kw in ban_keywords:
                if kw in s:
                    should_remove = True
                    break
            
            # Also check for specific patterns like "「...」と表示" or "「...」を配置"
            if '」と表示' in s or '」を配置' in s or '」という' in s:
                 # Double check if it looks like a text instruction
                 if '表示' in s or '配置' in s:
                     should_remove = True

            if not should_remove:
                cleaned_sentences.append(s)
        
        # Reconstruct text
        cleaned = '。'.join(cleaned_sentences)
        if cleaned:
            cleaned += '。'
            
        # Remove specific URL prefix if present
        prefix_to_remove = "https://www.youtube.com/@himitu-o4p https://www.youtube.com/@shaka__namu YouTubeサムネイル画像。"
        cleaned = cleaned.replace(prefix_to_remove, "")
        
        # Add the new style instruction
        style_instruction = "インパクト強く、水彩画と油絵を混ぜたような感じ。"
        if style_instruction not in cleaned:
            cleaned += " " + style_instruction
            
        # Append the "No text" instruction
        no_text_instruction = "文字は一切入れないでください。"
        if no_text_instruction not in cleaned:
             cleaned += " " + no_text_instruction
             
        return cleaned.strip()

    # Apply cleaning
    df[target_column] = df[target_column].apply(clean_text)

    # Save back to CSV
    df.to_csv(file_path, index=False)
    print(f"Successfully processed {file_path}")

    # Show a sample diff
    print("\n--- Sample of Modified Prompts ---")
    print(df[target_column].dropna().head(3).values)

if __name__ == "__main__":
    file_path = 'workspaces/planning/channels/CH02.csv'
    clean_prompts(file_path)
