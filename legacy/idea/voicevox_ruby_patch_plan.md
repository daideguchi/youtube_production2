# VOICEVOX 読み誤り対策（Ruby→KanaPatch 方式）ブレストログ

- 日付: 2025-12-11
- トピック: 危険語だけを LLM でルビ推定し、KanaPatch で局所修正するシンプル設計
- 背景/目的:
  - Voicevox の漢字文脈推定を活かしつつ、危険語の誤読だけを確実に潰す。
  - コスト・予算ロジックを極力排してシンプルにする。

## 最新仕様（実装決定版・2025-12-11）
- Ruby LLM は surface 単位で VOICEVOX 読みの OK/NG を判定し、NG のときだけ correct_kana を返す。文の書き換えは禁止。
- 1動画あたり Ruby LLM は最大2コール / surface 最大40件（batch20×2）で構造固定。パラメータではなく仕様。
- 送信対象は hazard レベルAのみ（英数字/数値/未知語/固有名詞/ハザード辞書）。レベルBは `include_level_b=True` のときだけ。
- Voicevox の聴きやすさ揺れ（コオテエ/キョオ等）は正規化＋trivial判定で LLM に送らない。禁止語（今日/今/助詞/1文字等）は全経路で除外。
- LLM I/O: 入力 items[{surface,mecab_kana,voicevox_kana,contexts,hazard_tags,positions}], 出力 items[{surface,decision=ok|ng|skip,correct_kana?}]; decision!=ng は無視。correct_kana はカタカナのみをローカル検証してから KanaPatch 化。
- KanaPatch は positions の全出現に適用し、accent_phrases で align。失敗時は長さクリップ＋fallback理由をログ。
- vocab LLM は本番パイプラインから切り離し（enable_vocab=False デフォルト）。辞書育成はオフラインで行う前提。

## アーキテクチャ図（テキスト）
```
┌─────────────────────────────┐
│ Aテキスト (assembled)       │
└─────────────┬───────────────┘
              │
              v
┌─────────────────────────────┐
│ MeCabトークン化 + SRT分割    │
└─────────────┬───────────────┘
              │
              v
┌─────────────────────────────┐
│ Twin-Engine 差分チェック     │
│ (mecab_kana vs voicevox_kana)│
└───────┬─────────┬───────────┘
        │一致      │不一致
        │(安全)    v
        │       ┌─────────────────────────────┐
        │       │ 危険トークン抽出             │
        │       │ - 英字/記号/数字             │
        │       │ - 未知語/複数読み            │
        │       │ - hazard辞書ヒット           │
        │       │ - 差分の漢字                 │
        │       │ - 禁止語はここで除外         │
        │       └──────────┬──────────────────┘
        │                  v
        │       ┌─────────────────────────────┐
        │       │ Ruby LLM (カナだけ返す)      │
        │       └──────────┬──────────────────┘
        │                  v
        │       ┌─────────────────────────────┐
        │       │ KanaPatch 生成               │
        │       │ - mora範囲: alignで優先      │
        │       │ - 失敗時: 長さでクリップ     │
        │       └──────────┬──────────────────┘
        │                  v
        │       ┌─────────────────────────────┐
        │       │ accent_phrases にパッチ適用  │
        │       │ (b_textは漢字のまま)         │
        │       └──────────┬──────────────────┘
        │                  v
        │       ┌─────────────────────────────┐
        │       │ VOICEVOX 合成                │
        │       └─────────────────────────────┘
        v（全体ログ出力: patches, budget, reason 等）
```

```
[ Aテキスト ]
    |
    v
[MeCabトークン化 + SRT分割]
    |
    v
[Twin-Engine 差分チェック]
    |  (voicevox_kana vs mecab_kana)
    |-- 一致 -> 安全ブロック (LLM不要)
    |-- 不一致 -> audit_needed ブロック
           |
           v
    [危険トークン抽出]
       - 英字/記号/数字
       - 未知語/複数読み
       - ハザード辞書ヒット
       - Twin-Engine 差分の漢字
       - 禁止語はここで除外
           |
           v
    [Ruby LLM]
       入力: 危険トークン + 前後文脈
       出力: カタカナ読み（空なら変更なし）
           |
           v
    [KanaPatch 生成]
       - align_moras_with_tokens で範囲決定
       - 失敗時はトークン長ベースでクリップ
       - 同一トークンは後勝ちマージ
           |
           v
    [/synthesis 前の accent_phrases へ apply_kana_patches]
           |
           v
    [Voicevox 合成]
       - 表示テキスト(b_text)は漢字維持
       - パッチで読みのみ局所修正
           |
           v
    (任意) 残差に語彙LLM -> 辞書更新
```

