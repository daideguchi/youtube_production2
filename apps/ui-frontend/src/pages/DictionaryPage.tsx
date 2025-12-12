import { useCallback, useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import type { ShellOutletContext } from "../layouts/AppShell";
import {
  deleteChannelReadingEntry,
  deleteKnowledgeBaseEntry,
  fetchChannelReadingDict,
  fetchKnowledgeBase,
  upsertChannelReadingEntry,
  upsertKnowledgeBaseEntry,
} from "../api/client";
import "./DictionaryPage.css";

type TabKey = "global" | "channel";

export function DictionaryPage() {
  const { selectedChannel, channels } = useOutletContext<ShellOutletContext>();
  const [activeTab, setActiveTab] = useState<TabKey>("global");
  const [activeChannel, setActiveChannel] = useState<string>(selectedChannel ?? channels[0]?.code ?? "CH01");
  const [globalWords, setGlobalWords] = useState<Record<string, string>>({});
  const [channelEntries, setChannelEntries] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [surfaceInput, setSurfaceInput] = useState("");
  const [readingInput, setReadingInput] = useState("");
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editingReading, setEditingReading] = useState("");

  useEffect(() => {
    if (selectedChannel && selectedChannel !== activeChannel) {
      setActiveChannel(selectedChannel);
    }
  }, [selectedChannel, activeChannel]);

  const cancelEdit = useCallback(() => {
    setEditingKey(null);
    setEditingReading("");
  }, []);

  const startEdit = useCallback((key: string, reading: string) => {
    setEditingKey(key);
    setEditingReading(reading);
  }, []);

  useEffect(() => {
    cancelEdit();
  }, [activeTab, activeChannel, cancelEdit]);

  const loadGlobal = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const kb = await fetchKnowledgeBase();
      setGlobalWords(kb.words ?? {});
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadChannel = useCallback(async (channel: string) => {
    setLoading(true);
    setError(null);
    try {
      const dict = await fetchChannelReadingDict(channel);
      setChannelEntries(dict ?? {});
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadGlobal();
  }, [loadGlobal]);

  useEffect(() => {
    if (activeTab === "channel") {
      loadChannel(activeChannel);
    }
  }, [activeTab, activeChannel, loadChannel]);

  const filteredGlobal = useMemo(() => {
    const q = query.trim().toLowerCase();
    const entries = Object.entries(globalWords);
    if (!q) return entries.sort((a, b) => a[0].localeCompare(b[0]));
    return entries
      .filter(([word, reading]) => word.toLowerCase().includes(q) || reading.toLowerCase().includes(q))
      .sort((a, b) => a[0].localeCompare(b[0]));
  }, [globalWords, query]);

  const filteredChannel = useMemo(() => {
    const q = query.trim().toLowerCase();
    const entries = Object.entries(channelEntries);
    if (!q) return entries.sort((a, b) => a[0].localeCompare(b[0]));
    return entries
      .filter(([surface, meta]) => {
        const reading = String(meta?.reading_kana ?? meta?.reading_hira ?? "");
        return surface.toLowerCase().includes(q) || reading.toLowerCase().includes(q);
      })
      .sort((a, b) => a[0].localeCompare(b[0]));
  }, [channelEntries, query]);

  const handleUpsert = useCallback(async () => {
    const surface = surfaceInput.trim();
    const reading = readingInput.trim();
    if (!surface || !reading) {
      setError("単語と読みを入力してください。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      cancelEdit();
      if (activeTab === "global") {
        const kb = await upsertKnowledgeBaseEntry(surface, reading);
        setGlobalWords(kb.words ?? {});
      } else {
        const merged = await upsertChannelReadingEntry(activeChannel, {
          surface,
          reading_kana: reading,
        });
        setChannelEntries(merged ?? {});
      }
      setSurfaceInput("");
      setReadingInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [surfaceInput, readingInput, activeTab, activeChannel, cancelEdit]);

  const handleEditSave = useCallback(async () => {
    if (!editingKey) {
      return;
    }
    const reading = editingReading.trim();
    if (!reading) {
      setError("読みを入力してください。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      if (activeTab === "global") {
        const kb = await upsertKnowledgeBaseEntry(editingKey, reading);
        setGlobalWords(kb.words ?? {});
      } else {
        const merged = await upsertChannelReadingEntry(activeChannel, {
          surface: editingKey,
          reading_kana: reading,
        });
        setChannelEntries(merged ?? {});
      }
      cancelEdit();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [editingKey, editingReading, activeTab, activeChannel, cancelEdit]);

  const handleDelete = useCallback(
    async (key: string) => {
      if (!window.confirm(`「${key}」を辞書から削除しますか？`)) return;
      setLoading(true);
      setError(null);
      try {
        if (activeTab === "global") {
          await deleteKnowledgeBaseEntry(key);
          await loadGlobal();
        } else {
          await deleteChannelReadingEntry(activeChannel, key);
          await loadChannel(activeChannel);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [activeTab, activeChannel, loadGlobal, loadChannel]
  );

  return (
    <section className="dictionary-page">
      <header className="dictionary-page__header">
        <div>
          <h1>読み辞書 管理</h1>
          <p className="muted small-text">
            誤読を見つけたらここで登録 → 以降の TTS に反映されます。
          </p>
          <p className="muted small-text">
            Voicevox元読みは登録時に観測した1例です（文脈で変わるので参考値として扱ってください）。
          </p>
        </div>
        <div className="dictionary-page__tabs" role="tablist">
          <button
            type="button"
            className={`dictionary-page__tab${activeTab === "global" ? " is-active" : ""}`}
            onClick={() => setActiveTab("global")}
            role="tab"
            aria-selected={activeTab === "global"}
          >
            グローバル辞書
          </button>
          <button
            type="button"
            className={`dictionary-page__tab${activeTab === "channel" ? " is-active" : ""}`}
            onClick={() => setActiveTab("channel")}
            role="tab"
            aria-selected={activeTab === "channel"}
          >
            チャンネル辞書
          </button>
        </div>
      </header>

      {activeTab === "channel" ? (
        <div className="dictionary-page__channel-picker">
          <label>
            対象チャンネル:
            <select
              value={activeChannel}
              onChange={(event) => setActiveChannel(event.target.value)}
            >
              {channels.map((ch) => (
                <option key={ch.code} value={ch.code}>
                  {ch.name ?? ch.code}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : null}

      <div className="dictionary-page__toolbar">
        <input
          type="search"
          placeholder="単語/読みで検索"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button type="button" onClick={() => (activeTab === "global" ? loadGlobal() : loadChannel(activeChannel))} disabled={loading}>
          再読み込み
        </button>
      </div>

      <div className="dictionary-page__form">
        <label>
          単語
          <input
            value={surfaceInput}
            onChange={(event) => setSurfaceInput(event.target.value)}
            placeholder="例: 御伽噺"
          />
        </label>
        <label>
          読み（カナ）
          <input
            value={readingInput}
            onChange={(event) => setReadingInput(event.target.value)}
            placeholder="例: オトギバナシ"
          />
        </label>
        <button type="button" onClick={handleUpsert} disabled={loading}>
          追加/更新
        </button>
      </div>

      {error ? <p className="dictionary-page__error">{error}</p> : null}
      {loading ? <p className="dictionary-page__loading">読み込み中…</p> : null}

      <div className="dictionary-page__table-wrapper">
        <table className="dictionary-page__table">
          <thead>
            <tr>
              <th>単語</th>
              <th>読み</th>
              <th>Voicevox元読み</th>
              {activeTab === "channel" ? (
                <>
                  <th>MeCab</th>
                  <th>md</th>
                  <th>sim</th>
                  <th>更新日</th>
                </>
              ) : null}
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {activeTab === "global" ? (
              filteredGlobal.map(([word, reading]) => (
                <tr key={word}>
                  <td className="dictionary-page__surface">{word}</td>
                  <td>
                    {editingKey === word ? (
                      <input
                        className="dictionary-page__inline-input"
                        value={editingReading}
                        onChange={(event) => setEditingReading(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            handleEditSave();
                          }
                          if (event.key === "Escape") {
                            cancelEdit();
                          }
                        }}
                      />
                    ) : (
                      reading
                    )}
                  </td>
                  <td className="muted small-text dictionary-page__voicevox">—</td>
                  <td>
                    {editingKey === word ? (
                      <div className="dictionary-page__actions">
                        <button type="button" className="btn btn--primary" onClick={handleEditSave} disabled={loading}>
                          💾 保存
                        </button>
                        <button type="button" className="btn btn--ghost" onClick={cancelEdit} disabled={loading}>
                          ↩️ 取消
                        </button>
                      </div>
                    ) : (
                      <div className="dictionary-page__actions">
                        <button
                          type="button"
                          className="btn btn--ghost"
                          onClick={() => startEdit(word, reading)}
                          disabled={loading}
                          title="読みを編集"
                        >
                          ✏️ 編集
                        </button>
                        <button
                          type="button"
                          className="btn btn--danger"
                          onClick={() => handleDelete(word)}
                          disabled={loading}
                          title="辞書から削除"
                        >
                          🗑️ 削除
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))
            ) : (
              filteredChannel.map(([surface, meta]) => {
                const readingText = String(meta?.reading_kana ?? meta?.reading_hira ?? "");
                return (
                  <tr key={surface}>
                    <td className="dictionary-page__surface">{surface}</td>
                    <td>
                      {editingKey === surface ? (
                        <input
                          className="dictionary-page__inline-input"
                          value={editingReading}
                          onChange={(event) => setEditingReading(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.preventDefault();
                              handleEditSave();
                            }
                            if (event.key === "Escape") {
                              cancelEdit();
                            }
                          }}
                        />
                      ) : (
                        readingText
                      )}
                    </td>
                    <td className="muted small-text dictionary-page__voicevox">{meta?.voicevox_kana ?? "—"}</td>
                    <td className="muted small-text dictionary-page__metric">{meta?.mecab_kana ?? "—"}</td>
                    <td className="muted small-text dictionary-page__metric">
                      {meta?.mora_diff !== undefined && meta?.mora_diff !== null ? String(meta.mora_diff) : "—"}
                    </td>
                    <td className="muted small-text dictionary-page__metric">
                      {meta?.similarity !== undefined && meta?.similarity !== null
                        ? Number(meta.similarity).toFixed(2)
                        : "—"}
                    </td>
                    <td className="muted small-text">{meta?.last_updated ?? ""}</td>
                    <td>
                      {editingKey === surface ? (
                        <div className="dictionary-page__actions">
                          <button type="button" className="btn btn--primary" onClick={handleEditSave} disabled={loading}>
                            💾 保存
                          </button>
                          <button type="button" className="btn btn--ghost" onClick={cancelEdit} disabled={loading}>
                            ↩️ 取消
                          </button>
                        </div>
                      ) : (
                        <div className="dictionary-page__actions">
                          <button
                            type="button"
                            className="btn btn--ghost"
                            onClick={() => startEdit(surface, readingText)}
                            disabled={loading}
                            title="読みを編集"
                          >
                            ✏️ 編集
                          </button>
                          <button
                            type="button"
                            className="btn btn--danger"
                            onClick={() => handleDelete(surface)}
                            disabled={loading}
                            title="辞書から削除"
                          >
                            🗑️ 削除
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>

        {activeTab === "global" && filteredGlobal.length === 0 ? (
          <p className="dictionary-page__empty muted">辞書エントリがありません。</p>
        ) : null}
        {activeTab === "channel" && filteredChannel.length === 0 ? (
          <p className="dictionary-page__empty muted">チャンネル辞書にエントリがありません。</p>
        ) : null}
      </div>
    </section>
  );
}
