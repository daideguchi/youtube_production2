import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { getLlmUsageSummary } from "../api/llmUsage";
import "./LlmUsageDashboardPage.css";

type Agg = {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_hit_calls: number;
  cache_hit_total_tokens: number;
};

type UsageItem = Agg & {
  provider?: string;
  task?: string;
  label?: string;
  model?: string;
  channel?: string;
  routing_key?: string;
};

type FailureListItem = { key: string; count: number };

type UsageSummaryResponse = {
  range: { key: string; since: string | null; until: string | null };
  log: { path: string; line_count: number; mtime: number | null };
  totals: Agg;
  providers: UsageItem[];
  tasks: UsageItem[];
  models: UsageItem[];
  channels: UsageItem[];
  routing_keys: UsageItem[];
  daily: Array<{ day: string } & Agg>;
  failures: {
    total: number;
    by_status_code: FailureListItem[];
    by_task: FailureListItem[];
    by_provider: FailureListItem[];
    recent: Array<{
      timestamp: string | null;
      status: string | null;
      status_code: number | string | null;
      task: string | null;
      routing_key: string | null;
      provider: string | null;
      model: string | null;
      error: string | null;
    }>;
  };
  top_calls: Array<{
    timestamp: string | null;
    task: string | null;
    task_label: string | null;
    routing_key: string | null;
    provider: string | null;
    model: string | null;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    finish_reason: string | null;
  }>;
};

type RangeKey = "today_jst" | "last_24h" | "last_7d" | "last_30d" | "all";

const RANGE_OPTIONS: Array<{ value: RangeKey; label: string }> = [
  { value: "today_jst", label: "今日（JST）" },
  { value: "last_24h", label: "過去24時間" },
  { value: "last_7d", label: "過去7日" },
  { value: "last_30d", label: "過去30日" },
  { value: "all", label: "全期間" },
];

const PROVIDER_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "すべて" },
  { value: "fireworks", label: "Fireworks" },
  { value: "codex_exec", label: "Codex exec" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "azure", label: "Azure" },
  { value: "gemini", label: "Gemini" },
];

function formatNumber(value: number): string {
  return (value ?? 0).toLocaleString("ja-JP");
}

function formatTokensCompact(value: number): string {
  const n = value ?? 0;
  const abs = Math.abs(n);
  if (abs >= 1e8) {
    return `${(n / 1e8).toFixed(abs >= 1e9 ? 1 : 2)}億`;
  }
  if (abs >= 1e4) {
    return `${(n / 1e4).toFixed(abs >= 1e6 ? 1 : 2)}万`;
  }
  return formatNumber(n);
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP");
}

