# PLAN_THUMBNAILS_SCALE_SYSTEM — サムネ作成・編集を「高品質×高速×スケール」させる計画（SSOT）

## Plan metadata
- **Plan ID**: PLAN_THUMBNAILS_SCALE_SYSTEM
- **ステータス**: Draft
- **担当/レビュー**: TBD（Orchestrator で owner/reviewer を固定する）
- **対象範囲 (In Scope)**:
  - UI: `apps/ui-frontend`（/thumbnails）, `apps/ui-backend`（thumbnail API）
  - CLI: `scripts/thumbnails/build.py`（build/retake/qc）
  - Core: `packages/script_pipeline/thumbnails/**`
  - SoT: `workspaces/thumbnails/**`, `workspaces/planning/channels/**.csv`
- **非対象 (Out of Scope)**:
  - クリエイティブの勝ち筋（コピー・デザインの内容そのもの）を決めること（設計は「直せる/量産できる」仕組みまで）
  - YouTube 投稿自動化・CTR計測の完全自動化（後続の別計画で扱う）
- **関連 SoT/依存**:
  - 運用SSOT: `ssot/ops/OPS_THUMBNAILS_PIPELINE.md`
  - I/O: `ssot/ops/OPS_IO_SCHEMAS.md`（Thumbnails）
  - データ配置: `ssot/ops/DATA_LAYOUT.md`
  - 全体TODO: `ssot/ops/OPS_GLOBAL_TODO.md`（TODO-THUMB-001..003）
  - 管理SoT: `workspaces/thumbnails/projects.json`, `workspaces/thumbnails/templates.json`
  - Planning SoT: `workspaces/planning/channels/CHxx.csv`
- **最終更新日**: 2025-12-29

---

## 1. 背景と目的
チャンネルが増え続ける前提では、サムネ運用は「職人作業」では破綻する。  
目標は **“崩れない・直せる・量産できる”** を維持しつつ、反復速度と再現性（いつでも同じ結果を再生成できる）を最大化すること。

そのために本計画では、サムネを「画像ファイル」ではなく **Spec（仕様）から毎回再生成できる成果物**として扱い、SoT/責務/生成物を整理して “迷い” と “手戻り” を構造的に消す。

---

## 2. 成果物と成功条件（Definition of Done）

### 2.1 成果物（最終的に残すもの）
- 計画（本書）に基づく **SoT設計の固定**（「何をどこで直すか」を即答できる状態）
- **動画単位の編集Spec（thumb spec）**の導入（最小情報・差分保存・後方互換）
- **Compiler Core の単一化**（UI/CLIが同じライブラリ関数を呼ぶ）
- **安全/高速の基盤**:
  - 画像・JSONの atomic write（破損/途中書き込みを止める）
  - “作業用(draft)” と “納品用(final)” の2段階出力（高速反復 + 最終最適化）
  - バッチ時の無駄I/O削減（planning CSV 等の invocation 内キャッシュ）
- **運用のスケール機構**:
  - チャンネル追加が「設定 + 既定値 + spec」で完了し、コードif分岐が増えない
  - バルク操作（例: pan/zoom一括適用）がUI/CLIで可能
- **可観測性**:
  - build meta（入力Spec/既定値/生成物/所要時間/エラー）が一定形式で残る
  - 失敗時に「どこを直すべきか」がログ/メタから追える

### 2.2 成功条件（DoD）
- **SoTの迷いが消える**: 典型的な修正指示（コピー、明るさ、pan/zoom、素材差替え）について「直す場所」が1つに収束している
- **破損で止まらない**: `image file is truncated` 等の破損事故を atomic write + verify で抑止できる
- **反復が速い**: 30本バッチ合成で planning CSV の読み込みが同一invocation内で1回に収束し、draft出力で体感待ちが大幅に減る
- **新チャンネルが追加可能**: チャンネル追加で必要なのが「テンプレ/既定値/（必要なら）layer_specs/stylepack」だけで、コード改修が必須にならない

