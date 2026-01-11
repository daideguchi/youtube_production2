# Open Questions（意思決定が必要な不明点）

このファイルは「実装とSSOTの両方を読んだ上で、**どちらに寄せるか決めないと固定ロジックにならない点**」を列挙する。
（回答が決まったら `ssot/ops/OPS_GAPS_REGISTER.md` の暫定→確定へ反映し、必要なら SSOT → 実装 の順で修正方針へ落とす）

---

## P0（先に決めないと事故る）

### Q1) Publish（投稿済み）を “外部SoT→ローカルロック” に自動反映する？

現状:
- `publish_from_sheet.py` は Sheet の `Status=uploaded` 等は更新するが、ローカル側（Planning CSV `進捗=投稿済み` / status.json `published_lock`）は自動では更新しない。
- UIには明示ロック操作がある（安全だが、人が忘れると後工程で誤編集し得る）。

決めたいこと:
- publisher が（任意フラグで）ローカルロックも更新するべき？
  - 例: `--also-lock-local`（CSV+status.json更新）など
- それとも “人間がUIでロックする” を固定運用にする？

関連: `ssot/ops/OPS_GAPS_REGISTER.md#GAP-003`

---

## P1（運用コスト/品質に効く）

### Q2) `script_validation` LLM品質ゲートの round 上限（3 vs 5）をどうする？

現状:
- SSOTは「最大3回」を推奨。
- 実装は draft provenance が `codex_exec` の場合、default/hard cap が 5。

決めたいこと:
- SSOTに「codex_exec例外」を正式化する？
- それとも実装を 3 に揃える？
- あるいは “既定はSSOT通り3、必要な回だけ env で 5” にする？

関連: `ssot/ops/OPS_GAPS_REGISTER.md#GAP-002`

### Q3) 意味整合の auto-fix をどこまで許容する？

現状:
- `script_outline` には bounded auto-fix（env制御）がある。
- `script_validation` は hard-coded で auto-fix 無効（CLI `semantic-align --apply` に誘導）。

決めたいこと:
- この二段構えを固定ルールとしてSSOTに明記する？
- それとも “outlineでも自動修正しない” に寄せる？

---

## P2（UI/入口の統一・迷子防止）

### Q4) Video 生成の正規入口を 1つに固定する？

現状:
- 正規主線は `auto_capcut_run` + `capcut_bulk_insert`（run_dir/進捗ログ/manifestも整備）。
- ただし `run_pipeline --engine capcut` が stub のため、誤用の余地がある。
- UI側は JobManager 経由で `run_srt2images.sh` / `capcut_bulk_insert.py` を実行する。

決めたいこと:
- `run_pipeline --engine capcut` を SSOTで “非推奨/実験” と明示する？
- 入口索引（OPS_ENTRYPOINTS_INDEX）上も、主線を `auto_capcut_run` に寄せ切る？

関連: `ssot/ops/OPS_GAPS_REGISTER.md#GAP-004`

### Q5) Audio の “Bテキスト手動上書き” をどこまで正式サポートする？

現状:
- run_tts は B-text を明示入力で許容（stale guardあり）。
- UIには `script_audio_human.txt` 保存→ `audio_prep/script_sanitized.txt` mirror の仕組みがある。
- ただし標準の音声生成 API は AテキストSoT（assembled_human→assembled）を強制するルートが中心。

決めたいこと:
- B-text 運用は “CLI/手動専用（例外）” としてSSOTに固定する？
- UIからも “Bで再生成” を正式に出す？

---

## SSOT=UI（今回の方針に直結）

### Q6) SSOTポータル（UI）は read-only で固定？

現状:
- SSOT UI は **read-only**（gitで編集→UIで閲覧）が前提。

決めたいこと:
- UIから編集→commit/push までやるか（権限/監査/競合解決/レビューの設計が必要）。

---

## P2（運用の衛生 / ゴミ増殖の防止）

### Q7) Publish の一時DL（yt_upload_*.bin）をどう扱う？

現状:
- `publish_from_sheet.py` は OS temp に `yt_upload_*.bin` を作成し、終了後も自動削除しない。
- SSOT/README 側は「repo/tmp」前提の記述が混在する。

決めたいこと:
- 「upload 成功後は削除」を標準にするか？
- 監査/再送のために保持するなら、置き場を `workspaces/tmp/publish/` 等へ寄せて、ログ（Sheet or log.json）に残すか？

---

## P1（モデル固定/運用の迷子防止）

### Q8) `script-main-2` は定義する？するなら何のため？

背景:
- dd質問: 「`script-main-1` は固定でOKだが `script-main-2` は？Kimiだよね？」

決めたいこと:
- `script-main-2` を **定義しない（存在しない=迷子を防ぐ）** で固定する？
- それとも **“手動で明示選択するための予備”** として `script-main-2` を作る？
  - 例: `script-main-2 = OpenRouter/Kimi`（ただし **自動フォールバックはしない**）
  - 使う場合は `models=[script-main-2]` を **明示**したときのみ（通常運用は `script-main-1` 固定）

---

## P1（PM運用/Slack）

### Q9) Slack 30分ポーリング（常時接続）をどこまで自動化する？

背景:
- dd要望: 「30分に一回ペースでSlack投稿を拾い、非対話で処理する仕掛け」

決めたいこと:
- 自動化の範囲:
  - A) **sync only**（SSOT inbox 更新だけ / Slackへは返信しない）
  - B) sync + digest返信（“新規だけ” を短く返信）
  - C) sync + digest + PIDスナップショット
- 実行形態:
  - launchd（Mac）で “Mac稼働中のみ”
  - 手動コマンド（必要時のみ）
- 重要:
  - secrets（token/channel/thread）は **gitに固定しない**
  - `codex exec` と “AIエージェント（agent/pending）” を混同しない（台本はAPI固定）

---

## P2（ストレージ/書庫）

### Q10) Gitを“ストレージ/書庫”として何を入れる？

背景:
- dd提案: 「gitをストレージ・書庫にして容量問題を解消し、整理を進めたい」

決めたいこと:
- GitHub Releases（書庫）に載せる対象:
  - A) 音声 final（mp3/wav）だけ
  - B) 音声 + サムネ + 重要ログ（最小セット）
  - C) run_dir / 大きい成果物も含める（容量/運用が重くなる）
- 命名/タグ（episode単位で pull できる形）の正本をどこに置くか

---

## P2（辞書/読み）

### Q11) ローカル辞書は残す？廃止する？

背景:
- ddコメント: 「ローカル辞書は疑問。不要と思う。汎用的な修正がむずしいから個別対応してるわけでしょ？」

決めたいこと:
- A) ローカル辞書を廃止し、個別episodeの `audio_prep` 側で確定させる
- B) “ユニーク誤読のみ” を辞書へ（D-014に寄せる）＋曖昧語は個別対応
- C) 例外的にチャンネル辞書は許容する（ただしSoTと運用が必要）
