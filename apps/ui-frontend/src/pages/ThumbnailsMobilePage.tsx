import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { fetchThumbnailOverview, resolveApiUrl } from "../api/client";
import type {
  ThumbnailChannelBlock,
  ThumbnailOverview,
  ThumbnailProject,
  ThumbnailVariant,
} from "../api/types";
import { safeLocalStorage } from "../utils/safeStorage";
import "../layouts/thumbnails-mobile.css";

type MobileViewMode = "selected" | "all" | "two_up";

type MobileThumbnailItem = {
  key: string;
  channel: string;
  video: string;
  title: string;
  status: string;
  variantId: string | null;
  variantLabel: string | null;
  imageUrl: string | null;
  updatedAt: string | null;
};

const STORAGE_KEY_LAST_CHANNEL = "ui.thumbnails.mobile.channel";
const DEFAULT_LIMIT = 30;
const EMPTY_CHANNELS: ThumbnailChannelBlock[] = [];

function normalizeChannelCode(value?: string | null): string | null {
  const trimmed = (value ?? "").trim();
  if (!trimmed) return null;
  return trimmed.toUpperCase();
}

function normalizeSearch(value: string): string {
  return value.trim().toLowerCase();
}

function parseViewMode(value?: string | null): MobileViewMode | null {
  if (!value) return null;
  if (value === "selected" || value === "all" || value === "two_up") return value;
  return null;
}

function compareVideoNumber(a: string, b: string): number {
  const an = Number.parseInt(a, 10);
  const bn = Number.parseInt(b, 10);
  const aOk = Number.isFinite(an);
  const bOk = Number.isFinite(bn);
  if (aOk && bOk) return an - bn;
  if (aOk) return -1;
  if (bOk) return 1;
  return a.localeCompare(b, "ja-JP");
}