function clamp01(n: number): number {
  if (n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

function percentLabel(value: number, total: number): string {
  if (!total) return "0%";
  return `${(clamp01(value / total) * 100).toFixed(1)}%`;
}

function BarList({
  title,
  subtitle,
  items,
  totalTokens,
  keyName,
}: {
  title: string;
  subtitle?: string;
  items: UsageItem[];
  totalTokens: number;
  keyName: keyof UsageItem;
}) {
  const maxTokens = useMemo(() => Math.max(1, ...items.map((it) => it.total_tokens ?? 0)), [items]);

  return (
    <section className="capcut-edit-page__section">
      <div className="shell-panel shell-panel--placeholder llm-usage-dashboard__panel">
        <div className="llm-usage-dashboard__panel-header">
          <div>
            <h2>{title}</h2>
            {subtitle ? <p className="shell-panel__subtitle">{subtitle}</p> : null}
          </div>
          <div className="muted small-text">Top {items.length}</div>
        </div>

        <div className="llm-usage-dashboard__bar-list">
          {items.map((it) => {
            const labelRaw = String(it[keyName] ?? "-");
            const label = keyName === "task" ? it.label ?? labelRaw : labelRaw;
            const width = `${clamp01((it.total_tokens ?? 0) / maxTokens) * 100}%`;
            const tokens = it.total_tokens ?? 0;
            return (
              <div key={labelRaw} className="llm-usage-dashboard__bar-row">
                <div className="llm-usage-dashboard__bar-label" title={label}>
                  <div className="llm-usage-dashboard__bar-label-title">{label}</div>
                  <div className="llm-usage-dashboard__bar-label-sub mono">{labelRaw}</div>
                </div>
                <div className="llm-usage-dashboard__bar">
                  <div className="llm-usage-dashboard__bar-fill" style={{ width }} />
                </div>
                <div className="llm-usage-dashboard__bar-value mono">
                  {formatNumber(tokens)}
                  <div className="muted">{percentLabel(tokens, totalTokens)}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

export function LlmUsageDashboardPage() {
  const [rangeKey, setRangeKey] = useState<RangeKey>(() => {
    const stored = localStorage.getItem("llmUsage.dashboard.range") as RangeKey | null;
    return stored ?? "today_jst";
  });
  const [provider, setProvider] = useState<string>(() => localStorage.getItem("llmUsage.dashboard.provider") ?? "");
  const [topN, setTopN] = useState<number>(() => {
    const stored = localStorage.getItem("llmUsage.dashboard.topN");
    return stored ? Number(stored) : 12;
  });

  const [data, setData] = useState<UsageSummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    localStorage.setItem("llmUsage.dashboard.range", rangeKey);
  }, [rangeKey]);
  useEffect(() => {
    localStorage.setItem("llmUsage.dashboard.provider", provider);
  }, [provider]);
  useEffect(() => {
    localStorage.setItem("llmUsage.dashboard.topN", String(topN));
  }, [topN]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getLlmUsageSummary({ range: rangeKey, topN, provider });
      setData(res as UsageSummaryResponse);
    } catch (e: any) {
      setError(e?.message || "failed to load");
    } finally {
      setLoading(false);
    }
  }, [provider, rangeKey, topN]);

  useEffect(() => {
    load();
  }, [load]);

  const totals = data?.totals;
  const totalTokens = totals?.total_tokens ?? 0;
  const failureTotal = data?.failures?.total ?? 0;
  const code402 = useMemo(() => {
    const items = data?.failures?.by_status_code ?? [];
    const hit = items.find((it) => String(it.key) === "402");
    return hit?.count ?? 0;
  }, [data]);

  const cacheHitTokens = totals?.cache_hit_total_tokens ?? 0;
  const cacheHitCalls = totals?.cache_hit_calls ?? 0;

  return (
    <div className="page llm-usage-dashboard-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">LLM Usage</p>
          <h1>LLMトークン消費ダッシュボード</h1>
          <p className="page-lead">「どの処理に、どれだけトークン/呼び出しが使われたか」をざっくり把握するための画面です。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button button--ghost" to="/llm-usage">
            ログ/Override →
          </Link>
        </div>
      </header>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder llm-usage-dashboard__panel">
          <h2>集計条件</h2>
          <p className="shell-panel__subtitle">OpenRouter がリミット/クレジット不足になった時の原因特定にも使えます。</p>

          <div className="llm-usage-dashboard__controls">
            <label className="llm-usage-dashboard__control">
              <span>期間</span>
              <select value={rangeKey} onChange={(e) => setRangeKey(e.target.value as RangeKey)}>
                {RANGE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="llm-usage-dashboard__control">
              <span>プロバイダ</span>
              <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                {PROVIDER_OPTIONS.map((opt) => (
                  <option key={opt.value || "all"} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="llm-usage-dashboard__control">
              <span>Top N</span>
              <input
                type="number"
                min={3}
                max={50}
                value={topN}
                onChange={(e) => setTopN(Math.max(3, Math.min(50, Number(e.target.value))))}
              />
            </label>

            <button className="button button--primary" onClick={load} disabled={loading}>
              {loading ? "集計中…" : "再集計"}
            </button>
          </div>

          {error ? <div className="error llm-usage-dashboard__error">エラー: {error}</div> : null}
          <div className="muted small-text">
            {data?.range?.since ? `since: ${formatDateTime(data.range.since)}` : null}
            {data?.range?.until ? ` / until: ${formatDateTime(data.range.until)}` : null}
            {data?.log?.line_count ? ` / records: ${formatNumber(data.log.line_count)}` : null}
          </div>
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="llm-usage-dashboard__summary-grid">
          <div className="summary-card summary-card--primary llm-usage-dashboard__summary-card">
            <div className="summary-card__title">総トークン</div>
            <div className="summary-card__value">{formatTokensCompact(totalTokens)}</div>
            <div className="summary-card__helper mono">{formatNumber(totalTokens)}</div>
          </div>

          <div className="summary-card summary-card--info llm-usage-dashboard__summary-card">
            <div className="summary-card__title">呼び出し回数（success）</div>
            <div className="summary-card__value">{formatNumber(totals?.calls ?? 0)}</div>
            <div className="summary-card__helper">prompt+completion の合計だけカウント</div>
          </div>

          <div className="summary-card summary-card--neutral llm-usage-dashboard__summary-card">
            <div className="summary-card__title">prompt tokens</div>
            <div className="summary-card__value">{formatTokensCompact(totals?.prompt_tokens ?? 0)}</div>
            <div className="summary-card__helper mono">{formatNumber(totals?.prompt_tokens ?? 0)}</div>
          </div>

          <div className="summary-card summary-card--neutral llm-usage-dashboard__summary-card">
            <div className="summary-card__title">completion tokens</div>
            <div className="summary-card__value">{formatTokensCompact(totals?.completion_tokens ?? 0)}</div>
            <div className="summary-card__helper mono">{formatNumber(totals?.completion_tokens ?? 0)}</div>
          </div>

          <div className="summary-card summary-card--warning llm-usage-dashboard__summary-card">
            <div className="summary-card__title">cache.hit（参考）</div>
            <div className="summary-card__value">{formatTokensCompact(cacheHitTokens)}</div>
            <div className="summary-card__helper">
              {formatNumber(cacheHitCalls)} calls / {percentLabel(cacheHitTokens, totalTokens)}
            </div>
          </div>

          <div className={`summary-card ${code402 ? "summary-card--danger" : "summary-card--success"} llm-usage-dashboard__summary-card`}>
            <div className="summary-card__title">失敗（non-success）</div>
            <div className="summary-card__value">{formatNumber(failureTotal)}</div>
            <div className="summary-card__helper">
              402（クレジット不足）: <span className={code402 ? "error mono" : "mono"}>{formatNumber(code402)}</span>
            </div>
          </div>
        </div>
      </section>

      {data ? (
        <>
          <BarList
            title="処理別（タスク）トークン使用量"
            subtitle="どの処理が最もトークンを食っているか（上位）"
            items={data.tasks ?? []}
            totalTokens={totalTokens}
            keyName="task"
          />

          <BarList title="モデル別トークン使用量" subtitle="どのモデルが最もトークンを使っているか（上位）" items={data.models ?? []} totalTokens={totalTokens} keyName="model" />

          {data.channels?.length ? (
            <BarList title="チャンネル別トークン使用量" subtitle="routing_key(CHxx-NNN) があるもののみ集計（上位）" items={data.channels ?? []} totalTokens={totalTokens} keyName="channel" />
          ) : null}

          {data.routing_keys?.length ? (
            <BarList title="動画（routing_key）別トークン使用量" subtitle="CHxx-NNN 形式のルーティングキー上位" items={data.routing_keys ?? []} totalTokens={totalTokens} keyName="routing_key" />
          ) : null}

          <section className="capcut-edit-page__section">
            <div className="shell-panel shell-panel--placeholder llm-usage-dashboard__panel">
              <h2>失敗（non-success）内訳</h2>
              <p className="shell-panel__subtitle">トークンは成功レスポンスの usage からのみ算出。失敗は回数で表示します。</p>

              <div className="llm-usage-dashboard__two-col">
                <div>
                  <h3>ステータスコード</h3>
                  <div className="llm-usage-dashboard__kv-list">
                    {(data.failures?.by_status_code ?? []).map((it) => (
                      <div key={it.key} className="llm-usage-dashboard__kv">
                        <span className="mono">{it.key}</span>
                        <span className="mono">{formatNumber(it.count)}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h3>タスク</h3>
                  <div className="llm-usage-dashboard__kv-list">
                    {(data.failures?.by_task ?? []).slice(0, topN).map((it) => (
                      <div key={it.key} className="llm-usage-dashboard__kv">
                        <span className="mono">{it.key}</span>
                        <span className="mono">{formatNumber(it.count)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {(data.failures?.recent ?? []).length ? (
                <>
                  <h3 style={{ marginTop: 18 }}>最近の失敗</h3>
                  <div className="llm-usage-dashboard__table-wrap">
                    <table className="llm-usage-dashboard__table">
                      <thead>
                        <tr>
                          <th>時刻</th>
                          <th>task</th>
                          <th>routing</th>
                          <th>code</th>
                          <th>error</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(data.failures.recent ?? []).slice(0, 25).map((it, idx) => (
                          <tr key={idx}>
                            <td className="mono">{formatDateTime(it.timestamp)}</td>
                            <td className="mono">{it.task ?? "-"}</td>
                            <td className="mono">{it.routing_key ?? "-"}</td>
                            <td className="mono">{String(it.status_code ?? "-")}</td>
                            <td className="mono llm-usage-dashboard__cell-truncate" title={it.error ?? ""}>
                              {it.error ?? "-"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : null}
            </div>
          </section>

          {(data.top_calls ?? []).length ? (
            <section className="capcut-edit-page__section">
              <div className="shell-panel shell-panel--placeholder llm-usage-dashboard__panel">
                <h2>巨大な単発呼び出し（Top）</h2>
                <p className="shell-panel__subtitle">max_tokens が大きい/出力が長いタスクが原因でクレジットを圧迫していないか確認できます。</p>
                <div className="llm-usage-dashboard__table-wrap">
                  <table className="llm-usage-dashboard__table">
                    <thead>
                      <tr>
                        <th>時刻</th>
                        <th>task</th>
                        <th>routing</th>
                        <th>model</th>
                        <th>tokens</th>
                        <th>p</th>
                        <th>c</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(data.top_calls ?? []).slice(0, 20).map((it, idx) => (
                        <tr key={idx}>
                          <td className="mono">{formatDateTime(it.timestamp)}</td>
                          <td>
                            <div>{it.task_label ?? it.task ?? "-"}</div>
                            <div className="muted mono small-text">{it.task ?? "-"}</div>
                          </td>
                          <td className="mono">{it.routing_key ?? "-"}</td>
                          <td className="mono">{it.model ?? "-"}</td>
                          <td className="mono">{formatNumber(it.total_tokens ?? 0)}</td>
                          <td className="mono">{formatNumber(it.prompt_tokens ?? 0)}</td>
                          <td className="mono">{formatNumber(it.completion_tokens ?? 0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          ) : null}

          <section className="capcut-edit-page__section">
            <div className="shell-panel shell-panel--placeholder llm-usage-dashboard__panel">
              <h2>備考</h2>
              <ul className="llm-usage-dashboard__notes">
                <li>
                  <span className="mono">cache.hit</span> は「ローカルキャッシュから返した」印です。トークンは元の usage を保持しているため、課金実態と一致しない可能性があります（参考値）。
                </li>
                <li>
                  402 が出たら OpenRouter 側のクレジット不足です。まず <span className="mono">web_search_openrouter</span> と <span className="mono">script_a_text_quality_shrink</span> の比率を確認してください。
                </li>
              </ul>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
                <Link className="button button--ghost" to="/llm-usage">
                  ログ/Overrideを開く →
                </Link>
              </div>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}

export default LlmUsageDashboardPage;
