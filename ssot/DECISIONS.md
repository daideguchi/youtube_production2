# DECISIONS — 意思決定（SSOTトップ）

このファイルは「**SSOT=UI（read-only）** を成立させるために、運用/実装を **どちらに寄せるか決める必要がある点**」を、**選択肢 + 推奨案 + 根拠** の形で並べた“意思決定台帳”です。

- 方針: **決まったら SSOT → 実装 の順で固定**し、`ops/OPS_GAPS_REGISTER.md` / 関連SSOTを更新していきます。
- 目的: 人間/AIの認識ズレ（=事故とコスト）をゼロにする。

---

## 0) 決め方（最短）

各 Decision は **推奨（Recommended）** を提示しています。  
もし違う運用にしたい場合は、代替案（Alternatives）から選び、理由があれば追記してください。

---

## 1) 一覧（P0→P2）

| ID | 重要度 | テーマ | Recommended | 状態 |
| --- | --- | --- | --- | --- |
| D-001 | P0 | redoフラグの正本 | **`status.json` 正本**（CSVは派生ビュー） | Proposed |
| D-002 | P0 | サイレント降格禁止（モデル/品質） | **明示モデル選択時はfallback禁止**（止めて報告） | Proposed |
| D-003 | P0 | Publish→ローカル投稿済みロック | **publisherに“任意フラグで”同期**（忘れ事故を防ぐ） | Proposed |
| D-013 | P0 | TTSのCodex（agent vs codex exec） | **TTSはAIエージェントCodex（pending）固定 / codex execと区別** | Done |
| D-014 | P0 | TTS辞書登録（ユニーク誤読/曖昧語） | **ユニーク誤読のみ辞書へ / 曖昧語は動画ローカルで修正** | Done |
| D-015 | P1 | Slack→Git書庫（PM Inbox） | **Slack→memos→git要約（hash keyで識別 / IDは残さない）** | Proposed |
| D-004 | P1 | `script_validation` 品質ゲートround | **既定=3**（必要時のみ明示で増やす） | Proposed |
| D-005 | P1 | 意味整合の自動修正範囲 | **outlineのみbounded / validationは手動適用** | Proposed |
| D-006 | P2 | Video入口の一本化 | **`auto_capcut_run` 主線固定**（capcut engine stubは非推奨） | Proposed |
| D-007 | P2 | AudioのBテキスト例外 | **例外導線（CLI/明示入力）として固定** | Proposed |
| D-008 | P2 | Publish一時DLの置き場/保持 | **`workspaces/tmp/publish/` へ寄せ、成功後削除（任意保持）** | Proposed |
| D-009 | P2 | “ゾンビ候補”の扱い | **隔離→入口索引から除外→archive-first削除**（確実ゴミのみ） | Proposed |
| D-010 | P1 | LLM設定SSOTの一本化 | **`llm_router.yaml` 系へ統一**（`llm.yml`/registryは段階廃止） | Done |
| D-011 | P1 | Script Pipelineのno-op stage | **stageは“明示output契約”必須**（`script_enhancement`は削除/実装） | Done |
| D-012 | P2 | channel_info の“同期メタ” | **動的メタは `workspaces/` へ分離**（packagesは静的設定のみ） | Done |

---

## D-001（P0）redoフラグの正本はどこか（CSV vs status.json）

### Decision
- **`workspaces/scripts/{CH}/{NNN}/status.json: metadata.redo_*` を正本**とし、Planning CSV は “派生ビュー” とする。

### Recommended（推奨）
1) redo正本 = `status.json`  
2) UIの進捗ビューは **CSV行 + status.json + 成果物（assembled, wav/srt 等）を “effective view” としてmerge**（read-only。status.json の欠損/古さを表示で吸収）  
3) CSV側に redo を書き戻さない（必要なら “表示用export” を別ファイルで生成）

### Rationale（根拠）
- redo は **制作状態（pipeline state）** であり、企画CSV（Planning facts）と責務が異なる。
- CSVは人間が編集しやすい一方、並列編集/列追加で **衝突・破壊が起きやすい**。
- `status.json` は下流ガード（validation/alignment/redo_audio等）と一体で、事故防止に向く。

### Alternatives（代替案）
- A) CSVを正本にする（非推奨）: 実装を大きく変え、書戻し/競合/監査の設計が必要。
- B) 双方向同期: “どちらが正か” が崩れやすく、事故ポイントが増える（非推奨）。

### Impact（影響/作業）
- `ops/OPS_CONFIRMED_PIPELINE_FLOW.md` 等のSSOT記述を `status.json` 正本に統一する。

