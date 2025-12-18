import { useCallback, useMemo, useState, type ReactNode } from "react";
import { updatePlanning } from "../api/client";
import type { ThumbnailChannelTemplates } from "../api/types";

type PlanningRow = Record<string, string>;

type ThumbnailBulkPanelProps = {
  channel: string;
  channelName?: string | null;
  channelTemplates?: ThumbnailChannelTemplates | null;
  planningRowsByVideo: Record<string, PlanningRow>;
  planningLoading: boolean;
  planningError?: string | null;
  onRefreshPlanning?: () => void;
  onUpdateLocalPlanningRow?: (video: string, patch: Partial<PlanningRow>) => void;
};

type BulkCopyEditState = {
  video: string;
  title: string;
  upper: string;
  middle: string;
  lower: string;
  saving: boolean;
  error?: string;
};

type CsvExportState = {
  pending: boolean;
  csv: string;
  filename: string;
  rowCount: number;
};

function csvEscape(value: string): string {
  const raw = value ?? "";
  if (/[",\n]/.test(raw)) {
    return `"${raw.replace(/"/g, '""')}"`;
  }
  return raw;
}

function downloadTextFile(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function copyToClipboard(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text);
  }
  return new Promise((resolve, reject) => {
    try {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
      resolve();
    } catch (error) {
      reject(error);
    }
  });
}

function normalizeWhitespace(value: string): string {
  return (value ?? "").replace(/\s+/g, " ").trim();
}

function renderInlinePreview(lines: { label: string; value: string; className: string }[]): ReactNode {
  return (
    <div className="thumbnail-bulk-preview">
      {lines.map((line) => (
        <div key={line.label} className={`thumbnail-bulk-preview__line ${line.className}`}>
          <span className="thumbnail-bulk-preview__badge">{line.label}</span>
          <span className="thumbnail-bulk-preview__text">{line.value || "—"}</span>
        </div>
      ))}
    </div>
  );
}

