import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchChannelProfile,
  fetchPersonaDocument,
  fetchPlanningTemplate,
  updateChannelProfile,
  updatePersonaDocument,
  updatePlanningTemplate,
} from "../api/client";
import type {
  ChannelProfileResponse,
  ChannelProfileUpdatePayload,
} from "../api/types";

type ChannelProfilePanelProps = {
  channelCode: string | null;
  channelName?: string | null;
};

type RuleRow = {
  id: string;
  section: string;
  voice: string;
};

type ChannelProfileFormState = {
  scriptPrompt: string;
  description: string;
  youtubeTitle: string;
  youtubeDescription: string;
  youtubeHandle: string;
  defaultTags: string[];
  audioDefaultVoiceKey: string;
  audioRules: RuleRow[];
};

type BannerState = {
  type: "success" | "error" | "info";
  text: string;
};

type PersonaEditorState = {
  open: boolean;
  loading: boolean;
  saving: boolean;
  content: string;
  error: string | null;
  path: string | null;
  original: string;
};

type TemplateEditorState = {
  open: boolean;
  loading: boolean;
  saving: boolean;
  content: string;
  error: string | null;
  path: string | null;
  headers: string[];
  sample: string[];
  original: string;
};

const createRuleRow = (section = "", voice = ""): RuleRow => ({
  id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
  section,
  voice,
});

function cloneFormState(form: ChannelProfileFormState): ChannelProfileFormState {
  return {
    ...form,
    defaultTags: [...form.defaultTags],
    audioRules: form.audioRules.map((rule) => ({ ...rule })),
  };
}

function convertResponseToForm(profile: ChannelProfileResponse): ChannelProfileFormState {
  const rules = profile.audio_section_voice_rules ?? {};
  const audioRules: RuleRow[] = Object.entries(rules).map(([section, voice]) =>
    createRuleRow(section, voice)
  );
  return {
    scriptPrompt: profile.script_prompt ?? "",
    description: profile.description ?? "",
    youtubeTitle: profile.youtube_title ?? "",
    youtubeDescription: profile.youtube_description ?? "",
    youtubeHandle: profile.youtube_handle ?? "",
    defaultTags: [...(profile.default_tags ?? [])],
    audioDefaultVoiceKey: profile.audio_default_voice_key ?? "",
    audioRules,
  };
}

function normalizeTags(tags: string[]): string[] {
  return tags.map((tag) => tag.trim()).filter((tag) => tag.length > 0);
}

function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) {
    return false;
  }
  return a.every((value, index) => value === b[index]);
}

function rulesToObject(rules: RuleRow[]): Record<string, string> {
  const map: Record<string, string> = {};
  for (const rule of rules) {
    const section = rule.section.trim();
    const voice = rule.voice.trim();
    if (!section || !voice) {
      continue;
    }
    map[section] = voice;
  }
  return map;
}

function mapsEqual(
  left: Record<string, string>,
  right: Record<string, string>
): boolean {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) {
    return false;
  }
  return leftKeys.every((key) => left[key] === right[key]);
}

function serializeForm(form: ChannelProfileFormState | null) {
  if (!form) {
    return null;
  }
  const normalizedRules = form.audioRules
    .map((rule) => ({
      section: rule.section.trim(),
      voice: rule.voice.trim(),
    }))
    .filter((rule) => rule.section && rule.voice)
    .sort((a, b) => {
      if (a.section === b.section) {
        return a.voice.localeCompare(b.voice);
      }
      return a.section.localeCompare(b.section);
    });
  return {
    scriptPrompt: form.scriptPrompt,
    description: form.description,
    youtubeTitle: form.youtubeTitle,
    youtubeDescription: form.youtubeDescription,
    youtubeHandle: form.youtubeHandle,
    defaultTags: normalizeTags(form.defaultTags),
    audioDefaultVoiceKey: form.audioDefaultVoiceKey.trim(),
    audioRules: normalizedRules,
  };
}

