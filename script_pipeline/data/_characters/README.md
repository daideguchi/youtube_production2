# Character Portraits (CH06)

- 1ファイル = 1チャンネル。`characters[].id` はシーンファイルや台詞ブロックから参照します。
- `speech_rules` は簡易ヒューリスティックで、`character_validator.py` がチェックします。
  - `required_prefix` / `required_suffix`: 台詞の1行目に一度でも含めること。
  - `required_closings`: 各文末の許可文字。
  - `forbidden_regex`: 正規表現。ヒットした行があれば警告。
  - `max_sentence_length`: 1文の最大文字数（全角ベース）。
- `relationships` は `target` が他キャラIDであることを保証し、両方向に記述するとバリデーション時に相互参照が確認されます。

> まだ仮スキーマです。`$schema` 先はメモ用途のダミー。
