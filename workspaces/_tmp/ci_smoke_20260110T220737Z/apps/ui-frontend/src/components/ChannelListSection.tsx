import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChannelSummary, DashboardChannelSummary } from "../api/types";

interface ChannelListSectionProps {
  channels: ChannelSummary[];
  channelStats?: DashboardChannelSummary[];
  selectedChannel: string | null;
  loading: boolean;
  error: string | null;
  onSelectChannel: (code: string | null) => void;
  variant?: "sidebar" | "dashboard";
  redoSummary?: Record<string, { redo_script: number; redo_audio: number; redo_both: number }>;
}

function formatNumber(value: number | undefined | null): string {
  return new Intl.NumberFormat("ja-JP").format(value ?? 0);
}

function formatPercent(value: number, total: number): string {
  if (!total) {
    return "0%";
  }
  return `${Math.round((value / total) * 100)}%`;
}

function formatCompactNumber(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  if (value >= 1_0000_0000) {
    return `${(value / 1_0000_0000).toFixed(1)}億`;
  }
  if (value >= 1_0000) {
    return `${(value / 1_0000).toFixed(1)}万`;
  }
  return formatNumber(value);
}

function formatLaunchDate(value?: string | null): string | null {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  return `${year}/${month}`;
}

function formatHandle(value?: string | null): string | null {
  if (!value) {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  return trimmed.startsWith("@") ? trimmed : `@${trimmed}`;
}

export function ChannelListSection({
  channels,
  channelStats,
  selectedChannel,
  loading,
  error,
  onSelectChannel,
  variant = "sidebar",
  redoSummary = {},
}: ChannelListSectionProps) {
  const [query, setQuery] = useState("");
  const statsMap = useMemo(() => {
    const map = new Map<string, DashboardChannelSummary>();
    channelStats?.forEach((stat) => map.set(stat.code, stat));
    return map;
  }, [channelStats]);

  const sortedChannels = useMemo(() => {
    const copy = [...channels];
    const sortKey = (code: string) => {
      const match = code.toUpperCase().match(/^CH(\d+)$/);
      return match ? Number(match[1]) : Number.POSITIVE_INFINITY;
    };
    copy.sort((a, b) => {
      const diff = sortKey(a.code) - sortKey(b.code);
      if (diff !== 0) return diff;
      return a.code.localeCompare(b.code);
    });
    return copy;
  }, [channels]);

  const visibleChannels = useMemo(() => {
    if (variant !== "sidebar") {
      return sortedChannels;
    }
    const token = query.trim().toLowerCase();
    if (!token) {
      return sortedChannels;
    }
    return sortedChannels.filter((channel) => {
      const displayName = channel.name ?? channel.branding?.title ?? channel.youtube_title ?? channel.code;
      const haystack = [
        channel.code,
        displayName,
        channel.youtube_handle,
        channel.branding?.handle,
        channel.branding?.custom_url,
        channel.branding?.title,
        channel.youtube_title,
        channel.genre,
      ]
        .filter((value): value is string => typeof value === "string")
        .join(" ")
        .toLowerCase();
      return haystack.includes(token);
    });
  }, [query, sortedChannels, variant]);

  const handleSelect = (code: string) => {
    if (variant !== "dashboard" && selectedChannel === code) {
      return;
    }
    onSelectChannel(code);
  };

  const handleClear = () => {
    onSelectChannel(null);
  };

  const containerClass =
    variant === "dashboard" ? "channel-list channel-list--dashboard" : "shell-panel shell-panel--sidebar";
  const gridClass = variant === "dashboard" ? "channel-chip-grid" : "channel-chip-list";
  const cardClass = (isActive: boolean) =>
    variant === "dashboard"
      ? `channel-chip channel-chip--dashboard${isActive ? " channel-chip--active" : ""}`
      : `channel-chip channel-chip--sidebar${isActive ? " channel-chip--active" : ""}`;

  return (
    <section className={containerClass}>
      {variant === "sidebar" ? (
        <header className="shell-panel__header">
          <div />
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <Link className="shell-link" to="/channel-settings?add=1">
              ＋追加
            </Link>
            {selectedChannel ? (
              <button type="button" className="shell-link" onClick={handleClear}>
                すべて表示
              </button>
            ) : null}
          </div>
        </header>
      ) : (
        <header className="channel-list__header">
          <h2>チャンネルサマリー</h2>
          <p className="muted small-text">YouTube側の登録者・投稿数と内部進捗を一目で確認できます。</p>
        </header>
      )}

      {variant === "sidebar" && !loading && !error ? (
        <div style={{ padding: "0 16px 12px" }}>
          <input
            type="search"
            className="channel-projects__search"
            placeholder="CHxx / 名前 / @handle で検索"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="チャンネル検索"
            style={{ width: "100%", minWidth: 0 }}
          />
        </div>
      ) : null}

      {loading ? (
        <p className={variant === "dashboard" ? "channel-list__message" : "shell-panel__message"}>
          チャンネルを読み込み中です…
        </p>
      ) : null}
      {error ? (
        <p
          className={
            variant === "dashboard" ? "channel-list__message channel-list__message--error" : "shell-panel__message shell-panel__message--error"
          }
        >
          取得に失敗しました: {error}
        </p>
      ) : null}

      {!loading && !error ? (
        visibleChannels.length === 0 ? (
          <p className={variant === "dashboard" ? "channel-list__message" : "shell-panel__message"}>
            {channels.length === 0 ? "チャンネルが登録されていません。" : "該当するチャンネルがありません。"}
          </p>
        ) : (
          <div className={gridClass}>
            {visibleChannels.map((channel) => {
              const stats = statsMap.get(channel.code);
              const hasStats = Boolean(stats);
              const displayName =
                channel.name ?? channel.branding?.title ?? channel.youtube_title ?? channel.code;
              // NOTE: "案件数/制作進捗" は Planning/Status SoT（dashboard overview）に準拠。
              // YouTube側の投稿数は別枠で表示するため、stats が取れない場合はダッシュで扱う。
              const total = hasStats ? stats?.total ?? 0 : 0;
              const scriptCompleted = hasStats ? stats?.script_completed ?? 0 : 0;
              const audioCompleted = hasStats ? stats?.audio_completed ?? 0 : 0;
              const readyForAudio = hasStats ? stats?.ready_for_audio ?? 0 : 0;
              const backlogAudio = hasStats ? Math.max(total - audioCompleted, 0) : 0;
              const backlogScript = hasStats ? Math.max(total - scriptCompleted, 0) : 0;
              const isActive = selectedChannel === channel.code;
              const avatarLabel = displayName.slice(0, 2);
              const avatarUrl = channel.branding?.avatar_url ?? null;
              const themeColor = channel.branding?.theme_color ?? null;
              const subscriberCount = channel.branding?.subscriber_count ?? null;
              const viewCount = channel.branding?.view_count ?? null;
              const youtubeVideoCount = channel.branding?.video_count ?? null;
              const genreLabel =
                typeof channel.genre === "string" && channel.genre.trim().length > 0 ? channel.genre.trim() : null;
              const customPath = channel.branding?.custom_url?.replace(/^\//, "") ?? null;
              const channelHandle = formatHandle(
                channel.branding?.handle ?? channel.youtube_handle ?? channel.branding?.custom_url ?? null
              );
              const youtubeUrl = channel.branding?.url ?? (customPath ? `https://www.youtube.com/${customPath}` : null);
              const launchDate = formatLaunchDate(channel.branding?.launch_date);
              const primaryMetrics = [
                { key: "videos", icon: "🗂️", label: "案件数", value: hasStats ? formatNumber(total) : "—" },
                { key: "script", icon: "📝", label: "台本完了", value: hasStats ? formatPercent(scriptCompleted, total) : "—" },
                { key: "audio", icon: "🎙️", label: "音声完了", value: hasStats ? formatPercent(audioCompleted, total) : "—" },
                { key: "ready", icon: "🔊", label: "音声準備", value: hasStats ? formatPercent(readyForAudio, total) : "—" },
              ];

              const youtubeMetrics: { key: string; icon: string; label: string; value: string }[] = [];
              youtubeMetrics.push({
                key: "ytVideos",
                icon: "📺",
                label: "投稿数",
                value: youtubeVideoCount != null ? formatNumber(youtubeVideoCount) : "—",
              });
              youtubeMetrics.push({
                key: "subs",
                icon: "👥",
                label: "登録者",
                value: subscriberCount != null ? formatCompactNumber(subscriberCount) : "—",
              });
              youtubeMetrics.push({
                key: "views",
                icon: "▶️",
                label: "総再生",
                value: viewCount != null ? formatCompactNumber(viewCount) : "—",
              });

              const avatarStyle = avatarUrl
                ? { backgroundImage: `url(${avatarUrl})` }
                : themeColor
                  ? { background: themeColor }
                  : undefined;

              if (variant === "sidebar") {
                return (
                  <button
                    key={channel.code}
                    type="button"
                    className={cardClass(isActive)}
                    onClick={() => handleSelect(channel.code)}
                    aria-label={`${displayName} の詳細を表示`}
                  >
                    <div className="channel-sidebar-card">
                      <div className="channel-sidebar-card__row">
                        <div
                          className={`channel-sidebar-card__avatar${avatarUrl ? " channel-sidebar-card__avatar--image" : ""}`}
                          style={avatarStyle}
                          aria-hidden
                        >
                          {!avatarUrl ? avatarLabel : null}
                        </div>
                        <div className="channel-sidebar-card__texts">
                          <span className="channel-sidebar-card__code">{channel.code}</span>
                          <span className="channel-sidebar-card__name">{displayName}</span>
                        </div>
                      </div>
                    </div>
                  </button>
                );
              }

              return (
                <button
                  key={channel.code}
                  type="button"
                  className={cardClass(isActive)}
                  onClick={() => handleSelect(channel.code)}
                  aria-label={`${displayName} の詳細を表示`}
                >
                  <div className="channel-chip__main">
                    <div className="channel-chip__header">
                      <div
                        className={`channel-chip__avatar${avatarUrl ? " channel-chip__avatar--image" : ""}`}
                        style={avatarStyle}
                        aria-hidden
                      >
                        {!avatarUrl ? avatarLabel : null}
                      </div>
                      <div className="channel-chip__info">
                        <div className="channel-chip__title-row">
                          <p className="channel-chip__name">{displayName}</p>
                          <span className="channel-chip__code">{channel.code}</span>
                        </div>
                        {genreLabel ? <p className="channel-chip__genre">{genreLabel}</p> : null}
                        {channelHandle ? <p className="channel-chip__handle">{channelHandle}</p> : null}
                        {launchDate ? <p className="channel-chip__launch">開設 {launchDate}</p> : null}
                        {youtubeUrl ? (
                          <a
                            className="channel-chip__yt-link"
                            href={youtubeUrl}
                            onClick={(event) => event.stopPropagation()}
                            target="_blank"
                            rel="noreferrer"
                            aria-label={`${channel.name ?? channel.code} のYouTubeチャンネルを開く`}
                          >
                            YouTubeを開く ↗
                          </a>
                        ) : null}
                      </div>
                    </div>

                    {redoSummary[channel.code] ? (
                      <div className="channel-chip__badges">
                        <span className="channel-chip__redo">
                          リテイク: 台本 {redoSummary[channel.code].redo_script} / 音声 {redoSummary[channel.code].redo_audio}
                        </span>
                      </div>
                    ) : null}

                    <div className="channel-chip__section">
                      <span className="channel-chip__section-title">制作進捗</span>
                      <div className="channel-chip__section-grid">
                      {primaryMetrics.map((metric) => (
                        <div key={metric.key} className="channel-chip__metric">
                          <span className="channel-chip__metric-icon" aria-hidden>
                            {metric.icon}
                          </span>
                          <div className="channel-chip__metric-label">{metric.label}</div>
                          <div className="channel-chip__metric-value">{metric.value}</div>
                        </div>
                      ))}
                      </div>
                    </div>

                    <div className="channel-chip__section channel-chip__section--youtube">
                      <span className="channel-chip__section-title">YouTube 指標</span>
                      <div className="channel-chip__section-grid">
                        {youtubeMetrics.map((metric) => (
                          <div key={metric.key} className="channel-chip__metric">
                            <span className="channel-chip__metric-icon" aria-hidden>
                              {metric.icon}
                            </span>
                            <div className="channel-chip__metric-label">{metric.label}</div>
                            <div className="channel-chip__metric-value">{metric.value}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className="channel-chip__badges">
                    {hasStats ? (
                      <span className="channel-chip__badge channel-chip__badge--ghost">
                        音声準備 {formatNumber(readyForAudio)}
                      </span>
                    ) : (
                      <span className="channel-chip__badge channel-chip__badge--ghost">進捗未取得</span>
                    )}
                    {hasStats && backlogScript > 0 ? (
                      <span className="channel-chip__badge">台本未完 {formatNumber(backlogScript)}</span>
                    ) : null}
                    {hasStats && backlogAudio > 0 ? (
                      <span className="channel-chip__badge">音声未完 {formatNumber(backlogAudio)}</span>
                    ) : null}
                  </div>
                </button>
              );
            })}
          </div>
        )
      ) : null}
    </section>
  );
}
