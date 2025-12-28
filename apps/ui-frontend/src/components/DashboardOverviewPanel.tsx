import { useState, type KeyboardEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import "./dashboard-clean.css";
import type { ChannelSummary, DashboardOverview, StageMatrix } from "../api/types";

interface DashboardOverviewPanelProps {
  overview: DashboardOverview | null;
  loading: boolean;
  error: string | null;
  channels?: ChannelSummary[] | null;
  selectedChannel?: string | null;
  onSelectChannel?: (code: string) => void;
  onFocusAudioBacklog?: (channelCode: string | null) => void;
  onFocusNeedsAttention?: (channelCode?: string | null) => void;
  title?: string;
  titleIcon?: string;
  subtitle?: string;
}

interface ChannelRow {
  code: string;
  displayName: string | null;
  avatarUrl: string | null;
  themeColor: string | null;
  total: number;
  scriptStarted: number;
  scriptCompleted: number;
  ttsReady: number;
  audioSubtitleCompleted: number;
  blocked: number;
  audioSubtitleBacklog: number;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("ja-JP").format(value);
}

function formatPercent(part: number, total: number): string {
  if (total === 0) {
    return "0%";
  }
  return `${Math.round((part / total) * 100)}%`;
}

function computeScriptStarted(total: number, matrix: StageMatrix | undefined, code: string): number {
  if (!matrix) {
    return total;
  }
  const stageCounts = matrix[code]?.script_outline;
  if (!stageCounts) {
    return total;
  }
  const pending = stageCounts.pending ?? 0;
  const started = total - pending;
  return Math.max(0, Math.min(total, started));
}

function renderCount(value: number, total: number) {
  return (
    <span className="dashboard-table__value">
      <span className="dashboard-table__count">{formatNumber(value)}</span>
      <span className="dashboard-table__percent">{formatPercent(value, total)}</span>
    </span>
  );
}

export function DashboardOverviewPanel({
  overview,
  loading,
  error,
  channels,
  onSelectChannel,
  selectedChannel,
  onFocusAudioBacklog,
  onFocusNeedsAttention,
  title,
  titleIcon = "📺",
  subtitle = "台本・音声・字幕の進行状況と滞留ポイントを一目で把握し、次のアクションへ繋げます。",
}: DashboardOverviewPanelProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [channelFilter, setChannelFilter] = useState("");
  if (loading) {
    return (
      <section className="dashboard-overview dashboard-clean">
        <p className="muted">ダッシュボードを読み込み中…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="dashboard-overview dashboard-clean">
        <p className="error">{error}</p>
      </section>
    );
  }

  if (!overview) {
    return null;
  }

  const channelMetaMap = new Map<string, ChannelSummary>();
  if (channels) {
    for (const channel of channels) {
      channelMetaMap.set(channel.code, channel);
    }
  }

  const overviewChannelMap = new Map<string, DashboardOverview["channels"][number]>();
  overview.channels.forEach((channel) => overviewChannelMap.set(channel.code, channel));

  const allChannelCodes = new Set<string>();
  overview.channels.forEach((channel) => allChannelCodes.add(channel.code));
  channels?.forEach((channel) => allChannelCodes.add(channel.code));

  const sortKey = (code: string) => {
    const match = code.trim().toUpperCase().match(/^CH(\d+)$/);
    return match ? Number(match[1]) : Number.POSITIVE_INFINITY;
  };

  const allChannelCodesList: string[] = [];
  allChannelCodes.forEach((code) => allChannelCodesList.push(code));

  const channelRows: ChannelRow[] = allChannelCodesList
    .sort((a, b) => {
      const diff = sortKey(a) - sortKey(b);
      if (diff !== 0) return diff;
      return a.localeCompare(b);
    })
    .map((code) => {
      const channel = overviewChannelMap.get(code) ?? {
        code,
        total: 0,
        script_completed: 0,
        audio_completed: 0,
        srt_completed: 0,
        blocked: 0,
        ready_for_audio: 0,
        pending_sync: 0,
      };

      const summary = channelMetaMap.get(code);
      const displayName =
        summary?.name ?? summary?.branding?.title ?? summary?.youtube_title ?? summary?.code ?? channel.code;
      const avatarUrl = summary?.branding?.avatar_url ?? null;
      const themeColor = summary?.branding?.theme_color ?? null;
      const total = channel.total;
      const scriptStarted = computeScriptStarted(total, overview.stage_matrix, channel.code);
      const scriptCompleted = channel.script_completed;
      const ttsReady = channel.ready_for_audio;
      const audioCompleted = channel.audio_completed;
      const subtitleCompleted = channel.srt_completed ?? 0;
      const audioSubtitleCompleted = Math.min(audioCompleted, subtitleCompleted);
      const audioSubtitleBacklog = Math.max(total - audioSubtitleCompleted, 0);
      return {
        code: channel.code,
        displayName,
        avatarUrl,
        themeColor,
        total,
        scriptStarted,
        scriptCompleted,
        ttsReady,
        audioSubtitleCompleted,
        blocked: channel.blocked,
        audioSubtitleBacklog,
      };
    });

  const totals = channelRows.reduce(
    (acc, row) => {
      acc.total += row.total;
      acc.scriptStarted += row.scriptStarted;
      acc.scriptCompleted += row.scriptCompleted;
      acc.ttsReady += row.ttsReady;
      acc.audioSubtitleCompleted += row.audioSubtitleCompleted;
      acc.blocked += row.blocked;
      acc.audioSubtitleBacklog += row.audioSubtitleBacklog;
      return acc;
    },
    {
      total: 0,
      scriptStarted: 0,
      scriptCompleted: 0,
      ttsReady: 0,
      audioSubtitleCompleted: 0,
      blocked: 0,
      audioSubtitleBacklog: 0,
    }
  );

  const kpiItems = [
    {
      key: "script",
      label: "台本完成",
      icon: "📝",
      value: totals.scriptCompleted,
      helper: `全 ${formatNumber(totals.total)} 件中 ${formatPercent(totals.scriptCompleted, totals.total)}`,
    },
    {
      key: "audioSubtitle",
      label: "音声・字幕完了",
      icon: "🎙️",
      value: totals.audioSubtitleCompleted,
      helper: formatPercent(totals.audioSubtitleCompleted, totals.total),
    },
    {
      key: "alert",
      label: "要対応",
      icon: "⚠️",
      value: totals.blocked,
      helper: "検証NG / 失敗の件数",
    },
  ];

  const blockedSorted = [...channelRows].filter((row) => row.blocked > 0).sort((a, b) => b.blocked - a.blocked);

  const focusCards =
    totals.blocked > 0
      ? [
          {
            key: "needsAttention",
            title: "要対応",
            description: "検証NG・再生成失敗などの要確認案件です。",
            primary: blockedSorted[0] ?? null,
            total: totals.blocked,
            metric: (row: ChannelRow) => row.blocked,
            action: (code: string | null) => {
              onFocusNeedsAttention?.(code);
              if (code) {
                handleRowSelect(code);
              }
            },
            actionLabel: "このチャンネルを表示",
            emptyMessage: "要対応案件はありません。",
            footnote: totals.blocked > 0 ? `全体の要対応: ${formatNumber(totals.blocked)} 件` : undefined,
          },
        ]
      : [];

  const normalizedFilter = channelFilter.trim().toLowerCase();
  const filteredChannelRows = normalizedFilter
    ? channelRows.filter((row) => {
        const code = row.code.toLowerCase();
        const name = (row.displayName ?? "").toLowerCase();
        return code.includes(normalizedFilter) || name.includes(normalizedFilter);
      })
    : channelRows;

  const filteredTotals = filteredChannelRows.reduce(
    (acc, row) => {
      acc.total += row.total;
      acc.scriptStarted += row.scriptStarted;
      acc.scriptCompleted += row.scriptCompleted;
      acc.ttsReady += row.ttsReady;
      acc.audioSubtitleCompleted += row.audioSubtitleCompleted;
      acc.blocked += row.blocked;
      acc.audioSubtitleBacklog += row.audioSubtitleBacklog;
      return acc;
    },
    {
      total: 0,
      scriptStarted: 0,
      scriptCompleted: 0,
      ttsReady: 0,
      audioSubtitleCompleted: 0,
      blocked: 0,
      audioSubtitleBacklog: 0,
    }
  );

  const handleRowSelect = (code: string) => {
    const target = `/channels/${encodeURIComponent(code)}`;
    onSelectChannel?.(code);
    if (location.pathname === target) {
      navigate(target, { replace: true, state: { refresh: Date.now() } });
    } else {
      navigate(target);
    }
  };

  const handleRowKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, code: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handleRowSelect(code);
    }
  };

  return (
    <section className="dashboard-overview dashboard-clean">
      <header className="dashboard-overview__header">
        <div>
          <h1>
            <span className="dashboard-overview__title-icon" aria-hidden>
              {titleIcon}
            </span>
            {title ?? "台本・音声制作ダッシュボード"}
          </h1>
          <p className="muted">{subtitle}</p>
        </div>
        <span className="dashboard-overview__timestamp">最終更新 {new Date(overview.generated_at).toLocaleString("ja-JP")}</span>
      </header>

      <section className="dashboard-overview__kpis" aria-label="主要指標">
        {kpiItems.map((item) => (
          <article key={item.key} className="kpi-card">
            <header>{item.label}</header>
            <div className="kpi-card__header">
              <span className="kpi-card__icon" aria-hidden>
                {item.icon}
              </span>
              <p className="kpi-card__value">{formatNumber(item.value)}</p>
            </div>
            <span className="kpi-card__meta">{item.helper}</span>
          </article>
        ))}
      </section>

      {focusCards.length > 0 ? (
        <section className="dashboard-focus" aria-label="滞留状況">
          {focusCards.map((card) => (
            <article
              key={card.key}
              className={`dashboard-focus-card${card.primary ? " dashboard-focus-card--clickable" : ""}`}
              role={card.primary ? "button" : undefined}
              tabIndex={card.primary ? 0 : -1}
              onClick={() => {
                if (card.primary) {
                  card.action(card.primary.code);
                }
              }}
              onKeyDown={(event) => {
                if (!card.primary) return;
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  card.action(card.primary.code);
                }
              }}
            >
              <header className="dashboard-focus-card__header">
                <h2>{card.title}</h2>
                <p className="muted">{card.description}</p>
              </header>
              {card.primary && card.total > 0 ? (
                <div className="dashboard-focus-card__body">
                  <div className="dashboard-focus-card__count">{formatNumber(card.metric(card.primary))}</div>
                  <p className="dashboard-focus-card__channel">
                    {card.primary.code} / {formatPercent(card.metric(card.primary), card.primary.total)}
                  </p>
                  <button
                    type="button"
                    className="dashboard-focus-card__action"
                    onClick={() => card.action(card.primary.code)}
                  >
                    {card.actionLabel}
                  </button>
                </div>
              ) : (
                <p className="muted dashboard-focus-card__empty">{card.emptyMessage}</p>
              )}
              {card.footnote && <span className="dashboard-focus-card__footnote">{card.footnote}</span>}
            </article>
          ))}
        </section>
      ) : null}

      <div className="dashboard-overview__controls" aria-label="チャンネル絞り込み">
        <label className="dashboard-filter">
          <span className="dashboard-filter__label">絞り込み</span>
          <input
            type="search"
            value={channelFilter}
            placeholder="CH13 / チャンネル名…"
            onChange={(event) => setChannelFilter(event.target.value)}
          />
        </label>
        <div className="dashboard-overview__controls-meta">
          <span className="muted small-text">
            表示 {formatNumber(filteredChannelRows.length)} / {formatNumber(channelRows.length)} チャンネル
          </span>
          {normalizedFilter ? (
            <button type="button" className="dashboard-filter__clear" onClick={() => setChannelFilter("")}>
              クリア
            </button>
          ) : null}
        </div>
      </div>

      <div className="dashboard-table-wrapper">
        <table className="dashboard-table">
          <thead>
            <tr>
              <th scope="col">チャンネル</th>
              <th scope="col">企画総数</th>
              <th scope="col">台本着手済み</th>
              <th scope="col">台本完成</th>
              <th scope="col">音声用テキスト完成</th>
              <th scope="col">音声・字幕完了</th>
            </tr>
          </thead>
          <tbody>
            {filteredChannelRows.map((row) => (
              <tr
                key={row.code}
                className={`dashboard-table__row${selectedChannel === row.code ? " dashboard-table__row--selected" : ""}`}
                onClick={() => handleRowSelect(row.code)}
                onKeyDown={(event) => handleRowKeyDown(event, row.code)}
                role="button"
                tabIndex={0}
              >
                <th scope="row">
                  <div className="dashboard-table__channel">
                      <span
                        className={`dashboard-table__avatar${row.avatarUrl ? " dashboard-table__avatar--image" : ""}`}
                        style={
                          row.avatarUrl
                            ? { backgroundImage: `url(${row.avatarUrl})` }
                            : row.themeColor
                              ? { backgroundColor: row.themeColor }
                              : undefined
                        }
                        aria-hidden
                      >
                        {!row.avatarUrl
                          ? (row.displayName ?? row.code).slice(0, 2).toUpperCase()
                          : null}
                      </span>
                    <div className="dashboard-table__channel-texts">
                      <span className="dashboard-table__channel-code">{row.code}</span>
                      {row.displayName ? (
                        <span className="dashboard-table__channel-name">{row.displayName}</span>
                      ) : null}
                    </div>
                  </div>
                </th>
                <td>{formatNumber(row.total)}</td>
                <td>{renderCount(row.scriptStarted, row.total)}</td>
                <td>{renderCount(row.scriptCompleted, row.total)}</td>
                <td>{renderCount(row.ttsReady, row.total)}</td>
                <td>{renderCount(row.audioSubtitleCompleted, row.total)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td>合計</td>
              <td>{formatNumber(filteredTotals.total)}</td>
              <td>{renderCount(filteredTotals.scriptStarted, filteredTotals.total)}</td>
              <td>{renderCount(filteredTotals.scriptCompleted, filteredTotals.total)}</td>
              <td>{renderCount(filteredTotals.ttsReady, filteredTotals.total)}</td>
              <td>{renderCount(filteredTotals.audioSubtitleCompleted, filteredTotals.total)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