---

## 3. スコープ詳細

### In Scope（対象）
- SoTの整理（projects/templates/planning/layer_specs/assets の責務分離）
- 生成パイプライン（背景正規化→補正→合成→出力→QC）の共通化
- UI/CLIの統合（同一の Compiler API を呼ぶ）
- 安全・性能の土台（atomic write、draft/final、キャッシュ）
- チャンネル追加/移行の手順（チェックリスト化）

### Out of Scope（非対象）
- どのデザインがCTRを最も上げるかの研究（別途運用・実験計画）
- YouTube側のA/Bテスト計測や自動投稿（別計画）

---

## 4. 現状と課題の整理（観測ベース）

### 4.1 SoTが分散し「どこを直すか」が即答できない
- 管理SoT（`projects.json`）と生成物（assets配下）と、CSV/layer_specsが混在し、修正が収束しない。

### 4.2 メタファイル衝突（同名 `meta.json` の二重用途）
- `apps/ui-backend/tools/assets_sync.py` が `workspaces/thumbnails/assets/{CH}/{NNN}/meta.json` に planning 由来メタを書く。
- `packages/script_pipeline/thumbnails/tools/layer_specs_builder.py` も同じパスに build メタ（`bg_pan_zoom` 等）を書いて上書きする。
- 結果: “どのメタが正か” が壊れる + 並列実行で破損/差分不明になる。

### 4.3 反復が遅い / 止まりやすい
- Pillow 保存 `optimize=True` が作業中の反復を遅くし、書き込み時間増加で破損リスクも上げる。
- planning CSV を動画ごとに都度ロード（force_refresh）しており、バッチが無駄I/Oで遅くなる。

### 4.4 収束しないパラメータ（例: CH22 bg_pan_zoom）
- チャンネル既定として pan/zoom のSoTが無く、CLI引数ベースで混在が発生しやすい。
- “一括適用→例外だけoverride” ができないため、リテイクが地獄化する。

---

## 5. 方針・設計概要（スケール前提）

### 5.1 設計原則（スケールのための固定ルール）
- **Spec-first**: サムネは「画像」ではなく「Spec（仕様）」から再生成できる成果物とする。
- **SoTは役割ごとに1つ**: 管理・型・動画差分・生成物を混ぜない。
- **共通ロジックは packages に集約**: UI/CLIの重複実装を禁止し、Compiler API を単一化する。
- **後方互換を壊さない**: 既存の `00_thumb.png` / `compiler/<build_id>/out_01.png` 運用は当面維持し、段階移行する。
- **安全優先**: 画像/JSONは必ず atomic write。破損検知（verify）を入れる。
- **パス直書き禁止**: 新規実装は必ず `factory_common.paths` を利用する（移設耐性）。

### 5.2 SoTレイヤ（責務分離の“最終形”）
| レイヤ | SoT | 役割 | 編集者 |
| --- | --- | --- | --- |
| 管理（追跡/採用） | `workspaces/thumbnails/projects.json` | status/notes/tags/selected_variant | UI |
| 型（チャンネル既定） | `workspaces/thumbnails/templates.json` | テンプレ/モデル/既定値/参照id | UI |
| 動画差分（編集Spec） | `workspaces/thumbnails/assets/{CH}/{NNN}/thumb_spec.json`（新設） | “直す対象”の最小差分 | UI/CLI |
| 生成物（派生） | `workspaces/thumbnails/assets/{CH}/{NNN}/00_thumb.png` 等 | 実ファイル（消えても再生成可） | compiler |

補足:
- layer_specs（YAML）は「レイアウト/背景指示のテンプレ」であり、動画差分は thumb_spec（または planning）へ寄せる。

### 5.3 “thumb spec（動画編集Spec）” の設計（v1）
目的: 「チャンネル追加が増えても、動画ごとの例外を **最小差分** で保持し、いつでも再生成できる」状態を作る。

