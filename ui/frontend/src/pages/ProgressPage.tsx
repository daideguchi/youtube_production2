import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchChannels, fetchProgressCsv, updateVideoRedo, fetchRedoSummary, lookupThumbnails, refreshPlanningStore } from "../api/client";
import type { ChannelSummary, RedoSummaryItem, ThumbnailLookupItem } from "../api/types";
import { RedoBadge } from "../components/RedoBadge";
import "./ProgressPage.css";

type Row = Record<string, string>;
const CHANNELS = ["CH01","CH02","CH03","CH04","CH05","CH06","CH07","CH08","CH09","CH10","CH11"];

const CHANNEL_META: Record<string, { icon: string; color: string }> = {
  CH01: { icon: "🎯", color: "chip-cyan" },
  CH02: { icon: "📚", color: "chip-blue" },
  CH03: { icon: "💡", color: "chip-green" },
  CH04: { icon: "🧭", color: "chip-indigo" },
  CH05: { icon: "💞", color: "chip-pink" },
  CH06: { icon: "🕯️", color: "chip-purple" },
  CH07: { icon: "🌿", color: "chip-emerald" },
  CH08: { icon: "🌙", color: "chip-slate" },
  CH09: { icon: "🏛️", color: "chip-amber" },
  CH10: { icon: "🧠", color: "chip-orange" },
  CH11: { icon: "📜", color: "chip-teal" },
};

const LONG_COLUMNS = new Set([
  "企画意図",
  "具体的な内容（話の構成案）",
  "説明文_この動画でわかること",
  "説明文_リード",
  "DALL-Eプロンプト（URL・テキスト指示込み）",
  "サムネ画像プロンプト（URL・テキスト指示込み）",
  "台本本文",
  "台本",
  "台本パス",
  "内容",
  "内容（企画要約）",
  "動画内挿絵AI向けプロンプト（10個）",
]);

const NARROW_COLUMNS = new Set(["動画番号", "動画ID", "進捗"]);
const MEDIUM_COLUMNS = new Set(["タイトル", "音声生成", "音声品質", "納品"]);
const THUMB_COLUMNS = new Set(["サムネ"]);

const COMPACT_PRIORITY = [
  "動画番号",
  "動画ID",
  "タイトル",
  "サムネ",
  "進捗",
  "更新日時",
  "台本パス",
  "企画意図",
  "具体的な内容（話の構成案）",
  "ターゲット層",
  "悩みタグ_メイン",
  "悩みタグ_サブ",
  "ライフシーン",
  "キーコンセプト",
  "ベネフィット一言",
  "説明文_リード",
  "説明文_この動画でわかること",
  "サムネタイトル",
  "サムネタイトル上",
  "サムネタイトル下",
  "音声生成",
  "音声品質",
  "納品",
];

const toBool = (v: any, fallback = true) => {
  if (v === true || v === false) return v;
  if (typeof v === "string") {
    const s = v.toLowerCase();
    if (["true", "1", "yes", "y", "ok", "redo"].includes(s)) return true;
    if (["false", "0", "no", "n"].includes(s)) return false;
  }
  return fallback;
};

