# OPS_ARTIFACT_DRIVEN_PIPELINES — artifact駆動設計（THINK/API共通）

## 目的
動画生成は「LLMの挙動」より「**固定スキーマのartifact**」をSoTにするほうが安定する。

- THINK MODE（agent）でも APIモードでも、**同じartifactを作る**。
- **artifactが揃ったら処理が進む**（未生成/不整合は即停止）。
- 生成物は「目視で直せる」「再実行しても同じ入力なら同じ結果」へ寄せる。

## 設計ルール
1. **型（schema）を先に決める**
   - 例: `ytm.srt_segments.v1`, `ytm.visual_cues_plan.v1`, `ytm.image_cues.v1`
2. **LLMは“artifact生成器”としてだけ使う**
   - LLMの出力は必ずschemaに落とし込み、以降はartifactを読む。
3. **入力取り違えを防ぐ**
   - `source_srt.sha1` のようなハッシュをartifactへ埋め、違えば止める（古いSRTを誤参照しない）。
4. **フォールバックで劣化させない**
   - 「機械分割」「雑な等間隔」などは契約上NG。
   - **禁止（画像プロンプト品質）**: キーワード辞書・固定モチーフ・固定プール等で `visual_focus`/主題を自動決め打ちすること（例: 「時間」→懐中時計、のような単純置換）。単調化・量産事故の原因になる。
   - **最重要（意味整合）**: 各cueの画像は **「そのセクション（区切ったキュー）の内容を正確に表現する」**。抽象/比喩でも、当該セクションの具体（行動/物体/状況/たとえ）に必ず紐づけ、**無関係な象徴（時計/懐中時計など）で埋めない**。単調になったら `visual_cues_plan.json` を直してから再実行する。
   - 不足は「pendingで停止 → 埋めて再実行」を徹底。

## 動画生成（SRT→画像→CapCut）のartifact（現行）
出力先: `workspaces/video/runs/<run_id>/`

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
出力先: `workspaces/scripts/{CH}/{NNN}/`

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
  - LLM呼び出し箇所で `workspaces/logs/agent_tasks/` にpendingを作り、プロセスは停止
  - 併せて `visual_cues_plan.json` を `status=pending` の骨格で出す（埋める場所を固定化）
  - 結果（artifact）が揃ったら同じコマンドを再実行して続行

### script_pipeline（台本生成）の補足
- `artifacts/llm/*.json` が存在し `status=ready` なら **APIを呼ばず** `content` をそのまま `output.path` に書き出して続行する。
- `status=pending` の場合は即停止し、担当エージェントが `content` を埋めて `ready` にしてから同じコマンドを再実行する。
- `sources.sha1` が一致しない（入力が変わった）場合は事故防止のため停止し、artifactの作り直しを要求する。

## 運用のコツ
- まず `srt_segments.json` の `source_srt.sha1` を見る（入力取り違えが最悪事故）。
- 画像が等間隔/画風崩れなどの品質問題は、最初に `visual_cues_plan.json` と `image_cues.json` を見て原因を切り分ける。
- **f-1（FLUX schnell）等で「似た絵が量産」された時の復旧**（外部LLM APIコストを使わない）:
  - 標準: `audit_fix_drafts.py` を **THINK MODE** で回し、画像プロンプト（`refined_prompt`）はエージェントが推論して埋める（pending→complete→rerun）。
    - 例: `./ops think video audit-fix-drafts -- --channel CHxx --min-id 43 --max-id 82 --refine-prompts --regen-refined-subject-dupes --refined-subject-dupe-min-count 2`
  - fallback（LLM無し）: `audit_fix_drafts.py --refine-prompts-local` は **非決定（SystemRandom）**でローカルに `refined_prompt` を再合成し、固定モチーフ/固定seedでの量産を避ける。
    - 例: `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN=f-1 PYTHONPATH=".:packages" python3 -m video_pipeline.tools.audit_fix_drafts --channel CHxx --min-id 43 --max-id 82 --refine-prompts-local --regen-refined-subject-dupes --refined-subject-dupe-min-count 2`
  - 既存の `refined_prompt` を横断で見て subject 重複を潰す: `--regen-refined-subject-dupes --refined-subject-dupe-min-count 2`
  - 文字/数字を誘発する小道具は `--avoid-props` / `YTM_DRAFT_AVOID_PROPS` で動的に避ける（チャンネル固有分岐を増やさない）。
- `visual_cues_plan.json` を作り直したい場合は `SRT2IMAGES_FORCE_CUES_PLAN=1` を使う。