最低要件:
- **存在しなくても動く**（未作成動画はチャンネル既定 + planning で生成可能）
- **差分だけ保存**（既定値を毎回全量保存しない）
- **スキーマversion**を持つ（将来拡張に備える）

例（最小差分イメージ）:
```json
{
  "schema": "ytm.thumbnail.thumb_spec.v1",
  "channel": "CH22",
  "video": "014",
  "overrides": {
    "bg_pan_zoom": { "zoom": 1.2, "pan_x": 0.0, "pan_y": -1.0 }
  },
  "updated_at": "2025-12-29T00:00:00Z"
}
```

注意:
- “コピー” は原則 planning を正とし、thumb spec には **例外（この動画だけ別コピー）** のみ保持する。
- 画像生成プロンプト（thumbnail_prompt）も同様に planning を正とし、thumb spec は例外のみ。

v1で許可する overrides（スケールのための“最小セット”）:
- `bg_pan_zoom`: `{zoom:float>=1.0, pan_x:float[-1..1], pan_y:float[-1..1]}`
  - 備考: `pan_y` は **負** 方向で “被写体を下へ寄せる” 方向になりやすい（`apply_pan_zoom` の定義に従う）。
- `bg_enhance`: `{brightness,contrast,color,gamma}`（各float。既定=1.0）
- `bg_enhance_band`: `{x0,x1,power,brightness,contrast,color,gamma}`（横方向の部分補正。x0/x1=0..1）
- `text_effects`（必要時のみ）:
  - `stroke`: `{color:string, width_px:int}`
  - `shadow`: `{color:string, alpha:float[0..1], offset_px:[int,int], blur_px:int}`
  - `glow`: `{color:string, alpha:float[0..1], blur_px:int}`
- `overlays`（必要時のみ）:
  - `left_tsz`: `{enabled:bool, color:string, alpha_left:float[0..1], alpha_right:float[0..1], x0:float, x1:float}`
  - `top_band`: `{enabled:bool, color:string, alpha_top:float[0..1], alpha_bottom:float[0..1], y0:float, y1:float}`
  - `bottom_band`: `{enabled:bool, color:string, alpha_top:float[0..1], alpha_bottom:float[0..1], y0:float, y1:float}`
- `copy_override`（例外のみ）: `{upper,title,lower}`（string。空文字は許可しない＝事故るため）

ガードレール（強制）:
- unknown key はエラー（silent accept しない）
- 数値は型/範囲で clamp せず、**エラーにして止める**（勝手に丸めると収束しない）
- overrides は「差分のみ」を推奨するが、v1は運用簡単化のため “差分判定” は必須にしない（実装側で正規化してもよい）

### 5.4 メタファイルの分離（衝突ゼロ）
`workspaces/thumbnails/assets/{CH}/{NNN}/` 配下のメタを用途別に分け、上書き競合を消す。

- `planning_meta.json`（派生）: planning 由来（title/flag/progress 等）。`assets_sync` が書く。
- `thumb_spec.json`（SoT）: 編集差分（overrides）。UI/CLIが書く。
- `builds/<build_id>/build_meta.json`（派生）: 実行時の確定入力（既定値展開後）と生成物、所要時間、エラー、spec hash。
  - `00_thumb.png` を更新する場合も、必ずどの build_id から来たか追えるようにする。

### 5.5 Compiler API の単一化（UI/CLI統合）
UI/CLI は “同じ関数” を呼ぶ。

方針:
- `packages/script_pipeline/thumbnails/` に「Compiler API（薄いFacade）」を追加し、UI/CLIはそれだけを呼ぶ。
- engine は内部で切替（auto/layer_specs/buddha/特殊）。
- planning/layer_specs/templates/projects へのアクセスもAPI側で統一する。

### 5.6 速度と安全（draft/final + atomic write + verify）
- **draft**: 反復作業向け。`optimize=False` + 軽い圧縮（例: `compress_level=1`）。
- **final**: 納品向け。`optimize=True`（重いがOK）。
- 画像/JSONは必ず `tmp → replace()` の atomic write。
- 書き込み後に `open + verify`（破損をその場で検知し、部分ファイルを残さない）。

