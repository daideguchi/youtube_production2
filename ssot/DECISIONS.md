# DECISIONS — 意思決定（SSOTトップ）

このファイルは「**SSOT=UI（read-only）** を成立させるために、運用/実装を **どちらに寄せるか決める必要がある点**」を、**選択肢 + 現案（Decision/Proposal） + 根拠** の形で並べた“意思決定台帳”です。

- 方針: **決まったら SSOT → 実装 の順で固定**し、`ops/OPS_GAPS_REGISTER.md` / 関連SSOTを更新していきます。
- 目的: 人間/AIの認識ズレ（=事故とコスト）をゼロにする。
- 重要: **`状態=Done` のみが「運用ルールとして確定」**。`Proposed` は未確定（議論用）なので、矛盾していても運用の正として扱わない。

---

## 0) 決め方（最短）

各 Decision は **現案（Plan）** を提示しています。  
もし違う運用にしたい場合は、代替案（Alternatives）から選び、理由があれば追記してください。

---

## 1) 一覧（P0→P2）

| ID | 重要度 | テーマ | 現案（要約） | 状態 |
| --- | --- | --- | --- | --- |
| D-001 | P0 | redoフラグの正本 | **`status.json` 正本**（CSVは派生ビュー） | Proposed |
| D-002 | P0 | サイレント降格禁止（モデル/品質） | **明示モデル選択時はfallback禁止**（止めて報告） | Proposed |
| D-019 | P0 | CLI-onlyの定義（pending含む） | **CLI-only=CLIで“成果物+状態”を確定**（THINK/AGENT pending運用を正規工程に含める） | Done |
| D-018 | P0 | 台本生成の緊急代替（Gemini Batch） | **master+個別プロンプト生成→Gemini Batch→assembled反映**（サイレントfallback禁止） | Proposed |
| D-003 | P0 | Publish→ローカル投稿済みロック | **publisherに“オプションフラグで”同期**（忘れ事故を防ぐ） | Proposed |
| D-013 | P0 | TTS運用ルート（推論=対話型AIエージェント / 読みLLM無効） | **`SKIP_TTS_READING=1` 必須（VOICEVOX: prepass mismatch=0 / VOICEPEAK: prepass→合成→サンプル再生OK）** | Done |
| D-014 | P0 | TTS辞書登録（ユニーク誤読/曖昧語） | **ユニーク誤読のみ辞書へ / 曖昧語は動画ローカルで修正** | Done |
| D-015 | P1 | Slack→Git書庫（PM Inbox） | **Slack→memos→git要約（hash keyで識別 / IDは残さない）** | Proposed |
| D-016 | P1 | 画像生成のコスト/待ち（Batch vs Sync） | **量産=Gemini Batch優先 / 即時=Imagen 4 Fast**（サイレント切替禁止） | Done |
| D-017 | P2 | 台本LLMのBatch化（Fireworks） | **当面は「本文は対話型AIが明示ルートで仕上げる」/ Batch化はPhase2** | Done |
| D-004 | P1 | `script_validation` 品質ゲートround | **既定=3**（必要時のみ明示で増やす） | Proposed |
| D-005 | P1 | 意味整合の自動修正範囲 | **outlineのみbounded / validationは手動適用** | Proposed |
| D-006 | P2 | Video入口の一本化 | **`auto_capcut_run` 主線固定**（capcut engine stub は運用対象外） | Proposed |
| D-020 | P1 | Video素材の共有（編集ソフト非依存） | **Episode Asset PackをGit追跡**（CapCut以外も同じ素材束で制作） | Done |
| D-007 | P2 | AudioのBテキスト例外 | **例外導線（CLI/明示入力）として固定** | Proposed |
| D-008 | P2 | Publish一時DLの置き場/保持 | **`workspaces/tmp/publish/` へ寄せ、成功後削除（保持はオプション）** | Proposed |
| D-009 | P2 | “ゾンビ候補”の扱い | **隔離→入口索引から除外→archive-first削除**（確実ゴミのみ） | Proposed |
| D-010 | P1 | LLM設定SSOTの一本化 | **`llm_router.yaml` 系へ統一**（`llm.yml`/registryは段階廃止） | Done |
| D-011 | P1 | Script Pipelineのno-op stage | **stageは“明示output契約”必須**（`script_enhancement`は削除/実装） | Done |
| D-012 | P2 | channel_info の“同期メタ” | **動的メタは `workspaces/` へ分離**（packagesは静的設定のみ） | Done |