function buildUpdatePayload(
  form: ChannelProfileFormState,
  profile: ChannelProfileResponse
): ChannelProfileUpdatePayload {
  const payload: ChannelProfileUpdatePayload = {};
  const originalPrompt = profile.script_prompt ?? "";
  if (form.scriptPrompt !== originalPrompt) {
    payload.script_prompt = form.scriptPrompt;
  }

  const originalDescription = profile.description ?? "";
  if (form.description !== originalDescription) {
    payload.description = form.description;
  }

  const originalTitle = profile.youtube_title ?? "";
  if (form.youtubeTitle !== originalTitle) {
    payload.youtube_title = form.youtubeTitle;
  }

  const originalDescriptionText = profile.youtube_description ?? "";
  if (form.youtubeDescription !== originalDescriptionText) {
    payload.youtube_description = form.youtubeDescription;
  }

  const originalHandle = profile.youtube_handle ?? "";
  if (form.youtubeHandle !== originalHandle) {
    payload.youtube_handle = form.youtubeHandle;
  }

  const normalizedTags = normalizeTags(form.defaultTags);
  const originalTags = normalizeTags(profile.default_tags ?? []);
  if (!arraysEqual(normalizedTags, originalTags)) {
    payload.default_tags = normalizedTags;
  }

  const audioPayload: NonNullable<ChannelProfileUpdatePayload["audio"]> = {};
  const trimmedVoiceKey = form.audioDefaultVoiceKey.trim();
  const originalVoiceKey = (profile.audio_default_voice_key ?? "").trim();
  if (trimmedVoiceKey && trimmedVoiceKey !== originalVoiceKey) {
    audioPayload.default_voice_key = trimmedVoiceKey;
  }

  const nextRules = rulesToObject(form.audioRules);
  const originalRules = profile.audio_section_voice_rules ?? {};
  if (!mapsEqual(nextRules, originalRules)) {
    audioPayload.section_voice_rules = nextRules;
  }

  if (Object.keys(audioPayload).length > 0) {
    payload.audio = audioPayload;
  }

  return payload;
}

