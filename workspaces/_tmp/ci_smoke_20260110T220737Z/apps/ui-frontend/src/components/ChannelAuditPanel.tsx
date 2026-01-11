import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchChannelAudit } from "../api/client";
import type { ChannelAuditItemResponse } from "../api/types";

type ChannelAuditPanelProps = {
  selectedChannel?: string | null;
  onSelectChannel?: (channel: string) => void;
};

function normalizeHandle(handle?: string | null): string | null {
  if (!handle) return null;
  const trimmed = handle.trim();
  if (!trimmed) return null;
  return trimmed.startsWith("@") ? trimmed : `@${trimmed}`;
}

function summarizeIssues(issues: string[]): string {
  if (!issues.length) return "OK";
  const primary = issues[0];
  const extra = issues.length - 1;
  return extra > 0 ? `${primary} +${extra}` : primary;
}

export function ChannelAuditPanel({ selectedChannel, onSelectChannel }: ChannelAuditPanelProps) {
  const [items, setItems] = useState<ChannelAuditItemResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [onlyMissing, setOnlyMissing] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchChannelAudit();
      setItems(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    if (!onlyMissing) return items;
    return items.filter((item) => item.issues?.length);
  }, [items, onlyMissing]);

  return (
    <section className="channel-audit-panel">
      <header className="channel-audit-panel__header">
        <div>
          <h2>監査（全チャンネル）</h2>
          <p className="channel-audit-panel__subtitle">
            YouTube/タグ/説明文/ベンチマーク/企画CSV/ペルソナ/プロンプトの欠損を横断チェックします。
          </p>
        </div>
        <div className="channel-audit-panel__actions">
          <label className="channel-audit-panel__toggle">
            <input
              type="checkbox"
              checked={onlyMissing}
              onChange={(event) => setOnlyMissing(event.target.checked)}
            />
            欠損のみ
          </label>
          <button type="button" className="channel-profile-button channel-profile-button--ghost" onClick={() => void load()} disabled={loading}>
            {loading ? "更新中…" : "再取得"}
          </button>
        </div>
      </header>

      {error ? <div className="channel-profile-banner channel-profile-banner--error">{error}</div> : null}

      <div className="channel-audit-panel__table-scroll">
        <table className="channel-audit-panel__table">
          <thead>
            <tr>
              <th>CH</th>
              <th>name</th>
              <th>handle</th>
              <th>tags</th>
              <th>bench</th>
              <th>planning</th>
              <th>files</th>
              <th>issues</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={8} className="channel-audit-panel__empty">
                  {onlyMissing ? "欠損は見つかりませんでした。" : "チャンネルがありません。"}
                </td>
              </tr>
            ) : (
              filtered.map((item) => {
                const active = selectedChannel?.toUpperCase() === item.code.toUpperCase();
                const handle = normalizeHandle(item.youtube_handle);
                const issueLabel = summarizeIssues(item.issues ?? []);
                const hasIssues = Boolean(item.issues?.length);
                const filesOkCount = [
                  Boolean(item.planning_csv_exists),
                  Boolean(item.persona_exists),
                  Boolean(item.script_prompt_exists),
                ].filter(Boolean).length;
                return (
                  <tr
                    key={item.code}
                    className={
                      active
                        ? "channel-audit-panel__row is-active"
                        : hasIssues
                          ? "channel-audit-panel__row is-missing"
                          : "channel-audit-panel__row"
                    }
                    onClick={() => onSelectChannel?.(item.code)}
                    role={onSelectChannel ? "button" : undefined}
                  >
                    <td className="mono">{item.code}</td>
                    <td>{item.name ?? "—"}</td>
                    <td className="mono">{handle ?? "—"}</td>
                    <td className="mono">{item.default_tags_count ?? 0}</td>
                    <td className="mono">
                      {item.benchmark_channels_count ?? 0}/{item.benchmark_script_samples_count ?? 0}
                    </td>
                    <td className="mono">{item.planning_rows ?? 0}</td>
                    <td className="mono">{filesOkCount}/3</td>
                    <td className={hasIssues ? "mono is-warn" : "mono is-ok"}>{issueLabel}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
