---
description: TTS音声生成の確定ワークフロー（対話型/非対話型）
---

# TTS Audio Generation Workflow

高品質な音声体験（聴き心地の良いBテキスト）と、正確な表示字幕（Aテキスト）を両立させるためのワークフロー。
機械的な処理と、AIによる推論（文脈理解）を適切に役割分担する。

## Core Concept
- **A-Text (Display)**: 画面に表示される字幕。`assembled.md` そのもの。
- **B-Text (Reading)**: 音声合成エンジンに渡すテキスト。
    - **Identification (知能)**: 見出し特定、重要箇所の抽出はLLM/正規表現が担う。
    - **Action (機械)**: 特定された箇所への「ポーズ挿入」「分割」は、ルールベースで機械的に実行する。

## Pause & Segmentation Rules
1. **Headings (見出し)**: 
    - **特定**: 正規表現 (`##`, `第X章`, `結び`) および LLMセグメンテーション。
    - **機械的処理**: 見出しの**前後**に必ず「一泊（1.0秒/0.75秒）」の間を挿入する。
2. **Important Remarks (重要な発言)**:
    - **特定**: LLMが文脈から「強調すべき」と判断した箇所。
    - **処理**: 前後に間を置く。

## Pattern 1: Interactive Mode (Agent Inference)
AIエージェント（あなた）がBテキストを作成するモード。

1. **Source Analysis**: エージェントが文脈を理解する。
2. **B-Text Creation**: `script_corrected.txt` を作成。
    - ヘッダーは削除せず、システムの正規表現ロジックが拾える形式、または明確に改行で分離しておく。
3. **Trigger TTS**:
    ```bash
    PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts \
      --channel {channel} \
      --video {video_id} \
      --input workspaces/scripts/{channel}/{video_id}/audio_prep/script_corrected.txt \
      --skip-annotation
    ```

## Pattern 2: Non-Interactive Mode (System Inference)
夜間バッチなどで自動実行するモード。

1. **Trigger TTS**:
    ```bash
    PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts \
      --channel {channel} \
      --video {video_id} \
      --input workspaces/scripts/{channel}/{video_id}/content/assembled.md
    ```
    - LLM Prompt (`SRT_SEGMENT_PROMPT`) により、見出しを必ず別セグメントとして出力させる。
    - Pythonロジック (`orchestrator.py`) が正規表現で見出しを検出し、機械的にポーズを適用する。