export function ThumbnailBulkPanel({
  channel,
  channelName,
  channelTemplates,
  planningRowsByVideo,
  planningLoading,
  planningError,
  onRefreshPlanning,
  onUpdateLocalPlanningRow,
}: ThumbnailBulkPanelProps) {
  const [query, setQuery] = useState("");
  const [filterMissing, setFilterMissing] = useState(false);
  const [copyEdit, setCopyEdit] = useState<BulkCopyEditState | null>(null);
  const [exportState, setExportState] = useState<CsvExportState | null>(null);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const rows = useMemo(() => {
    const items = Object.entries(planningRowsByVideo)
      .map(([video, row]) => ({ video, row }))
      .sort((a, b) => Number(a.video) - Number(b.video));
    const normalizedQuery = query.trim().toLowerCase();
    return items.filter(({ video, row }) => {
      const title = row["タイトル"] ?? "";
      const upper = row["サムネタイトル上"] ?? "";
      const middle = row["サムネタイトル"] ?? "";
      const lower = row["サムネタイトル下"] ?? "";
      const missing = !normalizeWhitespace(upper) || !normalizeWhitespace(middle) || !normalizeWhitespace(lower);
      if (filterMissing && !missing) {
        return false;
      }
      if (!normalizedQuery) {
        return true;
      }
      const hay = `${video} ${title} ${upper} ${middle} ${lower}`.toLowerCase();
      return hay.includes(normalizedQuery);
    });
  }, [filterMissing, planningRowsByVideo, query]);

  const style = channelTemplates?.channel_style ?? null;

  const openCopyEdit = useCallback(
    (video: string, row: PlanningRow) => {
      setCopyEdit({
        video,
        title: row["タイトル"] ?? "",
        upper: row["サムネタイトル上"] ?? "",
        middle: row["サムネタイトル"] ?? "",
        lower: row["サムネタイトル下"] ?? "",
        saving: false,
        error: undefined,
      });
    },
    []
  );

  const closeCopyEdit = useCallback(() => {
    setCopyEdit(null);
  }, []);

  const submitCopyEdit = useCallback(async () => {
    if (!copyEdit) {
      return;
    }
    const upper = normalizeWhitespace(copyEdit.upper);
    const middle = normalizeWhitespace(copyEdit.middle);
    const lower = normalizeWhitespace(copyEdit.lower);
    setCopyEdit((current) => (current ? { ...current, saving: true, error: undefined } : current));
    try {
      await updatePlanning(channel, copyEdit.video, {
        fields: {
          thumbnail_upper: upper ? upper : null,
          thumbnail_title: middle ? middle : null,
          thumbnail_lower: lower ? lower : null,
        },
      });
      onUpdateLocalPlanningRow?.(copyEdit.video, {
        サムネタイトル上: upper,
        サムネタイトル: middle,
        サムネタイトル下: lower,
      });
      setToast({ type: "success", message: `${channel}-${copyEdit.video} のコピーを保存しました。` });
      setCopyEdit(null);
      window.setTimeout(() => setToast(null), 2600);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setCopyEdit((current) => (current ? { ...current, saving: false, error: message } : current));
    }
  }, [channel, copyEdit, onUpdateLocalPlanningRow]);

  const openCsvExport = useCallback(() => {
    const timestamp = new Date()
      .toISOString()
      .replace(/[-:]/g, "")
      .replace(/\..+$/, "");
    const filename = `canva_thumbnails_${channel}_${timestamp}.csv`;

    const header = ["page_name", "channel", "video", "title", "thumb_upper", "thumb_title", "thumb_lower"];
    const lines: string[] = [header.join(",")];
    rows.forEach(({ video, row }) => {
      const title = row["タイトル"] ?? "";
      const upper = row["サムネタイトル上"] ?? "";
      const middle = row["サムネタイトル"] ?? "";
      const lower = row["サムネタイトル下"] ?? "";
      lines.push(
        [
          csvEscape(`${channel}-${video}`),
          csvEscape(channel),
          csvEscape(video),
          csvEscape(title),
          csvEscape(upper),
          csvEscape(middle),
          csvEscape(lower),
        ].join(",")
      );
    });
    const csv = `${lines.join("\n")}\n`;
    setExportState({ pending: false, csv, filename, rowCount: rows.length });
  }, [channel, rows]);

  const closeCsvExport = useCallback(() => {
    setExportState(null);
  }, []);

  const handleCsvCopy = useCallback(async () => {
    if (!exportState) {
      return;
    }
    setExportState((current) => (current ? { ...current, pending: true } : current));
    try {
      await copyToClipboard(exportState.csv);
      setToast({ type: "success", message: "CSVをクリップボードにコピーしました。" });
      window.setTimeout(() => setToast(null), 2400);
    } catch (error) {
      setToast({ type: "error", message: "コピーに失敗しました（権限/ブラウザ設定をご確認ください）。" });
      window.setTimeout(() => setToast(null), 3600);
    } finally {
      setExportState((current) => (current ? { ...current, pending: false } : current));
    }
  }, [exportState]);

  const handleCsvDownload = useCallback(() => {
    if (!exportState) {
      return;
    }
    downloadTextFile(exportState.filename, exportState.csv);
  }, [exportState]);

  return (
    <section className="thumbnail-bulk-panel">
      <header className="thumbnail-bulk-panel__header">
        <div>
          <h3>量産（Canva）</h3>
          <p className="thumbnail-bulk-panel__subtitle">
            コピーを整える → Canva一括取り込みCSVを出力 → 採用サムネを紐付け（任意）
          </p>
        </div>
        <div className="thumbnail-bulk-panel__actions">
          <button type="button" className="btn" onClick={openCsvExport} disabled={planningLoading || rows.length === 0}>
            Canva用CSV
          </button>
          <button type="button" className="btn btn--ghost" onClick={onRefreshPlanning} disabled={planningLoading}>
            企画CSVを再読込
          </button>
        </div>
      </header>

      {style ? (
        <section className="thumbnail-bulk-style">
          <div className="thumbnail-bulk-style__header">
            <div>
              <h4>このチャンネルの型</h4>
              <p>{style.name ?? "（未設定）"}</p>
            </div>
            <div className="thumbnail-bulk-style__meta">
              <span className="status-chip">{channelName ? `${channel} ${channelName}` : channel}</span>
              <span className="status-chip">AI生成: 生成後に実コスト表示</span>
              {style.benchmark_path ? (
                <span className="status-chip">
                  ベンチマーク: <code>{style.benchmark_path}</code>
                </span>
              ) : null}
            </div>
          </div>
          <div className="thumbnail-bulk-style__body">
            <div className="thumbnail-bulk-style__preview">
              {renderInlinePreview([
                { label: "上", value: style.preview_upper ?? "例: 放置は危険", className: "is-upper" },
                { label: "中", value: style.preview_title ?? "例: 夜の不安", className: "is-middle" },
                { label: "下", value: style.preview_lower ?? "例: 今夜眠れる", className: "is-lower" },
              ])}
            </div>
            <div className="thumbnail-bulk-style__rules">
              <ul>
                {(style.rules ?? []).slice(0, 8).map((rule) => (
                  <li key={rule}>{rule}</li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      ) : (
        <section className="thumbnail-bulk-style thumbnail-bulk-style--empty">
          <h4>このチャンネルの型</h4>
          <p className="muted">
            まだ型が登録されていません（テンプレ画面で <code>templates.json</code> を更新してください）。
          </p>
        </section>
      )}

      <section className="thumbnail-bulk-list">
        <div className="thumbnail-bulk-list__toolbar">
          <div className="thumbnail-bulk-list__search">
            <input
              type="search"
              placeholder="動画番号/タイトル/コピーで検索"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              disabled={planningLoading}
            />
          </div>
          <label className="thumbnail-bulk-list__toggle">
            <input
              type="checkbox"
              checked={filterMissing}
              onChange={(e) => setFilterMissing(e.target.checked)}
              disabled={planningLoading}
            />
            未入力のみ
          </label>
          <div className="thumbnail-bulk-list__count">{rows.length.toLocaleString("ja-JP")} 件</div>
        </div>

        {planningError ? <div className="thumbnail-alert thumbnail-alert--error">{planningError}</div> : null}
        {planningLoading ? <div className="thumbnail-loading">企画CSVを読み込み中…</div> : null}
        {!planningLoading && rows.length === 0 ? (
          <div className="thumbnail-empty">該当する企画がありません。</div>
        ) : null}

        <div className="thumbnail-bulk-grid">
          {rows.map(({ video, row }) => {
            const title = row["タイトル"] ?? "";
            const upper = row["サムネタイトル上"] ?? "";
            const middle = row["サムネタイトル"] ?? "";
            const lower = row["サムネタイトル下"] ?? "";
            const missing = !normalizeWhitespace(upper) || !normalizeWhitespace(middle) || !normalizeWhitespace(lower);
            return (
              <article key={video} className={`thumbnail-bulk-card${missing ? " is-missing" : ""}`}>
                <header className="thumbnail-bulk-card__header">
                  <div className="thumbnail-bulk-card__id">
                    <strong>
                      {channel}-{video}
                    </strong>
                    {missing ? <span className="thumbnail-bulk-card__badge">未入力</span> : null}
                  </div>
                  <div className="thumbnail-bulk-card__actions">
                    <button type="button" className="btn btn--ghost" onClick={() => openCopyEdit(video, row)}>
                      編集
                    </button>
                  </div>
                </header>
                <p className="thumbnail-bulk-card__title" title={title}>
                  {title || "（タイトル未設定）"}
                </p>
                {renderInlinePreview([
                  { label: "上", value: upper, className: "is-upper" },
                  { label: "中", value: middle, className: "is-middle" },
                  { label: "下", value: lower, className: "is-lower" },
                ])}
              </article>
            );
          })}
        </div>
      </section>

      {toast ? (
        <div className={`thumbnail-bulk-toast ${toast.type === "error" ? "is-error" : "is-success"}`}>{toast.message}</div>
      ) : null}

      {copyEdit ? (
        <div className="thumbnail-planning-dialog" role="dialog" aria-modal="true">
          <div className="thumbnail-planning-dialog__backdrop" onClick={closeCopyEdit} />
          <div className="thumbnail-planning-dialog__panel">
            <header className="thumbnail-planning-dialog__header">
              <div className="thumbnail-planning-dialog__eyebrow">
                <span className="status-chip">{channel}</span>
                <span className="status-chip">{copyEdit.video}</span>
              </div>
              <h2>サムネコピー編集</h2>
              <p className="thumbnail-planning-dialog__meta">{copyEdit.title}</p>
            </header>
            <div className="thumbnail-bulk-editor">
              <div className="thumbnail-bulk-editor__preview">
                {renderInlinePreview([
                  { label: "上", value: copyEdit.upper, className: "is-upper" },
                  { label: "中", value: copyEdit.middle, className: "is-middle" },
                  { label: "下", value: copyEdit.lower, className: "is-lower" },
                ])}
                <p className="thumbnail-bulk-editor__hint">
                  意味（台本コア）は変えず、表現だけ強くします（断言/禁止/だけで/今日で終わり等）。
                </p>
              </div>
              <div className="thumbnail-bulk-editor__fields">
                <label className="thumbnail-bulk-editor__field is-upper">
                  <span>上（赤）</span>
                  <input
                    type="text"
                    value={copyEdit.upper}
                    onChange={(e) => setCopyEdit((cur) => (cur ? { ...cur, upper: e.target.value } : cur))}
                    maxLength={40}
                    disabled={copyEdit.saving}
                  />
                </label>
                <label className="thumbnail-bulk-editor__field is-middle">
                  <span>中（黄）</span>
                  <input
                    type="text"
                    value={copyEdit.middle}
                    onChange={(e) => setCopyEdit((cur) => (cur ? { ...cur, middle: e.target.value } : cur))}
                    maxLength={40}
                    disabled={copyEdit.saving}
                  />
                </label>
                <label className="thumbnail-bulk-editor__field is-lower">
                  <span>下（白）</span>
                  <input
                    type="text"
                    value={copyEdit.lower}
                    onChange={(e) => setCopyEdit((cur) => (cur ? { ...cur, lower: e.target.value } : cur))}
                    maxLength={40}
                    disabled={copyEdit.saving}
                  />
                </label>
                {copyEdit.error ? <div className="thumbnail-planning-form__error">{copyEdit.error}</div> : null}
                <div className="thumbnail-bulk-editor__actions">
                  <button type="button" className="btn btn--ghost" onClick={closeCopyEdit} disabled={copyEdit.saving}>
                    キャンセル
                  </button>
                  <button type="button" className="btn" onClick={submitCopyEdit} disabled={copyEdit.saving}>
                    {copyEdit.saving ? "保存中…" : "保存"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {exportState ? (
        <div className="thumbnail-planning-dialog" role="dialog" aria-modal="true">
          <div className="thumbnail-planning-dialog__backdrop" onClick={closeCsvExport} />
          <div className="thumbnail-planning-dialog__panel">
            <header className="thumbnail-planning-dialog__header">
              <div className="thumbnail-planning-dialog__eyebrow">
                <span className="status-chip">Canva</span>
                <span className="status-chip">
                  {channel}-{exportState.rowCount.toLocaleString("ja-JP")}件
                </span>
              </div>
              <h2>Canva一括取り込みCSV</h2>
              <p className="thumbnail-planning-dialog__meta">
                1行=1サムネ。Canvaの「Bulk create」で列をマッピングしてください。
              </p>
            </header>
            <div className="thumbnail-bulk-export">
              <div className="thumbnail-bulk-export__actions">
                <button type="button" className="btn" onClick={handleCsvCopy} disabled={exportState.pending}>
                  {exportState.pending ? "コピー中…" : "コピー"}
                </button>
                <button type="button" className="btn btn--ghost" onClick={handleCsvDownload} disabled={exportState.pending}>
                  ダウンロード
                </button>
                <button type="button" className="btn btn--ghost" onClick={closeCsvExport} disabled={exportState.pending}>
                  閉じる
                </button>
              </div>
              <textarea className="thumbnail-bulk-export__textarea" value={exportState.csv} readOnly rows={14} />
              <p className="muted small-text">
                列: <code>page_name, channel, video, title, thumb_upper, thumb_title, thumb_lower</code>
              </p>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
