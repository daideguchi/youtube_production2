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

## 迷子/復帰（まず叩く）

SSOTが読めない/何が正かわからないときは、先に “現状” を確定します。

- 健康診断: `./ops doctor`
- 入口一覧（必要十分）: `./ops list`
- 進捗ビュー（read-only）: `./ops progress --channel CHxx --format summary`
- “最新の実行” ポインタ: `./ops latest --channel CHxx --video NNN`
- 復帰（固定導線）: `./ops resume episode --channel CHxx --video NNN`
- SSOTアンカーの更新状況: `./ops ssot status`
- 連絡/合意が必要なら: `reference/CONTACT_BOX.md`（secrets禁止）

---

## 矛盾を見つけたら（直し方）

「Aにこう書いてあるのにBには違うことが書いてある」を **読む側で頑張って解釈しない**。
矛盾は “差分” として切り出して、固定ルールに収束させます。

手順:
1. どのファイル同士が矛盾しているか（パス）を控える
2. 実装/実行結果（`./ops` の挙動）で現状を確認する
3. 乖離として `ssot/ops/OPS_GAPS_REGISTER.md` に根拠付きで記録する
4. 判断が必要なら `ssot/DECISIONS.md` に Decision を追加（Proposed→合意後 Done）
5. Done になったら SSOT→実装の順で修正し、再発防止のチェック（`./ops ssot check` 等）に乗せる
