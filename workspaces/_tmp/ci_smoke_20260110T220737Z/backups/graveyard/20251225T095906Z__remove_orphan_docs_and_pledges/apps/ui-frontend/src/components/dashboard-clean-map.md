## dashboard clean 適用範囲（スコープ限定版）

- `dashboard-clean.css` … `dashboard-overview.dashboard-clean` にだけ効く。カード/テーブル/フォーカスカードをフラット＆整列。
- `workspace-clean.css` … `workspace--dashboard-clean` がルートに付いたときだけ効く。サイドバー/nav/main/status/channellist を軽量化。

### コンポーネント対応
- `DashboardOverviewPanel` … `dashboard-clean` クラスを付与済み。→ `dashboard-clean.css` が効く。
- `AppShell` … view === dashboard のとき `workspace--dashboard-clean` を付与。→ `workspace-clean.css` が効く。
- `DashboardPage` … `ChannelListSection` の variant=`dashboard` を使用。→ `workspace-clean.css` の channel list 部分が効く。

### 既存スタイルとの関係
- グローバル `App.css` には触れていない。`dashboard-clean` / `workspace--dashboard-clean` が付いている要素だけ上書き。
- 他ページ（Channel/Audio/Thumbnail/Production など）には一切影響なし。

### 次の展開予定（同じ方式で段階移行）
1) Channel系（`channel-overview`, `channel-projects`, `video-card`）にクリーン版を追加し、`workspace--channel-clean` を導入。※ 完了
2) Audio系（`audio-workspace`, `status-chip` 群）にクリーン版を追加し、`workspace--audio-clean` を導入。※ 完了
3) Thumbnail系にクリーン版を追加し、`workspace--thumbnail-clean` を導入。※ 完了
4) Production系にクリーン版を追加し、`workspace--production-clean` を導入。※ 完了
5) Remotion系もスコープ付きクリーン版を追加予定。※ 完了
6) 直書き色の置換候補をリストアップし、2nd `:root` トークンに寄せる（置換は段階的）。

このファイルは進捗メモとして更新していく。
