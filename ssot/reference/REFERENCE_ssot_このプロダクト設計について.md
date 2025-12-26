# 最重要 SSOT — プロダクト設計メモ
管理者が手書きで書いた、プロダクトの骨格そのもの。ここに書かれた意図が最上位であり、他のドキュメントはすべて本書を補足・具体化する。
このドキュメントは管理者が手書きで書いたもの。つまり管理者の脳内。絶対的ssotである

======================================================
①人間が記述する部分
======================================================

このプロダクトのゴール・やりたいこと
youtube動画の量産を自動化すること。
youtube動画は基本的にaiで台本・音声・画像・動画が生成される

【このプロダクトの真価→人間が手間をかけてやっていた作業を自動化する】
・高品質な台本が自動で量産できる
・AI音声で自然な日本語で高品質なAI音声が量産できる
・youtubeに投稿できる高品質動画が量産できる。動画は基本、画像が切り替わっていく解説系。
ナビゲーション帯とかも自動で生成してくれる。できた動画は簡単に直感的に編集できる
・必要な場合SFXなども自動で挿入。

ーーー
【フェーズ１】
youtubeチャンネル立ち上げ
ベンチマークの分析。サムネイルとか尺とか、タイトル、この辺を分析して
どんな動画がどんな人たちに刺さっているかを分析。

youtube動画を作るにあたって順番は以下の通り

１、サムネイル or 企画を作成（これはcsv。1行1動画の想定。チャンネルごとにcsvを作成する）
２、台本を作成
３、音声を作成
４、動画を作成
５、動画を投稿

で、上記１に関して、サムネイルは一番大事なので、管理者が手動で作成する
作成したサムネイルはローカルにチャンネルごとにフォルダを作成して保存する。
その作成したサムネイルはたくさんあるので綺麗に管理したいし、
企画に対してサムネイルを紐づけて管理したい。
（つまり、このタイトルのこの動画はこのサムネイル！みたいに視覚的にわかるようにしたい）

ーーー
【フェーズ２】
- 台本手順・禁止事項は `ssot/ops/OPS_SCRIPT_GUIDE.md` を唯一の参照先とし、本書では手順を保持しない。
- 音声・字幕は `ssot/ops/OPS_AUDIO_TTS.md`（現行）を参照。
- 企画 CSV（チャンネル別）を正として進める。その他の記述は撤去。
全体の画風とかテーマとかについては一貫性のあるもので、見ていて視聴者が不自然に感じないもの

動画の左上に表示する文字列も生成。これが帯テキスト。
帯テキストにはメインとサブがある。メインは常時表示、サブは切り替わる（初期値はメイン１、サブ４）
サブのタイミングも自動で調整

動画生成は２種類
・capcutドラフト
・remotion動画

上記２つともuiで調整できる。画像とか帯テキストとか。

ーーー
【フェーズ３】
できた動画をyoutubeに自動投稿できるようにする
表形式でできた動画とかサムネとかタイトルとか動画投稿説明文とか、表形式に整理。
管理者がチェックを入れたらあらかじめ決めていた時間にyoutube自動投稿


ーーー
【フェーズ４】
投稿した動画のアナリティクスを取得し、改善できそうなところは改善する。次の企画に活かす
PDCAがうまく回るようにする



======================================================
②以下はAIが記述していい部分
======================================================

## 0. ミッション
- YouTube 用の解説動画を **リサーチ → 企画 → 台本 → 音声 → 画像/動画 → 投稿 → 分析** の全行程で自動化する。
- 台本・音声・画像・動画は AI が生成するが、**サムネイル作成と最終チェックは人間（管理者）が責任を持つ**。人手の判断を最小限にしつつ意思決定の重みを最大化する。
- 自動化によって「高品質台本」「自然なAI音声」「視聴者が違和感を感じない動画」「投稿・PDCAの高速化」を実現する。

## 1. 真価（人手作業の置き換え）
| 項目 | 自動化で得たい状態 |
| --- | --- |
| 台本 | 企画 CSV から台本を量産。詳細は `ssot/ops/OPS_SCRIPT_GUIDE.md` を参照。 |
| 音声 | Bテキストを自然な日本語で読み上げる TTS を UI 上で微調整できる。VOICEPEAK/VOICEVOX を使い分け可能。 |
| 動画 | SRT からチャンクを作り、コンテキスト一貫の画像・帯テキストを自動生成。CapCut/Remotion どちらでも調整できる。 |
| 投稿 | 完成した動画・サムネ・説明文を表形式で管理し、承認後に自動投稿。 |
| 分析 | 投稿後の指標を取得し、次の企画にフィードバックできる PDCA を回す。 |