## 危険語判定ルール（送る/送らない）
- 送る: Twin-Engine 差分ブロック内で、以下に該当
  - 英字/記号/数字を含むトークン
  - 漢字トークンで MeCab 未知語/複数読みフラグ
  - ハザード辞書ヒット
  - Twin-Engine で読み差分がある漢字トークン
- 送らない: 禁止語（今日/今/1文字/助詞/時制系など）、短文トリビアル一致

## トリビアル判定（is_trivial_diff の挙動メモ）
- 前処理: 全角数字/句読点/空白を除去、ひらがな→カタカナ正規化
- True になるケース（LLMスキップ）
  - 正規化後に完全一致
  - 長音揺れ（「ー」を抜いたら一致）
  - 文字数差 ≤1 かつ 異なる位置が1箇所だけ
- 具体例
  - 「キョウ」vs「キョオ」→ 1文字差 → True（スキップ）
  - 「高校」vs「コーコー」→ 長音揺れ → True（スキップ）
  - 「ツライ」vs「カライ」→ 2文字差 → False（監査対象）
  - 「オコリ」vs「イカリ」→ 2文字差 → False（監査対象）

## hazard / 禁止語 / 辞書 の棲み分け
| 種類      | 例               | LLM送信 | 辞書保存 | 手動カナ指定 |
|---------|-----------------|--------|--------|------------|
| hazard語 | 固有名詞/外来語など誤読しやすいもの | 送る     | 保存OK | OK |
| 禁止語     | 今日, 今, 1文字, 助詞/時制系など文脈依存 | 送らない | 保存NG | OK |
| 通常語     | 上記以外            | 条件付き | 保存OK | OK |

## モーラ範囲の扱い
- 優先: align_moras_with_tokens(accent_phrases, tokens) の範囲を使用
- フォールバック: アライン不能時はトークン長ベースでクリップ（1文字=1モーラ換算）
- パッチ生成: KanaPatch に読み文字列を1文字=1モーラで展開して埋める（後勝ちマージ）

## ログ（拡張案）
- `tts_voicevox_reading.jsonl` に
  - `timestamp, channel, video, block_id, token_index, surface, mecab_kana, voicevox_kana, ruby_kana, after_kana, mora_range, source, reason`
  - reason は hazard/unknown/差分/予算超過などを区別しておく
- 語彙LLMを走らせる場合は従来の辞書反映ログを維持

## フォールバック/停止方針
- **LLMエラーは即停止**（例外を投げてパイプライン中断）。サイレントフォールバック禁止。
- LLM返答が禁止語/空/非カナならパッチに採用しない（ログのみ）。
- align失敗時のモーラ範囲だけは長さクリップの機械フォールバックを許容（音素位置合わせ用）。

## 決定事項 / 次のアクション
- Ruby→KanaPatch 方式で実装する（b_textは漢字維持）
- 禁止語フィルタを LLM送信・返答適用・辞書適用の全経路で適用
- モーラ範囲のフォールバックとパッチマージルールを実装
- ログ拡充（上記フィールド追加）と reason 整備（budget_exceeded も1行で記録）
- ソフト上限は「非常停止用」に薄く残す（calls/terms しきい値超過で budget_exceeded を立てて LLMを打ち切り、Voicevoxデフォルトへ）

## LLM判定ロジック再設計（CH08ログを踏まえた根本整理）
- 前提: 1動画あたりの LLM コール数を O(1)（理想1〜2回）に設計上固定する。パラメータのキャップではなく仕様として拘束。

- コール数の仕様
  - Ruby判定用 LLM: 1〜2コール/動画（itemsが多い場合でも分割は最大2）。  
  - vocab/tts_reading: 本番パイプラインでは 0〜1コールに抑える。語彙学習は極力オフライン/別バッチに寄せる。

- LLMの責務
  - 文を直させない。  
  - surface単位で「Voicevoxの読みがOKか/NGか」を判定し、NGなら正しいカタカナを1つ返すだけ。

- 危険語レベル定義
  - レベルA（必須送信）: 意味が変わる多義語（例: 一行/一拍/一言）、未知語・固有名詞、英数字/数値/URL、hazard辞書ヒット。  
  - レベルB（余裕時のみ）: Twin-Engine差分ありだが hazard弱・一般形容詞/動詞など（静か/書く/引く…）。  
  - レベルC（送らない）: Voicevoxが問題なく読める汎用語（静かです/書きます/続きます…）。差分があってもスキップ。

- surface単位集約
  - 文単位ではなく、動画全体で surface ごとに1レコードに集約（代表文脈を2〜3件だけ保持）。  
  - 同じ surface が何十回出ても LLM に送るのは1回。返答を全出現にパッチ適用。

- Voicevox揺れの前処理
  - voicevox_kana_raw を長音/母音揺れ等で正規化 → voicevox_kana_norm。  
  - voicevox_kana_norm == mecab_kana なら自動OKで LLM 候補から外す（コオテエ/キョオ系をここで殺す）。