---

## D-002（P0）サイレント降格禁止（モデル/品質）

### Decision
- **明示的にモデル/品質を指定した場合、fallback（別モデル/別tier/別provider）は禁止**。失敗したら **停止して報告**し、代替案を提示する。

### Recommended（推奨）
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
- A) fallbackを常時許可（非推奨）: 目先の完了を優先し、品質が崩れる。
- B) provider内だけ許可: provider差分が大きい場合、結局品質差が出る（慎重に）。

---

## D-013（P0）TTSの「Codex」をどう固定する？（AIエージェント vs codex exec）

### Decision
- **TTS（`tts_*` / `voicevox_kana`）は AIエージェント（Codex）主担当（pending運用）に固定**し、**codex exec（非対話CLI）とは明確に区別**する。

### Recommended（推奨）
1) TTSは THINK MODE を入口にする（pendingを作って止める）
   - 例: `./scripts/think.sh --tts -- python -m script_pipeline.cli audio --channel CHxx --video NNN`
2) pending は Codex（AIエージェント）が runbook に沿って output を作って `complete` → rerun
3) **用語固定**:
   - 「Codex（AIエージェント）」= pending の output を作る担当
   - 「codex exec」= `codex exec` コマンドによる自動実行レイヤ（別物。TTSには寄せない）

### Rationale（根拠）
- TTSの `voicevox_kana` は “読み/誤読/根拠” の判断が重要で、雑な自動化は事故になりやすい。
- 「Codex」という言葉が “AIエージェント” と “codex exec（非対話CLI）” の両方を指し得て混乱源になるため、**先に言葉を固定**するほうが事故を減らせる。
- 台本（`script_*`）は API固定なので、TTSも “勝手に別経路へ流れない” 形（pending運用）に寄せると整合が取れる。

### Alternatives（代替案）
- A) codex exec をTTSに使う（非推奨）: 自動化の成功/失敗が運用理解に依存しやすく、混乱しやすい。
- B) LLM APIでTTS補助を完結する（非推奨）: コスト/品質/再現性の面で “止めて直す” 運用と相性が悪い。

### Impact（影響/作業）
- SSOT/Guide/UIの文言を「Codex agent」と「codex exec」で分離し、誤解が起きない導線に更新する。

---

## D-014（P0）TTS辞書登録をどう固定する？（ユニーク誤読 vs 曖昧語）

### Decision
- VOICEVOX / VOICEPEAK の辞書登録は **「正解読みが1つに確定できる（ユニーク）」な誤読のみ**に限定する。  
  読みが文脈で揺れる語（多義語/多読み）は **グローバル辞書に登録しない**（=事故の温床）。

### Recommended（推奨）
1) 3階層で固定する（どれを触るか迷わない）
   - A) グローバル（全チャンネル共通）: `packages/audio_tts/configs/learning_dict.json`
     - 追加条件: **ユニーク誤読のみ**（例: 「午前」など、読みが一意）
   - B) チャンネル辞書（そのCHだけ）: `packages/audio_tts/data/reading_dict/CHxx.yaml`
     - 追加条件: **そのチャンネルの運用上 “読みが一意”** であること
   - C) 動画ローカル（その回だけ）: `workspaces/scripts/{CH}/{VID}/audio_prep/`
     - **原則**: Bテキスト（TTS入力）をカナ表記にして個別対応する（最も分かりやすい）
     - 文脈で読みが割れる/同一台本内で読みを変えたい: `local_token_overrides.json`（位置指定）で対応する
     - `local_reading_dict.json`（surface→readingの一括置換）は **原則使わない**（台本内で一意に固定できる語だけに限定）
2) 「曖昧語」は辞書に入れない（例）
   - 例: 「人」「辛い」「行った」「怒り」など（文脈で読みが変わり得る/誤登録の影響が大きい）
