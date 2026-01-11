#!/usr/bin/env python3
"""
Extract 4 chapter titles from SRT file using Gemini LLM analysis.
"""
import os
import sys
import json
import re
from pathlib import Path
from typing import List, Dict


def parse_srt(srt_path: Path) -> List[Dict]:
    """Parse SRT and return segments with timing."""
    content = srt_path.read_text(encoding='utf-8').lstrip('\ufeff')
    pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\n\d+\n|\Z)'
    matches = re.findall(pattern, content, re.DOTALL)

    def time_to_sec(t):
        h, m, s_ms = t.split(':')
        s, ms = s_ms.split(',')
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    return [{
        'index': int(m[0]),
        'start_sec': time_to_sec(m[1]),
        'end_sec': time_to_sec(m[2]),
        'text': m[3].strip().replace('\n', ' ')
    } for m in matches]


def analyze_chapters(transcript: str, total_duration: float) -> List[Dict]:
    """Use Gemini to generate 4 chapter titles."""
    import google.generativeai as genai

    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')

    prompt = f"""以下は「人生の道標」シリーズの台本全文（総時間: {total_duration:.1f}秒）です。

【タスク】
この内容を時系列で4つのセクションに分割し、各セクションに簡潔でわかりやすいタイトルをつけてください。

【要件】
1. タイトル形式: 「①〜〜」「②〜〜」「③〜〜」「④〜〜」
2. 長さ: 各15〜30文字（CapCut帯レイヤーに表示）
3. 内容: 視聴者がそのセクションで何を学ぶかが明確
4. 時間配分: 各セクションの開始時刻を推定

【出力形式】
JSONのみ（説明不要）:
{{
  "chapters": [
    {{
      "title": "①〜〜",
      "start_sec": 0.0,
      "description": "セクション内容の簡潔な説明"
    }},
    ...
  ]
}}

【台本】
{transcript}
"""

    response = model.generate_content(prompt)
    result = response.text.strip()

    # Extract JSON
    if '```json' in result:
        result = result.split('```json')[1].split('```')[0].strip()
    elif '```' in result:
        result = result.split('```')[1].split('```')[0].strip()

    data = json.loads(result)
    chapters = data.get('chapters', [])

    # Fill end_sec
    for i, ch in enumerate(chapters):
        ch['end_sec'] = chapters[i + 1]['start_sec'] if i < len(chapters) - 1 else total_duration

    return chapters


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Extract 4 chapter titles from SRT")
    ap.add_argument("srt_file", help="SRT file path")
    ap.add_argument("--output", help="Output JSON file")
    args = ap.parse_args()

    srt_path = Path(args.srt_file)
    segments = parse_srt(srt_path)

    if not segments:
        print(f"❌ No segments in {srt_path}")
        sys.exit(1)

    total_duration = segments[-1]['end_sec']
    transcript = '\n'.join([s['text'] for s in segments])

    print(f"📖 Parsed {len(segments)} segments ({total_duration:.1f}s)")
    print(f"🤖 Analyzing with Gemini...")

    chapters = analyze_chapters(transcript, total_duration)

    print(f"\n✅ Generated {len(chapters)} chapters:\n")
    for ch in chapters:
        print(f"{ch['title']}")
        print(f"  {ch['start_sec']:.1f}s - {ch['end_sec']:.1f}s")
        print(f"  {ch.get('description', '')}\n")

    output_data = {'total_duration': total_duration, 'chapters': chapters}

    if args.output:
        Path(args.output).write_text(json.dumps(output_data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"💾 Saved: {args.output}")
    else:
        print(json.dumps(output_data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
