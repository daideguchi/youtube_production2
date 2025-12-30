import type { ChannelSummary } from "../api/types";
import { Link } from "react-router-dom";

function resolveDisplayName(channel: ChannelSummary): string {
  return channel.name ?? channel.branding?.title ?? channel.youtube_title ?? channel.code;
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

interface ChannelOverviewSnapshot {
  total: number;
  publishedCount: number;
  scriptCompleted: number;
  audioSubtitleCompleted: number;
  readyForAudio: number;
  audioSubtitleBacklog: number;
}

interface ChannelOverviewPanelProps {
  channel: ChannelSummary | null;
  snapshot: ChannelOverviewSnapshot | null;
  onBackToDashboard?: () => void;
  backLabel?: string;
}

function toPercent(value: number, total: number): number {
  if (total === 0) {
    return 0;
  }
  return Math.min(100, Math.round((value / total) * 100));
}

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== "number") {
    return "0";
  }
  return new Intl.NumberFormat("ja-JP").format(value);
}

export function ChannelOverviewPanel({ channel, snapshot, onBackToDashboard, backLabel }: ChannelOverviewPanelProps) {
  if (!channel || !snapshot) {
    return null;
  }

  const displayName = resolveDisplayName(channel);
  const portalPath = `/channels/${encodeURIComponent(channel.code)}/portal`;
  const channelSettingsPath = `/channel-settings?channel=${encodeURIComponent(channel.code)}`;
  const genre = channel.genre ?? null;
  const customPath = channel.branding?.custom_url?.replace(/^\//, "") ?? null;
  const channelHandle = formatHandle(
    channel.branding?.handle ?? channel.youtube_handle ?? channel.branding?.custom_url ?? null
  );
  const launchDate = formatLaunchDate(channel.branding?.launch_date);
  const youtubeUrl = channel.branding?.url ?? (customPath ? `https://www.youtube.com/${customPath}` : null);
  const spreadsheetUrl = channel.spreadsheet_id
    ? `https://docs.google.com/spreadsheets/d/${channel.spreadsheet_id}`
    : null;
  const themeColor = channel.branding?.theme_color ?? null;
  const avatarUrl = channel.branding?.avatar_url ?? null;
  const avatarStyle =
    avatarUrl != null
      ? { backgroundImage: `url(${avatarUrl})` }
      : themeColor
        ? { background: themeColor }
        : undefined;
  const avatarLabel = displayName.slice(0, 2);
  const description = channel.description ?? null;

  const { total, publishedCount, scriptCompleted, audioSubtitleCompleted, readyForAudio, audioSubtitleBacklog } = snapshot;
  const scriptPercent = toPercent(scriptCompleted, total);
  const audioSubtitlePercent = toPercent(audioSubtitleCompleted, total);

  return (
    <div className="channel-overview">
      <header className="channel-overview__header">
        <div className="channel-overview__identity">
          <div
            className={`channel-overview__avatar${avatarUrl ? " channel-overview__avatar--image" : ""}`}
            style={avatarStyle}
            aria-hidden
          >
            {!avatarUrl ? avatarLabel : null}
          </div>
          <div className="channel-overview__summary">
            <div className="channel-overview__meta-row">
              {genre ? <span className="channel-overview__genre">{genre}</span> : null}
              <span className="channel-overview__code">{channel.code}</span>
              {channelHandle ? <span className="channel-overview__handle">{channelHandle}</span> : null}
              {launchDate ? <span className="channel-overview__launch">開設 {launchDate}</span> : null}
            </div>
            <h1 className="channel-overview__title">{displayName}</h1>
            {description ? <p className="channel-overview__description">{description}</p> : null}
            {youtubeUrl || spreadsheetUrl || portalPath || channelSettingsPath ? (
              <div className="channel-overview__links">
                <Link className="channel-overview__link" to={portalPath}>
                  ポータル
                </Link>
                <Link className="channel-overview__link" to={channelSettingsPath}>
                  チャンネル設定
                </Link>
                {youtubeUrl ? (
                  <a
                    className="channel-overview__link"
                    href={youtubeUrl}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(event) => event.stopPropagation()}
                  >
                    YouTubeを開く ↗
                  </a>
                ) : null}
                {spreadsheetUrl ? (
                  <a
                    className="channel-overview__link"
                    href={spreadsheetUrl}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(event) => event.stopPropagation()}
                  >
                    管理シート ↗
                  </a>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
        {onBackToDashboard ? (
          <button type="button" className="channel-overview__back" onClick={onBackToDashboard}>
            {backLabel ?? "⬅ ダッシュボードへ戻る"}
          </button>
        ) : null}
      </header>

      <div className="channel-overview__metrics">
        <div className="metric-card">
          <span className="metric-card__label">企画総数</span>
          <span className="metric-card__value">{formatNumber(total)}</span>
        </div>
        <div className="metric-card">
          <span className="metric-card__label">投稿済み</span>
          <span className="metric-card__value">{formatNumber(publishedCount)}</span>
        </div>
        <div className="metric-card">
          <span className="metric-card__label">音声・字幕準備済み</span>
          <span className="metric-card__value">{formatNumber(readyForAudio)}</span>
        </div>
        <div className="metric-card">
          <span className="metric-card__label">音声・字幕未完</span>
          <span className="metric-card__value metric-card__value--warning">{formatNumber(audioSubtitleBacklog)}</span>
        </div>
      </div>

      <ul className="progress-list">
        <li className="progress-list__item">
          <div className="progress-list__label">
            <span className="progress-list__icon" aria-hidden>
              📝
            </span>
            <div>
              <p className="progress-list__title">台本</p>
              <p className="progress-list__description">完成 {scriptCompleted} / {total}</p>
            </div>
          </div>
          <div className="progress-list__bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={scriptPercent}>
            <div className="progress-list__bar-fill" style={{ width: `${scriptPercent}%` }} />
          </div>
          <span className="progress-list__status">{scriptPercent}%</span>
        </li>
        <li className="progress-list__item">
          <div className="progress-list__label">
            <span className="progress-list__icon" aria-hidden>
              🎙️
            </span>
            <div>
              <p className="progress-list__title">音声・字幕</p>
              <p className="progress-list__description">完了 {audioSubtitleCompleted} / {total}</p>
            </div>
          </div>
          <div className="progress-list__bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={audioSubtitlePercent}>
            <div className="progress-list__bar-fill" style={{ width: `${audioSubtitlePercent}%` }} />
          </div>
          <span className="progress-list__status">{audioSubtitlePercent}%</span>
        </li>
      </ul>
    </div>
  );
}
