import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchCodexSettings, fetchLlmSettings, fetchLlmModelScores, updateCodexSettings, updateLlmSettings } from "../api/client";
import { UiParamsPanel } from "../components/UiParamsPanel";
import type {
  CodexReasoningEffort,
  CodexSettings,
  LlmModelInfo,
  LlmSettings,
  PhaseDetail,
  PhaseModel,
} from "../api/types";
import llmModelsFallback from "../data/llm_models_fallback.json";

export function SettingsPage() {
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [codexSettings, setCodexSettings] = useState<CodexSettings | null>(null);
  const [provider, setProvider] = useState<"openai" | "openrouter">("openai");
  const [openaiModel, setOpenaiModel] = useState("");
  const [openrouterModel, setOpenrouterModel] = useState("");
  const [openaiKeyInput, setOpenaiKeyInput] = useState("");
  const [openrouterKeyInput, setOpenrouterKeyInput] = useState("");
  const [phaseModels, setPhaseModels] = useState<Record<string, PhaseModel>>({});
  const [phaseDetails, setPhaseDetails] = useState<Record<string, PhaseDetail>>({});
  const [newPhaseId, setNewPhaseId] = useState("");
  const [newPhaseLabel, setNewPhaseLabel] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [refreshingOpenrouterModels, setRefreshingOpenrouterModels] = useState(false);
  const [openaiOptions, setOpenaiOptions] = useState<string[]>([]);
  const [openrouterOptions, setOpenrouterOptions] = useState<string[]>([]);
  const [modelScores, setModelScores] = useState<LlmModelInfo[]>([]);
  const [modelError, setModelError] = useState<string | null>(null);
  const [codexProfile, setCodexProfile] = useState("");
  const [codexModelOverride, setCodexModelOverride] = useState("");
  const [codexCliProfile, setCodexCliProfile] = useState("");
  const [codexCliModel, setCodexCliModel] = useState("");
  const [codexReasoningEffort, setCodexReasoningEffort] = useState<CodexReasoningEffort>("xhigh");
  const [codexLoading, setCodexLoading] = useState(true);
  const [codexSaving, setCodexSaving] = useState(false);
  const [codexStatusMessage, setCodexStatusMessage] = useState<string | null>(null);
  const [codexErrorMessage, setCodexErrorMessage] = useState<string | null>(null);

  const applySettingsResponse = useCallback((data: LlmSettings) => {
    setSettings(data);
    setProvider(data.llm.caption_provider);
    setOpenaiModel(data.llm.openai_caption_model ?? "");
    setOpenrouterModel(data.llm.openrouter_caption_model ?? "");
    setOpenaiOptions(data.llm.openai_models ?? []);
    setOpenrouterOptions(data.llm.openrouter_models ?? []);
    setPhaseModels(data.llm.phase_models ?? {});
    setPhaseDetails(data.llm.phase_details ?? {});
  }, []);

  const applyCodexSettingsResponse = useCallback((data: CodexSettings) => {
    setCodexSettings(data);
    setCodexProfile(data.codex_exec.profile ?? "");
    setCodexModelOverride(data.codex_exec.model ?? "");
    setCodexCliProfile(data.active_profile?.name ?? data.codex_exec.profile ?? "");
    setCodexCliModel(data.active_profile?.model ?? "");
    const eff = (data.active_profile?.model_reasoning_effort ?? "").trim().toLowerCase();
    if (eff === "low" || eff === "medium" || eff === "high" || eff === "xhigh") {
      setCodexReasoningEffort(eff);
    } else {
      setCodexReasoningEffort("xhigh");
    }
  }, []);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setErrorMessage(null);
    try {
      const data = await fetchLlmSettings();
      applySettingsResponse(data);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setErrorMessage(message);
    } finally {
      setLoading(false);
    }
  }, [applySettingsResponse]);

  const loadCodexSettings = useCallback(async () => {
    setCodexLoading(true);
    setCodexErrorMessage(null);
    try {
      const data = await fetchCodexSettings();
      applyCodexSettingsResponse(data);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setCodexErrorMessage(message);
    } finally {
      setCodexLoading(false);
    }
  }, [applyCodexSettingsResponse]);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  useEffect(() => {
    void loadCodexSettings();
  }, [loadCodexSettings]);

  useEffect(() => {
    void (async () => {
      setModelError(null);
      try {
        const data = await fetchLlmModelScores();
        if (data && data.length > 0) {
          setModelScores(data);
          return;
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setModelError(message);
        setModelScores([]);
      }
      // fallback: static SSOT JSON (bundled)
      if (Array.isArray(llmModelsFallback) && llmModelsFallback.length > 0) {
        setModelScores(llmModelsFallback as LlmModelInfo[]);
        setModelError(null);
        return;
      }
      setModelError("モデルスコアがありません。APIキー設定後に再取得してください。");
    })();
  }, []);

  const handleApplyCodexRecommended = useCallback(() => {
    const profile = (codexProfile || codexSettings?.codex_exec.profile || "claude-code").trim() || "claude-code";
    setCodexProfile(profile);
    setCodexCliProfile(profile);
    setCodexCliModel("gpt-5.2");
    setCodexReasoningEffort("xhigh");
    setCodexStatusMessage("推奨設定を反映しました（未保存）");
    setCodexErrorMessage(null);
  }, [codexProfile, codexSettings]);

  const handleSaveCodexSettings = useCallback(async () => {
    setCodexSaving(true);
    setCodexStatusMessage(null);
    setCodexErrorMessage(null);
    try {
      const payload = {
        profile: codexProfile.trim(),
        model: codexModelOverride.trim(),
        cli_profile: codexCliProfile.trim(),
        cli_model: codexCliModel.trim() || undefined,
        model_reasoning_effort: codexReasoningEffort,
      };
      const response = await updateCodexSettings(payload);
      applyCodexSettingsResponse(response);
      setCodexStatusMessage("Codex exec 設定を保存しました");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setCodexErrorMessage(message);
    } finally {
      setCodexSaving(false);
    }
  }, [
    applyCodexSettingsResponse,
    codexCliModel,
    codexCliProfile,
    codexModelOverride,
    codexProfile,
    codexReasoningEffort,
  ]);

  const handleSaveModels = useCallback(async () => {
    setSaving(true);
    setStatusMessage(null);
    setErrorMessage(null);
    try {
      const response = await updateLlmSettings({
        caption_provider: provider,
        openai_caption_model: openaiModel.trim() || null,
        openrouter_caption_model: openrouterModel.trim() || null,
        phase_models: phaseModels,
      });
      applySettingsResponse(response);
      setStatusMessage("モデル設定を保存しました");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setErrorMessage(message);
    } finally {
      setSaving(false);
    }
  }, [applySettingsResponse, openaiModel, openrouterModel, provider, phaseModels]);

  const handleSaveOpenAiKey = useCallback(async () => {
    setSaving(true);
    setStatusMessage(null);
    setErrorMessage(null);
    try {
      const response = await updateLlmSettings({ openai_api_key: openaiKeyInput.trim() });
      applySettingsResponse(response);
      setOpenaiKeyInput("");
      setStatusMessage("OpenAI APIキーを更新しました");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setErrorMessage(message);
    } finally {
      setSaving(false);
    }
  }, [applySettingsResponse, openaiKeyInput]);

  const handleClearOpenAiKey = useCallback(async () => {
    setSaving(true);
    setStatusMessage(null);
    setErrorMessage(null);
    try {
      const response = await updateLlmSettings({ openai_api_key: "" });
      applySettingsResponse(response);
      setStatusMessage("OpenAI APIキーを削除しました");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setErrorMessage(message);
    } finally {
      setSaving(false);
    }
  }, [applySettingsResponse]);

  const handleSaveOpenRouterKey = useCallback(async () => {
    setSaving(true);
    setStatusMessage(null);
    setErrorMessage(null);
    try {
      const response = await updateLlmSettings({ openrouter_api_key: openrouterKeyInput.trim() });
      applySettingsResponse(response);
      setOpenrouterKeyInput("");
      setStatusMessage("OpenRouter APIキーを更新しました");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setErrorMessage(message);
    } finally {
      setSaving(false);
    }
  }, [applySettingsResponse, openrouterKeyInput]);

  const handleClearOpenRouterKey = useCallback(async () => {
    setSaving(true);
    setStatusMessage(null);
    setErrorMessage(null);
    try {
      const response = await updateLlmSettings({ openrouter_api_key: "" });
      applySettingsResponse(response);
      setStatusMessage("OpenRouter APIキーを削除しました");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setErrorMessage(message);
    } finally {
      setSaving(false);
    }
  }, [applySettingsResponse]);

  const handleRefreshOpenRouterModels = useCallback(async () => {
    setRefreshingOpenrouterModels(true);
    setStatusMessage(null);
    setErrorMessage(null);
    try {
      const data = await fetchLlmSettings();
      applySettingsResponse(data);
      setStatusMessage("OpenRouter モデル一覧を更新しました");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setErrorMessage(message);
    } finally {
      setRefreshingOpenrouterModels(false);
    }
  }, [applySettingsResponse]);

  const providerOptions = useMemo(() => {
    return (
      <select value={provider} onChange={(event) => setProvider(event.target.value as "openai" | "openrouter")}>
        <option value="openai">OpenAI（Visionモデル）</option>
        <option value="openrouter">OpenRouter（Qwen系）</option>
      </select>
    );
  }, [provider]);

  const openaiSelectOptions = useMemo(() => {
    const unique = new Set(openaiOptions ?? []);
    if (openaiModel && !unique.has(openaiModel)) {
      unique.add(openaiModel);
    }
    return Array.from(unique);
  }, [openaiOptions, openaiModel]);

  const openrouterSelectOptions = useMemo(() => {
    const unique = new Set(openrouterOptions ?? []);
    if (openrouterModel && !unique.has(openrouterModel)) {
      unique.add(openrouterModel);
    }
    return Array.from(unique);
  }, [openrouterOptions, openrouterModel]);

  const phaseModelSuggestions = useMemo(() => {
    const unique = new Set([...openaiSelectOptions, ...openrouterSelectOptions]);
    return Array.from(unique);
  }, [openaiSelectOptions, openrouterSelectOptions]);

  const modelTableRows = useMemo(() => modelScores ?? [], [modelScores]);

  const handlePhaseChange = useCallback(
    (phaseId: string, field: "provider" | "model") => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      const value = event.target.value;
      setPhaseModels((prev) => {
        const current = prev[phaseId] ?? { label: phaseId, provider: "openrouter", model: "" };
        return {
          ...prev,
          [phaseId]: {
            ...current,
            [field]: value,
          },
        };
      });
    },
    [setPhaseModels]
  );

  const handleAddPhase = useCallback(() => {
    const id = newPhaseId.trim();
    if (!id) return;
    setPhaseModels((prev) => ({
      ...prev,
      [id]: {
        label: newPhaseLabel.trim() || id,
        provider: "openrouter",
        model: "",
      },
    }));
    setNewPhaseId("");
    setNewPhaseLabel("");
  }, [newPhaseId, newPhaseLabel]);

  const summaryItems = useMemo(() => {
    const phases = Object.keys(phaseModels).length;
    return [
      `キャプション: ${provider === "openai" ? "OpenAI" : "OpenRouter"}`,
      `OpenAIモデル: ${openaiModel || "未設定"}`,
      `OpenRouterモデル: ${openrouterModel || "未設定"}`,
      `フェーズ設定: ${phases} 件`,
      `鍵(OpenAI/OR): ${settings?.llm?.openai_key_configured ? "OpenAI✓" : "OpenAI未"} / ${settings?.llm?.openrouter_key_configured ? "OR✓" : "OR未"}`,
    ];
  }, [provider, openaiModel, openrouterModel, phaseModels, settings]);

  const phaseSummaryRows = useMemo(() => {
    const entries = Object.entries(phaseModels || {});
    if (entries.length === 0) return [];
    return entries.map(([phaseId, info]) => {
      const detail = phaseDetails[phaseId];
      return {
        id: phaseId,
        label: info.label || detail?.label || phaseId,
        provider: info.provider,
        model: info.model || detail?.model || "(未設定)",
        role: detail?.role || "",
        path: detail?.path || "",
        prompt: detail?.prompt_source || "",
        endpoint: detail?.endpoint || "",
      };
    });
  }, [phaseModels, phaseDetails]);

  const codexCliProfiles = useMemo(() => {
    return codexSettings?.codex_cli?.profiles ?? [];
  }, [codexSettings]);

  const codexProfileOptions = useMemo(() => {
    const unique = new Set<string>();
    const options: string[] = [];
    for (const p of codexCliProfiles) {
      const name = (p?.name ?? "").trim();
      if (!name || unique.has(name)) continue;
      unique.add(name);
      options.push(name);
    }
    const effective = (codexSettings?.codex_exec?.profile ?? "").trim();
    if (effective && !unique.has(effective)) {
      options.push(effective);
      unique.add(effective);
    }
    const draft = codexProfile.trim();
    if (draft && !unique.has(draft)) {
      options.push(draft);
    }
    return options;
  }, [codexCliProfiles, codexProfile, codexSettings]);

  const codexReasoningOptions = useMemo(() => {
    const allowed = codexSettings?.allowed_reasoning_effort ?? ["low", "medium", "high", "xhigh"];
    const normalized: CodexReasoningEffort[] = [];
    for (const v of allowed) {
      const s = String(v || "").trim().toLowerCase();
      if (s === "low" || s === "medium" || s === "high" || s === "xhigh") {
        normalized.push(s);
      }
    }
    return normalized.length > 0 ? normalized : (["low", "medium", "high", "xhigh"] as CodexReasoningEffort[]);
  }, [codexSettings]);

  return (
    <section className="settings-page">
      <header>
        <h1>LLM設定</h1>
        <p>① どのプロバイダを使うか → ② そのモデル名 → ③ キー をここでまとめて設定できます。</p>
        {!loading && settings ? (
          <ul className="settings-page__summary">
            {summaryItems.map((item, idx) => (
              <li key={idx}>{item}</li>
            ))}
          </ul>
        ) : null}
      </header>
      {loading ? <p className="settings-page__placeholder">読み込み中です…</p> : null}
      {errorMessage ? <p className="settings-page__alert">{errorMessage}</p> : null}
      {statusMessage ? <p className="settings-page__status">{statusMessage}</p> : null}
      {!loading && settings ? (
        <div className="settings-page__grid">
          <section className="settings-card settings-card--wide">
            <h2>Codex exec（非対話）設定</h2>
            <p className="settings-card__hint">
              `codex exec`（サブスク）で実行する **モデル / 推論強度** を UI から切り替えます。推奨は `gpt-5.2` + `xhigh` です。
            </p>
            {codexLoading ? <p className="settings-card__hint">読み込み中…</p> : null}
            {codexErrorMessage ? <p className="settings-card__alert">{codexErrorMessage}</p> : null}
            {codexStatusMessage ? <p className="settings-card__status">{codexStatusMessage}</p> : null}
            {!codexLoading && codexSettings ? (
              <>
                <div className="settings-card__inline-actions">
                  <button
                    type="button"
                    className="settings-page__button settings-page__button--ghost"
                    onClick={handleApplyCodexRecommended}
                    disabled={codexSaving}
                  >
                    推奨を適用（gpt-5.2 / xhigh）
                  </button>
                  <button type="button" className="settings-page__button" onClick={handleSaveCodexSettings} disabled={codexSaving}>
                    {codexSaving ? "保存中…" : "Codex設定を保存"}
                  </button>
                </div>
                <div className="settings-card__table-wrapper">
                  <table className="settings-card__table">
                    <thead>
                      <tr>
                        <th>項目</th>
                        <th>値</th>
                        <th>出典</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>pipeline.profile</td>
                        <td>{codexSettings.codex_exec.profile}</td>
                        <td>{codexSettings.codex_exec.profile_source ?? "—"}</td>
                      </tr>
                      <tr>
                        <td>pipeline.model override</td>
                        <td>{codexSettings.codex_exec.model ?? "(未設定)"}</td>
                        <td>{codexSettings.codex_exec.model_source ?? "—"}</td>
                      </tr>
                      <tr>
                        <td>cli.model</td>
                        <td>{codexSettings.active_profile.model ?? "(未設定)"}</td>
                        <td>{codexSettings.codex_cli.exists ? codexSettings.codex_cli.config_path : "(未作成)"}</td>
                      </tr>
                      <tr>
                        <td>cli.model_reasoning_effort</td>
                        <td>{codexSettings.active_profile.model_reasoning_effort ?? "(未設定)"}</td>
                        <td>{codexSettings.codex_cli.exists ? codexSettings.codex_cli.config_path : "(未作成)"}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <label>
                  <span>Codex exec profile（pipeline）</span>
                  <input
                    list="codex-profiles"
                    value={codexProfile}
                    onChange={(event) => setCodexProfile(event.target.value)}
                    placeholder="例: claude-code"
                  />
                  <datalist id="codex-profiles">
                    {codexProfileOptions.map((p) => (
                      <option key={p} value={p} />
                    ))}
                  </datalist>
                  <p className="settings-card__hint">
                    Pipeline は `configs/codex_exec.local.yaml` に保存します（SSOTを汚さない）。通常運用は `LLM_EXEC_SLOT` とこの設定で管理します（ロックダウンONでは `YTM_CODEX_EXEC_*` の env 上書きは停止）。
                  </p>
                </label>
                <label>
                  <span>Codex exec model override（任意）</span>
                  <input
                    value={codexModelOverride}
                    onChange={(event) => setCodexModelOverride(event.target.value)}
                    placeholder="空欄なら CLI profile 側の model を使う"
                  />
                  <p className="settings-card__hint">空欄にすると `-m` を付けずに実行します。</p>
                </label>
                <label>
                  <span>Codex CLI profile（~/.codex/config.toml）</span>
                  <input
                    list="codex-profiles"
                    value={codexCliProfile}
                    onChange={(event) => setCodexCliProfile(event.target.value)}
                    placeholder="例: claude-code"
                  />
                  <p className="settings-card__hint">通常は pipeline.profile と同じ名前を指定します。</p>
                </label>
                <label>
                  <span>Codex CLI model（推奨: gpt-5.2）</span>
                  <input value={codexCliModel} onChange={(event) => setCodexCliModel(event.target.value)} placeholder="例: gpt-5.2" />
                </label>
                <label>
                  <span>推論強度（reasoning effort）</span>
                  <select value={codexReasoningEffort} onChange={(event) => setCodexReasoningEffort(event.target.value as CodexReasoningEffort)}>
                    {codexReasoningOptions.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                  <p className="settings-card__hint">非自明な処理は `xhigh` 推奨。ここは Codex CLI の profile 設定を書き換えます。</p>
                </label>
              </>
            ) : null}
          </section>
          {phaseSummaryRows.length > 0 ? (
            <section className="settings-card settings-card--wide">
              <h2>現在のフェーズ別適用モデル</h2>
              <p className="settings-card__hint">UI設定から保存された provider/model がそのまま実行時に使われます。役割/パス/プロンプト出典も合わせて表示しています。</p>
              <div className="settings-card__table-wrapper">
                <table className="settings-card__table">
                  <thead>
                    <tr>
                      <th>フェーズ</th>
                      <th>プロバイダ</th>
                      <th>モデル</th>
                      <th>役割</th>
                      <th>パス</th>
                      <th>プロンプト</th>
                      <th>エンドポイント</th>
                    </tr>
                  </thead>
                  <tbody>
                    {phaseSummaryRows.map((row) => (
                      <tr key={row.id}>
                        <td>{row.label}</td>
                        <td>{row.provider}</td>
                        <td>{row.model}</td>
                        <td>{row.role || "—"}</td>
                        <td>{row.path || "—"}</td>
                        <td>{row.prompt || "—"}</td>
                        <td>{row.endpoint || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}
          <section className="settings-card settings-card--wide">
            <h2>画像/帯パラメータ</h2>
            <p className="settings-card__hint">ドラフト生成時の目安値をUIから簡単に設定できます。</p>
            <UiParamsPanel />
          </section>
          <section className="settings-card">
            <h2>① プロバイダとモデル</h2>
            <p className="settings-card__hint">
              ここで選んだプロバイダ/モデルがサムネキャプション生成に使われます。入力欄は手動入力OK、下の候補は補助用です。
            </p>
            <label>
              <span>優先プロバイダ</span>
              {providerOptions}
            </label>
            <label>
              <span>OpenAI モデル（手入力可）</span>
              <input
                list="openai-models"
                value={openaiModel}
                onChange={(event) => setOpenaiModel(event.target.value)}
                placeholder="例: gpt-5-mini / gpt-5-chat"
              />
              <datalist id="openai-models">
                {openaiSelectOptions.map((model) => (
                  <option key={model} value={model} />
                ))}
              </datalist>
              {settings.llm?.openai_models_error ? (
                <p className="settings-card__hint settings-card__hint--warning">{settings.llm?.openai_models_error}</p>
              ) : openaiSelectOptions.length === 0 ? (
                <p className="settings-card__hint">APIキーを設定して最新モデルを取得してください。</p>
              ) : (
                <p className="settings-card__hint">候補をプルダウンから選ぶか、そのまま任意のモデル名を入力できます。</p>
              )}
            </label>
            <label>
              <span>OpenRouter モデル（手入力可）</span>
              <input
                list="openrouter-models"
                value={openrouterModel}
                onChange={(event) => setOpenrouterModel(event.target.value)}
                placeholder="例: qwen/qwen3-14b:free"
              />
              <datalist id="openrouter-models">
                {openrouterSelectOptions.map((model) => (
                  <option key={model} value={model} />
                ))}
              </datalist>
              {settings.llm?.openrouter_models_error ? (
                <p className="settings-card__hint settings-card__hint--warning">{settings.llm?.openrouter_models_error}</p>
              ) : openrouterSelectOptions.length === 0 ? (
                <p className="settings-card__hint">OpenRouter APIキーを設定してから再取得してください。</p>
              ) : (
                <p className="settings-card__hint">候補をプルダウンから選ぶか、そのまま任意のモデル名を入力できます。</p>
              )}
              <div className="settings-card__inline-actions">
                <button
                  type="button"
                  className="settings-page__button settings-page__button--ghost"
                  onClick={handleRefreshOpenRouterModels}
                  disabled={refreshingOpenrouterModels || loading}
                >
                  {refreshingOpenrouterModels ? "再取得中…" : "OpenRouterモデルを再取得"}
                </button>
              </div>
            </label>
            <button type="button" className="settings-page__button" onClick={handleSaveModels} disabled={saving}>
              {saving ? "保存中…" : "モデル設定を保存"}
            </button>
          </section>
          <section className="settings-card settings-card--wide">
            <h2>④ フェーズ別モデル割り当て</h2>
            <p className="settings-card__hint">
              ワークフローのフェーズごとに使うプロバイダ/モデルをメモしておけます（保存すると設定に記録）。例: 台本作成/リサーチ/音声テキスト生成。
            </p>
            <div className="settings-card__inline-actions">
              <input
                type="text"
                value={newPhaseId}
                onChange={(e) => setNewPhaseId(e.target.value)}
                placeholder="phase-id (例: research)"
              />
              <input
                type="text"
                value={newPhaseLabel}
                onChange={(e) => setNewPhaseLabel(e.target.value)}
                placeholder="表示名 (例: リサーチ)"
              />
              <button type="button" onClick={handleAddPhase} disabled={!newPhaseId.trim()}>
                フェーズ追加
              </button>
            </div>
            <div className="settings-card__table-wrapper">
              <table className="settings-card__table">
                <thead>
                  <tr>
                    <th>フェーズ</th>
                    <th>プロバイダ</th>
                    <th>モデル</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(phaseModels).map(([phaseId, info]) => (
                    <tr key={phaseId}>
                      <td>{info.label || phaseId}</td>
                      <td>
                        <select value={info.provider} onChange={handlePhaseChange(phaseId, "provider")}>
                          <option value="openai">OpenAI</option>
                          <option value="openrouter">OpenRouter</option>
                          <option value="gemini">Gemini</option>
                        </select>
                      </td>
                      <td>
                        <input
                          list="phase-models"
                          value={info.model || ""}
                          onChange={handlePhaseChange(phaseId, "model")}
                          placeholder="モデルIDを入力"
                        />
                      </td>
                    </tr>
                  ))}
                  {Object.keys(phaseModels).length === 0 ? (
                    <tr>
                      <td colSpan={3}>フェーズ設定がありません</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            <p className="settings-card__hint">
              入力したモデルIDは手動入力でOKです。将来の実行フロー側への適用は別途対応します。
            </p>
            <datalist id="phase-models">
              {phaseModelSuggestions.map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>
          </section>
          <section className="settings-card">
            <h2>② OpenAI APIキー</h2>
            <p className="settings-card__hint">
              状態: {settings.llm?.openai_key_preview ?? (settings.llm?.openai_key_configured ? "設定済み" : "未設定")}
            </p>
            <label>
              <span>APIキー</span>
              <input
                type="password"
                value={openaiKeyInput}
                onChange={(event) => setOpenaiKeyInput(event.target.value)}
                placeholder="sk-..."
              />
            </label>
            <div className="settings-card__actions">
              <button type="button" onClick={handleSaveOpenAiKey} disabled={saving || !openaiKeyInput.trim()}>
                更新
              </button>
              <button type="button" onClick={handleClearOpenAiKey} disabled={saving}>
                削除
              </button>
            </div>
          </section>
          <section className="settings-card">
            <h2>③ OpenRouter APIキー</h2>
            <p className="settings-card__hint">
              状態: {settings.llm?.openrouter_key_preview ?? (settings.llm?.openrouter_key_configured ? "設定済み" : "未設定")}
            </p>
            <label>
              <span>APIキー</span>
              <input
                type="password"
                value={openrouterKeyInput}
                onChange={(event) => setOpenrouterKeyInput(event.target.value)}
                placeholder="sk-or-..."
              />
            </label>
            <div className="settings-card__actions">
              <button type="button" onClick={handleSaveOpenRouterKey} disabled={saving || !openrouterKeyInput.trim()}>
                更新
              </button>
              <button type="button" onClick={handleClearOpenRouterKey} disabled={saving}>
                削除
              </button>
            </div>
          </section>
          <section className="settings-card settings-card--wide">
            <h2>LLMモデル性能表</h2>
            <p className="settings-card__hint">
              `ssot/history/HISTORY_llm_model_scores.json` 由来のスコアです。無料枠モデルの性能を確認できます。
            </p>
            {modelError ? <p className="settings-card__alert">{modelError}</p> : null}
            <div className="settings-card__table-wrapper">
              <table className="settings-card__table">
                <thead>
                  <tr>
                    <th>プロバイダ</th>
                    <th>モデル</th>
                    <th>IQ</th>
                    <th>知識指標</th>
                    <th>専門指標</th>
                    <th>備考</th>
                  </tr>
                </thead>
                <tbody>
                  {modelTableRows.map((entry) => (
                    <tr key={entry.id}>
                      <td>{entry.provider}</td>
                      <td>
                        <div className="settings-card__table-title">{entry.label}</div>
                        <div className="settings-card__table-sub">{entry.model_id}</div>
                      </td>
                      <td>{entry.iq ?? "—"}</td>
                      <td>
                        {entry.knowledge_metric?.name
                          ? `${entry.knowledge_metric.name}: ${entry.knowledge_metric.value ?? "—"}`
                          : "—"}
                      </td>
                      <td>
                        {entry.specialist_metric?.name
                          ? `${entry.specialist_metric.name}: ${entry.specialist_metric.value ?? "—"}`
                          : "—"}
                      </td>
                      <td className="settings-card__table-notes">
                        {entry.notes ?? "—"}
                        {entry.last_updated ? (
                          <div className="settings-card__table-sub">更新: {entry.last_updated}</div>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                  {modelTableRows.length === 0 ? (
                    <tr>
                      <td colSpan={6}>モデルスコアがありません。APIキー設定後に再取得してください。</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}
