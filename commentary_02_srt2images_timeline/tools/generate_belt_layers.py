#!/usr/bin/env python3
"""
Generate belt layer configuration for CapCut draft.

Combines episode title from spreadsheet and chapter titles from SRT analysis
to create belt layer JSON configuration.

Usage:
    python3 tools/generate_belt_layers.py \
        --episode-info output/jinsei186/episode_info.json \
        --chapters output/jinsei186/chapters.json \
        --output output/jinsei186/belt_config.json
"""
import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional


def generate_belt_layers(
    episode_info: Dict,
    chapters_data: Dict,
    equal_split: bool = False,
    labels: Optional[List[str]] = None,
    sections: int = 4,
    opening_offset: float = 3.0,
) -> Dict:
    """
    Generate belt layer configuration.

    Track 4 (lower): Main title - full duration
    Track 3 (upper): chapter titles - time-based segments

    When equal_split=True, split total_duration into `sections` contiguous parts
    and apply labels (defaults are Japanese).
    """
    episode_num = episode_info.get('episode', '')
    title = episode_info.get('title', '')

    # Clean title: remove 【】brackets if present
    clean_title = title.replace('【', '').replace('】', '')

    # Track 4: Main title (lower belt)
    main_title = clean_title

    total_duration = chapters_data.get('total_duration', 0)
    chapters = chapters_data.get('chapters', [])

    # Equal split fallback (ensures contiguity)
    belt_upper = []
    if equal_split or not chapters:
        labels = labels or [
            "序章: 序盤の導入",
            "転機: 気づきと変化",
            "対策: 実行ステップ",
            "結び: 行動と未来",
        ]
        sections = max(1, sections)
        span = total_duration / sections if sections else total_duration
        for i in range(sections):
            start = span * i
            end = total_duration if i == sections - 1 else span * (i + 1)
            belt_upper.append({
                "track": 3,
                "text": labels[i] if i < len(labels) else labels[-1],
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "duration_sec": round(end - start, 3),
                "font_size": 4.5,
                "position": "upper"
            })
    else:
        for idx, ch in enumerate(chapters, 1):
            title = ch['title']
            if not title.startswith(f"{idx}."):
                title = f"{idx}. {title}"
            belt_upper.append({
                "track": 3,
                "text": title,
                "start_sec": ch['start_sec'],
                "end_sec": ch['end_sec'],
                "duration_sec": ch['end_sec'] - ch['start_sec'],
                "font_size": 4.5,
                "position": "upper"
            })

    belt_lower = {
        "track": 4,
        "text": main_title,
        "start_sec": opening_offset,  # default opening offset
        "duration_sec": total_duration,
        "font_size": 5.0,
        "position": "upper"
    }

    return {
        "episode": episode_num,
        "main_title": main_title,
        "total_duration": total_duration,
        "belt_lower": belt_lower,
        "belt_upper": belt_upper
    }


def main():
    parser = argparse.ArgumentParser(description="Generate belt layer configuration")
    parser.add_argument('--episode-info', required=True, help="Episode info JSON file")
    parser.add_argument('--chapters', required=True, help="Chapters JSON file")
    parser.add_argument('--output', required=True, help="Output belt config JSON file")
    parser.add_argument('--equal-split', action='store_true', help="Split total duration evenly into sections (ignores chapter titles)")
    parser.add_argument('--sections', type=int, default=4, help="Number of equal sections when --equal-split")
    parser.add_argument('--labels', help="Comma-separated labels for equal split (Japanese recommended)")
    parser.add_argument('--opening-offset', type=float, default=3.0, help="Lower belt start offset")
    args = parser.parse_args()

    # Load episode info
    episode_info_path = Path(args.episode_info)
    if not episode_info_path.exists():
        print(f"❌ Episode info not found: {episode_info_path}")
        return 1

    with open(episode_info_path, 'r', encoding='utf-8') as f:
        episode_info = json.load(f)

    # Load chapters
    chapters_path = Path(args.chapters)
    if not chapters_path.exists():
        print(f"❌ Chapters not found: {chapters_path}")
        return 1

    with open(chapters_path, 'r', encoding='utf-8') as f:
        chapters_data = json.load(f)

    # Parse labels if provided
    labels = None
    if args.labels:
        labels = [x.strip() for x in args.labels.split(",") if x.strip()]

    # Generate belt configuration
    belt_config = generate_belt_layers(
        episode_info,
        chapters_data,
        equal_split=args.equal_split,
        labels=labels,
        sections=args.sections,
        opening_offset=args.opening_offset,
    )

    # Save to output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(belt_config, f, ensure_ascii=False, indent=2)

    # Display results
    print(f"✅ Belt layer configuration generated!")
    print(f"\n📋 Main Title (Track 4 - Lower):")
    print(f"   {belt_config['main_title']}")
    print(f"   Duration: {belt_config['belt_lower']['duration_sec']:.1f}s")

    print(f"\n📋 Chapter Titles (Track 3 - Upper):")
    for idx, ch in enumerate(belt_config['belt_upper'], 1):
        print(f"   {idx}. {ch['text']}")
        print(f"      {ch['start_sec']:.1f}s - {ch['end_sec']:.1f}s ({ch['duration_sec']:.1f}s)")

    print(f"\n💾 Saved to: {output_path}")

    return 0


if __name__ == "__main__":
    exit(main())
