# 作業コンテキスト記録：動画制作パイプラインの徹底型化

## 1. 現状の到達点 (As-Is)

「生の辞書データ」への依存を排除し、**型（Schema）× 定義（SSOT）× 変換（Adapter）** による堅牢な動画生成パイプラインを構築完了。

- **ドメイン層 (`src/core/domain/`)**:
  - `style_schema.py`: 動画の見た目（フォント、色、配置など）を論理的に定義。
  - `channel_schema.py`: チャンネル設定を構造化。
  - `asset_schema.py`: 中間素材（画像指示）のデータ構造を定義。
- **SSOT層 (`config/`)**:
  - `master_styles_v2.json`: 「人生の道標」等の具体的なデザイン定義の唯一の正解。
  - `channel_presets.json`: 各チャンネルがどのスタイルを使うかの設定。
- **アダプター層 (`src/adapters/`)**:
  - `capcut/style_mapper.py`: 論理定義を CapCut の内部パラメータ（座標反転など）に変換。
- **実行層 (`tools/`)**:
  - `capcut_bulk_insert.py`: 上記を利用して動画ドラフトを自動生成するようにリファクタリング済み。

## 2. 次のアクション (To-Be)

この「型」の基盤を活用し、以下の展開が可能。

1.  **他チャンネルへの横展開**:
    - `master_styles_v2.json` に新しいスタイル（例: 哲学系、シニア恋愛）を追加するだけで、コード修正なしに自動生成が可能。
2.  **UIとの完全連携**:
    - FastAPI で `ChannelConfig` や `VideoStyle` モデルを直接利用し、設定画面を自動生成・バリデーション。
3.  **Remotion への展開**:
    - `RemotionStyleAdapter` を作成するだけで、同じ `master_styles_v2.json` から React コード用のスタイル定数を生成可能。

## 3. 開発者が参照すべきドキュメント

- `spec/domain_schema_spec.md`: 型定義とアダプターの仕様詳細。
- `spec/データモデル定義書.md`: システム全体のデータ構造俯瞰。

## 4. 直近の検証結果

- コマンド: `python3 tools/capcut_bulk_insert.py ...`
- 結果: `人生の道標_191_3_SchemaTest` ドラフトが正常生成され、字幕スタイル・帯位置・エフェクト調整がSSOT通りに反映されたことを確認済み。
