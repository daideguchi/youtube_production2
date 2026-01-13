# SSOT_COMPASS — SSOTがカオスに見えた時の読み方（効力の順）

目的:
- 「何が正本か分からない」状態を潰す。
- 迷子のときに SSOT を読み漁らず、`./ops` を入口に **確実に復帰**できるようにする。

---

## TL;DR（結論: 効力の順）

SSOT配下の文章は「全部が同じ重み」ではありません。迷ったら次の順で扱います。

1. **実装/テスト/実行結果（`./ops ...` の実挙動）**
2. **確定フロー（観測ベースの正本）**: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
3. **人間向け“最終ルール”**: `ssot/reference/【消さないで！人間用】確定ロジック.md`
4. **入口索引（叩く場所）**: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` / `./ops list`
5. **意思決定台帳のうち確定分**: `ssot/DECISIONS.md` の `Done`
6. 各種 `ssot/ops/OPS_*.md` は上記の補足（矛盾したら上位に従う）
7. `ssot/DECISIONS.md` の `Proposed` と `ssot/plans/PLAN_*.md` は **未確定/計画**（運用の正として扱わない）
8. `ssot/history/` は参照用（現行運用の根拠にはしない）

---

## ドキュメント種類（混ぜない）

SSOT内の文章は “種類” が違うと役割も違います。混ぜて読むとカオス化します。

- **SoT（確定・正本）**: その工程の唯一の真実。矛盾したらこれに寄せる（例: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`）。
- **Guide（運用手順）**: 実行のしかた/落ちた時の復帰導線。SoTの補助（例: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`）。
- **Register（台帳）**: “事実/課題/実行ログ” を貯める場所。**正解を決める場所ではない**（例: `ssot/ops/OPS_GAPS_REGISTER.md`, `ssot/ops/OPS_GLOBAL_TODO.md`）。
- **Decision（意思決定）**: 正解を確定するための台帳。**`Done` だけが運用ルール**（`Proposed` は未確定）。
- **Plan（計画）**: こう変える/こう直す、の提案。運用の正として扱わない（`ssot/plans/`）。
- **History（履歴）**: いつ何が起きたかの記録。現行運用の根拠にはしない（`ssot/history/`）。

---

## 最短で回す（オペレーター用チートシート）

「理解してから動く」ではなく「動かしながら理解」を成立させるための最短導線。

- 入口（まずこれ）: `./ops list`
- 状態を見る: `./ops progress --channel CHxx --format summary`
- 迷子から復帰: `./ops resume episode -- --channel CHxx --video NNN`
- 台本（API固定）: `./ops api script new -- --channel CHxx --video NNN`（既存なら `resume`）
- 音声（TTS）: `./ops audio --llm think -- --channel CHxx --video NNN`
- 動画（CapCut等）: `./ops patterns list` → `./ops patterns show <PATTERN>` → 表示されたコマンドを実行
- SSOTアンカー確認: `./ops ssot status`

重要（CLIの契約）:
- `./ops` の一部サブコマンドは「内部ツールに引数をそのまま転送」するため、`--channel` などのフラグを渡すときは `--` 区切りを入れる（例: `./ops audio -- --channel CHxx --video NNN`）。

---

## 迷子/復帰（まず叩く）

SSOTが読めない/何が正かわからないときは、先に “現状” を確定します。

- 健康診断: `./ops doctor`
- 入口一覧（必要十分）: `./ops list`
- 進捗ビュー（read-only）: `./ops progress --channel CHxx --format summary`
- “最新の実行” ポインタ: `./ops latest --channel CHxx --video NNN`
- 復帰（固定導線）: `./ops resume episode -- --channel CHxx --video NNN`
- SSOTアンカーの更新状況: `./ops ssot status`
- 連絡/合意: `reference/CONTACT_BOX.md`（secrets禁止）

---

## 矛盾を見つけたら（直し方）

「Aにこう書いてあるのにBには違うことが書いてある」を **読む側で頑張って解釈しない**。
矛盾は “差分” として切り出して、固定ルールに収束させます。

手順:
1. どのファイル同士が矛盾しているか（パス）を控える
2. 実装/実行結果（`./ops` の挙動）で現状を確認する
3. 乖離として `ssot/ops/OPS_GAPS_REGISTER.md` に根拠付きで記録する
4. 判断が必要なものは `ssot/DECISIONS.md` に Decision を追加（Proposed→合意後 Done）
5. Done になったら SSOT→実装の順で修正し、再発防止のチェック（`./ops ssot check` 等）に乗せる
