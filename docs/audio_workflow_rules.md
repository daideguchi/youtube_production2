# Audio Generation Workflow Rules (Iron Rules)

## 1. Actors (The Trinity)
1. **User (Operator)**: The Human (You) or the Chat Interface (Me operating on your behalf). Controls the flow.
2. **AI Agent (Inference Engine)**: The Logic Code (`auditor.py`) that audits reads, context, and pauses. It "Thinks".
3. **API (Execution Engine)**: The low-level services (Voicevox/Voicepeak) that generate raw audio. It "Acts".

## 2. Routes (The Two Paths)

### Route 1: Batch LLM Automation (Non-Interactive)
*   **Definition**: API LLMを叩いて非対話で成果物を仕上げる。
*   **Command**: `python scripts/run_route1_batch.py` (Currently Disabled/Deprecated in favor of Agent Mode)
*   **Mechanism**: Fully automated. The script calls the LLM API for every decision (readings, pauses, audits).
*   **Cost**: High (API costs scales with volume).
*   **Role**: Unattended "Factory Mode".

### Route 2: AI Agent Manual (Interactive) [DEFAULT]
*   **Definition**: API LLMを使うのではなく、AIエージェントが手動でスクリプト実行し、AIエージェントが推論しながら成果物を仕上げる。
*   **Command**: `python scripts/run_route2_agent.py`
*   **Mechanism**: 
    1. **Manual Execution**: The Agent (or User) runs the script.
    2. **Local Inference**: Uses Local Twin-Engine (MeCab/Voicevox) instead of API LLM.
    3. **Agent Inference**: The Agent "thinks" (checks logs/results) and intervenes if necessary.
*   **Cost**: **ZERO** (No API calls).
*   **Role**: High-Quality, Cost-Free "Agentic Mode".

## 3. Zero Cost Policy (Iron Definition)
**"Zero Cost" specifically means: NOT utilizing pay-per-use API LLMs (e.g., GPT-4, GPT-4o, GPT-5-mini via Azure/OpenAI).**

*   **ALLOWED (Zero Cost)**:
    *   Running scripts locally.
    *   Using Local Engines (Voicevox Docker, Voicepeak).
    *   Using MeCab (Local CPU).
    *   **AI Agent's Time & Labor**: My thinking, verifying, and typing are considered "Zero Cost".
*   **FORBIDDEN (Costly)**:
    *   Any `requests.post()` to `openai.com` or `azure.com`.
    *   Automated LLM Audit loops.

## 4. Strict Entry Points
*   ✅ `scripts/run_route1_batch.py`
*   ✅ `scripts/run_route2_agent.py`
*   ⛔ `scripts/_core_audio.py` (INTERNAL USE ONLY - Do not run directly)
*   ⛔ `scripts/run_tts.py` (BACKEND ENGINE - Do not run directly)

## 4. Default Policy
**Route 2 is the STRICT DEFAULT.**
Any request to "Generate Audio" implies Route 2 unless "Batch Route 1" is explicitly requested.