### 5.7 スケール運用（チャンネル増に耐える“Channel Pack”）
新チャンネル追加を「設定だけ」で済ませるための固定パッケージを定義する。

最小構成（必須）:
- `workspaces/thumbnails/templates.json: channels[CHxx]`
  - `compiler_defaults`（bg_enhance/bg_pan_zoom/qc 等）
  - `default_template_id`（AI生成を使う場合）
  - `layer_specs`（layer_specs_v3 を使う場合）
- （engineにより）`workspaces/thumbnails/compiler/stylepacks/*.yaml` または `workspaces/thumbnails/compiler/layer_specs/*.yaml`

追加構成（任意）:
- portrait policy（例: `workspaces/thumbnails/compiler/policies/*.yaml`）
- safe area / テキストfitルール（layer_specs側に集約）

### 5.8 検証（lint/validate）の入口を固定
チャンネル増に伴う“設定ミス”を、人間レビュー前に機械で止める。

最低限チェックすること:
- templates.json の参照整合（default_template_id / layer_specs ids / registry）
- planning CSV の必須列（コピー/プロンプト）存在チェック（空欄は警告/エラーの方針を固定）
- 画像ファイルの破損検知（`10_bg.*`, `00_thumb.png`, portrait）
- thumb_spec.json のスキーマ検証

### 5.9 LLMで「コメント → パラメータ更新」を安全に回す（推論はするが、勝手に壊さない）
結論: **LLMに推論させて良い**。ただしスケールさせるには「LLMは翻訳機」「正はパラメータ契約」を固定する必要がある。  
（自由作文で“やってしまう”LLMは、チャンネル増で必ず再カオス化する）

方針:
- LLMの役割は **コメント（自然言語）→ 構造化パッチ** の変換まで（=翻訳）。
- 適用できるのは **許可されたパス（allowlist）** だけ。値は型/範囲で検証し、外れたら不適用（=ガードレール）。
- 既定値（チャンネルの型）を勝手に書き換えない。v1は **動画単位（thumb_spec）だけ** を対象にする。
- 適用前後に **draft再合成→プレビュー** して、人間が確認できること（“迅速”と“正確”を両立）。
- LLMが曖昧な場合は「質問」を返し、勝手に適用しない（=誤爆防止）。

入力（LLMに渡す文脈）:
- `comment`: 人間の指示（例:「顔を少し下に。文字の影を強く。」）
- `target`: `{channel, video}` + 対象ファイル（`00_thumb.png` 等）
- `current_effective_params`（要約）:
  - channel defaults（templates.json の compiler_defaults / engine）
  - planning copy（CSVのサムネ上/中/下/プロンプト）
  - thumb_spec overrides（差分）
  - 直近 build_meta の要点（pan/zoom/明るさ/効果）
- 任意: 現在のサムネ画像（data URL）  
  - UI backend には既に画像→キャプション生成の仕組みがあるため、同様に視覚文脈を渡せる（`visual_thumbnail_caption`）。

出力（LLMの“契約”＝必ずこの形で返す）: `ytm.thumbnail.comment_patch.v1`
```json
{
  "schema": "ytm.thumbnail.comment_patch.v1",
  "target": { "channel": "CH22", "video": "014" },
  "confidence": 0.0,
  "clarifying_questions": [],
  "ops": [
    { "op": "set", "path": "overrides.bg_pan_zoom.pan_y", "value": -0.6, "reason": "顔を下に" },
    { "op": "set", "path": "overrides.text_effects.shadow.alpha", "value": 0.78, "reason": "文字の影を強く" }
  ]
}
```

検証（適用前の必須チェック）:
- `schema` が一致する
- `target` が妥当（CH/NNN）
- `ops[*].path` が allowlist に含まれる
- `value` が型/範囲に収まる（例: `pan_y ∈ [-1..1]`, `zoom ≥ 1.0`）
- `confidence` が閾値未満なら **適用せず質問**（運用で閾値を固定）