export function ProgressPage() {
  const [channel, setChannel] = useState<string>("CH02");
  const [rows, setRows] = useState<Row[]>([]);
  const [filteredRows, setFilteredRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [redoOnly, setRedoOnly] = useState(false);
  const [detailRow, setDetailRow] = useState<Row | null>(null);
  const [channelMap, setChannelMap] = useState<Record<string, ChannelSummary>>({});
  const [saving, setSaving] = useState(false);
  const [redoScriptValue, setRedoScriptValue] = useState<boolean>(true);
  const [redoAudioValue, setRedoAudioValue] = useState<boolean>(true);
  const [redoNoteValue, setRedoNoteValue] = useState<string>("");
  const [redoSummary, setRedoSummary] = useState<RedoSummaryItem | null>(null);
  const [thumbMap, setThumbMap] = useState<Record<string, ThumbnailLookupItem[]>>({});
  const [thumbPreview, setThumbPreview] = useState<string | null>(null);
  const [thumbPreviewItems, setThumbPreviewItems] = useState<ThumbnailLookupItem[] | null>(null);
  const [thumbPreviewIndex, setThumbPreviewIndex] = useState<number>(0);
  const [selectedCell, setSelectedCell] = useState<{ key: string; value: string } | null>(null);
  const thumbRequestedRef = useRef<Set<string>>(new Set());

  const findThumbOverride = useCallback((row: Row): string | null => {
    // 明示的なサムネ列を優先
    const explicitKeys = ["thumbnail_url", "サムネURL", "サムネ画像URL", "サムネ画像"];
    for (const key of explicitKeys) {
      const v = row[key];
      if (typeof v === "string" && v.trim()) {
        const val = v.trim();
        if (/https?:\/\/.+\.(png|jpe?g|webp)$/i.test(val)) return val;
        if (/(\.png|\.jpg|\.jpeg|\.webp)$/i.test(val)) return val;
      }
    }
    // それ以外のセルからもURL/拡張子を拾う
    for (const value of Object.values(row)) {
      if (typeof value !== "string") continue;
      const v = value.trim();
      if (!v) continue;
      if (/https?:\/\/.+\.(png|jpe?g|webp)$/i.test(v)) return v;
      if (/(\.png|\.jpg|\.jpeg|\.webp)$/i.test(v)) return v;
    }
    return null;
  }, []);

  const requestThumbForRow = useCallback(
    (row: Row) => {
      const ch = row["チャンネル"] || channel;
      const vid = row["動画番号"] || row["video"] || "";
      if (!ch || !vid) return;
      const key = `${ch}-${vid}`;
      if (thumbRequestedRef.current.has(key)) return;
      thumbRequestedRef.current.add(key);

      const override = findThumbOverride(row);
      if (override) {
        setThumbMap((prev) => ({
          ...prev,
          [key]: [{ path: override, url: override, name: override }],
        }));
        return;
      }

      lookupThumbnails(ch, vid, row["タイトル"] || undefined, 1)
        .then((res) => {
          setThumbMap((prev) => ({
            ...prev,
            [key]: res.items || [],
          }));
        })
        .catch(() => {
          // allow retry later on scroll/refresh
          thumbRequestedRef.current.delete(key);
        });
    },
    [channel, findThumbOverride]
  );

  useEffect(() => {
    Object.keys(thumbMap).forEach((key) => thumbRequestedRef.current.add(key));
  }, [thumbMap]);

  useEffect(() => {
    // load channel metadata for icons
    fetchChannels()
      .then((list) => {
        const map: Record<string, ChannelSummary> = {};
        list.forEach((c) => {
          map[c.code] = c;
        });
        setChannelMap(map);
      })
      .catch(() => {
        /* non-blocking */
      });
  }, []);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetchProgressCsv(channel);
        setRows(res.rows || []);
        const summary = await fetchRedoSummary(channel);
        setRedoSummary(summary[0] ?? null);
      } catch (e: any) {
        setError(e?.message || "読み込みに失敗しました");
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [channel]);

  useEffect(() => {
    const next = redoOnly
      ? rows.filter((row) => toBool(row["redo_script"], true) || toBool(row["redo_audio"], true))
      : rows;
    setFilteredRows(next);
    // サムネを上位40件だけ事前取得（ベストエフォート）
    next.slice(0, 40).forEach(requestThumbForRow);
  }, [rows, redoOnly, channel, requestThumbForRow]);

  const columns = useMemo(() => {
    const first = rows[0];
    if (!first) return ["動画番号", "タイトル", "進捗", "更新日時", "台本パス"];
    const all = Object.keys(first);
    const priority = COMPACT_PRIORITY.filter((c) => all.includes(c));
    const rest = all.filter((c) => !priority.includes(c));
    const ordered = [...priority, ...rest];
    if (!ordered.includes("サムネ")) {
      const titleIndex = ordered.indexOf("タイトル");
      if (titleIndex >= 0) {
        ordered.splice(titleIndex + 1, 0, "サムネ");
      } else {
        ordered.unshift("サムネ");
      }
    }
    if (showAll) return ordered;
    // compact: keep first 16 cols (主要確認列を含む)
    return ordered.slice(0, Math.min(16, ordered.length));
  }, [rows, showAll]);

  useEffect(() => {
    if (!detailRow) return;
    setRedoScriptValue(toBool(detailRow["redo_script"], true));
    setRedoAudioValue(toBool(detailRow["redo_audio"], true));
    setRedoNoteValue(detailRow["redo_note"] || "");
  }, [detailRow]);

  return (
    <div className="progress-page">
      <div className="progress-page__controls">
        <label>
          チャンネル:
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            {CHANNELS.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
        <div className="progress-page__channel-icons">
          {CHANNELS.map((c) => (
            <button
              key={c}
              type="button"
              className={`progress-page__chip ${channel === c ? "is-active" : ""} ${CHANNEL_META[c]?.color || ""}`}
              onClick={() => setChannel(c)}
              title={c}
            >
              {channelMap[c]?.branding?.avatar_url ? (
                <img
                  src={channelMap[c]?.branding?.avatar_url || ""}
                  alt={c}
                  className="progress-page__chip-avatar"
                />
              ) : (
                <span className="progress-page__chip-icon" aria-hidden="true">
                  {CHANNEL_META[c]?.icon || "●"}
                </span>
              )}
              <span className="progress-page__chip-text">{c}</span>
            </button>
          ))}
        </div>
        <label className="progress-page__toggle">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
          />
          全列を表示
        </label>
        <label className="progress-page__toggle">
          <input
            type="checkbox"
            checked={redoOnly}
            onChange={(e) => setRedoOnly(e.target.checked)}
          />
          リテイクのみ
        </label>
        <button
          type="button"
          className="progress-page__refresh"
          onClick={async () => {
            setLoading(true);
            setError(null);
            try {
              await refreshPlanningStore(channel);
              const res = await fetchProgressCsv(channel);
              setRows(res.rows || []);
              const summary = await fetchRedoSummary(channel);
              setRedoSummary(summary[0] ?? null);
            } catch (e: any) {
              setError(e?.message || "再読込に失敗しました");
            } finally {
              setLoading(false);
            }
          }}
          disabled={loading}
          title="外部で編集した企画CSVを強制再読込します"
        >
          企画を再読込
        </button>
        {redoSummary ? (
          <div className="progress-page__summary">
            <RedoBadge note="台本リテイク件数" label={`台本 ${redoSummary.redo_script}`} />
            <RedoBadge note="音声リテイク件数" label={`音声 ${redoSummary.redo_audio}`} />
            <RedoBadge note="両方リテイク件数" label={`両方 ${redoSummary.redo_both}`} />
          </div>
        ) : null}
        {loading && <span className="progress-page__status">読み込み中...</span>}
        {error && <span className="progress-page__error">{error}</span>}
      </div>
      <div className="progress-page__table-wrapper">
        <table className="progress-page__table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((row, idx) => (
              <tr
                key={idx}
                className="progress-page__row"
                onClick={() => setDetailRow(row)}
              >
                {columns.map((col) => {
                  const isRedo = toBool(row["redo_script"], true) || toBool(row["redo_audio"], true);
                  const isLong = LONG_COLUMNS.has(col);
                  const isNarrow = NARROW_COLUMNS.has(col);
                  const isMedium = MEDIUM_COLUMNS.has(col);
                  const isThumb = THUMB_COLUMNS.has(col);
                  const thumbKey = `${row["チャンネル"] || channel}-${row["動画番号"] || row["video"] || ""}`;
                  const thumbs = thumbMap[thumbKey] || [];
                  if (isThumb && !thumbMap[thumbKey]) {
                    requestThumbForRow(row);
                  }
                  return (
                    <td
                      key={col}
                      className={`${isLong ? "progress-page__cell progress-page__cell--long" : "progress-page__cell"}${isNarrow ? " progress-page__cell--narrow" : ""}${
                        isMedium ? " progress-page__cell--medium" : ""
                      }${isThumb ? " progress-page__cell--thumb" : ""} ${isRedo ? "progress-page__cell--redo" : ""}`}
                      title={row[col] ?? ""}
                    >
                      {col === "タイトル" && isRedo ? (
                        <span
                          className="progress-page__redo-dot"
                          title={row["redo_note"] || "リテイク対象"}
                          aria-label="リテイク対象"
                        />
                      ) : null}
                      {col === "サムネ" ? (
                        thumbs.length ? (
                          <button
                            type="button"
                            className="progress-page__thumb"
                            onClick={(e) => {
                              e.stopPropagation();
                              setThumbPreviewItems(thumbs);
                              setThumbPreviewIndex(0);
                              setThumbPreview(thumbs[0].url);
                            }}
                            title="サムネをプレビュー"
                          >
                            <img src={thumbs[0].url} alt="thumb" loading="lazy" />
                            {thumbs.length > 1 ? (
                              <span className="progress-page__thumb-count">+{thumbs.length - 1}</span>
                            ) : null}
                          </button>
                        ) : (
                          <span className="progress-page__cell-text muted">なし</span>
                        )
                      ) : (
                        <span className="progress-page__cell-text" title={row[col] ?? ""}>
                          {row[col] ?? ""}
                          {isLong && (row[col] ?? "").length > 0 ? (
                            <button
                              type="button"
                              className="progress-page__expand"
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedCell({ key: col, value: row[col] ?? "" });
                              }}
                            >
                              全文
                            </button>
                          ) : null}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detailRow && (
        <div className="progress-page__overlay" onClick={() => setDetailRow(null)}>
          <div className="progress-page__detail" onClick={(e) => e.stopPropagation()}>
            <div className="progress-page__detail-header">
              <div className="progress-page__detail-title">
                {detailRow["動画ID"] || detailRow["動画番号"] || ""} {detailRow["タイトル"] || ""}
              </div>
              <button className="progress-page__close" onClick={() => setDetailRow(null)}>× 閉じる</button>
            </div>
            <div className="progress-page__detail-body">
              <div className="progress-page__detail-row">
                <div className="progress-page__detail-key">リテイク（台本）</div>
                <div className="progress-page__detail-value">
                  <label className="progress-page__toggle">
                    <input
                      type="checkbox"
                      checked={redoScriptValue}
                      onChange={(e) => setRedoScriptValue(e.target.checked)}
                    />
                    再作成が必要
                  </label>
                </div>
              </div>
              <div className="progress-page__detail-row">
                <div className="progress-page__detail-key">リテイク（音声）</div>
                <div className="progress-page__detail-value">
                  <label className="progress-page__toggle">
                    <input
                      type="checkbox"
                      checked={redoAudioValue}
                      onChange={(e) => setRedoAudioValue(e.target.checked)}
                    />
                    再収録が必要
                  </label>
                </div>
              </div>
              <div className="progress-page__detail-row">
                <div className="progress-page__detail-key">リテイクメモ</div>
                <div className="progress-page__detail-value">
                  <textarea
                    className="progress-page__note"
                    value={redoNoteValue}
                    onChange={(e) => setRedoNoteValue(e.target.value)}
                    rows={3}
                  />
                  <div className="progress-page__note-actions">
                    <button
                      className="progress-page__save"
                      onClick={async () => {
                        if (!detailRow) return;
                        setSaving(true);
                        try {
                          await updateVideoRedo(
                            detailRow["チャンネル"] || detailRow["チャンネルコード"] || channel,
                            detailRow["動画番号"] || detailRow["video"] || "",
                            {
                              redo_script: redoScriptValue,
                              redo_audio: redoAudioValue,
                              redo_note: redoNoteValue,
                            }
                          );
                          setRows((prev) =>
                            prev.map((r) =>
                              (r["動画番号"] || r["video"]) === (detailRow["動画番号"] || detailRow["video"])
                                ? {
                                    ...r,
                                    redo_script: redoScriptValue ? "true" : "false",
                                    redo_audio: redoAudioValue ? "true" : "false",
                                    redo_note: redoNoteValue,
                                  }
                                : r
                            )
                          );
                          setDetailRow((prev) =>
                            prev
                              ? {
                                  ...prev,
                                  redo_script: redoScriptValue ? "true" : "false",
                                  redo_audio: redoAudioValue ? "true" : "false",
                                  redo_note: redoNoteValue,
                                }
                              : prev
                          );
                        } finally {
                          setSaving(false);
                        }
                      }}
                      disabled={saving}
                    >
                      {saving ? "保存中..." : "保存"}
                    </button>
                  </div>
                </div>
              </div>
              {Object.entries(detailRow).map(([k, v]) => (
                <div key={k} className="progress-page__detail-row">
                  <div className="progress-page__detail-key">{k}</div>
                  <div className="progress-page__detail-value">{v || ""}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      {thumbPreview ? (
        <div className="progress-page__overlay" onClick={() => setThumbPreview(null)}>
          <div className="progress-page__preview" onClick={(e) => e.stopPropagation()}>
            <button className="progress-page__close" onClick={() => setThumbPreview(null)}>× 閉じる</button>
            <div className="progress-page__preview-body">
              <img src={thumbPreview} alt="thumbnail preview" loading="lazy" />
              {thumbPreviewItems && thumbPreviewItems.length > 1 ? (
                <div className="progress-page__preview-strip">
                  {thumbPreviewItems.map((item, i) => (
                    <button
                      key={`${item.path}-${i}`}
                      type="button"
                      className={`progress-page__preview-thumb ${i === thumbPreviewIndex ? "is-active" : ""}`}
                      onClick={() => {
                        setThumbPreviewIndex(i);
                        setThumbPreview(item.url);
                      }}
                    >
                      <img src={item.url} alt={`thumb ${i + 1}`} loading="lazy" />
                    </button>
                  ))}
                </div>
              ) : null}
              <a href={thumbPreview} target="_blank" rel="noreferrer" className="progress-page__preview-link">別タブで開く ↗</a>
            </div>
          </div>
        </div>
      ) : null}

      {selectedCell ? (
        <div className="progress-page__inspector">
          <div className="progress-page__inspector-header">
            <div className="progress-page__inspector-title">{selectedCell.key}</div>
            <button className="progress-page__close" onClick={() => setSelectedCell(null)}>
              × 閉じる
            </button>
          </div>
          <div className="progress-page__inspector-body">
            <pre className="progress-page__inspector-text">{selectedCell.value}</pre>
          </div>
        </div>
      ) : null}
    </div>
  );
}
