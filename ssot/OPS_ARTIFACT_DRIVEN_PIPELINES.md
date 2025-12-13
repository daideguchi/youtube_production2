# OPS_ARTIFACT_DRIVEN_PIPELINES — artifact駆動設計（THINK/API共通）

## 目的
動画生成は「LLMの挙動」より「**固定スキーマのartifact**」をSoTにするほうが安定する。

- THINK MODE（agent）でも APIモードでも、**同じartifactを作る**。
- **artifactが揃ったら処理が進む**（未生成/不整合は即停止）。
- 生成物は「目視で直せる」「再実行しても同じ入力なら同じ結果」へ寄せる。

## 設計原則
1. **型（schema）を先に決める**
   - 例: `ytm.srt_segments.v1`, `ytm.visual_cues_plan.v1`, `ytm.image_cues.v1`
2. **LLMは“artifact生成器”としてだけ使う**
   - LLMの出力は必ずschemaに落とし込み、以降はartifactを読む。
3. **入力取り違えを防ぐ**
   - `source_srt.sha1` のようなハッシュをartifactへ埋め、違えば止める（古いSRTを誤参照しない）。
4. **フォールバックで劣化させない**
   - 「機械分割」「雑な等間隔」などは契約上NG。
   - 不足は「pendingで停止 → 埋めて再実行」を徹底。

## 動画生成（SRT→画像→CapCut）のartifact（現行）
出力先: `commentary_02_srt2images_timeline/output/<run_id>/`

- `srt_segments.json`
  - schema: `ytm.srt_segments.v1`
  - SRTを決定論でパースしたsegments（start/end/text）
- `visual_cues_plan.json`
  - schema: `ytm.visual_cues_plan.v1`
  - sections（start/end segment + visual_focus等）
  - THINK/AGENTで未完の場合は `status=pending`（埋めたら `ready` 扱い）
- `image_cues.json`
  - schema: `ytm.image_cues.v1`
  - 最終的なcue（start/end/prompt含む）と画像生成設定

## 台本生成（script_pipeline）のartifact（追加）
出力先: `script_pipeline/data/{CH}/{NNN}/`（または `workspaces/scripts/{CH}/{NNN}/`）

- `artifacts/llm/*.json`
  - schema: `ytm.llm_text_output.v1`
  - 用途: **LLMが作るテキスト出力を固定スキーマでSoT化**（THINK/API共通）
  - 命名: `<stage><log_suffix>__<output_relpath>.json`
    - 例: `script_outline__content__outline.md.json`
  - 重要フィールド:
    - `status`: `pending|ready`
    - `output.path`: 書き出すべき出力ファイル（絶対パス）
    - `sources`: 参照した入力ファイルとsha1（入力取り違え防止）
    - `content`: 出力本文（ready時に必須）

## THINK MODE / APIモードの挙動
- APIモード: LLMがartifactを生成し、即保存 → 続行
- THINK/AGENT:
  - LLM呼び出し箇所で `logs/agent_tasks/` にpendingを作り、プロセスは停止
  - 併せて `visual_cues_plan.json` を `status=pending` の骨格で出す（埋める場所を固定化）
  - 結果（artifact）が揃ったら同じコマンドを再実行して続行

### script_pipeline（台本生成）の補足
- `artifacts/llm/*.json` が存在し `status=ready` なら **APIを呼ばず** `content` をそのまま `output.path` に書き出して続行する。
- `status=pending` の場合は即停止し、担当エージェントが `content` を埋めて `ready` にしてから同じコマンドを再実行する。
- `sources.sha1` が一致しない（入力が変わった）場合は事故防止のため停止し、artifactの作り直しを要求する。

## 運用のコツ
- まず `srt_segments.json` の `source_srt.sha1` を見る（入力取り違えが最悪事故）。
- 画像が等間隔/画風崩れなどの品質問題は、最初に `visual_cues_plan.json` と `image_cues.json` を見て原因を切り分ける。
- `visual_cues_plan.json` を作り直したい場合は `SRT2IMAGES_FORCE_CUES_PLAN=1` を使う。
