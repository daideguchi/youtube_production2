# 高品質台本生成ワークフロー（OpenRouter連携版）
最終更新: 2025-11-07
目的: 「自然な日本語」「ドラマ性」「再現性」を両立しつつ、OpenRouter経由の上位モデルを活用して台本量産を安定化させる。

---

## 1. 全体像
1. **企画インプット整備**: `tools/generate_prompt_input.py`＋`マスタープロンプト`で入力テンプレを確定。
2. **下書きアウトライン**: Codex/ローカルで10段階ストーリーの要約を整え、OpenRouterへ渡す構造を固定。
3. **OpenRouterドラフト生成**: 自然な日本語が得意なモデルで本文を生成。必要に応じて複数モデル→ベスト選択/マージ。
4. **Codex仕上げ & QA**: `tools/check_script.py`, textlint, 表記ルール適用で揺れを除去。
5. **最終レビュー**: ベンチマーク観察メモと照合し、逆転シーンの呼吸/余韻を手で微調整。

---

## 2. Step-by-Step
### Step0: 環境
- OpenRouter APIキー: https://openrouter.ai/ で発行 → `export OPENROUTER_API_KEY=...`
- 推奨HTTPヘッダ:
  - `Authorization: Bearer $OPENROUTER_API_KEY`
  - `HTTP-Referer`: 自サイトURL
  - `X-Title`: 「Jinsei-no-Michishirube Script Lab」など

### Step1: 企画テンプレ生成
- コマンド:
  ```bash
  python3 tools/generate_prompt_input.py \
    --csv "2025_人生の道標企画 - 企画.csv" \
    --video-id CH01-010 \
    --reference "逆転の一言" \
    --teaching "怒りを慈悲で包む" \
    --protagonist "川口誠一 / 62歳 / 元設計士" \
    --antagonist "田島悠斗 / 28歳 / 起業家" \
    --turning-line "学ばせてもらえますか" \
    --constraints "6000-8000字 / 15-20分"
  ```
- 出力を `マスタープロンプト_人生の道標台本.md` の<<INPUT TEMPLATE>>に貼り付け。

### Step2: アウトライン草稿
- `outline_TEMPLATE.md` を `outline_$(VIDEO).txt` などのファイル名でコピーし、10段階ストーリーを2-3行ずつ埋める。必要なら `戦略.md` のチェックリストを引用し、OpenRouterへ渡す前に構造を凍結。
- ここで主人公/敵役のセリフ案を数本用意（攻撃セリフ2案、逆転セリフ1案）。完成したら `make input VIDEO=...` でINPUTテンプレを生成、`make draft VIDEO=... MODEL=...` を実行してドラフトを取得。

### Step3: OpenRouterドラフト
1. **推奨モデル**
   - `anthropic/claude-3.5-sonnet` (ナラティブ力＋自然な日本語)
   - `google/gemini-1.5-pro` (構成の堅さ)
   - `meta-llama/llama-3.1-70b-instruct` (会話の自然さ)
   - `rinna/youri-7b-chat` (日本語特化) をサブで併用
2. **curl例**
   ```bash
   curl https://openrouter.ai/api/v1/chat/completions \
     -H "Authorization: Bearer $OPENROUTER_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "anthropic/claude-3.5-sonnet",
       "messages": [
         {"role": "system", "content": "あなたは... (マスタープロンプト<<ROLE>>引用)"},
         {"role": "user", "content": "<<INPUT TEMPLATE>>..."},
         {"role": "user", "content": "10段階アウトライン:\n1...."}
       ],
       "temperature": 0.6,
       "max_tokens": 4096
     }'
   ```
3. **ベスト・オブ戦略**
   - まずSonnetでドラフト、Gemini/LLamaで補助。特定セクションのみ別モデルに差し替える場合は該当セクションだけ再プロンプト。
   - OpenRouterの`metadata.preferred_models`を活用するとリトライ時に優先順位を変更可。

### Step4: ハイブリッド仕上げ
1. Codexで微修正: セクション構成や注釈を整える（この時点で英語や記号が残っていないか確認）。
2. `make qa FILE=drafts/$(VIDEO).txt` を実行（主語チェックに使う名前があれば `CHECK_NAMES=\"清水 石川\"` のように追加）。内部で `tools/check_script.py` と `textlint --preset textlint-rule-preset-jtf-style` を実行。
3. `make audio FILE=drafts/$(VIDEO).txt` もしくは `say -f drafts/$(VIDEO).txt` で簡易TTSを流し、耳で違和感をメモ。気になった行はその場で修正する。
4. 表記差分を反映し、`ベンチマーク_逆転の一言_観察メモ.md` の呼吸タイミングを挿入。

### Step5: 品質ゲート
- チェックリスト（抜粋）
  - [ ] 10段階ストーリー全実装
  - [ ] 逆転シーンの呼吸・沈黙を秒数で注記
  - [ ] 攻撃セリフが視聴者実体験レベル
  - [ ] 語尾連続検出ツールOK
  - [ ] textlint警告ゼロor理由付き許容
  - [ ] `学習整理` ToDo更新

---

## 3. OpenRouter活用Tips
- **Few-shot**: 参考台本の数行を `assistant` メッセージとして挿入→「この調子で続きを書いて」と指示すると語感が揃う。
- **断片生成**: 物語パート・仏教解説・実践ステップを別プロンプトで生成し、最後にCodexで結合すればモデル負荷を下げられる。
- **翻案モード**: OpenRouter出力が英語寄りなら、`rinna/youri` や `llama-3.1-japanese`に同じアウトラインを渡し、言い回しだけ日本語特化モデルで再生成。
- **コスト管理**: `max_tokens`と`temperature`をセクション毎に調整。物語は0.7、実践パートは0.3など役割ごとに変える。

---

## 4. 実装ロードマップ
1. `.env` で `OPENROUTER_API_KEY` を管理し、テンプレcurlをMakefile化。
2. `make draft VIDEO=CH01-010 MODEL=anthropic/claude-3.5-sonnet` のようにコマンド化。
3. textlintと `tools/check_script.py` を `make qa FILE=...` にまとめ、PR前フックに設定。
4. 完成台本→`学習整理`へ学び追記、`ベンチマーク観察メモ`に呼吸秒数を加筆。

---

## 5. 参考リソース
- OpenRouter Docs: https://openrouter.ai/docs
- JTFスタイルガイド: https://github.com/textlint-rule/textlint-rule-preset-jtf-style
- 既存資産: `マスタープロンプト_人生の道標台本.md`, `運用マニュアル_人生の道標台本制作.md`, `ベンチマーク_逆転の一言_観察メモ.md`