- LLM入出力スキーマ（例）
  - 入力 item: {surface, mecab_kana_candidates, voicevox_kana_norm_examples, hazard_level, hazard_tags, contexts[]}  
  - 出力 item: {surface, judgement: ok|ng, correct_kana?, reason?, confidence?}
  - judgement が ng のときだけ correct_kana を返し、後段でパッチに使う。

- 出力の検証（誤修正防止）
  - 辞書チェック: correct_kana が MeCab候補または hazard辞書の正解読みと整合しない場合は破棄。  
  - 禁止語フィルタ: correct_kana に禁止語（今日/今/助詞/1文字/時制系）が混ざれば破棄。  
  - モーラ長: accent_phrases のモーラ数と ±2 以上ずれる場合は不採用。  
  - confidence: high 以外はデフォルト不採用（Voicevox任せ）。  
  - 採用分だけ KanaPatch 化、それ以外はログのみ。

- バッチ設計と上限
  - surface 上限 S_max（例: 40）を設け、hazardレベルAを優先してスコア順に選ぶ。レベルAだけで超える場合は低スコアを送らない。  
  - バッチサイズ B_size（例: 32）。calls_ruby = ceil(min(selected_surfaces, S_max)/B_size) を常に <= 2 に設定。  
  - レベルBは枠が余ったときだけ上位から詰める。枠が無ければゼロでもよい。

- ログと受入基準
  - ログに surface単位の選抜数/採用数、llm_calls_ruby/vocab、mecab_kana/voicevox_kana_norm/ruby_kana、reason を記録。  
  - 受入基準（例: CH08-001長尺でも）:  
    - llm_calls_ruby <= 2, llm_calls_vocab <= 1（理想0）  
    - llm_surface_selected <= S_max（例: 40）、レベルC語（静か/書く/続く…）が送られていない  
    - 聞きやすさ揺れ（コオテエ/キョオ等）は前処理で自動OK、LLM候補に乗らない  
    - 危険語（例: 一行/一拍/一言/辛い/怒り等）のみがパッチ適用対象になっていること

- 実装ステップ（骨子）
  1) 危険トークン抽出にレベルA/B/C付与を導入。  
  2) surface 集約レイヤーを追加し、代表文脈・hazard_tags・スコアを保持。  
  3) S_max/B_size による選抜と calls_ruby<=2 をコードで保証。  
  4) LLM I/O スキーマを上記に揃え、Ruby判定を1〜2コールに固定。  
  5) 出力検証（辞書・禁止語・モーラ長・confidence）を auditor で実装。  
  6) ログ拡張（surface選抜/採用件数、calls数、reason）と集計スクリプト更新。  

## LLM使用量を根本から絞るための判定ロジック整理（価値を保ったまま削る）
- **レベル分け（A/B）で送信対象を明確化**  
  - レベルA（必送）: hazard辞書ヒット、英数字混在、数値フォーマット（URL/メール/バージョン/0.003/2024年など）  
  - レベルB（余力があれば）: Twin-Engine差分のみの漢字（hazardなし）。デフォルトは送らない。  
  - 禁止語は全経路で除外（送らない・保存しない・返答も適用しない）。
- **重複削減**  
  - 同じ surface は動画内で1回だけ送る（代表文脈を数件だけ添付）。  
  - ブロック内に同一hazardが複数あっても1件にまとめる。
- **スコアリングで送信枠を自動配分**  
  - レベルAでも優先順位をつける: 英数字・数値 > hazard辞書 > その他。  
  - レベルBはスコア順で上位K件だけ送る（Kはブロック数に比例して決める）。  
  - Twin-Engine差分が多いときはスコアしきい値を自動で引き上げ、低スコアはVoicevoxに任せる。
- **バッチ設計の見直し**  
  - Ruby: レベルAのみを20件/回程度でバッチ化。上限コール数はハード固定（非常停止）だが、送信対象自体を優先順位で削るので「上限頼み」にならない。  
  - vocab（辞書学習）はレベルAのみ、かつ hazardが少ないときだけ実行するモードにし、長尺ではデフォルト抑制。  
  - 返答は surface 単位でまとめ、同一surfaceへの重複適用は避ける。
- **フォールバック方針**  
  - レベルBが落ちた場合は Voicevox 既定に任せる（ログだけ残す）。  
  - LLMエラー時は即停止（設計どおり）。  
  - align失敗のみ機械フォールバック（長さクリップ）。

## 担当 / 関係者
- Codex

## メモ
- 予算ロジックは外しつつ、緊急停止用の soft limit を薄く残す。
- ゲート（hazard + Twin-Engine）と禁止語フィルタで絞るシンプル設計。
