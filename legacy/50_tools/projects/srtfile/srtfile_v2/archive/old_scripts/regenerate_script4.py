#!/usr/bin/env python3
"""台本4再生成: カルマの法則と魂の成長（6000字以上・高品質版）"""

import os
import re
import google.generativeai as genai

# Gemini設定
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-pro')

# 超厳格な形式指示
strict_format = '''
【絶対遵守】出力形式の厳守:

【ルール1】1行は必ず27文字以内（これを超えたら即エラー）
【ルール2】1セクションは必ず1行または2行のみ（3行以上は絶対禁止）
【ルール3】セクション終わりに必ず「///」
【ルール4】前置き・見出し・箇条書き一切禁止
【ルール5】最初の行からすぐに台本本文を開始すること
【ルール6】括弧 () （） を一切使わないこと
【ルール7】読点は適度に使用（少なめ・1行に最大2個まで）
【ルール8】総文字数は必ず6000字以上（重要）

【悪い例】:
- 3行セクション（絶対禁止）
- 括弧使用「カルマ（業）」→「カルマ、つまり業」と言い換える
- 読点過多「今日は、あなたに、大切な、話を」→「今日はあなたに、大切な話を」

【良い例（2行セクション・読点少なめ）】:
カルマの法則は宇宙の
根本原理です。///

あなたの行いは必ず、
自分自身に還ってきます。///

【重要】文字数6000字以上を確保するため、各セクションで深い洞察を提供してください。
'''

# プロンプト読み込み
with open('scripts/アカシック台本プロンプト', 'r', encoding='utf-8') as f:
    base_prompt = f.read()

# テーマ指示（詳細版）
theme = '''
【台本4: カルマの法則と魂の成長】（6000字以上必須）

このテーマで、アカシックレコードとスピリチュアルな視点からカルマの法則を解説する台本を作成してください。

【重点ポイント】:
1. カルマの本質
   - 因果応報ではなく魂の学びの法則
   - 罰ではなく愛の教え
   - 宇宙の完璧なバランスシステム

2. カルマの種類
   - 個人的カルマ（今世の行い）
   - 過去世からのカルマ（魂の宿題）
   - 集合的カルマ（家族・民族・人類）

3. カルマの解消方法
   - 気づきと自己観察
   - 愛と許しの実践
   - 行動パターンの変容

4. 魂の成長とカルマの関係
   - 困難な経験は魂が選んだ学び
   - カルマは固定されていない
   - 自由意志による変容可能性

5. 実践的アプローチ
   - 日常での気づきの訓練
   - 反応から応答への転換
   - 無条件の愛の実践

6. 究極の解放
   - カルマからの卒業
   - 悟りと解脱
   - 宇宙意識との一体化

【文章の質】:
- 小難しい概念も平易な言葉で説明
- 具体例と比喩を多用
- 視聴者が人生に適用できる実践的内容
- 深い洞察と希望を与える内容

【文字数確保のため】:
- 各セクションで丁寧に解説
- 具体例を豊富に
- 7つのセクション全てをしっかり展開
- 目標: 6000-7000字
'''

print('🧠 Gemini 2.5 Pro: 台本4再生成開始（6000字以上・高品質版）...')
response = model.generate_content(strict_format + base_prompt + theme)

# 前処理（メタコメント・見出し削除）
content = response.text
content = re.sub(r'^.*?[\n]*(カルマ|あなたの|もし|なぜ|私たち|魂)', r'\1', content, flags=re.DOTALL)
content = re.sub(r'###\s*セクション\d+:.*?\n\n?', '', content)
content = re.sub(r'\n{3,}', '\n\n', content)

# 保存
with open('scripts/4_アカシック台本', 'w', encoding='utf-8') as f:
    f.write(content)

print(f'✅ 台本4再生成完了: scripts/4_アカシック台本')
print(f'📊 文字数: {len(content)}文字')

# 即座に品質チェック
import subprocess
result = subprocess.run(
    ['python3', 'tools/script_quality_checker.py', 'scripts/4_アカシック台本'],
    capture_output=True,
    text=True
)
print(result.stderr)
print(result.stdout)