---

## D-001（P0）redoフラグの正本はどこか（CSV vs status.json）

### Decision
- **`workspaces/scripts/{CH}/{NNN}/status.json: metadata.redo_*` を正本**とし、Planning CSV は “派生ビュー” とする。

### Plan（手順）
1) redo正本 = `status.json`  
2) UIの進捗ビューは **CSV行 + status.json + 成果物（assembled, wav/srt 等）を “effective view” としてmerge**（read-only。status.json の欠損/古さを表示で吸収）  
3) CSV側に redo を書き戻さない（表示用exportが必要な場合は、別ファイルで生成）

### Rationale（根拠）
- redo は **制作状態（pipeline state）** であり、企画CSV（Planning facts）と責務が異なる。
- CSVは人間が編集しやすい一方、並列編集/列追加で **衝突・破壊が起きやすい**。
- `status.json` は下流ガード（validation/alignment/redo_audio等）と一体で、事故防止に向く。

### Alternatives（代替案）
- A) CSVを正本にする（不採用）: 実装を大きく変え、書戻し/競合/監査の設計が必要。
- B) 双方向同期（不採用）: “どちらが正か” が崩れやすく、事故ポイントが増える。

### Impact（影響/作業）
- `ops/OPS_CONFIRMED_PIPELINE_FLOW.md` 等のSSOT記述を `status.json` 正本に統一する。

---

## D-002（P0）サイレント降格禁止（モデル/品質）

### Decision
- **明示的にモデル/品質を指定した場合、fallback（別モデル/別tier/別provider）は禁止**。失敗したら **停止して報告**し、代替案を提示する。

### Plan（手順）
- LLM（`factory_common.llm_router`）:
  - **明示選択（env/override/call-time、またはtask設定でmodelsをpin）= strict** とし、`allow_fallback=true` を明示しない限り先頭モデルのみ。
- 画像（`factory_common.image_client`）:
  - **明示 model_key（templates/env/profile/call-time）= strict** とし、`allow_fallback=true` を明示しない限り代替モデルを試さない。
  - tier候補（`configs/image_models.yaml: tiers`）の自動切替は、`tasks.<task>.allow_fallback=true`（または per-call `extra.allow_fallback=true`）を明示した場合のみ。
- 例外的に代替を許す場合は **許可の根拠をSSOTに残す**（taskごとに `allow_fallback=true` を宣言）。

### Rationale（根拠）
- 品質の“勝手な妥協”は、後工程のやり直しで **コストが最も増える**。
- “止めて相談” に寄せると、判断の責任所在（人間）と実行（AI）が分離できる。

### Alternatives（代替案）
- A) fallbackを常時許可（不採用）: 目先の完了を優先し、品質が崩れる。
- B) provider内だけ許可: provider差分が大きい場合、結局品質差が出る（慎重に）。

---

## D-019（P0）CLI-onlyの定義（pending運用を正規工程に含める）

### Decision
- **CLI-only =「成果物の確定 + 状態遷移」を、このリポジトリのCLIだけで完了できる**ことを指す。
  - 人間/対話AIの“思考”は例外扱いにしない（隠さない）。
  - 代わりに、**CLIが発行する `pending task` を契約**として扱い、CLIで `complete` して確定させる。
- 正本入口は `./ops` に統一する（`./ops list` を唯一の入口索引として扱う）。
- `./ops` の passthrough 系コマンドで `--channel` などのフラグを渡す場合は、**必ず `--` 区切りを入れる**。
  - 例: `./ops audio -- --channel CHxx --video NNN`（推論=対話型AIエージェント / 読みLLM無効: `SKIP_TTS_READING=1`）

### Rationale（根拠）
- UI/人間手作業/対話AIが混ざっても、**「確定の瞬間」をCLIに寄せれば再現性と監査が成立**する。
- “曖昧表現”が増える最大原因は、**完了条件と入口が固定されていない**こと（読む人の判断が必要になる）なので、
  入口・完了条件・復帰導線をSSOTで固定して判断を不要にする。