失敗/非同期（THINK MODE）:
- LLMRouter が provider=`agent` を返した場合は「結果未完了」として扱い、**適用を保留**し、agent_runner の完了後に同じ契約で再取得して適用する。
- これにより「LLMが落ちても作業が止まらない」運用と整合する（既存のサムネキャプション生成と同じ思想）。

### 5.10 エフェクト/効果を“パラメータとプリセット”でスケール管理する
前提: 既にエフェクトはデータ駆動で存在する。
- `layer_specs_v3` は `effects_defaults` / overlays（帯・グラデ等）を持つ（`packages/script_pipeline/thumbnails/compiler/compose_text_layout.py`）。
- `buddha_3line_v1` は stylepack に vignette / base_pan_zoom / enhance 等を持つ（`workspaces/thumbnails/compiler/stylepacks/*.yaml`）。

スケールする設計の要点:
- **プリセット（Preset）** を主線にする（チャンネル増に強い）。
  - layer_specs: `template_id`（レイアウト）+ global effects（文字効果/overlay）
  - buddha: `stylepack_id`（組版+効果+背景処理）
- **動画単位は差分（Override）だけ** を持つ（thumb_spec）。
- コメントで頻出する“ノブ”は、エンジン差を吸収する **共通コントロール** として定義してよい（例: 「文字の縁を太く」→ stroke.width_px）。
- 同じoverrideが複数動画で繰り返されるなら、チャンネル既定（templates.json / layer_specs / stylepack）へ昇格し、動画差分を減らす。

thumb_spec に入れるエフェクト（v1の推奨範囲）:
- 背景: `bg_enhance`（brightness/contrast/color/gamma）, `bg_pan_zoom`（zoom/pan_x/pan_y）, `bg_enhance_band`（x0..x1/power + enhance）
- 文字: `text_effects.stroke`（width/color）, `text_effects.shadow`（alpha/offset/blur/color）, `text_effects.glow`（alpha/blur/color）
- Overlay（必要時）: `overlays.left_tsz` / `overlays.top_band` / `overlays.bottom_band` の enabled/alpha/color

重要: 「エフェクト全部を動画差分で持つ」は禁止（破綻する）。
- 動画差分は “コメント対応で必要な最小変更” に限定する。
- 迷ったらプリセットを追加/更新して吸収する（チャンネル単位でスケールする）。

---

## 6. 影響範囲と依存関係

主な影響範囲（予定）:
- CLI: `scripts/thumbnails/build.py`
- Core: `packages/script_pipeline/thumbnails/layers/*.py`, `packages/script_pipeline/thumbnails/tools/*.py`
- UI Backend: `apps/ui-backend/backend/main.py`（thumbnail API）, `apps/ui-backend/tools/assets_sync.py`
- UI Frontend: `apps/ui-frontend/src/components/ThumbnailWorkspace.tsx`
- Workspaces: `workspaces/thumbnails/**`, `workspaces/planning/channels/**`

依存:
- Pillow（画像処理）
- planning CSV（現行運用を継続）
- `factory_common.paths`（パス解決の正）

---

