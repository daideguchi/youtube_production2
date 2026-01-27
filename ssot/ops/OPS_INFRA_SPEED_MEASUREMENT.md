# OPS_INFRA_SPEED_MEASUREMENT — インフラ（通信/保存）速度の実測と集約（SSOT）

目的（最重要 / 迷子防止）:
- **Macでサクサク動く状態を維持しつつ、容量はがっつり空ける**
- **共通ストレージ環境を整える**（どの端末から見ても同じSoT/同じ場所）
- **重い処理は夜間バッチ**に寄せる（昼の作業を止めない）

結論の出し方:
- 体感/憶測で決めない。**必ず実測**して、Notion + `/files` に集約する。

---

## 1) 何を測るか（指標）

最小セット（迷ったらこれだけ）:
1. **Ping（遅延）**: Mac→Acer / Mac→Lenovo（ms）
2. **SMB write/read（MB/s）**: Mac→共有候補ストレージ（Acer/Lenovo）への実測
3. **Disk write/read（MB/s）**: 各端末ローカル（Mac SSD / Lenovo NVMe 等）

補足:
- CapCutドラフト（未投稿=Hot）は速度最優先のため **Macローカルが正**。共有に置かない（`ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`）。

---

## 2) 記録の正本（集約先）

- Notion（Network NOW配下）:
  - ページ（入口）: `SMB-5G-Tailscale`（UIから辿れる）
  - 計測DB（正本）: `2f503c60-8a71-813b-ac34-d31149ea42cc`
- UI（/files）:
  - `https://acer-dai.tail8c523e.ts.net/files/_reports/infra_speed_summary.html`
  - JSON（機械読み）: `.../infra_speed_summary.json`

---

## 3) 実測コマンド（Macから）

### 3.1 基本（ping + read/write）

`scripts/ops/infra_speed_bench.py` を使う（実測だけ。移行/削除はしない）:
```bash
python3 scripts/ops/infra_speed_bench.py \
  --ping 192.168.11.14 --ping 100.98.188.38 \
  --ping 192.168.11.4 --ping 100.73.254.74 \
  --target "Mac SSD(workspaces/tmp)=workspaces/tmp" \
  --target "Acer workspace(SMB)=/Users/dd/mounts/workspace" \
  --out workspaces/logs/ops/infra/infra_speed_bench_latest.json
```

注意:
- SMB read はOS cacheで盛られることがある。厳密にやる場合は **unmount+remount** を挟む（Notion側の備考に必ず書く）。
- `lenovo_share` のような “外部マウントの中の外部マウント” は、落ちてると **stat/listdir が固まる** ことがある。計測・可視化は **タイムアウト前提**で進める。

### 3.2 実測の結果を `/files` に出す

現状は運用として、Acerの `_reports` 配下に JSON/HTML を置いて参照する（例）:
- JSON: `/srv/workspace/doraemon/workspace/_reports/infra_speed_summary.json`
- HTML: `/srv/workspace/doraemon/workspace/_reports/infra_speed_summary.html`

（AcerのFiles公開: `https://acer-dai.tail8c523e.ts.net/files/_reports/...`）

---

## 4) 判定ルール（母艦=どこ？）

原則:
- **Hot（未投稿）**: Macローカル（例外なし）
- **共有SoT（台本/サムネ/進捗）**: “一箇所” に固定（UIが見る正本）
- **Cold（投稿済み/監査保管）**: 共有ストレージへ退避（容量対策）

母艦（共有ストレージ）を Acer/Lenovo で迷ったら:
- **速度（SMB read/write）で勝つ方**を正本にする。
- ただし、AcerがHDD/外付けが不安定なら、SoTの正本にすると “遅い/壊れる” が直撃する。
  - → Acerは “UIハブ/ゲートウェイ” に寄せ、ストレージは Lenovo NVMe/外付け/NAS に寄せるのが安全。

---

## 5) 付記（事故防止）

- 外部ストレージが落ちた時に、Macの作業が止まるのはNG。
  - `./ops storage doctor` を常に入口に置く（配線の可視化）。
  - 共有が落ちている時は “サイレントfallbackで別場所に書く” をしない（分岐でカオス化する）。