### Impact（影響/作業）
- SoT/Guide は次の表記に統一する:
  - `正本入口:`（コピペで動く `./ops ...` を1つだけ）
  - `互換:`（理由付きで併記する場合のみ）
  - `禁止:`（事故理由付き）
- `./ops ssot check` に “曖昧/非実行形” の検出を組み込み、増殖を止める（再発防止）。
- THINK/AGENT（pending）運用は “正規工程” として `ssot/SSOT_COMPASS.md` / `ssot/ops/OPS_EXECUTION_PATTERNS.md` に固定する。

---

## D-018（P0）台本生成が詰まった時の緊急代替（Gemini Batch）

### Decision
- Fireworks/OpenRouter が使えず **台本生成がブロック**した場合、`script_pipeline` を無理に回さず、**Gemini Developer API Batch** で台本本文を生成する。
  - ただし **サイレントfallbackは禁止**（正本: `D-002`）。切替は必ず「明示コマンド/明示ログ」で追える状態にする。

### Plan（手順）
1) 先に「下準備」＝ **マスタープロンプト + 台本ごとの個別プロンプト** を生成し、Gitに保存してレビュー可能にする  
   - master（固定）: `prompts/antigravity_gemini/MASTER_PROMPT.md`  
   - individual（台本ごと）: `prompts/antigravity_gemini/CHxx/CHxx_NNN_PROMPT.md`  
   - full（Batch投入の実体）: `prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md`  
2) Gemini Batch に投げるのは **台本本文（Aテキスト）だけ**に限定する（外部検索/勝手な補完で破綻しやすいため）  
3) 生成物は `workspaces/scripts/{CH}/{NNN}/content/assembled.md` に反映し、下流（TTS/動画）へ渡せる状態にする

### Rationale（根拠）
- 台本は長文・禁則が多く、半端なfallbackで **静かに品質が崩れる**のが最大事故。
- Batchは安い一方で非同期なので、運用を「下準備→Batch→反映」に固定しないと迷子になる。

### Alternatives（代替案）
- A) Fireworksが復旧するまで待つ: 正攻法だが、停止期間が長いと量産が止まる。
- B) Gemini CLI（非Batch）で手動/半自動: 速い。証跡は `./ops gemini script`（明示実行）で残せるが、大量処理はBatchを優先。

### Impact（影響/作業）
- SSOT: `ssot/ops/OPS_SCRIPT_PIPELINE_SSOT.md` に「Batch運用（台本）」の導線を追加する
- Tool: `scripts/ops/gemini_batch_script_prompts.py`（下準備）と `scripts/ops/gemini_batch_generate_scripts.py`（submit/fetch）を追加する

---

## D-013（P0）TTS運用ルート（推論=対話型AIエージェント / 読みLLM無効）

### Decision
- 音声/TTS の「アノテーション確定」は **推論=対話型AIエージェント** が担当し、読みLLM（auditor）は使わない（`SKIP_TTS_READING=1`）。
- 合否条件（固定）:
  - VOICEVOX: `--prepass` で mismatch=0（`reading_mismatches__*.json` が出ない）
  - VOICEPEAK: `--prepass` → B安全形（ASCII/数字が残らない）→ 合成 → `afplay` で要所確認OK（証跡を残す）
- `YTM_ROUTING_LOCKDOWN=1`（default）では `SKIP_TTS_READING=0` を禁止し、誤って LLM 経路へ流れないようにする。例外は `YTM_EMERGENCY_OVERRIDE=1` を明示した実行のみ。

### Plan（手順）
1) prepass（wav作らない）: `./ops audio -- --channel CHxx --video NNN --prepass`
2) engine を確認（`audio_prep/log.json` の `engine`）→ 分岐（SoT: `ssot/ops/OPS_TTS_ANNOTATION_FLOW.md`）
3) VOICEVOX: mismatch が出たら停止→辞書/overrideでB側を修正→ prepass を繰り返して mismatch=0 → 合成
4) VOICEPEAK: B点検（ASCII/数字ゼロ）→ 合成→ `afplay` で要所確認OK（証跡を残す）

