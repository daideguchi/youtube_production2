# 429 Resilient Pipeline Design

## 概要

このドキュメントは、SRT→画像パイプラインの429エラー耐性向上のための設計変更を定義します。

**核心的な変更**: LLMコールを「動画1本につき1回」に削減し、429発生時は明示的に失敗させる

---

## 現状の問題

### 1. 二重LLM構造
```
SRT → llm_context_analyzer (1回) → PromptRefiner (N回) → 画像生成 (N回)
```

- `llm_context_analyzer`が既に`visual_focus`, `summary`, `role_tag`を生成
- `PromptRefiner`がcue毎に同じ情報を再加工（無駄なLLMコール）
- 動画1本で約80回のAPIコール → 429祭り

### 2. サイレント失敗
- 429でrefinerが失敗 → fallbackルートで続行（品質劣化）
- 429で画像生成が失敗 → placeholder画像で続行（ゴミ画像）
- 「どこまで成功したか」「なぜ失敗したか」が不明瞭

---

## 設計変更

### アーキテクチャ図

```
SRT Parse
    ↓
llm_context_analyzer (動画1本:1回のLLM)
    ↓ visual_focus, summary, role_tag
make_cues (セクション境界決定)
    ↓
build_prompt_from_template (決定的ロジック・LLM不要)
    ↓
nanobanana_client (完全直列・レート制限)
    ↓
    ├─[成功] → CapCut Draft
    └─[429×3連続] → QuotaExhaustedError → RUN_FAILED_QUOTA.txt + exit(1)
```

### 変更ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `src/srt2images/llm_prompt_refiner.py` | デフォルトOFF (`SRT2IMAGES_REFINE_PROMPTS=False`) |
| `src/srt2images/nanobanana_client.py` | `QuotaExhaustedError`追加、retry削減、placeholder禁止 |
| `src/srt2images/orchestration/pipeline.py` | visual_focus活用強化、例外ハンドリング追加 |
| `src/srt2images/generators.py` | 例外伝播の確保 |
| `tools/auto_capcut_run.py` | concurrencyデフォルト1に変更 |

---

## 詳細設計

### 1. llm_prompt_refiner.py

**変更箇所**: 27行目

```python
# Before
self.enabled = _env_flag("SRT2IMAGES_REFINE_PROMPTS", True)

# After
self.enabled = _env_flag("SRT2IMAGES_REFINE_PROMPTS", False)
```

**理由**: `llm_context_analyzer`の出力だけで十分。必要な場合は環境変数でONにできる。

---

### 2. nanobanana_client.py

**追加**: 新しい例外クラス

```python
class QuotaExhaustedError(Exception):
    """Gemini APIクォータ制限により処理継続不可"""
    def __init__(self, message: str, successful_count: int = 0, failed_count: int = 0):
        super().__init__(message)
        self.successful_count = successful_count
        self.failed_count = failed_count
```

**変更1**: 連続429検知

```python
# モジュールレベル
_CONSECUTIVE_429_COUNT = 0
_MAX_CONSECUTIVE_429 = 3

# _run_direct()内
if is_rate_limit:
    global _CONSECUTIVE_429_COUNT
    _CONSECUTIVE_429_COUNT += 1
    if _CONSECUTIVE_429_COUNT >= _MAX_CONSECUTIVE_429:
        raise QuotaExhaustedError(
            f"Gemini API 429エラーが{_MAX_CONSECUTIVE_429}回連続発生",
            failed_count=_CONSECUTIVE_429_COUNT
        )
```

**変更2**: retry回数削減

```python
# Before
max_retries = 5

# After
max_retries = 3
```

**変更3**: placeholder禁止（オプション）

```python
# _gen_one()のelse節
# fail_silently引数を追加して制御
if not fail_silently:
    raise RuntimeError(f"画像生成失敗: {out_path}")
else:
    _make_placeholder_png(...)  # --allow-placeholderフラグ時のみ
```

---

### 3. pipeline.py

**変更1**: プロンプト構築（251行目付近）

```python
for cue in cues:
    parts = []
    
    # llm_context_analyzerの出力を直接使用
    if cue.get("visual_focus"):
        parts.append(f"Visual Focus: {cue['visual_focus']}")
    if cue.get("summary"):
        parts.append(f"Scene: {cue['summary']}")
    if cue.get("emotional_tone"):
        parts.append(f"Tone: {cue['emotional_tone']}")
    if cue.get("role_tag"):
        role_hint = PromptRefiner().role_hints.get(cue["role_tag"], "")
        if role_hint:
            parts.append(f"Role Guidance: {role_hint}")
    
    # diversity_note等は従来通り追加
    if cue.get("diversity_note"):
        parts.append(cue["diversity_note"])
```

**変更2**: 例外ハンドリング（307行目付近）

```python
from srt2images.nanobanana_client import QuotaExhaustedError

try:
    image_generator.generate_batch(...)
except QuotaExhaustedError as e:
    fail_marker = out_dir / "RUN_FAILED_QUOTA.txt"
    fail_marker.write_text(
        f"🚨 Gemini APIクォータ制限により中断\n"
        f"エラー: {e}\n"
        f"成功画像数: {e.successful_count}\n"
        f"失敗回数: {e.failed_count}\n"
        f"タイムスタンプ: {datetime.now().isoformat()}\n",
        encoding="utf-8"
    )
    logging.error("🚨 Gemini APIクォータ制限により中断: %s", e)
    sys.exit(1)
```

---

### 4. auto_capcut_run.py

**変更**: デフォルトconcurrency

```python
# Before
ap.add_argument("--img-concurrency", type=int, default=3, ...)

# After
ap.add_argument("--img-concurrency", type=int, default=1, ...)
```

---

## 期待される効果

| 指標 | Before | After |
|-----|--------|-------|
| LLMコール/動画 | 1+2N (約80) | 1+N (約41) |
| 429リスク | 高（並列＋多数コール） | 低（直列＋半減） |
| 失敗時の挙動 | サイレント（placeholder） | 明示的（ログ+フラグ+exit） |
| デバッグ性 | 低（どこで失敗したか不明） | 高（RUN_FAILED_QUOTA.txt） |

---

## テスト計画

1. **正常系**: SRT→画像パイプラインが従来通り動作することを確認
2. **429模擬**: 環境変数でAPIキーを無効化→QuotaExhaustedErrorが投げられることを確認
3. **フラグ確認**: RUN_FAILED_QUOTA.txtが適切に出力されることを確認
4. **exit code確認**: 失敗時に`sys.exit(1)`が呼ばれることを確認

---

## 後方互換性

- `SRT2IMAGES_REFINE_PROMPTS=1` で従来のrefiner動作を有効化可能
- `--allow-placeholder` フラグ（将来追加）でplaceholder生成を許可可能
- 既存の`image_cues.json`フォーマットは変更なし