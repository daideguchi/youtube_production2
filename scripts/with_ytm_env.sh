#!/usr/bin/env bash
# Ensures all factory_commentary automation commands inherit the shared .env
# Usage: ./scripts/with_ytm_env.sh <command> [args...]
# Example: ./scripts/with_ytm_env.sh python3 scripts/check_env.py
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"

# Prefer repo-local virtualenv when present (Homebrew Python is PEP668 externally-managed).
VENV_DIR="$ROOT_DIR/.venv"
if [[ -x "$VENV_DIR/bin/python3" || -x "$VENV_DIR/bin/python" ]]; then
  export PATH="$VENV_DIR/bin:$PATH"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ .env not found at $ENV_FILE" >&2
  exit 1
fi
# Ensure repo imports work regardless of caller CWD/global PYTHONPATH.
export PYTHONPATH="$ROOT_DIR:$ROOT_DIR/packages"
# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

# Default: disable LLM-backed web search (cost control). Override per-run if needed:
#   YTM_WEB_SEARCH_PROVIDER=brave ./scripts/with_ytm_env.sh ...
: "${YTM_WEB_SEARCH_PROVIDER:=disabled}"
export YTM_WEB_SEARCH_PROVIDER

# Routing lockdown (default: ON) to prevent ad-hoc overrides that cause drift across agents.
# - Set YTM_ROUTING_LOCKDOWN=0 to temporarily restore legacy behavior.
# - Set YTM_EMERGENCY_OVERRIDE=1 for one-off debugging (not for normal ops).
: "${YTM_ROUTING_LOCKDOWN:=1}"
: "${YTM_EMERGENCY_OVERRIDE:=0}"
export YTM_ROUTING_LOCKDOWN
export YTM_EMERGENCY_OVERRIDE

# Fireworks(text) is disabled in normal ops (ban/412 + drift prevention).
# Re-enable ONLY for one-off debugging with:
#   YTM_EMERGENCY_OVERRIDE=1 YTM_DISABLE_FIREWORKS_TEXT=0
: "${YTM_DISABLE_FIREWORKS_TEXT:=1}"
export YTM_DISABLE_FIREWORKS_TEXT