### Rationale（根拠）
- 誤読は後工程（動画/公開）でのやり直しコストが最大になるため、**決定論 + fail-fast** が最も安全。
- LLM 経路はコスト/再現性/逸脱（勝手な切替）のリスクが高く、並列運用の混乱源になりやすい。

### Alternatives（代替案）
- A) `SKIP_TTS_READING=0` で LLM 読み補助を使う（不採用/緊急デバッグのみ）: コスト/再現性/逸脱のリスクが高い。

### Impact（影響/作業）
- SSOT/Runbook/入口コマンドを `SKIP_TTS_READING=1` 前提へ統一し、違反時は停止するガードを実装する。

---

## D-014（P0）TTS辞書登録をどう固定する？（ユニーク誤読 vs 曖昧語）

### Decision
- VOICEVOX / VOICEPEAK の辞書登録は **「正解読みが1つに確定できる（ユニーク）」な誤読のみ**に限定する。  
  読みが文脈で揺れる語（多義語/多読み）は **グローバル辞書に登録しない**（=事故の温床）。

### Plan（手順）
1) 3階層で固定する（どれを触るか迷わない）
   - A) グローバル（全チャンネル共通・確定語）: `packages/audio_tts/data/global_knowledge_base.json`
     - 追加条件: **ユニーク誤読のみ**（どの文脈でも読みが一意。公式ユーザー辞書へ同期してOK）
   - B) チャンネル辞書（そのCHだけ）: `packages/audio_tts/data/reading_dict/CHxx.yaml`
     - 追加条件: **そのチャンネルの運用上 “読みが一意”** であること
   - C) 動画ローカル（その回だけ）: `workspaces/scripts/{CH}/{VID}/audio_prep/`
     - 標準: Bテキスト（TTS入力）をカナ表記にして個別対応する（最も分かりやすい）
     - 文脈で読みが割れる/同一台本内で読みを変えたい: `local_token_overrides.json`（位置指定）で対応する
     - `local_reading_dict.json`（surface→readingの一括置換）は通常運用では使わない（使うのは台本内で一意に固定できる語だけ）
   - 補助（自動学習/前処理）: `packages/audio_tts/configs/learning_dict.json`
     - strict B生成には使うが、公式ユーザー辞書へは **自動同期しない**（量/事故リスクのため）
2) 「曖昧語」は辞書に入れない（例）
   - 例: 「人」「辛い」「行った」「怒り」など（文脈で読みが変わり得る/誤登録の影響が大きい）
3) VOICEPEAK/VOICEVOX の“公式辞書（ユーザー辞書）”は、上記SoTから **同期**して使う（運用の利便性のため）
   - VOICEPEAK: `python3 -m audio_tts.scripts.sync_voicepeak_user_dict`（`run_tts` 開始時にも追記同期を試行する。`YTM_ROUTING_LOCKDOWN=1` では失敗したら停止）
   - VOICEVOX（運用固定: グローバルのみ）: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --global-only --overwrite`
   - VOICEVOX（必要時: CH語も同期）: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --channel CHxx --overwrite`

### Rationale（根拠）
- 辞書（特にグローバル）は影響範囲が大きく、曖昧語の登録は **静かに全動画へ事故を拡散**する。
- 「辞書で直す」か「動画ローカルで直す」かを先に固定すると、TTSの停止→修正→再実行が迷わない。

### Alternatives（代替案）
- A) なんでも辞書で直す（不採用）: 一時的には楽だが、後で必ず誤読回収が発生する。

---

## D-015（P1）Slackの指示/決定をどう“取りこぼさず”書庫化する？（Git as archive）

### Decision
- Slackは一次受け（通知/会話）として使い、**Git（SSOT）には “要約Inbox” だけを残す**。  
  生ログ/識別子（channel_id/user_id/thread_ts 等）は git に固定しない。

### Plan（手順）
1) Slack返信（dd意思決定）は `scripts/ops/slack_notify.py` で取り込み（memos化）
2) 取り込んだ内容は PM Inbox として **要約だけ** git に残す
   - 出力: `ssot/history/HISTORY_slack_pm_inbox.md`
   - 同期: `python3 scripts/ops/slack_inbox_sync.py sync`
