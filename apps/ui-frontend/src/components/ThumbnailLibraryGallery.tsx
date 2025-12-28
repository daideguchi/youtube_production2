import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchThumbnailLibrary,
  fetchThumbnailOverview,
  resolveApiUrl,
  deleteThumbnailLibraryAsset,
} from "../api/client";
import { ThumbnailChannelBlock, ThumbnailLibraryAsset, ThumbnailOverview } from "../api/types";

type ChannelLibraryState = {
  assets: ThumbnailLibraryAsset[];
  loading: boolean;
  loaded: boolean;
  error?: string | null;
};

export function ThumbnailLibraryGallery() {
  const [overview, setOverview] = useState<ThumbnailOverview | null>(null);
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);
  const [channelStates, setChannelStates] = useState<Record<string, ChannelLibraryState>>({});
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [showQcOnly, setShowQcOnly] = useState<boolean>(false);
  const [refreshNonce, setRefreshNonce] = useState<number>(0);
  const loadedChannelsRef = useRef<Set<string>>(new Set());

  const channels: ThumbnailChannelBlock[] = useMemo(() => overview?.channels ?? [], [overview]);
  const activeState = selectedChannel ? channelStates[selectedChannel] : undefined;
  const visibleAssets = useMemo(() => {
    const assets = [...(activeState?.assets ?? [])];
    assets.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
    if (!showQcOnly) return assets;
    return assets.filter((asset) => {
      const rel = (asset.relative_path ?? "").replace(/\\/g, "/");
      return (
        rel.startsWith("_qc/")
        || rel.startsWith("library/qc/")
        || rel.startsWith("qc/")
        || asset.file_name.startsWith("qc__")
      );
    });
  }, [activeState?.assets, showQcOnly]);

  useEffect(() => {
    let active = true;
    fetchThumbnailOverview()
      .then((data) => {
        if (!active) return;
        setOverview(data);
        if (data.channels.length) {
          setSelectedChannel((prev) => {
            if (prev && data.channels.some((c) => c.channel === prev)) {
              return prev;
            }
            return data.channels[0].channel;
          });
        }
      })
      .catch((err) => {
        if (!active) return;
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const channel = selectedChannel;
    if (!channel) return;
    if (loadedChannelsRef.current.has(channel)) {
      return;
    }
    let canceled = false;
    setChannelStates((prev) => ({
      ...prev,
      [channel]: { assets: prev[channel]?.assets ?? [], loading: true, loaded: false, error: null },
    }));
    fetchThumbnailLibrary(channel)
      .then((assets) => {
        if (canceled) return;
        loadedChannelsRef.current.add(channel);
        setChannelStates((prev) => ({
          ...prev,
          [channel]: { assets, loading: false, loaded: true, error: null },
        }));
      })
      .catch((err) => {
        if (canceled) return;
        const message = err instanceof Error ? err.message : String(err);
        loadedChannelsRef.current.add(channel);
        setChannelStates((prev) => ({
          ...prev,
          [channel]: { assets: prev[channel]?.assets ?? [], loading: false, loaded: true, error: message },
        }));
      });
    return () => {
      canceled = true;
    };
  }, [selectedChannel, refreshNonce]);

  return (
    <section className="thumbnail-library-panel">
      <div className="thumbnail-library-panel__header">
        <div>
          <h3>参考サムネライブラリ</h3>
          <p className="muted small-text">thumbnails 配下に置いた画像をチャンネルごとに確認できます</p>
        </div>
        <div className="thumbnail-hub__tabs" role="tablist" aria-label="チャンネル切替">
          {channels.map((channel) => (
            <button
              key={channel.channel}
              className={`thumbnail-hub__tab${
                selectedChannel === channel.channel ? " thumbnail-hub__tab--active" : ""
              }`}
              onClick={() => setSelectedChannel(channel.channel)}
              role="tab"
              aria-selected={selectedChannel === channel.channel}
            >
              {channel.channel_title ?? channel.channel}
            </button>
          ))}
        </div>
      </div>

      {error ? <p className="thumbnail-library__alert">{error}</p> : null}

      <div className="thumbnail-library-panel__header" style={{ marginTop: "8px" }}>
        <div>
          <strong>{selectedChannel ?? "—"}</strong>
          {selectedChannel && overview
            ? (
                <p className="thumbnail-library-card__meta-info">
                  {overview.channels.find((c) => c.channel === selectedChannel)?.library_path ?? ""}
                </p>
              )
            : null}
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => {
              if (!selectedChannel) return;
              loadedChannelsRef.current.delete(selectedChannel);
              setRefreshNonce((v) => v + 1);
            }}
            disabled={!selectedChannel}
          >
            再読み込み
          </button>
          <label className="muted small-text" style={{ display: "inline-flex", gap: "6px", alignItems: "center" }}>
            <input
              type="checkbox"
              checked={showQcOnly}
              onChange={(event) => setShowQcOnly(event.target.checked)}
            />
            QCのみ
          </label>
        </div>
      </div>

      {activeState?.error ? <p className="thumbnail-library__alert">{activeState.error}</p> : null}
      {activeState?.loading ? <p className="thumbnail-library__placeholder">画像を読み込んでいます…</p> : null}
      {!activeState?.loading && visibleAssets.length === 0 ? (
        <p className="thumbnail-library__placeholder">
          {showQcOnly ? "QC画像が見つかりませんでした。" : "画像が見つかりませんでした。"}
        </p>
      ) : null}

      {visibleAssets.length ? (
        <div className="thumbnail-library-grid">
          {visibleAssets.map((asset) => {
            const baseUrl = resolveApiUrl(asset.public_url);
            const sep = baseUrl.includes("?") ? "&" : "?";
            const previewUrl = `${baseUrl}${sep}v=${encodeURIComponent(asset.updated_at ?? "")}`;
            return (
              <article key={asset.id} className="thumbnail-library-card">
                <div className="thumbnail-library-card__preview">
                  <img src={previewUrl} alt={asset.file_name} loading="lazy" />
                </div>
                <div className="thumbnail-library-card__meta">
                  <strong title={asset.file_name}>{asset.file_name}</strong>
                  <div className="thumbnail-library-card__meta-info">{asset.relative_path}</div>
                </div>
                <div className="thumbnail-library-card__actions">
                  <button
                    type="button"
                    className="btn btn--ghost"
                    onClick={() => {
                      if (!selectedChannel) return;
                      setDeletingId(asset.id);
                      deleteThumbnailLibraryAsset(selectedChannel, asset.relative_path)
                        .then(() => {
                          setChannelStates((prev) => {
                            const current = prev[selectedChannel];
                            if (!current) return prev;
                            return {
                              ...prev,
                              [selectedChannel]: {
                                ...current,
                                assets: current.assets.filter((item) => item.id !== asset.id),
                              },
                            };
                          });
                        })
                        .catch((err) => {
                          const message = err instanceof Error ? err.message : String(err);
                          setChannelStates((prev) => ({
                            ...prev,
                            [selectedChannel]: {
                              ...(prev[selectedChannel] ?? { assets: [], loading: false, loaded: true }),
                              error: message,
                            },
                          }));
                        })
                        .finally(() => {
                          setDeletingId((current) => (current === asset.id ? null : current));
                        });
                    }}
                    disabled={deletingId === asset.id}
                  >
                    {deletingId === asset.id ? "削除中…" : "削除"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}