3) VOICEPEAK/VOICEVOX の“公式辞書（ユーザー辞書）”は、上記SoTから **同期**して使う（運用の利便性のため）
   - VOICEPEAK: `python3 -m audio_tts.scripts.sync_voicepeak_user_dict`（`run_tts` 開始時にもbest-effortで追記同期される）
   - VOICEVOX: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --channel CHxx`（必要時に手動実行）

### Rationale（根拠）
- 辞書（特にグローバル）は影響範囲が大きく、曖昧語の登録は **静かに全動画へ事故を拡散**する。
- 「辞書で直す」か「動画ローカルで直す」かを先に固定すると、TTSの停止→修正→再実行が迷わない。

### Alternatives（代替案）
- A) なんでも辞書で直す（非推奨）: 一時的には楽だが、後で必ず誤読回収が発生する。

---

## D-015（P1）Slackの指示/決定をどう“取りこぼさず”書庫化する？（Git as archive）

### Decision
- Slackは一次受け（通知/会話）として使い、**Git（SSOT）には “要約Inbox” だけを残す**。  
  生ログ/識別子（channel_id/user_id/thread_ts 等）は git に固定しない。

### Recommended（推奨）
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
- A) Slackだけで運用（非推奨）: 取りこぼし/認識ズレが発生しやすい
- B) Slack生ログをgitに保存（非推奨）: 機微/容量/公開時リスクが大きい
- C) 外部のチケット管理（Jira等）へ移管: 既存導線と二重化しやすい（導入するなら別Decisionで）

### Impact（影響/作業）
- SSOT: `ssot/plans/PLAN_OPS_SLACK_GIT_ARCHIVE.md` を運用正本にする
- Tool: `scripts/ops/slack_inbox_sync.py` を追加（要約Inbox生成）

### Impact（影響/作業）
- `ssot/ops/OPS_AUDIO_TTS.md` / `ssot/ops/OPS_TTS_MANUAL_READING_AUDIT.md` に辞書運用（A/B/C）を明記して固定する。

---

## D-010（P1）LLM設定のSSOTを `llm_router.yaml` 系へ一本化する？

### Decision
- LLMの「タスク→モデル/プロバイダ」設定を **`configs/llm_router.yaml` + `configs/llm_task_overrides.yaml`（+ codes/slots）** に統一する。  
  旧 registry（`llm_registry.json`, `llm_model_registry.yaml`）は **archive-first→削除済み（2026-01-08）** のため復活禁止。`llm.yml` + `factory_common.llm_client` は legacy（互換）扱い。

### Recommended（推奨）
1) SSOT（正本）: `llm_router.yaml`（tiers/models/tasks） + `llm_task_overrides.yaml`（taskごとの上書き）  
2) UI/集計のために残っている registry 参照は **router/slot由来へ置換**する（同じ情報を二重管理しない）  
   - UI backend: 置換済み（2026-01-08）。以後 `llm_model`（provider:model 直指定）は禁止し、数字スロット（`LLM_MODEL_SLOT`）で運用する。  
3) 旧系（`llm.yml` + `factory_common.llm_client`）は “legacy隔離” を経て削除対象へ（削除までは SSOT と明示して迷いを止める）

### Rationale（根拠）
- 現状は「複数の設定SSOT」が併存し、運用者/エージェントが必ず迷う（=誤モデル/誤コスト）。
- 実装主線（script/audio/video）は既に `llm_router` を使っており、`llm_client` 側は参照が薄い（監査/テスト以外）。
- SSOT=UI を成立させるには、モデル決定ロジックを **1枚**に寄せる必要がある。

### Alternatives（代替案）
- A) `llm.yml` を正本に戻す: router/overrides/Fireworks lease 等の現行設計と逆行し、移行コストが大きい（非推奨）。
- B) “併存” を認める: ドキュメント/実装/可視化コストが永続し、ゾンビ増殖が止まらない（非推奨）。

### Impact（影響/作業）
- SSOT側: `ops/OPS_LLM_MODEL_CHEATSHEET.md` 等の「正本: llm.yml」記述を `llm_router.yaml` に寄せて統一する。
- 実装側: UI backend / 集計が `llm_registry.json` を参照している箇所を router由来に置換する（段階導入）。  
  - UI backend: 置換済み（2026-01-08）

---

## D-011（P1）Script Pipeline の stage は “no-op禁止” にする？（`script_enhancement` の扱い）

### Decision
- stage は「**明示的なoutput契約（SoT）を持つ**」か「**明示的に廃止/skip**」のどちらかにする。no-op stage（存在するが何もしない）は禁止する。

### Recommended（推奨）
1) `script_enhancement` は **stages.yaml から外す**（現状は outputs=[] のため実行されず、完了扱いになる）  
2) “章の改善パス” が必要なら、後日あらためて **output契約を定義して実装**する（例: `chapter_enhancement` が `content/chapters/chapter_N.md` を上書き or `chapters_enhanced/` を生成）

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

### Recommended（推奨）
1) `packages/script_pipeline/channels/**/channel_info.json` は **静的設定のみ**（prompt template / channel code / handle / 方針など）  
2) 動的なチャンネル統計/同期時刻は **`workspaces/channels/<CH>/channel_stats.json`**（SoT=workspaces）に保存  
3) UI/SSOT は “静的設定（packages）” と “統計（workspaces）” を分けて表示（混ぜない）

### Rationale（根拠）
- `packages/` はコードと同じく “安定した履歴” を持たせたいが、統計/同期時刻は **更新頻度が高く差分ノイズ**になる。
- 並列運用時に「誰がいつ sync したか」で tracked 差分が増えると、**本当に重要な変更（テンプレ/ルール）のレビューが埋もれる**。

### Alternatives（代替案）
- A) 現状維持（非推奨）: 変更ノイズが増え続け、ゾンビ差分の温床になる。
- B) `channel_info.json` を workspaces へ移す: 依存解決/参照変更が大きくなる（段階移行なら可）。

### Impact（影響/作業）
- sync（YouTube metadata fetch）の出力先を `workspaces/` に変更し、`packages/` 側の動的フィールド更新を止める。
- 既存の `channel_info.json` 内の統計フィールドは **legacy（残っていても更新しない）** として扱い、UI は `workspaces/channels/<CH>/channel_stats.json` を優先する。

---

## D-003（P0）Publish（外部SoT）→ローカル投稿済みロックを同期する？

### Decision
- 外部SoT（Sheet）が `uploaded` になったとき、ローカル側も “投稿済みロック” を **同期できる**ようにする。

### Recommended（推奨）
- publisherに `--also-lock-local` のような **任意フラグ**を追加し、以下を同期:
  - `status.json: published_lock=true`（以後の破壊的操作をガード）
  - Planning CSV: `進捗=投稿済み`（人間の一覧性のため。ただし“正本は外部”）

### Rationale（根拠）
- 「Sheetは更新されたがローカルが未ロック」事故が最も起きやすい（忘れ/並列作業）。
- 任意フラグなら、初期は手動運用も残しつつ段階導入できる。

### Alternatives（代替案）
- A) UIで手動ロック固定: “忘れ” が残る（非推奨）。
- B) 常時自動同期: 安全だが、誤ったSheet更新時にローカルも巻き込む（導入は慎重に）。

---

## D-004（P1）`script_validation` 品質ゲート round 上限

### Decision
- 既定は **最大3** に揃える（必要時のみ明示で増やす）。

### Recommended（推奨）
- 既定=3（SSOT側で固定）  
- 例外は “明示スイッチ（env/flag）” で 5 にできる（緊急時/長尺のみ）

### Rationale（根拠）
- round増はコスト/時間に直結するため、既定は抑えるべき。
- “必要な回だけ上げる” は意思決定の可視化（監査）に向く。

---

## D-005（P1）意味整合の自動修正（auto-fix）範囲

### Decision
- auto-fixは **outlineのみ bounded**。`script_validation` は **手動適用** に固定する。

### Recommended（推奨）
- outline段階: 章立ての崩れを軽く直す（bounded）  
- validation段階: Aテキストは下流（TTS/Video）へ直結するため、勝手な書換えを避ける

### Rationale（根拠）
- 早期修正は被害が小さいが、最終稿の自動書換えは事故影響が大きい。

---

## D-006（P2）Video 入口一本化（CapCut）

### Decision
- “主線” は `auto_capcut_run` + `capcut_bulk_insert` に固定する。

### Recommended（推奨）
- `run_pipeline --engine capcut` は **stub/非推奨** として明記し、誤用導線を消す。

---

## D-007（P2）Audio “Bテキスト” 例外運用

### Decision
- Bテキストは **例外導線（明示入力）** として残す（デフォルトはAテキストSoT強制）。

### Recommended（推奨）
- 例外は CLI/明示入力のみ（暗黙fallback禁止）  
- split-brain/alignment stamp/stale guard を崩さない

---

## D-008（P2）Publishの一時DL保持

### Decision
- 一時DLは repo直下ではなく `workspaces/tmp/publish/` に寄せ、成功後削除を基本にする。

### Recommended（推奨）
- 成功後削除（既定）  
- 監査/再送が必要な場合のみ保持（保持期間/容量上限をSSOT化）

---

## D-009（P2）ゾンビコードの整理方針

### Decision
- “確実ゴミ” 以外は、まず **隔離（入口索引から外す）→監査→archive-first削除**。

### Recommended（推奨）
- `ops/OPS_ZOMBIE_CODE_REGISTER.md` に根拠付きで列挙
- 削除時は `plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` に従う