3) Inboxの各項目は **hash key** で識別し、Slack IDは保存しない（公開repoでも安全にする）
4) 実データ（対応表/JSON）は `workspaces/logs/ops/` に保存（git管理しない）

### Rationale（根拠）
- Slackはメッセージ量が増えると埋もれやすく、指示の取りこぼしが事故要因になる。
- その一方で、生ログをgitに入れるのは機微/ノイズ/容量の面でコストが大きい。
- “要約Inbox + SSOT反映” に寄せると、PMが追える形でプロダクトを前に進められる。

### Alternatives（代替案）
- A) Slackだけで運用（不採用）: 取りこぼし/認識ズレが発生しやすい
- B) Slack生ログをgitに保存（不採用）: 機微/容量/公開時リスクが大きい
- C) 外部のチケット管理（Jira等）へ移管: 既存導線と二重化しやすい（導入するなら別Decisionで）

### Impact（影響/作業）
- SSOT: `ssot/plans/PLAN_OPS_SLACK_GIT_ARCHIVE.md` を運用正本にする
- Tool: `scripts/ops/slack_inbox_sync.py` を追加（要約Inbox生成）

### Impact（影響/作業）
- `ssot/ops/OPS_AUDIO_TTS.md` / `ssot/ops/OPS_TTS_ANNOTATION_FLOW.md` に辞書運用（A/B/C）を明記して固定する。

---

## D-016（P1）画像生成の「安さ」と「待ち」をどう両立する？（Batch vs Sync）

### Decision
- **量産（コスト最優先）と即時（比較/リテイク）を分ける**。Batch APIは安いが非同期のため、**用途を固定**して迷いを無くす。

### Plan（手順）
- 量産（大量/夜間）: **Gemini Batch** を優先（コスト最優先）
  - 参考単価（1024×1024相当）: `$0.0195/枚`（例: `gemini-2.5-flash-image` Batch / `gemini-2.0-flash` Batch）
  - 注意: Batchは非同期（完了まで最大24hターゲット）。パイプラインは「投げて終わり」ではなく、**submit→poll→fetch（止まってもresume可能）**の導線を持つ。
  - 実装（動画内画像）: `--nanobanana batch`（既定）で Gemini Batch を使う（run_dir に manifest を残し、再実行で回収できる）。
- 即時（数枚の比較/急ぎ）: **Imagen 4 Fast**（Gemini API）を使う
  - 参考単価（1024×1024相当）: `$0.02/枚`（`imagen-4.0-fast-generate-001`）
  - slot code（例）: `i-1`（Imagen 4 Fast）
- 固定ルール:
  - **サイレント切替は禁止**（正本: `D-002`）。Batch⇄Syncの切替は slot code / model_key で必ず明示する。
  - Batch は Gemini provider のみ対応（Imagen / OpenRouter / Fireworks は対象外）。
    - 対象外の model_key で `batch` を選んだ場合は **停止（hard-stop）** して、運用者が `direct` を明示して再実行する（勝手にフォールバックしない）。
  - 目標（動画内画像）: Batch運用が実装できたら、必要なチャンネルから `image_generation.model_key` を **Gemini系（g-1）へ寄せる**（FLUX は必要時の明示選択へ。削除はしない）。
  - 注（サムネ）: サムネ背景生成は **Gemini 2.5 Flash Image 固定**。比較/リテイク目的の別モデル切替はしない（別SSOT: `ssot/ops/OPS_THUMBNAILS_PIPELINE.md`）。

### Rationale（根拠）
- 量産は「安さ」が最重要だが、即時作業は「待ち」がボトルネックになるため、両方を同じ既定にすると運用が破綻しやすい。
- “明示切替”に寄せると、コスト/納期/品質のトレードオフが追える。

### Alternatives（代替案）
- A) 常に即時（Sync）（不採用）: 速いがコストが積み上がる
- B) 常にBatch（不採用）: 安いがリテイク/比較が遅くなり、開発速度が落ちる

