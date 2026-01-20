# PLAN_REMOTION_MAINLINE — Remotion本線化（CapCut級の自動レンダー）計画

## Plan metadata
- **Plan ID**: PLAN_REMOTION_MAINLINE
- **ステータス**: Draft（本線化ロードマップ）
- **目的**: CapCut相当の見た目/テンポの動画を **Remotionで自動生成**できる状態にする（人手の“編集”依存を減らす）
- **対象範囲**: `workspaces/video/runs/<run_id>` から `final.mp4` を安定生成し、運用品質にする
- **非対象（初期）**: 生成AIの大改造（画像生成ロジック/台本ロジック自体の刷新）、投稿自動化の全面刷新
- **関連**:
  - 実装（Reference）: `apps/remotion/README.md`, `apps/remotion/REMOTION_PLAN.md`
  - 入口（正本）: `./ops remotion render-batch -- --channel CHxx --videos NNN --run`（実体: `scripts/ops/render_remotion_batch.py`）
  - I/O正本: `ssot/ops/OPS_IO_SCHEMAS.md`, `ssot/ops/DATA_LAYOUT.md`
  - 共有ストレージ: `ssot/ops/OPS_SHARED_ASSET_STORE.md`

---

## 0) 北極星（このプロダクトの価値）

- **CapCut級の品質を“クリックで再現”できる**こと。
- そのために、Remotionを **本線レンダラ**（= 既定の動画生成エンジン）へ昇格させる。

固定ルール:
- サイレントfallback禁止（失敗したら止めて原因を示す）
- 設計と実装の分離（I/Oは `run_dir` に固定、レンダラは差し替え可能）
- チャンネル別の見た目は “preset” を正本にし、コードへ散らさない

---

## 0.1 CapCut級の定義（品質仕様 / 固定）

この Plan で言う「CapCut級」は、次を満たす状態を指す（= 実装のゴールを曖昧にしない）。

- **字幕**: 読める（縁取り/背景/行数/改行が崩れない）+ チャンネルpresetで固定できる
- **帯（belt）**: 4行日本語でも崩れない + チャンネルpresetで固定できる
- **カット**: 画像切替が “音声に追従” している（cues/chapters の契約どおり）
- **動き**: Ken Burns / クロスフェード等の “軽い動き” を preset でオン/オフできる
- **破綻しない**: 欠損（画像/音声/SRT/JSON）や危険値（ASCII混入/空字幕/無限尺）で必ず止まる

非ゴール（初期）:
- “CapCutの全機能” の再現（エフェクト/テンプレ網羅）
- 人手での細かい手動編集を前提にしたワークフロー

## 1) DoD（完了条件）

### D1: 単発レンダーが安定
- 入口固定: `./ops remotion render-batch -- --channel CHxx --videos NNN --run`
- 出力固定: `workspaces/video/runs/<run_id>/remotion/output/final.mp4`
- 欠損（画像/音声/SRT/JSON）があれば **非0終了**し、原因がログで一発特定できる

### D2: CapCut互換レイアウト（最低限）
- 帯/字幕/画像の位置と見え方が、チャンネルpresetで再現できる
- `snapshot`（静止画）で比較でき、ズレが出たら preset で修正できる

### D3: 大規模バッチの運用が成立
- chunkレンダー + resume が標準（中断→再実行で完成まで行ける）
- レンダー後は不要な中間物を cleanup（容量を増やさない）

### D4: 成果物が共有ストレージへ集約できる
- `YTM_SHARED_STORAGE_ROOT` 設定時は、`final.mp4` を共有ストレージへミラーできる
- 共有側に manifest（hash/params/生成日時）が残る

---

## 2) 現行資産（すでにあるもの）

- Remotionアプリ: `apps/remotion/`（CapCut互換のワークフローと実装が存在）
- I/O: `workspaces/video/runs/<run_id>` に `image_cues.json` / `belt_config.json` / `chapters.json` / `episode_info.json` 等がある
- レンダーバッチ（実体）: `scripts/ops/render_remotion_batch.py`（chunk/resume/cleanup を備える）

この Plan は「既存資産を “本線運用” に引き上げる」ための配線/規約/品質ゲートを固める。

---

## 2.1 I/O契約（Remotion run_dir / 固定）

Remotion は `workspaces/video/runs/<run_id>` を入力SoTにする（= “どのレンダラでも同じ入力”）。

- 入力SoT（例）:
  - `workspaces/video/runs/<run_id>/episode_info.json`
  - `workspaces/video/runs/<run_id>/chapters.json`
  - `workspaces/video/runs/<run_id>/image_cues.json`
  - `workspaces/video/runs/<run_id>/belt_config.json`
  - `workspaces/audio/final/{CH}/{NNN}/CHxx-NNN.wav` + `.srt`
- 出力SoT（固定）:
  - `workspaces/video/runs/<run_id>/remotion/output/final.mp4`

このI/O契約は `ssot/ops/OPS_IO_SCHEMAS.md` / `ssot/ops/DATA_LAYOUT.md` を正とする。

## 3) マイルストーン（実装順）

### M0: 入口の一本化（迷子ゼロ）
- `./ops remotion ...` を追加し、Remotion系の入口を `./ops` に集約する（render/snapshot/cleanup）
- `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` と `ssot/OPS_SYSTEM_OVERVIEW.md` を更新し、CapCut/Remotionの位置づけを明確化

### M1: 品質ゲート（CapCut級の“最低ライン”）
- レンダー前の preflight を強化（欠損・矛盾・危険値を先に止める）
- `preset_layouts.json` / channel presets の整備を前提に、ズレを preset で吸収できるようにする
- 画像欠損/URL疎通/字幕の不正（改行/タグ）を “決定論” で弾く

### M2: 表現力（CapCutに寄せる）
- クロスフェード + 軽いブラー、Ken Burns（安全な範囲）など “CapCutらしさ” をテンプレ化
- 字幕: 読みやすい縁取り/背景（チャンネル別）を preset 化
- 帯: 4行日本語の崩れゼロ（ASCII混入の即停止）

### M3: 共有ストレージ統合
- `OPS_SHARED_ASSET_STORE` の仕様どおりに、Remotion `final.mp4` を共有ストレージへ同期
- 共有ストレージが未設定なら **停止して報告**（サイレントでローカルに残して終わらない）

### M4: UI統合（後段 / 別スコープ）
- UIで run_dir を選び、レンダー/スナップショット/比較/再実行ができる導線

---

## 4) 確定事項（固定）

- 共有ストレージのマウント先（固定）: `YTM_SHARED_STORAGE_ROOT=/Volumes/workspace/doraemon/workspace/lenovo_share`
- 共有側の置き場所（固定）: `$YTM_SHARED_STORAGE_ROOT/uploads/$YTM_SHARED_STORAGE_NAMESPACE/`
  - 既定namespace: `factory_commentary`

## 5) 決定すべき点（Owner確認待ち）

- “本線” の定義:
  - Remotionを既定にするタイミング（いつから `CapCut=fallback` にするか）
  - publish前に必須とする品質チェック（D2の“ズレ許容”の閾値）