## 2. エンドツーエンド工程と SoT
| ステップ | 主要成果 | 正本 (SoT) | 人間の役割 |
| --- | --- | --- | --- |
| 1. リサーチ / サムネ企画 | CSV 企画案 + サムネ案 | `workspaces/planning/channels/CHxx.csv`, `workspaces/thumbnails/assets/<channel>/` | ベンチマーク分析、サムネ手作り、CSVへの投入 |
| 2. 台本 | 詳細手順は `ssot/ops/OPS_SCRIPT_GUIDE.md` を参照（本書は手順を保持しない） | `workspaces/scripts/CHxx/<video>/content` | - |
| 3. 音声 + SRT | WAV / SRT / Readyフラグ | `workspaces/audio/final/<channel>/<video>/` | 誤読修正、Ready承認 |
| 4. 画像+動画 | 画像一式 / 帯設定 / CapCut・Remotion draft | `workspaces/video/runs/<project>` | 不自然な画像の差し替え、動画全体の最終確認 |
| 5. 投稿準備 | 表形式の投稿データ | `workspaces/planning/channels/CHxx.csv`, delivery フォルダ | チェックマーク、投稿タイミング決定 |
| 6. 自動投稿 | 投稿実行ログ | `workspaces/logs/regression/…`, UI からの承認ログ | 予約設定の承認 |
| 7. 分析/PDCA | アナリティクス + 改善メモ | `workspaces/planning/analytics/`, `ssot/history/HISTORY_codex-memory.md` | 指標の読み取り、次企画への指示 |

## 3. フェーズ別要件

### フェーズ1：チャンネル起ち上げ / サムネ体系化
- ベンチマーク分析で「誰に刺さるか / サムネ構成 / タイトル / 尺」の勝ちパターンを洗い出す。
- 企画 CSV をチャンネルごとに作成（1行1動画）。SoT は `workspaces/planning/channels/CHxx.csv`（master channels CSV は廃止）。
- サムネイルは管理者が手動で作り、チャンネルごとのフォルダに保存。**企画とサムネが必ず紐づいて確認できる UI / フォルダ構造**を維持。

### フェーズ2：台本・音声ラインの自動化
- 台本工程: `ssot/ops/OPS_SCRIPT_GUIDE.md` を唯一の参照とする。本書に手順は記載しない。
- 音声/字幕: `ssot/ops/OPS_AUDIO_TTS.md` を参照。
- 企画 CSV はチャンネル別 SoT（`workspaces/planning/channels/CHxx.csv`）。

### フェーズ3：投稿自動化
- 生成された動画・サムネ・タイトル・説明文を表形式に整理し、管理者がチェックを入れたら指定時刻に自動投稿。
- 認証や投稿テンプレは `channel_info` と `.env` に一元化し、UI から編集できる。

### フェーズ4：分析とPDCA
- 投稿した動画のアナリティクスを取得（CTR, 視聴維持率, 登録者増など）し、改善アイデアを次の企画 CSV へ反映。
- PDCA を止めないため、分析→改善メモ→次企画への移し替えを毎サイクルで実施。

## 4. 自動化ポリシーと判断ポイント
| 項目 | 自動化 | 人間の判断 |
| --- | --- | --- |
| サムネ作成 | なし（UI で管理のみ） | 作成・差し替え・承認をすべて管理者が行う |
| 台本生成 | Qwen 無料モデル。A/B整備まで自動 | A/B品質確認、差し戻し |
| 音声/SRT | 自動合成と自動チャンクを標準（音声ラインは `audio_tts`） | 読みやすさの最終判断、Ready承認 |
| 画像/帯 | 自動生成（同じ画風、帯テキスト自動） | 不自然箇所を差し替える |
| 動画 | CapCut / Remotion に同じデータを流す | 全体の流れ・テンポの最終チェック |
| 投稿 | 承認済みデータをバッチ投稿 | 投稿GO/NO-GOとスケジュール判断 |
| 分析 | 指標取得は自動 | 改善策の判断と次企画への反映 |

## 5. データ管理・SoT
- 企画: `workspaces/planning/channels/CHxx.csv`（チャンネル別が正本。master channels CSV は使用しない）
- 台本A/B: `workspaces/scripts/CHxx/<video>/content` / 音声: `workspaces/audio/final/<channel>/<video>/`
- 画像・帯・動画ドラフト: `workspaces/video/runs/<project>/`
- 投稿成果: `delivery/CHxx/CHxx-###/`
- サムネ: `workspaces/thumbnails/assets/<channel>/...`（フォルダ構成で管理）
- 投稿後指標/改善メモ: `workspaces/planning/analytics/<channel>.csv`（アナリティクスの SoT として運用する）

## 6. PDCA とログ
- すべての工程でログを残し、`ssot/history/HISTORY_codex-memory.md` に記録。  
- UI/CLI で実行したガード（SSOT guard / `planning_lint` / `workflow_precheck` など）はログディレクトリへ保存し、異常があれば即時に差し戻す。  
- Ready queue / 投稿 / 分析結果は CSV + 履歴ログの双方に記録し、次フェーズへのインプットにする。

### 6.1 サムネイル承認フロー（人間の責務）
1. **候補作成**: 管理者がサムネ画像を作成し、`workspaces/thumbnails/assets/<channel>/` に配置。各案には意図や KPI をメモする。
2. **UI で紐付け**: Thumbnail Workspace のライブラリから企画行へ紐付け。紐付け時に誰が選んだかをログに残す。
3. **承認チェック**: 投稿前に管理者が最終案を選定し、承認フラグ（チェックボックスなど）を付ける。未承認のまま投稿自動化が走ることは許可しない。
4. **変更履歴**: 承認の可否や差し替え理由を `ssot/history/HISTORY_codex-memory.md` またはサムネイルログに記録し、後から参照できるようにする。