function withCacheBust(url: string, token?: string | null): string {
  const value = (token ?? "").trim();
  if (!value) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}v=${encodeURIComponent(value)}`;
}

function resolveVariantImageUrl(variant: ThumbnailVariant, project: ThumbnailProject): string | null {
  const cacheBustToken = variant.updated_at ?? project.updated_at ?? project.status_updated_at ?? null;
  const base =
    variant.preview_url
      ? resolveApiUrl(variant.preview_url)
      : variant.image_url
        ? resolveApiUrl(variant.image_url)
        : variant.image_path
          ? resolveApiUrl(`/thumbnails/assets/${variant.image_path}`)
          : null;
  return base ? withCacheBust(base, cacheBustToken) : null;
}

function resolveProjectTitle(project: ThumbnailProject): string {
  return project.title ?? project.sheet_title ?? "（タイトル未設定）";
}

function resolveSelectedVariant(project: ThumbnailProject): ThumbnailVariant | null {
  const variants = Array.isArray(project.variants) ? project.variants : [];
  if (variants.length === 0) return null;
  const selectedId = (project.selected_variant_id ?? "").trim();
  if (selectedId) {
    const match = variants.find((variant) => variant.id === selectedId);
    if (match) return match;
  }
  const flagged = variants.find((variant) => Boolean(variant.is_selected));
  if (flagged) return flagged;
  return variants[0] ?? null;
}

function extractStableSlot(variant: ThumbnailVariant): "00_thumb_1" | "00_thumb_2" | null {
  const candidates = [variant.image_path, variant.image_url, variant.preview_url].filter(Boolean).join(" ");
  if (!candidates) return null;
  if (candidates.includes("00_thumb_1")) return "00_thumb_1";
  if (candidates.includes("00_thumb_2")) return "00_thumb_2";
  return null;
}

function channelHasTwoUp(channel: ThumbnailChannelBlock): boolean {
  for (const project of channel.projects ?? []) {
    for (const variant of project.variants ?? []) {
      if (extractStableSlot(variant)) return true;
    }
  }
  return false;
}

function buildItemsForChannel(channel: ThumbnailChannelBlock, mode: MobileViewMode): MobileThumbnailItem[] {
  const items: MobileThumbnailItem[] = [];
  const projects = Array.isArray(channel.projects) ? channel.projects : [];
  for (const project of projects) {
    const title = resolveProjectTitle(project);
    const status = project.status ?? "draft";
    if (mode === "all") {
      const variants = Array.isArray(project.variants) ? project.variants : [];
      for (const variant of variants) {
        const imageUrl = resolveVariantImageUrl(variant, project);
        items.push({
          key: `${project.channel}-${project.video}-${variant.id}`,
          channel: project.channel,
          video: project.video,
          title,
          status: variant.status ?? status,
          variantId: variant.id,
          variantLabel: variant.label ?? variant.id,
          imageUrl,
          updatedAt: variant.updated_at ?? project.updated_at ?? null,
        });
      }
      continue;
    }

    if (mode === "two_up") {
      const variants = Array.isArray(project.variants) ? project.variants : [];
      const bySlot: Record<"00_thumb_1" | "00_thumb_2", ThumbnailVariant | null> = {
        "00_thumb_1": null,
        "00_thumb_2": null,
      };
      for (const variant of variants) {
        const slot = extractStableSlot(variant);
        if (!slot) continue;
        if (!bySlot[slot]) {
          bySlot[slot] = variant;
        }
      }
      for (const slot of ["00_thumb_1", "00_thumb_2"] as const) {
        const variant = bySlot[slot];
        if (!variant) continue;
        items.push({
          key: `${project.channel}-${project.video}-${slot}`,
          channel: project.channel,
          video: project.video,
          title,
          status: variant.status ?? status,
          variantId: variant.id,
          variantLabel: `${slot}${variant.label ? ` / ${variant.label}` : ""}`,
          imageUrl: resolveVariantImageUrl(variant, project),
          updatedAt: variant.updated_at ?? project.updated_at ?? null,
        });
      }
      continue;
    }

    const selected = resolveSelectedVariant(project);
    if (!selected) {
      items.push({
        key: `${project.channel}-${project.video}-empty`,
        channel: project.channel,
        video: project.video,
        title,
        status,
        variantId: null,
        variantLabel: null,
        imageUrl: null,
        updatedAt: project.updated_at ?? null,
      });
      continue;
    }
    items.push({
      key: `${project.channel}-${project.video}-selected`,
      channel: project.channel,
      video: project.video,
      title,
      status,
      variantId: selected.id,
      variantLabel: selected.label ?? selected.id,
      imageUrl: resolveVariantImageUrl(selected, project),
      updatedAt: selected.updated_at ?? project.updated_at ?? null,
    });
  }
  return items.sort((a, b) => compareVideoNumber(a.video, b.video));
}

export function ThumbnailsMobilePage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [overview, setOverview] = useState<ThumbnailOverview | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState<string>("");
  const [limit, setLimit] = useState<number>(DEFAULT_LIMIT);
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);

  const channels = overview?.channels ?? EMPTY_CHANNELS;
  const channelParam = normalizeChannelCode(searchParams.get("channel"));
  const storedChannel = normalizeChannelCode(safeLocalStorage.getItem(STORAGE_KEY_LAST_CHANNEL));

  const activeChannelCode = useMemo(() => {
    const available = new Set(channels.map((ch) => ch.channel));
    if (channelParam && available.has(channelParam)) return channelParam;
    if (storedChannel && available.has(storedChannel)) return storedChannel;
    return channels[0]?.channel ?? null;
  }, [channelParam, channels, storedChannel]);

  const activeChannel = useMemo(() => {
    if (!activeChannelCode) return null;
    return channels.find((block) => block.channel === activeChannelCode) ?? null;
  }, [activeChannelCode, channels]);

  const hasTwoUp = useMemo(() => (activeChannel ? channelHasTwoUp(activeChannel) : false), [activeChannel]);
  const requestedMode = parseViewMode(searchParams.get("mode")) ?? "selected";
  const viewMode = requestedMode === "two_up" && !hasTwoUp ? "selected" : requestedMode;

  const items = useMemo(() => {
    if (!activeChannel) return [];
    return buildItemsForChannel(activeChannel, viewMode);
  }, [activeChannel, viewMode]);

  const filteredItems = useMemo(() => {
    const term = normalizeSearch(search);
    if (!term) return items;
    return items.filter((item) => {
      if (item.video.toLowerCase().includes(term)) return true;
      if (item.title.toLowerCase().includes(term)) return true;
      return false;
    });
  }, [items, search]);

  const visibleItems = useMemo(() => filteredItems.slice(0, limit), [filteredItems, limit]);

  const viewerItem = viewerIndex === null ? null : filteredItems[viewerIndex] ?? null;

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setError(null);
    fetchThumbnailOverview()
      .then((data) => {
        if (!mounted) return;
        setOverview(data);
        setLoading(false);
      })
      .catch((err) => {
        if (!mounted) return;
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!activeChannelCode) return;
    safeLocalStorage.setItem(STORAGE_KEY_LAST_CHANNEL, activeChannelCode);
  }, [activeChannelCode]);

  useEffect(() => {
    if (!activeChannelCode) return;
    if (channelParam === activeChannelCode) return;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("channel", activeChannelCode);
      if (viewMode === "selected") {
        next.delete("mode");
      } else {
        next.set("mode", viewMode);
      }
      return next;
    }, { replace: true });
  }, [activeChannelCode, channelParam, setSearchParams, viewMode]);

  useEffect(() => {
    setLimit(DEFAULT_LIMIT);
    setViewerIndex(null);
  }, [activeChannelCode, viewMode]);

  useEffect(() => {
    if (viewerIndex === null) return;
    if (viewerIndex < 0 || viewerIndex >= filteredItems.length) {
      setViewerIndex(null);
      return;
    }
    if (viewerIndex < limit) return;
    setLimit((current) => Math.max(current, viewerIndex + 1));
  }, [filteredItems.length, limit, viewerIndex]);

  useEffect(() => {
    if (!viewerItem || viewerIndex === null) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setViewerIndex(null);
        return;
      }
      if (event.key === "ArrowLeft") {
        setViewerIndex((current) => {
          if (current === null) return current;
          return Math.max(0, current - 1);
        });
        return;
      }
      if (event.key === "ArrowRight") {
        setViewerIndex((current) => {
          if (current === null) return current;
          return Math.min(filteredItems.length - 1, current + 1);
        });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [filteredItems.length, viewerIndex, viewerItem]);

  const handleChangeMode = useCallback(
    (mode: MobileViewMode) => {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        if (mode === "selected") {
          next.delete("mode");
        } else {
          next.set("mode", mode);
        }
        return next;
      });
    },
    [setSearchParams]
  );

  const handleChangeChannel = useCallback(
    (value: string) => {
      const code = normalizeChannelCode(value);
      if (!code) return;
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("channel", code);
        if (viewMode === "selected") {
          next.delete("mode");
        } else {
          next.set("mode", viewMode);
        }
        return next;
      });
    },
    [setSearchParams, viewMode]
  );

  return (
    <section className="thumbnail-mobile-page">
      <header className="thumbnail-mobile-header">
        <div className="thumbnail-mobile-header__title-row">
          <div>
            <h1 className="thumbnail-mobile-header__title">サムネ（モバイル確認）</h1>
            <p className="thumbnail-mobile-header__subtitle">スマホで「見え方」だけをサクッと確認するビューです。</p>
          </div>
          <div className="thumbnail-mobile-header__actions">
            <Link className="action-chip" to={activeChannelCode ? `/thumbnails?channel=${encodeURIComponent(activeChannelCode)}` : "/thumbnails"}>
              通常UIへ
            </Link>
            <Link className="action-chip" to="/dashboard">
              ダッシュボード
            </Link>
          </div>
        </div>

        <div className="thumbnail-mobile-toolbar">
          <label className="thumbnail-mobile-toolbar__field">
            <span className="thumbnail-mobile-toolbar__label">チャンネル</span>
            <select
              value={activeChannelCode ?? ""}
              onChange={(event) => handleChangeChannel(event.target.value)}
              disabled={loading || channels.length === 0}
            >
              {channels.map((channel) => (
                <option key={channel.channel} value={channel.channel}>
                  {channel.channel_title ? `${channel.channel} — ${channel.channel_title}` : channel.channel}
                </option>
              ))}
            </select>
          </label>

          <label className="thumbnail-mobile-toolbar__field">
            <span className="thumbnail-mobile-toolbar__label">検索</span>
            <input
              type="search"
              inputMode="search"
              placeholder="番号・タイトルで検索"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>

          <div className="thumbnail-mobile-toolbar__mode" role="group" aria-label="表示モード">
            <button
              type="button"
              className={`action-chip ${viewMode === "selected" ? "action-chip--active" : ""}`}
              onClick={() => handleChangeMode("selected")}
              disabled={loading}
            >
              選択中
            </button>
            <button
              type="button"
              className={`action-chip ${viewMode === "all" ? "action-chip--active" : ""}`}
              onClick={() => handleChangeMode("all")}
              disabled={loading}
            >
              全部
            </button>
            {hasTwoUp ? (
              <button
                type="button"
                className={`action-chip ${viewMode === "two_up" ? "action-chip--active" : ""}`}
                onClick={() => handleChangeMode("two_up")}
                disabled={loading}
              >
                2案
              </button>
            ) : null}
          </div>

          <div className="thumbnail-mobile-toolbar__meta muted small-text">
            {loading ? "読み込み中..." : error ? "読み込み失敗" : `表示: ${Math.min(visibleItems.length, filteredItems.length)} / ${filteredItems.length}`}
          </div>
        </div>
      </header>

      {error ? (
        <div className="thumbnail-mobile-message thumbnail-mobile-message--error">
          <div>取得に失敗しました: {error}</div>
          <button type="button" className="action-chip" onClick={() => navigate(0)}>
            再読み込み
          </button>
        </div>
      ) : null}

      <div className="thumbnail-mobile-list">
        {visibleItems.map((item, idx) => (
          <article key={item.key} className="thumbnail-mobile-card">
            <div className="thumbnail-mobile-card__media">
              {item.imageUrl ? (
                <button
                  type="button"
                  className="thumbnail-mobile-card__media-button"
                  onClick={() => setViewerIndex(idx)}
                  title="タップで拡大"
                >
                  <img src={item.imageUrl} alt={`${item.channel}-${item.video}`} loading="lazy" draggable={false} />
                </button>
              ) : (
                <div className="thumbnail-mobile-card__placeholder">No image</div>
              )}
            </div>

            <div className="thumbnail-mobile-card__meta">
              <div className="thumbnail-mobile-card__meta-top">
                <div className="thumbnail-mobile-card__code">{item.channel}-{item.video}</div>
                <span className="thumbnail-mobile-card__status">{item.status}</span>
              </div>
              <div className="thumbnail-mobile-card__title">{item.title}</div>
              {item.variantLabel ? <div className="thumbnail-mobile-card__variant">{item.variantLabel}</div> : null}
              <div className="thumbnail-mobile-card__actions">
                {item.imageUrl ? (
                  <>
                    <a className="action-chip" href={item.imageUrl} target="_blank" rel="noreferrer">
                      開く
                    </a>
                    <a className="action-chip" href={item.imageUrl} download={`${item.channel}-${item.video}.png`}>
                      DL
                    </a>
                  </>
                ) : null}
              </div>
            </div>
          </article>
        ))}
      </div>

      {filteredItems.length > limit ? (
        <div className="thumbnail-mobile-more">
          <button type="button" className="action-chip" onClick={() => setLimit((current) => current + DEFAULT_LIMIT)}>
            さらに表示
          </button>
          <span className="muted small-text">
            {Math.min(limit, filteredItems.length)} / {filteredItems.length}
          </span>
        </div>
      ) : null}

      {viewerItem ? (
        <div className="thumbnail-mobile-viewer" role="dialog" aria-modal="true" aria-label="サムネ拡大表示">
          <div className="thumbnail-mobile-viewer__topbar">
            <button type="button" className="action-chip" onClick={() => setViewerIndex(null)}>
              閉じる
            </button>
            <div className="thumbnail-mobile-viewer__topbar-title">
              {viewerItem.channel}-{viewerItem.video}
              {viewerItem.variantLabel ? ` · ${viewerItem.variantLabel}` : ""}
            </div>
            <div className="thumbnail-mobile-viewer__nav">
              <button
                type="button"
                className="action-chip"
                onClick={() => setViewerIndex((current) => (current === null ? current : Math.max(0, current - 1)))}
                disabled={viewerIndex === 0}
              >
                前
              </button>
              <button
                type="button"
                className="action-chip"
                onClick={() =>
                  setViewerIndex((current) => (current === null ? current : Math.min(filteredItems.length - 1, current + 1)))
                }
                disabled={viewerIndex === filteredItems.length - 1}
              >
                次
              </button>
            </div>
          </div>
          <div className="thumbnail-mobile-viewer__body" onClick={() => setViewerIndex(null)}>
            {viewerItem.imageUrl ? (
              <img
                className="thumbnail-mobile-viewer__img"
                src={viewerItem.imageUrl}
                alt={`${viewerItem.channel}-${viewerItem.video}`}
                draggable={false}
                onClick={(event) => event.stopPropagation()}
              />
            ) : (
              <div className="thumbnail-mobile-viewer__placeholder">No image</div>
            )}
            <div className="thumbnail-mobile-viewer__hint muted small-text">タップで閉じる / 左右キーで移動（PC）</div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
