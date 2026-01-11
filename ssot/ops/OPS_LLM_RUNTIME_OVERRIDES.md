# OPS_LLM_RUNTIME_OVERRIDES — 設定ファイルを編集せずに「この実行だけ」LLMを調整する

目的:
- `configs/llm_router.yaml` / `configs/llm_task_overrides.yaml`（SoT）を**無断で編集しない**
- 事故を避けつつ、**1回の実行だけ**モデル/温度等を切り替えてデバッグできるようにする

関連:
- `ssot/ops/OPS_ENV_VARS.md`
- `ssot/ops/OPS_LLM_MODEL_CHEATSHEET.md`

---

## 1) 原則（固定）

- **通常運用（default）**: `YTM_ROUTING_LOCKDOWN=1`（ON）
  - ルーティングの上書き経路を潰し、エージェント間のブレを防ぐ
  - **モデル名/タスクpinの ad-hoc override は禁止**
- 例外は **緊急デバッグのみ**: `YTM_EMERGENCY_OVERRIDE=1`
  - 「このプロセス/この実行だけ」ロックダウンを解除する
  - 終わったら必ず戻す（恒久運用しない）

---

## 2) 推奨（安全）: 数字スロットで切替える

### 2.1 LLMモデル（tier→モデル）: `LLM_MODEL_SLOT`
- 正本: `configs/llm_model_slots.yaml`（必要なら `configs/llm_model_slots.local.yaml` で**ローカルだけ**上書き）
- 使い方（例）:
  - `LLM_MODEL_SLOT=2 ./ops ...`
  - `./scripts/with_ytm_env.sh 2 python3 ...`（先頭が整数ならslot扱い）

### 2.2 実行モード（api/think/codex/agent）: `LLM_EXEC_SLOT`
- 正本: `configs/llm_exec_slots.yaml`
- 使い方（例）:
  - `./scripts/with_ytm_env.sh --exec-slot 0 python3 ...`（API）
  - `./scripts/with_ytm_env.sh --exec-slot 3 python3 ...`（THINK）

---

## 3) 緊急デバッグ（非推奨）: モデル/オプションの“この実行だけ”上書き

ロックダウンON（既定）では **停止**する。使うなら必ず `YTM_EMERGENCY_OVERRIDE=1` をセットする。

### 3.1 モデル上書き（タスク別）
- env:
  - `LLM_FORCE_TASK_MODELS_JSON='{"task":["model_code_1","model_code_2"]}'`
- CLI（入口が env をセット）:
  - `python3 scripts/ops/script_runbook.py resume --channel CHxx --video NNN --llm-task-model task=model_code_1`

### 3.2 オプション上書き（タスク別; 温度など）
- env:
  - `LLM_FORCE_TASK_OPTIONS_JSON='{"task":{"temperature":0.0}}'`
- 用途例:
  - JSONの形を崩さないように `temperature=0.0` に落とす
  - 特定モデルで `extra_body.reasoning` を無効化する 等

注:
- 上書きは **task overrides の上**に overlay される（ただし、呼び出し側が明示した引数が最優先）。
- オプションは最終的に `sanitize_params`（モデルcapabilityガード）を通る。

