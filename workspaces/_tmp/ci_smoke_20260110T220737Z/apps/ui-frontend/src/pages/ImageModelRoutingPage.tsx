import { useCallback, useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { fetchImageModelRouting, updateImageModelRoutingChannel } from "../api/client";
import type {
  ChannelImageModelRouting,
  ChannelSummary,
  ImageModelCatalogOption,
  ImageModelRoutingResponse,
  ImageModelRoutingSelection,
  ImageModelRoutingUpdatePayload,
} from "../api/types";
import type { ShellOutletContext } from "../layouts/AppShell";

type DraftRow = {
  thumbnail_model_key?: string;
  video_image_model_key?: string;
};

function normalizeKey(value?: string | null): string {
  return (value ?? "").trim();
}

function groupByProvider(options: ImageModelCatalogOption[]): Record<string, ImageModelCatalogOption[]> {
  const groups: Record<string, ImageModelCatalogOption[]> = {};
  for (const opt of options) {
    const key = opt.provider_group || "other";
    if (!groups[key]) groups[key] = [];
    groups[key].push(opt);
  }
  return groups;
}

function selectionLabel(sel: ImageModelRoutingSelection): string {
  const mk = normalizeKey(sel.model_key ?? "");
  if (!mk) return "未設定";
  const provider = normalizeKey(sel.provider ?? "");
  const model = normalizeKey(sel.model_name ?? "");
  if (provider && model) return `${mk} (${provider} / ${model})`;
  if (provider) return `${mk} (${provider})`;
  return mk;
}

function channelNameFromList(channels: ChannelSummary[], code: string): string {
  const hit = channels.find((c) => (c.code ?? "").toUpperCase() === code.toUpperCase());
  return (hit?.name ?? "").trim() || code;
}

export function ImageModelRoutingPage() {
  const { channels: channelSummaries } = useOutletContext<ShellOutletContext>();
  const [data, setData] = useState<ImageModelRoutingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [drafts, setDrafts] = useState<Record<string, DraftRow>>({});
  const [saving, setSaving] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    setBanner(null);
    try {
      const resp = await fetchImageModelRouting();
      setData(resp);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rows = useMemo(() => {
    const list = data?.channels ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((row) => {
      const name = channelNameFromList(channelSummaries, row.channel).toLowerCase();
      return row.channel.toLowerCase().includes(q) || name.includes(q);
    });
  }, [data, query, channelSummaries]);

  const thumbnailOptions = useMemo(() => data?.catalog?.thumbnail ?? [], [data]);
  const videoOptions = useMemo(() => data?.catalog?.video_image ?? [], [data]);
  const thumbGroups = useMemo(() => groupByProvider(thumbnailOptions), [thumbnailOptions]);
  const videoGroups = useMemo(() => groupByProvider(videoOptions), [videoOptions]);

  const handleDraftChange = useCallback(
    (channel: string, key: keyof DraftRow, value: string) => {
      setDrafts((prev) => ({
        ...prev,
        [channel]: {
          ...(prev[channel] ?? {}),
          [key]: value,
        },
      }));
    },
    []
  );

  const resolveDraftValue = useCallback(
    (row: ChannelImageModelRouting, key: keyof DraftRow): string => {
      const drafted = drafts[row.channel]?.[key];
      if (drafted !== undefined) return drafted;
      if (key === "thumbnail_model_key") return normalizeKey(row.thumbnail.model_key ?? "");
      return normalizeKey(row.video_image.model_key ?? "");
    },
    [drafts]
  );

  const buildUpdatePayload = useCallback(
    (row: ChannelImageModelRouting): ImageModelRoutingUpdatePayload | null => {
      const d = drafts[row.channel];
      if (!d) return null;
      const payload: ImageModelRoutingUpdatePayload = {};
      if (d.thumbnail_model_key !== undefined) {
        const cur = normalizeKey(row.thumbnail.model_key ?? "");
        const next = normalizeKey(d.thumbnail_model_key);
        if (next !== cur) payload.thumbnail_model_key = next;
      }
      if (d.video_image_model_key !== undefined) {
        const cur = normalizeKey(row.video_image.model_key ?? "");
        const next = normalizeKey(d.video_image_model_key);
        if (next !== cur) payload.video_image_model_key = next;
      }
      return Object.keys(payload).length > 0 ? payload : null;
    },
    [drafts]
  );

  const handleSave = useCallback(
    async (row: ChannelImageModelRouting) => {
      const payload = buildUpdatePayload(row);
      if (!payload) return;
      setSaving((prev) => ({ ...prev, [row.channel]: true }));
      setBanner(null);
      setError(null);
      try {
        const updated = await updateImageModelRoutingChannel(row.channel, payload);
        setData((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            channels: prev.channels.map((it) => (it.channel === row.channel ? updated : it)),
          };
        });
        setDrafts((prev) => {
          const next = { ...prev };
          delete next[row.channel];
          return next;
        });
        setBanner(`${row.channel} を保存しました`);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setSaving((prev) => ({ ...prev, [row.channel]: false }));
      }
    },
    [buildUpdatePayload]
  );

  return (
    <section className="main-content" style={{ padding: 18 }}>
      <div className="main-status" style={{ justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span className="status-chip">画像モデル設定（チャンネル別）</span>
          <span className="status-chip" style={{ opacity: 0.8 }}>
            サムネ（thumbnail_image_gen）と動画内画像（visual_image_gen）のモデルキーを整理して指定します。
          </span>
          <span className="status-chip status-chip--danger" style={{ opacity: 0.9 }}>
            ポリシー: Gemini 3 preview は設定/使用しません（BANリスク）
          </span>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="CHコード/チャンネル名で検索…"
            style={{
              padding: "8px 10px",
              borderRadius: 8,
              border: "1px solid rgba(148,163,184,0.35)",
              background: "rgba(15,23,42,0.18)",
              color: "inherit",
              minWidth: 260,
            }}
          />
          <button type="button" className="workspace-button" onClick={() => void refresh()} disabled={loading}>
            再読み込み
          </button>
        </div>
      </div>

      {loading || error || banner ? (
        <div className="main-status" style={{ marginTop: 12, gap: 10, flexWrap: "wrap" }}>
          {loading ? <span className="status-chip">読み込み中…</span> : null}
          {banner ? <span className="status-chip">{banner}</span> : null}
          {error ? <span className="status-chip status-chip--danger">{error}</span> : null}
        </div>
      ) : null}

      <div style={{ marginTop: 14, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <span className="status-chip" style={{ opacity: 0.85 }}>
          チャンネル数: {rows.length}
        </span>
        {data?.generated_at ? (
          <span className="status-chip" style={{ opacity: 0.75 }}>
            generated_at: <span className="mono">{data.generated_at}</span>
          </span>
        ) : null}
      </div>

      <div style={{ marginTop: 16, overflowX: "auto" }}>
        <table style={{ width: "100%", minWidth: 980, borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>
                Channel
              </th>
              <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>
                サムネ
              </th>
              <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>
                動画内画像
              </th>
              <th style={{ textAlign: "left", padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.25)" }}>
                操作
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const name = channelNameFromList(channelSummaries, row.channel);
              const thumbValue = resolveDraftValue(row, "thumbnail_model_key");
              const videoValue = resolveDraftValue(row, "video_image_model_key");
              const dirty = Boolean(drafts[row.channel]);
              const payload = buildUpdatePayload(row);
              const hasChanges = Boolean(payload);
              const invalidThumbEmpty = payload?.thumbnail_model_key === "";
              const savingNow = Boolean(saving[row.channel]);
              const thumbDisabled = Boolean(row.thumbnail.blocked);
              const videoDisabled = Boolean(row.video_image.blocked);

              const canSave = hasChanges && !savingNow && !(thumbDisabled || videoDisabled) && !invalidThumbEmpty;
              return (
                <tr key={row.channel}>
                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span className="mono" style={{ fontWeight: 700 }}>
                        {row.channel}
                      </span>
                      <span style={{ opacity: 0.85 }}>{name}</span>
                    </div>
                  </td>

                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      <select
                        value={thumbValue}
                        onChange={(e) => handleDraftChange(row.channel, "thumbnail_model_key", e.target.value)}
                        style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid rgba(148,163,184,0.35)" }}
                      >
                        <option value="">（未設定）</option>
                        {Object.entries(thumbGroups).map(([group, opts]) => (
                          <optgroup key={group} label={group}>
                            {opts.map((opt) => (
                              <option key={opt.id} value={opt.model_key ?? ""} disabled={!opt.enabled}>
                                {opt.label}
                              </option>
                            ))}
                          </optgroup>
                        ))}
                      </select>
                      <div style={{ fontSize: 12, opacity: 0.8 }}>
                        <span className="mono">{selectionLabel(row.thumbnail)}</span>
                        {row.thumbnail.note ? <span> · {row.thumbnail.note}</span> : null}
                      </div>
                    </div>
                  </td>

                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      <select
                        value={videoValue}
                        onChange={(e) => handleDraftChange(row.channel, "video_image_model_key", e.target.value)}
                        style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid rgba(148,163,184,0.35)" }}
                      >
                        <option value="">（tier default / 未設定）</option>
                        {Object.entries(videoGroups).map(([group, opts]) => (
                          <optgroup key={group} label={group}>
                            {opts.map((opt) => (
                              <option key={opt.id} value={opt.model_key ?? ""} disabled={!opt.enabled}>
                                {opt.label}
                              </option>
                            ))}
                          </optgroup>
                        ))}
                      </select>
                      <div style={{ fontSize: 12, opacity: 0.8 }}>
                        <span className="mono">{selectionLabel(row.video_image)}</span>
                        {row.video_image.note ? <span> · {row.video_image.note}</span> : null}
                      </div>
                    </div>
                  </td>

                  <td style={{ padding: "10px 10px", borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                      <button
                        type="button"
                        className={canSave ? "workspace-button workspace-button--primary" : "workspace-button"}
                        onClick={() => void handleSave(row)}
                        disabled={!canSave}
                      >
                        {savingNow ? "保存中…" : hasChanges ? "保存" : "変更なし"}
                      </button>
                      <button
                        type="button"
                        className="workspace-button"
                        onClick={() =>
                          setDrafts((prev) => {
                            const next = { ...prev };
                            delete next[row.channel];
                            return next;
                          })
                        }
                        disabled={!dirty || savingNow}
                      >
                        取消
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
