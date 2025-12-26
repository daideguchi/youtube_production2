import { useCallback, useEffect, useMemo } from "react";
import { useLocation, useNavigate, useOutletContext } from "react-router-dom";
import { ChannelBenchmarksPanel } from "../components/ChannelBenchmarksPanel";
import type { ChannelSummary } from "../api/types";
import type { ShellOutletContext } from "../layouts/AppShell";

function compareChannelCode(a: string, b: string): number {
  const an = Number.parseInt(a.replace(/[^0-9]/g, ""), 10);
  const bn = Number.parseInt(b.replace(/[^0-9]/g, ""), 10);
  const aNum = Number.isFinite(an);
  const bNum = Number.isFinite(bn);
  if (aNum && bNum) {
    return an - bn;
  }
  if (aNum) return -1;
  if (bNum) return 1;
  return a.localeCompare(b, "ja-JP");
}

function resolveChannelDisplayName(channel: ChannelSummary): string {
  return channel.branding?.title ?? channel.youtube_title ?? channel.name ?? channel.code;
}

export function BenchmarksPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { channels, selectedChannel, selectedChannelSummary, selectChannel } = useOutletContext<ShellOutletContext>();

  const sortedChannels = useMemo(() => {
    const list = [...(channels ?? [])];
    list.sort((left, right) => compareChannelCode(left.code, right.code));
    return list;
  }, [channels]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const requested = params.get("channel");
    if (!requested) return;
    const normalized = requested.trim().toUpperCase();
    if (!normalized) return;
    if (normalized !== selectedChannel) {
      selectChannel(normalized);
    }
  }, [location.search, selectChannel, selectedChannel]);

  const handleSelectChange = useCallback(
    (event: React.ChangeEvent<HTMLSelectElement>) => {
      const value = event.target.value || null;
      selectChannel(value);
      const params = new URLSearchParams(location.search);
      if (value) {
        params.set("channel", value);
      } else {
        params.delete("channel");
      }
      const search = params.toString();
      navigate(`/benchmarks${search ? `?${search}` : ""}`, { replace: true });
    },
    [location.search, navigate, selectChannel]
  );

  const workflow = selectedChannelSummary?.video_workflow ?? null;
  const workflowText = workflow ? `${workflow.label}（${workflow.id}）` : "制作型: 未設定";

  return (
    <section className="channel-settings-page workspace--channel-clean">
      <header className="channel-settings-page__header">
        <div>
          <h1>ベンチマーク</h1>
          <p className="channel-settings-page__summary-subtitle">{workflowText}</p>
        </div>
        <div className="channel-settings-page__controls">
          <label className="channel-settings-page__select-label">
            <span>チャンネル</span>
            <select value={selectedChannel ?? ""} onChange={handleSelectChange}>
              <option value="">未選択</option>
              {sortedChannels.map((channel) => (
                <option key={channel.code} value={channel.code}>
                  {channel.code} / {resolveChannelDisplayName(channel)}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>

      <div className="channel-settings-page__quick-select" role="tablist" aria-label="チャンネル切り替え">
        {sortedChannels.map((channel) => (
          <button
            key={channel.code}
            type="button"
            className={
              channel.code === selectedChannel
                ? "channel-settings-page__pill channel-settings-page__pill--active"
                : "channel-settings-page__pill"
            }
            onClick={() => {
              selectChannel(channel.code);
              navigate(`/benchmarks?channel=${encodeURIComponent(channel.code)}`, { replace: true });
            }}
          >
            {channel.code}
          </button>
        ))}
      </div>

      {workflow ? (
        <div className="channel-profile-banner channel-profile-banner--info">{workflow.description}</div>
      ) : (
        <p className="channel-settings-page__placeholder">左上のプルダウンからチャンネルを選択してください。</p>
      )}

      <ChannelBenchmarksPanel channelCode={selectedChannel} />
    </section>
  );
}