## 7. マイルストーン / 実装ステップ（段階移行）
| ステージ | 具体タスク | オーナー | 期日 | ステータス |
| --- | --- | --- | --- | --- |
| 0 (P0) | atomic write + verify の共通化（画像/JSON） | TBD | TBD | Draft |
| 0 (P0) | draft/final 出力モード導入（optimize切替） | TBD | TBD | Draft |
| 0 (P0) | planning CSV の invocation 内キャッシュ（バッチ高速化） | TBD | TBD | Draft |
| 1 (P0) | `meta.json` 衝突解消（planning_meta / build_meta へ分離） | TBD | TBD | Draft |
| 1 (P1) | `bg_pan_zoom` を templates.json 既定 + per-video override に収束 | TBD | TBD | Draft |
| 2 (P1) | `thumb_spec.json`（最小差分）導入 + UIから編集できる導線 | TBD | TBD | Draft |
| 2 (P1) | UI/CLI を Compiler API 統合（重複ロジック削減） | TBD | TBD | Draft |
| 2 (P1) | LLM「コメント→thumb_specパッチ」導線（契約+検証+dry-runプレビュー） | TBD | TBD | Draft |
| 3 (P2) | バルク操作（pan/zoom/明るさの一括適用 + 例外管理） | TBD | TBD | Draft |
| 3 (P2) | QC: 2バリアントを1コマンド/1画面で生成・公開 | TBD | TBD | Draft |
| 4 (P2) | lint/validate コマンド整備（新チャンネル追加を事故らせない） | TBD | TBD | Draft |

---

## 8. TODO / チェックリスト（実装時の迷いを減らす）
- [ ] `TODO-THUMB-001` bg_pan_zoom のSoT固定（既定 + 明示override）
- [ ] `TODO-THUMB-002` planning CSV ロードのビルド単位キャッシュ
- [ ] `TODO-THUMB-003` コピーSoT（CSV vs layer_specs）を迷わない1択に固定（UIに“参照元”表示）
- [ ] `TODO-THUMB-PERF-001` optimize切替・再利用で再合成を高速化
- [ ] `TODO-THUMB-QC-001` 2バリアントQCを1コマンド化
- [ ] `TODO-THUMB-LLM-001` コメント→パラメータ更新（thumb_specパッチ）の契約/検証/プレビューを固定
- [ ] `TODO-THUMB-EFFECT-001` エフェクト（stroke/shadow/glow/overlay 等）の“プリセット優先 + 差分override”運用を固定
- [ ] 破損検知（verify）を build/retake/qc 前に入れる（スキップ/自動復旧方針も固定）
- [ ] projects/templates JSON の atomic write（FastAPI側も含めて）で破損リスクを下げる
- [ ] “選択中バリアントの安定出力”（例: `00_thumb.png`）と build履歴の関係を固定（コピー/リンク/メタ参照）

---

## 9. 決定ログ（ADR 簡易）
- 2025-12-29: **thumb spec（動画差分）を導入**し、チャンネル増でも収束する設計にする（既定 + 差分）。
- 2025-12-29: **meta分離**（planning_meta/spec/build_meta）で上書き衝突をゼロにする。
- 2025-12-29: **draft/final** と **atomic write + verify** をP0として扱う（速度と事故の両方を同時に解決するため）。

---

## 10. リスクと対策
- リスク: 既存運用（`00_thumb.png` / build出力パス）を壊す  
  - 対策: 互換出力を維持しつつ “安定出力の意味” を段階的に固定する（移行ステージ設計）
- リスク: projects/templates が server 書き込みで壊れる（途中書き込み）  
  - 対策: FastAPI側も tmp→replace の atomic write へ寄せる
- リスク: SoTが増えて逆に迷う  
  - 対策: thumb_spec は “差分のみ/存在しなくても動く” を強制し、UIで参照元を明示する

---

## 11. 非対応事項 / バックログ
- CTR計測・学習フィードバック（YouTubeアナリティクス連携）
- 自動で「勝ちサムネ」を探索する最適化（大規模実験）
- 企画CSVの列体系の統一（現行列名を尊重して段階移行）

---

## 12. 参照リンク
- `ssot/ops/OPS_THUMBNAILS_PIPELINE.md`
- `ssot/ops/OPS_IO_SCHEMAS.md`
- `ssot/ops/OPS_GLOBAL_TODO.md`
- `workspaces/thumbnails/README.md`
- `scripts/thumbnails/build.py`
- `packages/script_pipeline/thumbnails/tools/layer_specs_builder.py`
- `apps/ui-backend/backend/main.py`（thumbnails API）
- `apps/ui-backend/tools/assets_sync.py`