### Impact（影響/作業）
- `video_pipeline`（`nanobanana=batch`）が Gemini Batch 運用（submit/poll/fetch/resume）に対応する（run_dir に manifest を保存）
- SSOT: `ssot/ops/OPS_CHANNEL_MODEL_ROUTING.md` / `ssot/ops/OPS_THUMBNAILS_PIPELINE.md` に運用導線を追記する
- Plan: `ssot/plans/PLAN_IMAGE_BATCH_MIGRATION.md`（段階導入 / DoD / ロールバック / 観測）

---

## D-017（P2）台本LLM（Fireworks）のBatch化はやるべき？（コスト vs 複雑さ）

### Decision
- **当面は「台本本文は対話型AIエージェントが仕上げる」運用を主線**にし、台本パイプライン内部のBatch化（Fireworks Batch等）は **Phase2で検討**する。

### Plan（手順）
- いま（Phase1）:
  - デフォルト: THINK（pending）で回す（勝手に外部LLM APIを叩かない）。
  - 台本本文（Aテキスト）は、対話型AIエージェントが次のいずれか **明示ルート**で作る:
    - Gemini CLI（明示）: `./ops gemini script -- --channel CHxx --video NNN --run`
    - `qwen -p`（運用で選択）
    - LLM API（必要時のみ明示）: `./ops api script <MODE> -- --channel CHxx --video NNN`
  - 禁止: API失敗→THINK の自動フォールバック（止めて報告）。
- Phase2（やるなら）:
  - **stage単位のバッチ**（例: 1ステージを複数動画でまとめて submit → 完了待ち → 回収して次ステージへ）として設計する。
  - 必須要件:
    - 出力契約（どのファイルに何を書くか）が明確で、`status.json` で **resume** できる
    - 勝手なモデル切替/フォールバックを許さない（明示運用）
    - 失敗時は “止めて報告”（silent fallback禁止）

### Rationale（根拠）
- Batchはコストに効く一方、台本は「段階的な生成/審査/修正」が多く、非同期化すると **オーケストレーションが一気に難しくなる**。
- まず画像Batch（非同期の運用に慣れる）→その後に台本Batch、の順が事故りにくい。
- なお Fireworks の Batch Inference は serverless より安いことが多い（概算: 約50%）。ただし本件はコストよりも “正確に完走する” を優先して Phase2 に送る。

### Alternatives（代替案）
- A) すべてBatchに寄せる（不採用）: コストは下がるが、日中の反復速度が落ちやすい
- B) 一切Batchにしない: 実装は簡単だが、長期コストが積み上がる（用途次第）

### Impact（影響/作業）
- Phase2開始時に、専用の submit/poll/resume CLI と `workspaces/` のjob_id管理（SoT）を追加する

---

## D-010（P1）LLM設定のSSOTを `llm_router.yaml` 系へ一本化する？

### Decision
- LLMの「タスク→モデル/プロバイダ」設定を **`configs/llm_router.yaml` + `configs/llm_task_overrides.yaml`（+ codes/slots）** に統一する。  
  旧 registry（`llm_registry.json`, `llm_model_registry.yaml`）は **archive-first→削除済み（2026-01-08）** のため復活禁止。`llm.yml` + `factory_common.llm_client` は legacy（互換）扱い。

### Plan（手順）
1) SSOT（正本）: `llm_router.yaml`（tiers/models/tasks） + `llm_task_overrides.yaml`（taskごとの上書き）  
2) UI/集計のために残っている registry 参照は **router/slot由来へ置換**する（同じ情報を二重管理しない）  
   - UI backend: 置換済み（2026-01-08）。以後 `llm_model`（provider:model 直指定）は禁止し、数字スロット（`LLM_MODEL_SLOT`）で運用する。  
3) 旧系（`llm.yml` + `factory_common.llm_client`）は “legacy隔離” を経て削除対象へ（削除までは SSOT と明示して迷いを止める）

### Rationale（根拠）
- 現状は「複数の設定SSOT」が併存し、運用者/エージェントが必ず迷う（=誤モデル/誤コスト）。
- 実装主線（script/audio/video）は既に `llm_router` を使っており、`llm_client` 側は参照が薄い（監査/テスト以外）。
- SSOT=UI を成立させるには、モデル決定ロジックを **1枚**に寄せる必要がある。