export function ChannelProfilePanel({ channelCode, channelName }: ChannelProfilePanelProps) {
  const [profile, setProfile] = useState<ChannelProfileResponse | null>(null);
  const [formState, setFormState] = useState<ChannelProfileFormState | null>(null);
  const [initialState, setInitialState] = useState<ChannelProfileFormState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<BannerState | null>(null);
  const [personaEditor, setPersonaEditor] = useState<PersonaEditorState>({
    open: false,
    loading: false,
    saving: false,
    content: "",
    error: null,
    path: null,
    original: "",
  });
  const [templateEditor, setTemplateEditor] = useState<TemplateEditorState>({
    open: false,
    loading: false,
    saving: false,
    content: "",
    error: null,
    path: null,
    headers: [],
    sample: [],
    original: "",
  });
  const planningPersona = useMemo(
    () =>
      profile?.planning_persona ??
      profile?.persona_summary ??
      profile?.audience_profile ??
      "",
    [profile]
  );
  const planningPersonaPath = profile?.planning_persona_path ?? null;
  const planningTemplatePath = profile?.planning_template_path ?? null;
  const planningTemplateHeaders = profile?.planning_template_headers ?? [];
  const planningTemplateSample = profile?.planning_template_sample ?? [];
  const planningDefaultsEntries = useMemo(
    () => Object.entries(profile?.planning_description_defaults ?? {}),
    [profile]
  );
  const planningRequiredSets = profile?.planning_required_fieldsets ?? [];
  const personaDirty = personaEditor.open && personaEditor.content !== personaEditor.original;
  const templateDirty = templateEditor.open && templateEditor.content !== templateEditor.original;

  const applyProfile = useCallback((response: ChannelProfileResponse) => {
    setProfile(response);
    const converted = convertResponseToForm(response);
    setFormState(cloneFormState(converted));
    setInitialState(cloneFormState(converted));
    setBanner(null);
  }, []);

  const loadProfile = useCallback(
    async (target?: string) => {
      const code = target ?? channelCode;
      if (!code) {
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const response = await fetchChannelProfile(code);
        applyProfile(response);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      } finally {
        setLoading(false);
      }
    },
    [channelCode, applyProfile]
  );

  useEffect(() => {
    if (!channelCode) {
      setProfile(null);
      setFormState(null);
      setInitialState(null);
      setError(null);
      setBanner(null);
      setPersonaEditor({ open: false, loading: false, saving: false, content: "", error: null, path: null, original: "" });
      setTemplateEditor({
        open: false,
        loading: false,
        saving: false,
        content: "",
        error: null,
        path: null,
        headers: [],
        sample: [],
        original: "",
      });
      return;
    }
    loadProfile(channelCode);
  }, [channelCode, loadProfile]);

  const isDirty = useMemo(() => {
    if (!formState || !initialState) {
      return false;
    }
    const current = serializeForm(formState);
    const baseline = serializeForm(initialState);
    return JSON.stringify(current) !== JSON.stringify(baseline);
  }, [formState, initialState]);

  const handleFieldChange = useCallback(
    (field: keyof ChannelProfileFormState, value: string | string[]) => {
      setFormState((prev) => {
        if (!prev) {
          return prev;
        }
        if (Array.isArray(value)) {
          return { ...prev, [field]: value };
        }
        return { ...prev, [field]: value };
      });
    },
    []
  );

  const handleTagsChange = useCallback((value: string) => {
    const items = value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
    handleFieldChange("defaultTags", items);
  }, [handleFieldChange]);

  const handleRuleChange = useCallback(
    (id: string, key: "section" | "voice", value: string) => {
      setFormState((prev) => {
        if (!prev) {
          return prev;
        }
        return {
          ...prev,
          audioRules: prev.audioRules.map((rule) =>
            rule.id === id ? { ...rule, [key]: value } : rule
          ),
        };
      });
    },
    []
  );

  const handleAddRule = useCallback(() => {
    setFormState((prev) => {
      if (!prev) {
        return prev;
      }
      return {
        ...prev,
        audioRules: [...prev.audioRules, createRuleRow()],
      };
    });
  }, []);

  const handleRemoveRule = useCallback((id: string) => {
    setFormState((prev) => {
      if (!prev) {
        return prev;
      }
      return {
        ...prev,
        audioRules: prev.audioRules.filter((rule) => rule.id !== id),
      };
    });
  }, []);

  const handleReset = useCallback(() => {
    if (!initialState) {
      return;
    }
    setFormState(cloneFormState(initialState));
    setBanner(null);
  }, [initialState]);

  const handleReload = useCallback(() => {
    if (channelCode) {
      loadProfile(channelCode);
    }
  }, [channelCode, loadProfile]);

  const handleCopy = useCallback((text: string, successMessage: string) => {
    if (!text) {
      return;
    }
    if (navigator?.clipboard?.writeText) {
      navigator.clipboard
        .writeText(text)
        .then(() => setBanner({ type: "info", text: successMessage }))
        .catch(() =>
          setBanner({
            type: "error",
            text: "クリップボードにコピーできませんでした。",
          })
        );
    } else {
      try {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
        setBanner({ type: "info", text: successMessage });
      } catch (error) {
        setBanner({
          type: "error",
          text: "クリップボードにコピーできませんでした。",
        });
      }
    }
  }, []);

  const fetchPersonaDoc = useCallback(async (code: string) => {
    setPersonaEditor({
      open: true,
      loading: true,
      saving: false,
      content: "",
      error: null,
      path: null,
      original: "",
    });
    try {
      const response = await fetchPersonaDocument(code);
      setPersonaEditor({
        open: true,
        loading: false,
        saving: false,
        content: response.content,
        error: null,
        path: response.path ?? null,
        original: response.content,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setPersonaEditor((prev) => ({ ...prev, loading: false, error: message }));
    }
  }, []);

  const fetchTemplateDoc = useCallback(async (code: string) => {
    setTemplateEditor({
      open: true,
      loading: true,
      saving: false,
      content: "",
      error: null,
      path: null,
      headers: [],
      sample: [],
      original: "",
    });
    try {
      const response = await fetchPlanningTemplate(code);
      setTemplateEditor({
        open: true,
        loading: false,
        saving: false,
        content: response.content,
        error: null,
        path: response.path ?? null,
        headers: response.headers ?? [],
        sample: response.sample ?? [],
        original: response.content,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setTemplateEditor((prev) => ({ ...prev, loading: false, error: message }));
    }
  }, []);

  const handlePersonaEditorOpen = useCallback(() => {
    if (!channelCode) {
      return;
    }
    void fetchPersonaDoc(channelCode);
  }, [channelCode, fetchPersonaDoc]);

  const handleTemplateEditorOpen = useCallback(() => {
    if (!channelCode) {
      return;
    }
    void fetchTemplateDoc(channelCode);
  }, [channelCode, fetchTemplateDoc]);

  const handlePersonaContentChange = useCallback((value: string) => {
    setPersonaEditor((prev) => ({ ...prev, content: value }));
  }, []);

  const handleTemplateContentChange = useCallback((value: string) => {
    setTemplateEditor((prev) => ({ ...prev, content: value }));
  }, []);

  const handlePersonaSave = useCallback(async () => {
    if (!channelCode) {
      return;
    }
    setPersonaEditor((prev) => ({ ...prev, saving: true, error: null }));
    try {
      const response = await updatePersonaDocument(channelCode, { content: personaEditor.content });
      setPersonaEditor({
        open: true,
        loading: false,
        saving: false,
        content: response.content,
        error: null,
        path: response.path ?? null,
        original: response.content,
      });
      setBanner({ type: "success", text: "ペルソナを更新しました。" });
      await loadProfile(channelCode);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setPersonaEditor((prev) => ({ ...prev, saving: false, error: message }));
    }
  }, [channelCode, personaEditor.content, loadProfile]);

  const handleTemplateSave = useCallback(async () => {
    if (!channelCode) {
      return;
    }
    setTemplateEditor((prev) => ({ ...prev, saving: true, error: null }));
    try {
      const response = await updatePlanningTemplate(channelCode, { content: templateEditor.content });
      setTemplateEditor({
        open: true,
        loading: false,
        saving: false,
        content: response.content,
        error: null,
        path: response.path ?? null,
        headers: response.headers ?? [],
        sample: response.sample ?? [],
        original: response.content,
      });
      setBanner({ type: "success", text: "テンプレートCSVを更新しました。" });
      await loadProfile(channelCode);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setTemplateEditor((prev) => ({ ...prev, saving: false, error: message }));
    }
  }, [channelCode, templateEditor.content, loadProfile]);

  const handlePersonaClose = useCallback(() => {
    setPersonaEditor({
      open: false,
      loading: false,
      saving: false,
      content: "",
      error: null,
      path: null,
      original: "",
    });
  }, []);

  const handleTemplateClose = useCallback(() => {
    setTemplateEditor({
      open: false,
      loading: false,
      saving: false,
      content: "",
      error: null,
      path: null,
      headers: [],
      sample: [],
      original: "",
    });
  }, []);

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!formState || !profile || !channelCode) {
        return;
      }
      if (!isDirty) {
        setBanner({ type: "info", text: "変更はありません。" });
        return;
      }
      const payload = buildUpdatePayload(formState, profile);
      const { audio, ...rest } = payload;
      const hasTopLevelChanges = Object.keys(rest).length > 0;
      const hasAudioChanges = Boolean(audio && Object.keys(audio).length > 0);
      if (!hasTopLevelChanges && !hasAudioChanges) {
        setBanner({ type: "info", text: "変更はありません。" });
        return;
      }
      setSaving(true);
      setBanner(null);
      try {
        const requestPayload: ChannelProfileUpdatePayload = { ...rest };
        if (hasAudioChanges && audio) {
          requestPayload.audio = audio;
        }
        const response = await updateChannelProfile(channelCode, requestPayload);
        applyProfile(response);
        setBanner({ type: "success", text: "チャンネル設定を保存しました。" });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setBanner({ type: "error", text: message });
      } finally {
        setSaving(false);
      }
    },
    [applyProfile, channelCode, formState, isDirty, profile]
  );

  const defaultTagsText = formState ? formState.defaultTags.join("\n") : "";

  const resolvedChannelName = profile?.channel_name ?? channelName ?? null;
  const channelLabel = channelCode
    ? `${channelCode}${resolvedChannelName ? ` — ${resolvedChannelName}` : ""}`
    : "チャンネル未選択";

  return (
    <section className="channel-profile-panel">
      <header className="channel-profile-panel__header">
        <div>
          <h2>チャンネル設定</h2>
          <p className="channel-profile-panel__subtitle">
            {channelCode ? channelLabel : "チャンネルを選択すると編集できます"}
          </p>
        </div>
        <div className="channel-profile-panel__actions">
          <button
            type="button"
            className="channel-profile-button"
            onClick={handleReload}
            disabled={!channelCode || loading}
          >
            再読み込み
          </button>
          <button
            type="button"
            className="channel-profile-button"
            onClick={handleReset}
            disabled={!channelCode || !isDirty || saving}
          >
            リセット
          </button>
        </div>
      </header>
      {banner ? (
        <div className={`channel-profile-banner channel-profile-banner--${banner.type}`}>
          {banner.text}
        </div>
      ) : null}
      {error ? (
        <div className="channel-profile-banner channel-profile-banner--error">
          {error}
        </div>
      ) : null}
      {profile &&
      (planningPersona ||
        planningDefaultsEntries.length > 0 ||
        planningRequiredSets.length > 0 ||
        planningTemplateHeaders.length > 0 ||
        planningPersonaPath ||
        planningTemplatePath) ? (
        <section className="channel-profile-info">
          <div className="channel-profile-info__heading">
            <h3>企画シート SSOT</h3>
            <p className="channel-profile-text-muted">
              planning.csv に自動適用されるペルソナ・説明文の既定値・必須列を確認できます。
            </p>
          </div>
          {planningPersona ? (
            <div className="channel-profile-info__group">
              <div className="channel-profile-info__label">固定ペルソナ</div>
              <p className="channel-profile-info__persona">{planningPersona}</p>
            </div>
          ) : null}
          {planningPersonaPath ? (
            <div className="channel-profile-info__group">
              <div className="channel-profile-info__label">Persona ドキュメント</div>
              <div className="channel-profile-info__path">
                <code>{planningPersonaPath}</code>
                <button
                  type="button"
                  className="channel-profile-button channel-profile-button--ghost"
                  onClick={() => handleCopy(planningPersonaPath, "Persona ドキュメントのパスをコピーしました。")}
                >
                  パスをコピー
                </button>
              </div>
            </div>
          ) : null}
          {planningDefaultsEntries.length > 0 ? (
            <div className="channel-profile-info__group">
              <div className="channel-profile-info__label">説明文デフォルト</div>
              <ul className="channel-profile-info__list">
                {planningDefaultsEntries.map(([key, value]) => (
                  <li key={key}>
                    <strong>{key}</strong>: {value}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {planningRequiredSets.length > 0 ? (
            <div className="channel-profile-info__group">
              <div className="channel-profile-info__label">必須列ルール</div>
              <div className="channel-profile-info__requirements">
                {planningRequiredSets.map((set, index) => (
                  <div key={`req-${set.min_no ?? `all`}-${index}`} className="channel-profile-info__requirement">
                    <div className="channel-profile-info__requirement-label">
                      {set.min_no ? `No.${set.min_no}以降` : "全No."}
                    </div>
                    <ul className="channel-profile-info__list">
                      {set.required_columns.map((column) => (
                        <li key={`${set.min_no ?? "all"}-${column}`}>{column}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {planningTemplateHeaders.length > 0 || planningTemplatePath ? (
            <div className="channel-profile-info__group">
              <div className="channel-profile-info__label">テンプレート CSV</div>
              {planningTemplatePath ? (
                <div className="channel-profile-info__path">
                  <code>{planningTemplatePath}</code>
                  <button
                    type="button"
                    className="channel-profile-button channel-profile-button--ghost"
                    onClick={() => handleCopy(planningTemplatePath, "テンプレートのパスをコピーしました。")}
                  >
                    パスをコピー
                  </button>
                </div>
              ) : null}
              {planningTemplateHeaders.length > 0 ? (
                <div className="channel-profile-info__table-scroll">
                  <table className="channel-profile-info__table">
                    <thead>
                      <tr>
                        {planningTemplateHeaders.map((header) => (
                          <th key={`template-header-${header}`}>{header}</th>
                        ))}
                      </tr>
                    </thead>
                    {planningTemplateSample.length > 0 ? (
                      <tbody>
                        <tr>
                          {planningTemplateHeaders.map((_, index) => (
                            <td key={`template-sample-${index}`}>{planningTemplateSample[index] ?? ""}</td>
                          ))}
                        </tr>
                      </tbody>
                    ) : null}
                  </table>
                </div>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}
      {channelCode ? (
        <section className="channel-profile-editor">
          <div className="channel-profile-editor__group">
            <div className="channel-profile-info__label">ペルソナ編集</div>
            {!personaEditor.open ? (
              <button type="button" className="channel-profile-button" onClick={handlePersonaEditorOpen}>
                ペルソナを読み込む
              </button>
            ) : null}
            {personaEditor.open ? (
              <>
                {personaEditor.path ? (
                  <div className="channel-profile-info__path">
                    <code>{personaEditor.path}</code>
                    <button
                      type="button"
                      className="channel-profile-button channel-profile-button--ghost"
                      onClick={() => handleCopy(personaEditor.path ?? "", "ペルソナファイルのパスをコピーしました。")}
                    >
                      パスをコピー
                    </button>
                  </div>
                ) : null}
                {personaEditor.loading ? (
                  <p className="channel-profile-text-muted">読み込み中…</p>
                ) : (
                  <textarea
                    className="channel-profile-editor__textarea"
                    rows={6}
                    value={personaEditor.content}
                    onChange={(event) => handlePersonaContentChange(event.target.value)}
                    disabled={personaEditor.saving}
                  />
                )}
                {personaEditor.error ? (
                  <p className="channel-profile-editor__error">{personaEditor.error}</p>
                ) : null}
                <div className="channel-profile-editor__actions">
                  <button
                    type="button"
                    className="channel-profile-button channel-profile-button--primary"
                    onClick={handlePersonaSave}
                    disabled={!personaDirty || personaEditor.saving || personaEditor.loading}
                  >
                    {personaEditor.saving ? "保存中…" : "保存"}
                  </button>
                  <button type="button" className="channel-profile-button" onClick={handlePersonaClose} disabled={personaEditor.saving}>
                    閉じる
                  </button>
                </div>
              </>
            ) : null}
          </div>
          <div className="channel-profile-editor__group">
            <div className="channel-profile-info__label">テンプレートCSV編集</div>
            {!templateEditor.open ? (
              <button type="button" className="channel-profile-button" onClick={handleTemplateEditorOpen}>
                テンプレートを読み込む
              </button>
            ) : null}
            {templateEditor.open ? (
              <>
                {templateEditor.path ? (
                  <div className="channel-profile-info__path">
                    <code>{templateEditor.path}</code>
                    <button
                      type="button"
                      className="channel-profile-button channel-profile-button--ghost"
                      onClick={() => handleCopy(templateEditor.path ?? "", "テンプレートパスをコピーしました。")}
                    >
                      パスをコピー
                    </button>
                  </div>
                ) : null}
                {templateEditor.loading ? (
                  <p className="channel-profile-text-muted">読み込み中…</p>
                ) : (
                  <textarea
                    className="channel-profile-editor__textarea"
                    rows={10}
                    value={templateEditor.content}
                    onChange={(event) => handleTemplateContentChange(event.target.value)}
                    disabled={templateEditor.saving}
                  />
                )}
                {templateEditor.error ? (
                  <p className="channel-profile-editor__error">{templateEditor.error}</p>
                ) : null}
                <div className="channel-profile-editor__actions">
                  <button
                    type="button"
                    className="channel-profile-button channel-profile-button--primary"
                    onClick={handleTemplateSave}
                    disabled={!templateDirty || templateEditor.saving || templateEditor.loading}
                  >
                    {templateEditor.saving ? "保存中…" : "保存"}
                  </button>
                  <button type="button" className="channel-profile-button" onClick={handleTemplateClose} disabled={templateEditor.saving}>
                    閉じる
                  </button>
                </div>
              </>
            ) : null}
          </div>
        </section>
      ) : null}
      {!channelCode ? (
        <p className="channel-profile-text-muted">まず左のリストからチャンネルを選択してください。</p>
      ) : null}
      {channelCode && loading ? (
        <p className="channel-profile-text-muted">読み込み中…</p>
      ) : null}
      {channelCode && !loading && formState ? (
        <form className="channel-profile-form" onSubmit={handleSubmit}>
          <div className="channel-profile-grid">
            <div className="channel-profile-field channel-profile-field--full">
              <label htmlFor="channel-profile-script">台本プロンプト</label>
              <textarea
                id="channel-profile-script"
                value={formState.scriptPrompt}
                onChange={(event) => handleFieldChange("scriptPrompt", event.target.value)}
                placeholder="Qwen向け台本テンプレートを入力"
              />
              <p className="channel-profile-hint">
                「///」など旧式の記号は使用禁止です。段落ベースで自然な文章にしてください。
              </p>
            </div>
            <div className="channel-profile-field channel-profile-field--full">
              <label htmlFor="channel-profile-description">チャンネル説明文</label>
              <textarea
                id="channel-profile-description"
                value={formState.description}
                onChange={(event) => handleFieldChange("description", event.target.value)}
                placeholder="チャンネル概要"
              />
            </div>
            <div className="channel-profile-field">
              <label htmlFor="channel-profile-youtube-title">YouTubeタイトル</label>
              <input
                id="channel-profile-youtube-title"
                type="text"
                value={formState.youtubeTitle}
                onChange={(event) => handleFieldChange("youtubeTitle", event.target.value)}
              />
            </div>
            <div className="channel-profile-field">
              <label htmlFor="channel-profile-youtube-handle">YouTubeハンドル</label>
              <input
                id="channel-profile-youtube-handle"
                type="text"
                value={formState.youtubeHandle}
                onChange={(event) => handleFieldChange("youtubeHandle", event.target.value)}
                placeholder="@example"
              />
            </div>
            <div className="channel-profile-field channel-profile-field--full">
              <label htmlFor="channel-profile-youtube-description">YouTube説明文 / 投稿テンプレ</label>
              <textarea
                id="channel-profile-youtube-description"
                value={formState.youtubeDescription}
                onChange={(event) => handleFieldChange("youtubeDescription", event.target.value)}
              />
            </div>
            <div className="channel-profile-field channel-profile-field--full">
              <label htmlFor="channel-profile-tags">デフォルトタグ（改行区切り）</label>
              <textarea
                id="channel-profile-tags"
                value={defaultTagsText}
                onChange={(event) => handleTagsChange(event.target.value)}
                placeholder="例：シニア健康&#10;朗読&#10;癒やし"
              />
            </div>
            <div className="channel-profile-field">
              <label htmlFor="channel-profile-audio-default">音声デフォルトボイス</label>
              <input
                id="channel-profile-audio-default"
                type="text"
                value={formState.audioDefaultVoiceKey}
                onChange={(event) => handleFieldChange("audioDefaultVoiceKey", event.target.value)}
                placeholder="voicevox_namioto"
              />
              <p className="channel-profile-hint">voice_config.json のキー名を指定してください。</p>
            </div>
          </div>

          <div className="channel-profile-section">
            <div className="channel-profile-section__header">
              <h3>セクション別ボイス設定</h3>
              <button
                type="button"
                className="channel-profile-button channel-profile-button--ghost"
                onClick={handleAddRule}
              >
                ルールを追加
              </button>
            </div>
            {formState.audioRules.length === 0 ? (
              <p className="channel-profile-text-muted">ルールはまだありません。</p>
            ) : null}
            {formState.audioRules.map((rule) => (
              <div key={rule.id} className="channel-profile-rule-row">
                <div className="channel-profile-field">
                  <label>セクションキー</label>
                  <input
                    type="text"
                    value={rule.section}
                    onChange={(event) => handleRuleChange(rule.id, "section", event.target.value)}
                    placeholder="intro / outro など"
                  />
                </div>
                <div className="channel-profile-field">
                  <label>ボイスキー</label>
                  <input
                    type="text"
                    value={rule.voice}
                    onChange={(event) => handleRuleChange(rule.id, "voice", event.target.value)}
                    placeholder="voicevox_namioto"
                  />
                </div>
                <button
                  type="button"
                  className="channel-profile-button channel-profile-button--ghost"
                  onClick={() => handleRemoveRule(rule.id)}
                >
                  削除
                </button>
              </div>
            ))}
          </div>

          <div className="channel-profile-actions">
            <button
              type="submit"
              className="channel-profile-button channel-profile-button--primary"
              disabled={saving || !isDirty}
            >
              {saving ? "保存中…" : "保存"}
            </button>
            <button
              type="button"
              className="channel-profile-button"
              onClick={handleReset}
              disabled={saving || !isDirty}
            >
              破棄
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
