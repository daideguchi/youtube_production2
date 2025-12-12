import { useCallback, useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import {
  fetchPromptDocument,
  fetchPromptDocuments,
  updatePromptDocument,
} from "../api/client";
import type {
  PromptDocumentDetail,
  PromptDocumentSummary,
  PromptSyncTarget,
} from "../api/types";
import type { ShellOutletContext } from "../layouts/AppShell";

type SyncStatus = "ok" | "diff" | "missing";

type SyncDiagnostic = PromptSyncTarget & {
  status: SyncStatus;
};

function formatTimestamp(value?: string | null): string {
  if (!value) {
    return "―";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP", { hour12: false });
}

function formatBytes(value?: number | null): string {
  if (value === undefined || value === null) {
    return "―";
  }
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const display = unitIndex === 0 ? Math.round(size).toString() : size.toFixed(1);
  return `${display} ${units[unitIndex]}`;
}

export function PromptManagerPage() {
  const { placeholderPanel } = useOutletContext<ShellOutletContext>();

  const [documents, setDocuments] = useState<PromptDocumentSummary[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [detail, setDetail] = useState<PromptDocumentDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [editorValue, setEditorValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);

  const refreshDocuments = useCallback(async () => {
    setDocumentsLoading(true);
    setDocumentsError(null);
    try {
      const data = await fetchPromptDocuments();
      setDocuments(data);
      setSelectedId((current) => {
        if (current && data.some((item) => item.id === current)) {
          return current;
        }
        return data[0]?.id ?? null;
      });
    } catch (error) {
      setDocuments([]);
      setDocumentsError(error instanceof Error ? error.message : String(error));
    } finally {
      setDocumentsLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (promptId: string) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      const data = await fetchPromptDocument(promptId);
      setDetail(data);
      setEditorValue(data.content);
    } catch (error) {
      setDetail(null);
      setEditorValue("");
      setDetailError(error instanceof Error ? error.message : String(error));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshDocuments();
  }, [refreshDocuments]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setEditorValue("");
      return;
    }
    loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  const hasUnsavedChanges = useMemo(() => {
    if (!detail) {
      return false;
    }
    return editorValue !== detail.content;
  }, [detail, editorValue]);

  const syncDiagnostics = useMemo<SyncDiagnostic[]>(() => {
    if (!detail) {
      return [];
    }
    return detail.sync_targets.map((target) => {
      let status: SyncStatus = "missing";
      if (target.exists) {
        status = target.checksum && target.checksum === detail.checksum ? "ok" : "diff";
      }
      return {
        ...target,
        status,
      };
    });
  }, [detail]);

  const handleSave = useCallback(async () => {
    if (!detail) {
      return;
    }
    setSaving(true);
    setSaveError(null);
    setSaveSuccess(null);
    try {
      const updated = await updatePromptDocument(detail.id, {
        content: editorValue,
        expectedChecksum: detail.checksum,
      });
      setDetail(updated);
      setEditorValue(updated.content);
      setSaveSuccess("保存しました");
      setDocuments((current) =>
        current.map((doc) =>
          doc.id === updated.id
            ? {
                ...doc,
                checksum: updated.checksum,
                size_bytes: updated.size_bytes,
                updated_at: updated.updated_at,
                sync_targets: updated.sync_targets,
              }
            : doc
        )
      );
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }, [detail, editorValue]);

  const handleReset = useCallback(() => {
    if (!detail) {
      return;
    }
    setEditorValue(detail.content);
    setSaveError(null);
    setSaveSuccess(null);
  }, [detail]);

  const handleReload = useCallback(() => {
    if (selectedId) {
      loadDetail(selectedId);
    }
  }, [selectedId, loadDetail]);

  return (
    <section className="main-content main-content--workspace">
      <div className="prompt-manager">
        <header className="prompt-manager__header">
          <div>
            <p className="prompt-manager__eyebrow">ScriptFactory / Prompts</p>
            <h1>台本プロンプト管理</h1>
            <p className="prompt-manager__subtitle">
              {placeholderPanel?.description ?? "台本量産ワークフローで使用する初期プロンプトを直接編集できます。"}
            </p>
          </div>
          <div className="prompt-manager__header-actions">
            <button
              type="button"
              className="action-button"
              onClick={refreshDocuments}
              disabled={documentsLoading}
            >
              {documentsLoading ? "更新中..." : "一覧を再読込"}
            </button>
          </div>
        </header>

        <div className="prompt-manager__grid">
          <section className="prompt-manager__panel prompt-manager__panel--list">
            <header>
              <h2>プロンプトファイル</h2>
              <p>Qwen の初期化に使うテンプレート。編集内容は即座に SoT へ反映されます。</p>
            </header>
            {documentsError ? <p className="prompt-manager__error">{documentsError}</p> : null}
            {documentsLoading && !documents.length ? <p className="prompt-manager__hint">読み込み中...</p> : null}
            {!documentsLoading && documents.length === 0 ? (
              <p className="prompt-manager__hint">管理対象のプロンプトが登録されていません。</p>
            ) : null}
            <ul className="prompt-manager__list">
              {documents.map((doc) => (
                <li key={doc.id}>
                  <button
                    type="button"
                    className={
                      doc.id === selectedId
                        ? "prompt-manager__doc-button prompt-manager__doc-button--active"
                        : "prompt-manager__doc-button"
                    }
                    onClick={() => setSelectedId(doc.id)}
                    disabled={doc.id === selectedId || documentsLoading}
                  >
                    <div className="prompt-manager__doc-title">{doc.label}</div>
                    <div className="prompt-manager__doc-path">{doc.relative_path}</div>
                    <div className="prompt-manager__doc-meta">
                      <span>{formatBytes(doc.size_bytes)}</span>
                      <span>{formatTimestamp(doc.updated_at)}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section className="prompt-manager__panel prompt-manager__panel--editor">
            <header className="prompt-manager__panel-header">
              <div>
                <h2>{detail?.label ?? "プロンプト内容"}</h2>
                <p>{detail?.description ?? "ファイルを選択して内容を編集してください。"}</p>
              </div>
              <div className="prompt-manager__editor-actions">
                <button
                  type="button"
                  className="action-button"
                  onClick={handleReload}
                  disabled={!selectedId || detailLoading}
                >
                  {detailLoading ? "読込中..." : "最新を取得"}
                </button>
                <button
                  type="button"
                  className="action-button"
                  onClick={handleReset}
                  disabled={!hasUnsavedChanges}
                >
                  変更を破棄
                </button>
                <button
                  type="button"
                  className="action-button action-button--primary"
                  onClick={handleSave}
                  disabled={!detail || !hasUnsavedChanges || saving}
                >
                  {saving ? "保存中..." : "保存"}
                </button>
              </div>
            </header>

            {detailError ? <p className="prompt-manager__error">{detailError}</p> : null}

            <div className="prompt-manager__meta-grid">
              <div>
                <span className="prompt-manager__meta-label">ファイル</span>
                <code className="prompt-manager__meta-value">{detail?.relative_path ?? "―"}</code>
              </div>
              <div>
                <span className="prompt-manager__meta-label">サイズ</span>
                <p className="prompt-manager__meta-value">{formatBytes(detail?.size_bytes)}</p>
              </div>
              <div>
                <span className="prompt-manager__meta-label">更新日時</span>
                <p className="prompt-manager__meta-value">{formatTimestamp(detail?.updated_at)}</p>
              </div>
              <div>
                <span className="prompt-manager__meta-label">チェックサム</span>
                <p className="prompt-manager__meta-value checksum-value">{detail?.checksum ?? "―"}</p>
              </div>
            </div>

            <label className="prompt-manager__textarea-label" htmlFor="prompt-editor">
              プロンプト本文
            </label>
            <textarea
              id="prompt-editor"
              className="prompt-manager__textarea"
              value={editorValue}
              onChange={(event) => {
                setEditorValue(event.target.value);
                setSaveError(null);
                setSaveSuccess(null);
              }}
              disabled={!detail || detailLoading}
              spellCheck={false}
            />

            <div className="prompt-manager__status-row">
              {saveSuccess ? <span className="prompt-manager__status prompt-manager__status--success">{saveSuccess}</span> : null}
              {saveError ? <span className="prompt-manager__status prompt-manager__status--error">{saveError}</span> : null}
              {!saveSuccess && !saveError && hasUnsavedChanges ? (
                <span className="prompt-manager__status">未保存の変更があります</span>
              ) : null}
            </div>

            <div>
              <h3 className="prompt-manager__sync-title">同期状態</h3>
              {syncDiagnostics.length === 0 ? (
                <p className="prompt-manager__hint">同期先は登録されていません。</p>
              ) : (
                <ul className="prompt-manager__sync-list">
                  {syncDiagnostics.map((target) => (
                    <li key={target.path} className="prompt-manager__sync-item">
                      <span className={`prompt-manager__sync-badge prompt-manager__sync-badge--${target.status}`}>
                        {target.status === "ok" && "同期済み"}
                        {target.status === "diff" && "差分あり"}
                        {target.status === "missing" && "未作成"}
                      </span>
                      <div className="prompt-manager__sync-path">{target.path}</div>
                      <div className="prompt-manager__sync-meta">
                        <span>{target.exists ? formatTimestamp(target.updated_at) : "―"}</span>
                        <span>{target.exists ? target.checksum ?? "―" : ""}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </div>
      </div>
    </section>
  );
}
