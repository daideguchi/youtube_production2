## 色トークン移行メモ（置換前の設計メモ、変更なし）

ベースとなる 2nd `:root`（App.css 5781行〜）に合わせて、頻出色をトークンに寄せる計画。まずはクリーン版CSS群（スコープ限定）から置換する方針。

### 頻出色と割当案
| 色(頻度) | 用途例 | 割当案 |
| --- | --- | --- |
| `#0f172a` (184) | 強い文字色 | `--color-text-strong` |
| `#475569` (152) | muted文字色 | `--color-text-muted` |
| `#e2e8f0` (160) | 枠線・テーブル罫線 | `--color-border` |
| `#ffffff`/`#fff` (計180+) | サーフェス背景 | `--color-surface` |
| `#f8fafc` (84) | サーフェスサブ背景 | `--color-surface-subtle` |
| `#1d4ed8` (多) | アクセント文字/ボタン | `--color-primary` |
| `#b91c1c` | エラー系文字/背景 | 新規 `--color-danger` |
| `#cbd5f5` | 淡い枠線 | 新規 `--color-border-muted` |
| `rgba(15,23,42,0.08)` | 影/罫線 | `--color-shadow` or `--color-border-weak` |
| `rgba(148,163,184,0.6)` | 境界/文字薄 | 新規 `--color-muted-strong` |
| `rgba(37,99,235,0.12)` | アクセント淡背景 | 新規 `--color-primary-soft` |

### 適用順（副作用を避ける）
1. クリーン版CSS群のみ置換（dashboard/channel/audio/thumbnail/production/remotion）。グローバルは触らない。
2. 置換後に画面確認。問題なければグローバル `App.css` の上位頻出色を順次置換（トップ5から）。
3. 新トークン（`--color-danger`, `--color-border-muted`, `--color-primary-soft` など）を 2nd `:root` に追加してから置換を実施。

### 現状の 2nd :root（App.css 5781行〜）既存トークン
- `--color-bg-base`, `--color-surface`, `--color-surface-subtle`, `--color-border`, `--color-shadow`, `--color-text-strong`, `--color-text-muted`, `--color-primary`, `--color-sidebar`, `--color-sidebar-text`

### 次のアクション
- 新トークン（danger/border-muted/primary-soft 等）を 2nd :root に追加。
- クリーン版CSS群をこれらトークンに置換（スコープ限定なので副作用小）。
