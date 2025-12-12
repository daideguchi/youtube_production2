# Remotion版 自動動画生成 要件定義・実装計画（CapCut互換パイプライン）

## ゴール
- SRTと画像から、CapCutドラフトと同等レイアウトの動画をRemotionで自動生成できるようにする。
- 台本・チャンネルに依存せず、SRTを差し替えるだけで同ルートをたどれる「型」を提供する。
- 既存SSOT（環境変数・プリセット・帯ルール）を流用し、キャラ一貫/帯日本語4本/アニメ抑制を徹底する。

## スコープ
- 対応レンジ: 16:9 1080p（CapCut既定）をRemotionで出力（mp4 or mov）。
- 入力: `image_cues.json` + `belt_config.json` + `chapters.json` + `episode_info.json` + SRT（既存パイプライン出力を再利用）。
- 出力: `remotion/out/<run>.mp4` と、レンダリングに使ったメタ情報ログ。
- 非スコープ（初期版）: 音声合成、BGM/MIX、テロップ装飾（Remotion上のテキスト配置のみ）。

## 前提・依存
- `.env` は `/Users/dd/youtube_master/.env` を唯一参照（GEMINI/OPENROUTER等）。
- 画像生成は既存 `run_pipeline.py` 経由で得た静止画をそのまま使う（Remotion側は再生成しない）。
- 帯（4本、日本語）は `belt_config.json` に従う。英語/ASCIIは拒否。
- キャラ一貫・トーン: `config/channel_presets.json` とテンプレート群を参照（アニメ誇張禁止）。

## 成功条件（受け入れ基準）
1) `node ./remotion/scripts/render.js --run output/xxx --draft-name 192_draft` のようなワンショットで動画ファイルが得られる。  
2) 帯・字幕・画像位置がCapCutドラフトとほぼ一致し、スケール=1基準で破綻がない。  
3) ログに使用バージョン（preset、belt、cues、画像枚数、所要時間）が残る。  
4) どのチャンネル・SRTでも、SRT差し替えだけで実行できる（追加設定不要）。  

## 実装計画
### 1. プロジェクトひな形
- `remotion/package.json`（Remotion/React/TypeScript）を初期化。
- tsconfig/eslint/prettierの最低限設定を追加。
- `remotion/src/Root.tsx` にRemotionエントリ（Composition定義）。

### 2. データ読み込みレイヤ
- `remotion/src/lib/loadRunData.ts`: `run_dir` から `image_cues.json`, `belt_config.json`, `chapters.json`, `episode_info.json`, `srt` を読み込み・検証。
- バリデーション: 帯は日本語4本、画像枚数とキュー数一致チェック、尺を算出。

### 3. レイアウトコンポーネント
- `Scene`: 画像＋テキスト（SRT該当セクション）＋帯オーバーレイを描画。
- `BeltOverlay`: `belt_config` をタイムラインにマッピング、色/余白のデフォルトテーマを定義。
- `SubtitleLayer`: SRTを時間ベースで表示（フォント/縁取りは軽量実装）。
- `TitleCard`: 冒頭タイトル（CapCut JSON注入相当）。
- パラメータ: `tx/ty/scale` は preset の position を適用、スケール=1 を基準にする。

### 4. タイムライン組み立て
- `RootComposition`: 全シーンを `image_cues` の start/end に沿って連結。クロスフェード長 `--crossfade` を設定可能にする。
- FPS/解像度は CLI 引数から受け、デフォルト 1920x1080 30fps。

### 5. CLI／レンダラ
- `remotion/scripts/render.js`（Node）で以下を受け取る: `--run <run_dir>`, `--channel`, `--title`, `--size`, `--fps`, `--crossfade`, `--out`.
- 既存 SSOT をロードし、preset を適用。環境変数チェック（GEMINI_API_KEY 等）が足りない場合は警告のみ（画像生成しないため）。
- 実行後に `run_dir/remotion_run_info.json` を出力（パラメータ・尺・書き出しパス・所要時間）。

### 6. UIテーマ/スタイル
- フォント: 日本語可読（例: Noto Sans JP）を同梱 or fallback 指定。
- 色: 水彩系ライトテーマ（CapCut相当）をデフォルト、チャンネル別テーマをオプション化。
- 禁止: 文字描画の多色飾り、巨大フォント、アニメ風アウトライン。

### 7. テストとサンプル
- サンプル実行: `input/CH01_人生の道標/192.srt` + 既存 `output/<run>` を使って `remotion/out/192_sample.mp4` を生成。
- スナップショット: 任意フレームを書き出すデバッグスクリプトを用意し、レイアウト破綻検知を容易にする。

### 8. ロギング/監視
- 主要パラメータと所要時間、エラーを `remotion_run_info.json` に保存。
- 非致命の警告（ラベル数不一致など）は標準出力にも表示。

### 9. 今後の拡張（非スコープだが設計で意識）
- BGM/SE差込、TTS連携（VOICEVOX/ElevenLabs等）。
- Diffusionによる直接生成（RemotionからAPI呼び出し）に備え、ロードレイヤは純粋関数で分離。
- 9:16対応、複数解像度書き出し。

### 10. チャンクレンダリング（タイムアウト対策）
- `scripts/render.js` に `--chunk-sec`（例: 8）と `--resume-chunks` を追加。既存チャンクをスキップしながら続きからレンダリングし、最後に ffmpeg で結合する。
- デフォルトのチャンク出力先は `remotion/out/chunks_<runDirBasename>`（`--chunk-dir` で上書き可）。`--out` に最終mp4を保存。
- 60秒制限がある環境でも、複数回のコマンド実行で全チャンクを埋めれば完成させられる設計とする。`--max-chunks-per-run` で1回に処理するチャンク数を絞れる。

## フォルダ構成（初期案）
```
remotion/
  package.json
  tsconfig.json
  src/
    Root.tsx
    components/
      Scene.tsx
      BeltOverlay.tsx
      SubtitleLayer.tsx
      TitleCard.tsx
    lib/
      loadRunData.ts
      types.ts
  scripts/
    render.js
```

## 次アクション（着手順）
1) `remotion/package.json` 初期化（Remotion, React, TS, @remotion/cli）。  
2) `scripts/render.js` ひな形と `src/Root.tsx` のComposition枠を用意。  
3) `lib/loadRunData.ts` で既存runの読み込み&バリデーションを実装。  
4) 簡易レイアウト（画像+字幕+帯）を描画 → サンプルmp4を書き出して位置確認。  
5) スタイル調整/日本語フォント指定 → 192サンプルで差分検証。  