### 6.2 投稿 Go/No-Go 判断
1. **準備完了チェック**: channels CSV 上で「台本」「音声」「動画」「サムネ」「説明文」が揃っているかを UI から確認。
2. **人間の承認**: 管理者が最終確認を行い、Go/No-Go の判断結果を channels CSV 行に記録（例: `承認者` 列、`承認日`）。承認されていない行は自動投稿キューに乗らないよう制御する。
3. **ログ保存**: 投稿実行ログ (`workspaces/logs/regression/*`) と承認ログ（CSV or HISTORY）を紐付けておき、後で「誰がいつ承認したか」を追跡できる状態にする。

## 6. PDCA とログ
- すべての工程でログを残し、`ssot/history/HISTORY_codex-memory.md` に記録。  
- UI/CLI で実行したガード（SSOT guard / `planning_lint` / `workflow_precheck` など）はログディレクトリへ保存し、異常があれば即時に差し戻す。  
- Ready queue / 投稿 / 分析結果は CSV + 履歴ログの双方に記録し、次フェーズへのインプットにする。

## 7. 処理フロー（視覚アーキテクチャ）
```mermaid
flowchart LR
    A[企画CSV\nworkspaces/planning/channels/CHxx.csv] --> B[台本A/B生成\n(script_pipeline など)]
    B --> C[TTS生成\nworkspaces/audio/final/<ch>/<vid>]
    C --> D[画像/帯生成\ncommentary_02]
    D --> E[CapCut / Remotion Draft]
    E --> F[納品ZIP / delivery]
    F --> G[投稿承認\nUI]
    G --> H[自動投稿\nAPI/CLI]
    H --> I[アナリティクス収集\nworkspaces/planning/analytics]
    I --> A
```

- 音声成果物の公式保存先: `workspaces/audio/final/<channel>/<video>/`。  
- TTSエンジン: CH01→Voicepeak（男性3, CLI）、CH06-033→VOICEVOX（青山流星）、デフォルトVoicevox。LLMは Azure gpt-5-mini 固定。  

## 8. TODO / 実装状況
| 項目 | 内容 | 状態 |
| --- | --- | --- |
| T1 | 企画CSVテンプレ統一（CH01〜CH06） | ✅ 完了（personas + templates） |
| T2 | A/B台本整備と音声Readyフラグ | ✅ 完了（OPS_AUDIO_TTS） |
| T3 | CapCutドラフト自動生成 + Remotion計画 | ⏳ 差し替え導線/Remotionパイプライン整備中 |
| T4 | 投稿自動化（承認→予約） | ⏳ 承認ログ反映と API 連携拡張が必要 |
| T5 | 分析SoT (`workspaces/planning/analytics`) 整備 | 🚧 ディレクトリ生成済み、データ投入とUI連携が未着手 |
| T6 | チャンネル横断のサムネライブラリ QA | ⏳ CH01〜CH06 の棚卸し確認を実施する |

## 9. API / CLI 依存関係（主要）
- `/api/planning/*` ↔ `planning_store` / SSOT guard ↔ `workspaces/planning/channels/CHxx.csv`
- `/api/channels/{code}/profile` ↔ `channel_profile`, `workspaces/planning/personas`
- `/api/video-production/*` ↔ `commentary_02` CLI (`generate_belt_layers.py`, `capcut_bulk_insert.py`)
- `/api/ssot/persona/{channel}` ↔ `workspaces/planning/personas/CHxx_PERSONA.md`
- 台本ステージは `PYTHONPATH=".:packages" python3 -m script_pipeline.cli run-all --channel CHxx --video NNN` を唯一の入口とし、CLI が `status.json` と `workspaces/planning/channels/CHxx.csv` を同期する。手動更新は禁止。

## 10. 絶対に忘れてはならないこと
1. **REFERENCE が最上位**：疑問があれば必ずここに立ち返り、矛盾は REFERENCE → README/OPS の順で解消する。
2. **サムネイルは人間の責務**：自動アサインは行わず、承認ログを残してから投稿へ進める。
3. **status.json が唯一の進捗 SoT**。channels CSV はミラーであり、status.json を基準に同期する。Sheets や一時ファイルで編集しない。
4. **A/B テキストを混ぜない**：表示と音声で管理ファイルを分離し、辞書/ポーズルールを厳守する。
5. **ログを残す**：ガード結果・承認判断・改善メモを `ssot/history/HISTORY_codex-memory.md` に即記録。
6. **分析で終わり分析に戻る**：`workspaces/planning/analytics/<channel>.csv` を必ず更新し、次の企画に反映する。

---

## 付録: 要点整理（再掲）
- 勝ちパターンを崩さず、全工程を自動化。サムネと最終承認は人間の責務で残す。
- CSV/フォルダ/ログで SoT を一元化して迷子を防ぐ。
- フェーズ1〜4で「分析→大量企画→A/B台本→音声→動画→投稿→アナリティクス」のループを止めない。