# Optional: numeric slots (operator-friendly; avoids editing configs).
#
# - LLM model slot (what model code each tier uses):
#     LLM_MODEL_SLOT   (configs/llm_model_slots.yaml)
# - LLM exec slot (where/how it runs: api/codex exec/think/agent/failover):
#     LLM_EXEC_SLOT    (configs/llm_exec_slots.yaml)
#
# Examples:
#   ./scripts/with_ytm_env.sh --llm-slot 2 python3 ...
#   ./scripts/with_ytm_env.sh --exec-slot 3 python3 ...
#   ./scripts/with_ytm_env.sh x3 2 python3 ...   # shorthand: xN=exec slot, N=model slot
while [[ $# -ge 1 ]]; do
  case "$1" in
    --llm-slot|--model-slot)
      if [[ $# -lt 2 ]]; then
        echo "❌ Missing value for $1 (expected an integer slot id)" >&2
        exit 2
      fi
      if [[ ! "$2" =~ ^[0-9]+$ ]]; then
        echo "❌ Invalid LLM slot: $2 (expected an integer)" >&2
        exit 2
      fi
      export LLM_MODEL_SLOT="$2"
      shift 2
      ;;
    --llm-slot=*|--model-slot=*)
      SLOT_VAL="${1#*=}"
      if [[ ! "$SLOT_VAL" =~ ^[0-9]+$ ]]; then
        echo "❌ Invalid LLM slot: $SLOT_VAL (expected an integer)" >&2
        exit 2
      fi
      export LLM_MODEL_SLOT="$SLOT_VAL"
      shift 1
      ;;
    --exec-slot|--llm-exec-slot)
      if [[ $# -lt 2 ]]; then
        echo "❌ Missing value for $1 (expected an integer slot id)" >&2
        exit 2
      fi
      if [[ ! "$2" =~ ^[0-9]+$ ]]; then
        echo "❌ Invalid exec slot: $2 (expected an integer)" >&2
        exit 2
      fi
      export LLM_EXEC_SLOT="$2"
      shift 2
      ;;
    --exec-slot=*|--llm-exec-slot=*)
      EXEC_VAL="${1#*=}"
      if [[ ! "$EXEC_VAL" =~ ^[0-9]+$ ]]; then
        echo "❌ Invalid exec slot: $EXEC_VAL (expected an integer)" >&2
        exit 2
      fi
      export LLM_EXEC_SLOT="$EXEC_VAL"
      shift 1
      ;;
    x*)
      EXEC_VAL="${1#x}"
      if [[ "$EXEC_VAL" =~ ^[0-9]+$ ]]; then
        export LLM_EXEC_SLOT="$EXEC_VAL"
        shift 1
        continue
      fi
      break
      ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then
        export LLM_MODEL_SLOT="$1"
        shift 1
        continue
      fi
      break
      ;;
  esac
done

# Hard-stop on forbidden overrides (drift prevention).
if [[ "${YTM_ROUTING_LOCKDOWN}" != "0" && "${YTM_EMERGENCY_OVERRIDE}" == "0" ]]; then
  # Hard-stop on legacy model-pinning env vars (drift prevention / operator confusion).
  # Model selection must be done via slots/codes (LLM_MODEL_SLOT + image_model_slots + presets/templates),
  # not via ad-hoc "MODEL" env vars.
  if [[ -n "${GEMINI_MODEL:-}" ]]; then
    echo "❌ [LOCKDOWN] GEMINI_MODEL is forbidden (legacy/unused; causes model confusion)." >&2
    echo "    remove it from: $ENV_FILE" >&2
    echo "    use:" >&2
    echo "      - text LLM: LLM_MODEL_SLOT (configs/llm_model_slots.yaml)" >&2
    echo "      - video images: packages/video_pipeline/config/channel_presets.json" >&2
    echo "      - thumbnails: workspaces/thumbnails/templates.json" >&2
    echo "      - one-off thumbnail override: IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN=gemini_3_pro_image_preview ..." >&2
    echo "    emergency: YTM_EMERGENCY_OVERRIDE=1（この実行だけ例外。通常運用では使わない）" >&2
    exit 3
  fi
  # SSOT integrity guard: under lockdown we do not allow ad-hoc edits to model routing configs.
  # (This prevents "someone rewrote the model YAML" drift across agents.)
  if command -v git >/dev/null 2>&1; then
    if ! git -C "$ROOT_DIR" diff --quiet -- "configs/llm_task_overrides.yaml" 2>/dev/null; then
      echo "❌ [LOCKDOWN] Uncommitted changes detected: configs/llm_task_overrides.yaml" >&2
      echo "    モデル/タスク上書きの書き換えは禁止です。slot/code 運用に戻し、revert してください。" >&2
      echo "    emergency: YTM_EMERGENCY_OVERRIDE=1（この実行だけ例外。通常運用では使わない）" >&2
      exit 3
    fi
  fi
  # Hard-ban: azure_gpt5_mini must never be selected via task overrides (fallbackでも不可).
  if [[ -f "$ROOT_DIR/configs/llm_task_overrides.yaml" ]]; then
    if grep -qE "az-gpt5-mini-1|azure_gpt5_mini" "$ROOT_DIR/configs/llm_task_overrides.yaml"; then
      echo "❌ [LOCKDOWN] Forbidden model key detected in configs/llm_task_overrides.yaml: az-gpt5-mini-1" >&2
      echo "    このモデルは使用禁止です（fallbackでも不可）。slot/code のみで運用し、設定を戻してください。" >&2
      exit 3
    fi
  fi
  if [[ -n "${LLM_MODE:-}" ]]; then
    echo "❌ [LOCKDOWN] LLM_MODE is forbidden. Use LLM_EXEC_SLOT / --exec-slot instead." >&2
    exit 3
  fi
  if [[ -n "${LLM_API_FAILOVER_TO_THINK:-}" || -n "${LLM_API_FALLBACK_TO_THINK:-}" ]]; then
    echo "❌ [LOCKDOWN] LLM_API_FAILOVER_TO_THINK is forbidden. Use LLM_EXEC_SLOT (e.g. slot 5 = failover OFF)." >&2
    exit 3
  fi
  FORCE_ALL="${LLM_FORCE_MODELS:-${LLM_FORCE_MODEL:-}}"
  if [[ -n "${FORCE_ALL}" && ! "${FORCE_ALL}" =~ ^[0-9]+$ ]]; then
    echo "❌ [LOCKDOWN] LLM_FORCE_MODELS is forbidden. Use LLM_MODEL_SLOT / --llm-slot instead." >&2
    exit 3
  fi
  if [[ -n "${LLM_FORCE_TASK_MODELS_JSON:-}" ]]; then
    echo "❌ [LOCKDOWN] LLM_FORCE_TASK_MODELS_JSON is forbidden. Use SSOT task routing + slots instead." >&2
    exit 3
  fi
  if [[ -n "${YTM_DISABLE_FIREWORKS_TEXT:-}" ]]; then
    _v="$(printf '%s' "${YTM_DISABLE_FIREWORKS_TEXT:-}" | tr '[:upper:]' '[:lower:]')"
    if [[ "${_v}" == "" || "${_v}" == "0" || "${_v}" == "false" || "${_v}" == "no" || "${_v}" == "off" ]]; then
      echo "❌ [LOCKDOWN] Fireworks(text) must remain disabled in normal ops." >&2
      echo "    debug only: YTM_EMERGENCY_OVERRIDE=1 YTM_DISABLE_FIREWORKS_TEXT=0" >&2
      exit 3
    fi
  fi
  if [[ -n "${YTM_SCRIPT_ALLOW_OPENROUTER:-}" ]]; then
    _v="$(printf '%s' "${YTM_SCRIPT_ALLOW_OPENROUTER:-}" | tr '[:upper:]' '[:lower:]')"
    if [[ "${_v}" != "" && "${_v}" != "0" && "${_v}" != "false" && "${_v}" != "no" && "${_v}" != "off" ]]; then
      echo "❌ [LOCKDOWN] YTM_SCRIPT_ALLOW_OPENROUTER is forbidden. Use LLM_MODEL_SLOT (slotの script_allow_openrouter) instead." >&2
      echo "    emergency: YTM_EMERGENCY_OVERRIDE=1（この実行だけ例外。通常運用では使わない）" >&2
      exit 3
    fi
  fi
  if [[ -n "${LLM_ENABLE_TIER_CANDIDATES_OVERRIDE:-}" ]]; then
    _v="$(printf '%s' "${LLM_ENABLE_TIER_CANDIDATES_OVERRIDE:-}" | tr '[:upper:]' '[:lower:]')"
    if [[ "${_v}" != "" && "${_v}" != "0" && "${_v}" != "false" && "${_v}" != "no" && "${_v}" != "off" ]]; then
      echo "❌ [LOCKDOWN] LLM_ENABLE_TIER_CANDIDATES_OVERRIDE is forbidden. Use slots/codes only (no hidden tier swap)." >&2
      echo "    emergency: YTM_EMERGENCY_OVERRIDE=1（この実行だけ例外。通常運用では使わない）" >&2
      exit 3
    fi
  fi
  # Hard-stop: persistent image model overrides in `.env` are a major drift source.
  # Normal ops must use SoT configs (preset/template). One-off comparisons are OK as per-run env prefix.
  if grep -qE '^[[:space:]]*IMAGE_CLIENT_FORCE_MODEL_KEY([A-Z0-9_]+)?=' "$ENV_FILE" 2>/dev/null; then
    echo "❌ [LOCKDOWN] IMAGE_CLIENT_FORCE_MODEL_KEY* must not be set in .env: $ENV_FILE" >&2
    echo "    found:" >&2
    grep -nE '^[[:space:]]*IMAGE_CLIENT_FORCE_MODEL_KEY([A-Z0-9_]+)?=' "$ENV_FILE" 2>/dev/null | sed -E 's/=.*$/=<set>/' >&2 || true
    echo "    SoT:" >&2
    echo "      - video images: packages/video_pipeline/config/channel_presets.json" >&2
    echo "      - thumbnails:   workspaces/thumbnails/templates.json" >&2
    echo "    one-off (per-run):" >&2
    echo "      - IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN=f-1 ./ops video ..." >&2
    echo "      - IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN=f-4 ./ops thumbnails ..." >&2
    exit 3
  fi
  # Policy: Gemini 3 image models are forbidden for *video images* (visual_image_gen),
  # but allowed for thumbnails (thumbnail_image_gen).
  for v in IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN IMAGE_CLIENT_FORCE_MODEL_KEY_IMAGE_GENERATION IMAGE_CLIENT_FORCE_MODEL_KEY; do
    _val="${!v:-}"
    if [[ -z "${_val}" ]]; then
      continue
    fi
    case "${_val}" in
      *gemini-3*|*gemini_3* )
        echo "❌ [LOCKDOWN] Forbidden image model override detected: ${v}=${_val}" >&2
        echo "    動画内画像（visual_image_gen）での Gemini 3 系は使用禁止です。img-gemini-flash-1（=g-1）等の許可モデルを使ってください。" >&2
        echo "    ※サムネ（thumbnail_image_gen）で Gemini 3 を使う場合は IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN を使う（global/visual には入れない）。" >&2
        echo "    debug only: YTM_EMERGENCY_OVERRIDE=1（この実行だけ例外。通常運用では使わない）" >&2
        exit 3
        ;;
    esac
  done
  # Also forbid passing the override via `env FOO=... command` style args.
  for _arg in "$@"; do
    case "${_arg}" in
      IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN=*gemini-3*|IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN=*gemini_3*|IMAGE_CLIENT_FORCE_MODEL_KEY_IMAGE_GENERATION=*gemini-3*|IMAGE_CLIENT_FORCE_MODEL_KEY_IMAGE_GENERATION=*gemini_3*|IMAGE_CLIENT_FORCE_MODEL_KEY=*gemini-3*|IMAGE_CLIENT_FORCE_MODEL_KEY=*gemini_3* )
        echo "❌ [LOCKDOWN] Forbidden image model override detected in command args: ${_arg}" >&2
        echo "    動画内画像（visual_image_gen）での Gemini 3 系は使用禁止です。img-gemini-flash-1（=g-1）等の許可モデルを使ってください。" >&2
        echo "    ※サムネ（thumbnail_image_gen）で Gemini 3 を使う場合は IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN を使う（global/visual には入れない）。" >&2
        echo "    debug only: YTM_EMERGENCY_OVERRIDE=1（この実行だけ例外。通常運用では使わない）" >&2
        exit 3
        ;;
    esac
  done
  for v in \
    YTM_CODEX_EXEC_DISABLE \
    YTM_CODEX_EXEC_ENABLED \
    YTM_CODEX_EXEC_PROFILE \
    YTM_CODEX_EXEC_SANDBOX \
    YTM_CODEX_EXEC_TIMEOUT_S \
    YTM_CODEX_EXEC_MODEL \
    YTM_CODEX_EXEC_EXCLUDE_TASKS \
    YTM_CODEX_EXEC_ENABLE_IN_PYTEST; do
    if [[ -n "${!v:-}" ]]; then
      echo "❌ [LOCKDOWN] ${v} is forbidden. Use LLM_EXEC_SLOT + configs/codex_exec.yaml instead." >&2
      exit 3
    fi
  done
fi
if [[ $# -eq 0 ]]; then
  echo "✅ Environment loaded. Run commands like: ./scripts/with_ytm_env.sh python3 ..." >&2
  exit 0
fi
exec "$@"
