# OPS_PATHREF_CONVENTION — 動的参照パス（PathRef）の規約

目的:
- 共有SoT/manifest/log/json に **ホスト固有の絶対パス** が混入して参照切れ・UI誤判定・移設事故が起きるのを防ぐ。
- ストレージ実体（Hot=Mac / Vault=Lenovo / NAS など）が変わっても、参照側は “root の解決” を変えるだけで動くようにする。

非交渉条件:
- Hot（未投稿）資産は **Macローカルに実体がある**こと（外部だけに存在はNG）。
- 外部（共有/NAS）が落ちても **Macの編集/生成を止めない**（同期は復旧後追随でOK）。
- UI配線を壊さない（リモート端末で「存在しないパス」を SoT と誤認しない）。
- `Path.resolve()` をネットワークマウントに対して安易に使わない（ハング/遅延の原因）。

---

## 1) PathRef v1（データ形式）

PathRef は JSON に埋め込める dict とする（文字列エンコードではなく構造で持つ）。

```json
{
  "schema": "ytm.path_ref.v1",
  "root": "workspace",
  "rel": "video/runs/CH06-002_capcut_v1/capcut_draft_info.json"
}
```

- `schema`: 固定（`ytm.path_ref.v1`）
- `root`: root key（後述）
- `rel`: POSIX相対パス（先頭 `/` なし・`..` 禁止）

---

## 2) root keys（解決方法）

PathRef の解決は **必ず `factory_common.paths` 経由**（パス直書き禁止）。

標準 root:
- `repo`: `factory_common.paths.repo_root()`
- `workspace`: `factory_common.paths.workspace_root()`

CapCut 用 root（ローカル編集想定）:
- `capcut_draft_root`: CapCut の `com.lveditor.draft` ルート
  - env: `YTM_CAPCUT_DRAFT_ROOT` を優先（互換で `CAPCUT_DRAFT_ROOT` も許容）
- `capcut_fallback_root`: `factory_common.paths.video_capcut_local_drafts_root()`（=`workspaces/video/_capcut_drafts`）

追加する場合:
- **SSOT更新 → 実装**の順で行う（追加rootは “仕様”）。

---

## 3) 生成側ルール（Producer）

- 共有される JSON/manifest では **PathRef を優先**して保存する。
- 旧フィールド（例: `*_path`）を残す場合:
  - “ローカル便宜/デバッグ用のヒント” に限定し、他端末での存在判定の根拠にしない。
  - 共有マウントに対して `resolve()` した絶対パスを埋め込まない。
- run_dir 内の symlink（例: `capcut_draft`）は **ローカル利便用途**に限定し、UI/共有SoTの正本にしない。

---

## 4) 消費側ルール（Consumer）

- `*_path_ref` があればそれを優先して解決する。
- 解決できない/存在しない場合:
  - “missing/broken” と断定しない（別ホストにある/Hotローカルの可能性がある）。
  - UIは “unresolved/remote” として扱い、誤って NG 判定しない。
- 旧 `*_path` しかない場合:
  - legacy として扱い、存在判定は **そのホスト上でのみ**行う（他ホストを前提にしない）。

---

## 5) 移行方針（後方互換）

- まず `*_path_ref` を追加する（`schema v2` への全面更新は後工程）。
- 既存データは破壊しない。新コードは旧データを読めること。

---

## 6) 適用先（当面）

- `workspaces/video/runs/**/capcut_draft_info.json`: `draft_path_ref` を追加（`draft_path` は legacy）
- `workspaces/video/runs/**/timeline_manifest.json`: `derived.capcut_draft.path_ref` を追加（`path` は legacy）
- UI/API: `draft_path` の存在チェックを PathRef 優先にし、解決不能時は “remote/unresolved” 表示へ

関連SSOT:
- `ssot/ops/OPS_IO_SCHEMAS.md`
- `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`
- `ssot/ops/OPS_CAPCUT_DRAFT_EDITING_WORKFLOW.md`
- `ssot/ops/OPS_SHARED_WORKSPACES_REMOTE_UI.md`

