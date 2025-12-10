# 対話型TTS音声生成 - 再現性100%プロトコル

## 概要

このドキュメントは、私（Claude）が対話型モードでTTS音声を生成する際の完全なプロトコルである。
**役割を忘れないこと。私がLLM推論を担当する。**

---

## 前提条件

- Voicevoxサーバーが起動中: `http://localhost:50021`
- 環境変数が設定済み: `AOYAMA_SPEAKER_ID=13`

---

## 1エピソードあたりの処理フロー

### Step 1: Aテキスト読み込み・分析

```
ファイル: script_pipeline/data/<CH>/<VIDEO>/content/assembled.md
```

**分析項目:**
1. 誤読リスクのある固有名詞・漢字を特定
2. 中黒（・）の使用箇所を確認し、除去判断
3. 章構成を把握

### Step 2: Bテキスト作成

**出力先:** `script_pipeline/data/<CH>/<VIDEO>/audio_prep/script_corrected.txt`

**変換ルール:**

| 項目 | 変換前 | 変換後 | 判断基準 |
|------|--------|--------|----------|
| 中黒（固有名詞） | レオナルド・ダ・ヴィンチ | レオナルドダヴィンチ | 繰り返し登場する固有名詞 |
| 中黒（意味区切り） | あれ・これ・それ | そのまま | 意味的な列挙 |
| 章タイトル | ## 第1章：タイトル | 第1章、タイトル | ##削除、：→、 |
| 誤読漢字 | 青山（文脈で判断） | せいざん/あおやま | 文脈による |
| AI | AI | エーアイ | 読み方統一 |

### Step 3: TTS実行

```bash
PYTHONPATH=audio_tts_v2 python audio_tts_v2/scripts/run_tts.py \
  --channel <CH> \
  --video <VIDEO> \
  --input script_pipeline/data/<CH>/<VIDEO>/audio_prep/script_corrected.txt \
  --skip-annotation
```

**重要:** `--skip-annotation` を必ず付ける。私がLLM推論を担当したから。

### Step 4: 出力確認

```
audio_tts_v2/artifacts/final/<CH>/<VIDEO>/
├── <CH>-<VIDEO>.wav    # 音声ファイル
├── <CH>-<VIDEO>.srt    # 字幕（Aテキスト表示）
└── log.json            # 処理ログ
```

---

## バッチ処理の効率化

### 複数エピソードを連続処理する場合

1. まず全エピソードのBテキストを作成
2. 次にTTSを順次実行

```bash
# 例: CH06の002-010を処理
for vid in 002 003 004 005 006 007 008 009 010; do
  PYTHONPATH=audio_tts_v2 python audio_tts_v2/scripts/run_tts.py \
    --channel CH06 --video $vid \
    --input script_pipeline/data/CH06/$vid/audio_prep/script_corrected.txt \
    --skip-annotation
done
```

---

## チャンネル別設定

| CH | 名前 | エンジン | Speaker ID |
|----|------|----------|------------|
| CH01 | 人生の道標 | Voicepeak | Male 3 |
| CH02 | 静寂の哲学 | Voicevox | 13 (青山流星) |
| CH03 | シニアの健康 | Voicepeak | Female 1 |
| CH04 | 隠れ書庫アカシック | Voicevox | 13 (青山流星) |
| CH05 | シニア恋愛 | Voicevox | 9 (波音リツ) |
| CH06 | 都市伝説のダーク図書館 | Voicevox | 13 (青山流星) |
| CH07〜11 | 新規チャンネル | Voicevox | 13 (青山流星) |

---

## 忘却防止チェックリスト

毎回確認すること：

- [ ] **私がLLM推論を担当している**（gpt-5-miniではない）
- [ ] Bテキスト（script_corrected.txt）を自分で作成した
- [ ] `--skip-annotation` を付けてTTS実行した
- [ ] 中黒除去は固有名詞のみ（意味区切りは除去禁止）

---

## 現在の進捗

### CH06 (33本)
- [x] 001 - 完了
- [x] 002 - 完了
- [/] 003 - TTS実行中
- [ ] 004 - Bテキスト作成済み
- [ ] 005 - Bテキスト作成済み
- [ ] 006-033 - 未処理

### CH02 (82本)
- [ ] 001-082 - 未処理

### CH04 (30本)
- [ ] 001-030 - 未処理