### Alternatives（代替案）
- A) `llm.yml` を正本に戻す（不採用）: router/overrides/Fireworks lease 等の現行設計と逆行し、移行コストが大きい。
- B) “併存” を認める（不採用）: ドキュメント/実装/可視化コストが永続し、ゾンビ増殖が止まらない。

### Impact（影響/作業）
- SSOT側: `ops/OPS_LLM_MODEL_CHEATSHEET.md` 等の「正本: llm.yml」記述を `llm_router.yaml` に寄せて統一する。
- 実装側: UI backend / 集計が `llm_registry.json` を参照している箇所を router由来に置換する（段階導入）。  
  - UI backend: 置換済み（2026-01-08）

---

## D-011（P1）Script Pipeline の stage は “no-op禁止” にする？（`script_enhancement` の扱い）

### Decision
- stage は「**明示的なoutput契約（SoT）を持つ**」か「**明示的に廃止/skip**」のどちらかにする。no-op stage（存在するが何もしない）は禁止する。

### Plan（手順）
1) `script_enhancement` は **stages.yaml から外す**（現状は outputs=[] のため実行されず、完了扱いになる）  
2) “章の改善パス” が必要になった場合は、後日あらためて **output契約を定義して実装**する（例: `chapter_enhancement` が `content/chapters/chapter_N.md` を上書き or `chapters_enhanced/` を生成）

### Rationale（根拠）
- no-op stage は「完了したように見える」ため、運用ミスとコスト事故を誘発する。
- SSOT=UI を成立させるには「ステップ=実処理」が一致している必要がある。

### Alternatives（代替案）
- A) `script_enhancement` を残し、SKIP_STAGES に入れて “deprecated” 表示にする（暫定）。  
- B) stage を残しつつ output_override で既存ファイルを書き換える（事故リスクが高いので、契約を先に固める必要がある）。

### Impact（影響/作業）
- `packages/script_pipeline/stages.yaml` の整理（削除 or output契約追加）。
- `ssot/ops/OPS_ZOMBIE_CODE_REGISTER.md` へ記録し、確定後に archive-first で掃除計画へ落とす。
- 実装（2026-01-04）: `script_enhancement` を `stages.yaml` 主線から除外（no-op解消）。

---

## D-012（P2）`packages/script_pipeline/channels/**/channel_info.json` の “同期メタ” をどこに置く？

### Decision
- `channel_info.json` に含まれる **動的に変わる同期メタ（view_count/subscriber_count/video_count/synced_at 等）** を、`packages/` から `workspaces/` へ分離する。

### Plan（手順）
1) `packages/script_pipeline/channels/**/channel_info.json` は **静的設定のみ**（prompt template / channel code / handle / 方針など）  
2) 動的なチャンネル統計/同期時刻は **`workspaces/channels/<CH>/channel_stats.json`**（SoT=workspaces）に保存  
3) UI/SSOT は “静的設定（packages）” と “統計（workspaces）” を分けて表示（混ぜない）

### Rationale（根拠）
- `packages/` はコードと同じく “安定した履歴” を持たせたいが、統計/同期時刻は **更新頻度が高く差分ノイズ**になる。
- 並列運用時に「誰がいつ sync したか」で tracked 差分が増えると、**本当に重要な変更（テンプレ/ルール）のレビューが埋もれる**。

### Alternatives（代替案）
- A) 現状維持（不採用）: 変更ノイズが増え続け、ゾンビ差分の温床になる。
- B) `channel_info.json` を workspaces へ移す: 依存解決/参照変更が大きくなる（段階移行なら可）。

### Impact（影響/作業）
- sync（YouTube metadata fetch）の出力先を `workspaces/` に変更し、`packages/` 側の動的フィールド更新を止める。
- 既存の `channel_info.json` 内の統計フィールドは **legacy（残っていても更新しない）** として扱い、UI は `workspaces/channels/<CH>/channel_stats.json` を優先する。

---

## D-003（P0）Publish（外部SoT）→ローカル投稿済みロックを同期する？

### Decision
- 外部SoT（Sheet）が `uploaded` になったとき、ローカル側も “投稿済みロック” を **同期できる**ようにする。

### Plan（手順）
- publisherに `--also-lock-local` のような **オプションフラグ**を追加し、以下を同期:
  - `status.json: published_lock=true`（以後の破壊的操作をガード）
  - Planning CSV: `進捗=投稿済み`（人間の一覧性のため。ただし“正本は外部”）

### Rationale（根拠）
- 「Sheetは更新されたがローカルが未ロック」事故が最も起きやすい（忘れ/並列作業）。
- オプションフラグなら、初期は手動運用も残しつつ段階導入できる。

### Alternatives（代替案）
- A) UIで手動ロック固定（不採用）: “忘れ” が残る。
- B) 常時自動同期: 安全だが、誤ったSheet更新時にローカルも巻き込む（導入は慎重に）。

---

## D-004（P1）`script_validation` 品質ゲート round 上限

### Decision
- 既定は **最大3** に揃える（必要時のみ明示で増やす）。

### Plan（手順）
- 既定=3（SSOT側で固定）  
- 例外は “明示スイッチ（env/flag）” で 5 にできる（緊急時/長尺のみ）

### Rationale（根拠）
- round増はコスト/時間に直結するため、既定は抑えるべき。
- “必要な回だけ上げる” は意思決定の可視化（監査）に向く。

---

## D-005（P1）意味整合の自動修正（auto-fix）範囲

### Decision
- auto-fixは **outlineのみ bounded**。`script_validation` は **手動適用** に固定する。

### Plan（手順）
- outline段階: 章立ての崩れを軽く直す（bounded）  
- validation段階: Aテキストは下流（TTS/Video）へ直結するため、勝手な書換えを避ける

### Rationale（根拠）
- 早期修正は被害が小さいが、最終稿の自動書換えは事故影響が大きい。

---

## D-006（P2）Video 入口一本化（CapCut）

### Decision
- “主線” は `auto_capcut_run` + `capcut_bulk_insert` に固定する。

### Plan（手順）
- `run_pipeline --engine capcut` は **stub（運用対象外）** として明記し、誤用導線を消す。

---

## D-020（P1）Video素材の共有（編集ソフト非依存）

### Decision
- CapCut以外（Vrew等）でも制作できるよう、**編集ソフト非依存の「Episode Asset Pack」をGit追跡**する。
  - 正本: `workspaces/video/assets/episodes/{CH}/{NNN}/`（images/audio/subtitles/manifest）
  - `workspaces/video/runs/**`（run_dir）は実行時作業場として **gitignoreのまま**（巨大化/差分ノイズのため）。

### Plan（手順）
1) run_dir から必要な素材（`images/0001.png...`, `image_cues.json`）を Asset Pack へ export  
2) 音声/字幕（`workspaces/audio/final`）も Asset Pack へ export（共有/ダウンロードのため）  
3) CapCutルートは従来どおり run_dir で進める  
4) CapCut以外ルートは Asset Pack をWebから取得し、そのまま編集ソフトへ投入  

### Rationale（根拠）
- “CapCutで作る前提”だと、別編集ソフトや外部作業者の導線が詰まる（素材の取得ができない）。
- Git追跡に寄せれば、**pull/URL参照だけで同じ素材を共有**でき、意思疎通コストを下げられる。

### References
- `ops/OPS_VIDEO_ASSET_PACK.md`
- `ops/DATA_LAYOUT.md`

---

## D-007（P2）Audio “Bテキスト” 例外運用

### Decision
- Bテキストは **例外導線（明示入力）** として残す（デフォルトはAテキストSoT強制）。

### Plan（手順）
- 例外は CLI/明示入力のみ（暗黙fallback禁止）  
- split-brain/alignment stamp/stale guard を崩さない

---

## D-008（P2）Publishの一時DL保持

### Decision
- 一時DLは repo直下ではなく `workspaces/tmp/publish/` に寄せ、成功後削除を基本にする。

### Plan（手順）
- 成功後削除（既定）  
- 監査/再送が必要な場合のみ保持（保持期間/容量上限をSSOT化）

---

## D-009（P2）ゾンビコードの整理方針

### Decision
- “確実ゴミ” 以外は、まず **隔離（入口索引から外す）→監査→archive-first削除**。

### Plan（手順）
- `ops/OPS_ZOMBIE_CODE_REGISTER.md` に根拠付きで列挙
- 削除時は `plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` に従う
