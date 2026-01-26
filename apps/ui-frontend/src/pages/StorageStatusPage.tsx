import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { fetchStorageStatus } from "../api/client";
import type { StorageStatusResponse } from "../api/types";

function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ja-JP");
}

function formatGiB(value?: number | null): string {
  if (value === undefined || value === null) return "-";
  return `${value.toFixed(2)} GiB`;
}

type Tone = "ok" | "warn" | "danger" | "info";

function badgeClass(tone: Tone): string {
  switch (tone) {
    case "danger":
      return "badge badge--alert";
    case "warn":
      return "badge badge--warning";
    case "info":
      return "badge badge--active";
    case "ok":
    default:
      return "badge";
  }
}

function diskTone(freeGiB?: number | null): Tone {
  if (freeGiB === undefined || freeGiB === null) return "info";
  if (freeGiB <= 30) return "danger";
  if (freeGiB <= 60) return "warn";
  return "ok";
}

function triStateText(value?: boolean | null): string {
  if (value === true) return "YES";
  if (value === false) return "NO";
  return "UNKNOWN";
}

export function StorageStatusPage() {
  const [data, setData] = useState<StorageStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (fresh = false) => {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchStorageStatus(fresh);
      setData(payload);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const doctor = data?.storage_doctor ?? null;
  const disk = doctor?.disk ?? null;
  const freeGiB = disk?.free_gib ?? null;
  const diskBadgeTone = diskTone(freeGiB);

  const sharedRoot = doctor?.paths?.shared_storage_root ?? null;
  const planningRoot = doctor?.paths?.planning_root ?? null;
  const workspaceRoot = doctor?.paths?.workspace_root ?? null;
  const vaultRoot = doctor?.paths?.vault_workspaces_root ?? null;
  const assetVault = doctor?.paths?.asset_vault_root ?? null;

  const sharedStatus = useMemo(() => {
    if (!sharedRoot) {
      return { tone: "info" as Tone, label: "共有なし (No Shared)" };
    }
    if (data?.shared_storage_stub === true) {
      return { tone: "warn" as Tone, label: "共有OFFLINE/STUB" };
    }
    const baseOk = data?.shared_storage_base_present;
    const vaultOk = data?.vault_workspaces_present;
    if (baseOk === false || vaultOk === false) {
      const missing: string[] = [];
      if (baseOk === false) missing.push("uploads/<repo>");
      if (vaultOk === false) missing.push("ytm_workspaces");
      return { tone: "warn" as Tone, label: `共有不整合 (Missing: ${missing.join(", ")})` };
    }
    if (data?.shared_storage_stub === false && baseOk !== null && vaultOk !== null) {
      return { tone: "ok" as Tone, label: "共有OK" };
    }
    return { tone: "info" as Tone, label: "共有状態=不明 (Unknown)" };
  }, [data?.shared_storage_base_present, data?.shared_storage_stub, data?.vault_workspaces_present, sharedRoot]);

  const hotSummary = data?.hot_assets ?? null;
  const hotViolations = hotSummary?.violations_total ?? null;
  const hotWarnings = hotSummary?.warnings_total ?? null;
  const hotTone: Tone = hotViolations === null ? "info" : hotViolations > 0 ? "danger" : "ok";

  const warnings = doctor?.warnings ?? [];

  return (
    <div className="page audit-page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">Mac Status / Storage</p>
          <h1>Mac状態（Hot/外部/容量）</h1>
          <p className="page-lead">
            Hot=未投稿は <b>Macローカルに実体が必須</b>。外部（Lenovo共有/Vault）が落ちても止まらないための状態確認ページです。
          </p>
          <p className="page-lead" style={{ marginTop: 6 }}>
            更新: {formatDateTime(data?.generated_at)} {data?.cached ? "(cached)" : ""}
          </p>
        </div>
        <div className="capcut-edit-page__actions">
          <button type="button" className="button button--ghost" onClick={() => load(true)} disabled={loading}>
            {loading ? "取得中…" : "再取得 (Refresh)"}
          </button>
          <Link className="button button--ghost" to="/agent-board">
            共有ボード
          </Link>
          <Link className="button button--ghost" to="/ssot">
            SSOT
          </Link>
        </div>
      </header>

      {error ? (
        <section className="capcut-edit-page__section">
          <div className="shell-panel shell-panel--placeholder">
            <h2>エラー</h2>
            <p className="warning mono">{error}</p>
          </div>
        </section>
      ) : null}

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>サマリ（Summary）</h2>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <span className={badgeClass(diskBadgeTone)}>
              💾 空き容量 (Disk Free): {formatGiB(freeGiB)}{" "}
              <span className="mono">({disk?.used_pct?.toFixed?.(1) ?? "-"}%)</span>
            </span>
            <span className={badgeClass(sharedStatus.tone)}>
              🗄️ {sharedStatus.label} {sharedRoot ? <span className="mono">{sharedRoot}</span> : null}
            </span>
            <span className={badgeClass(hotTone)}>
              🔥 Hot違反 (Hot Violations):{" "}
              <span className="mono">{hotViolations === null ? "-" : String(hotViolations)}</span>
              {hotWarnings !== null ? <span className="mono"> / warnings={hotWarnings}</span> : null}
            </span>
            <span className={badgeClass(data?.vault_sentinel_present ? "ok" : "warn")}>
              🧷 Vault sentinel: {triStateText(data?.vault_sentinel_present)}
            </span>
          </div>
          <p className="shell-panel__subtitle" style={{ marginTop: 10 }}>
            目標: 「未投稿がMacに無い」「参照パスが死ぬ」「外部ダウンで作業停止」を作らない。
          </p>
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>主要パス（Paths）</h2>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            <li>
              workspace_root: <span className="mono">{workspaceRoot ?? "-"}</span>
            </li>
            <li>
              planning_root(effective): <span className="mono">{planningRoot ?? "-"}</span>
            </li>
            <li>
              vault_workspaces_root: <span className="mono">{vaultRoot ?? "-"}</span>
            </li>
            <li>
              asset_vault_root: <span className="mono">{assetVault ?? "-"}</span>
            </li>
          </ul>
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>警告（Warnings）</h2>
          {warnings.length ? (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {warnings.map((w, idx) => (
                <li key={`${idx}-${w}`} className="mono">
                  {w}
                </li>
              ))}
            </ul>
          ) : (
            <p className="shell-panel__subtitle">警告なし。</p>
          )}
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>Hot doctor（直近レポート）</h2>
          <p className="shell-panel__subtitle">
            ソース: <span className="mono">{hotSummary?.report_path ?? "-"}</span>
          </p>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <span className="badge">
              channels: <span className="mono">{hotSummary?.channels_total ?? "-"}</span>
            </span>
            <span className="badge">
              checked_hot: <span className="mono">{hotSummary?.hot_checked_total ?? "-"}</span>
            </span>
            <span className="badge">
              violations: <span className="mono">{hotSummary?.violations_total ?? "-"}</span>
            </span>
          </div>
          {hotSummary?.channels_with_violations?.length ? (
            <p className="warning" style={{ marginTop: 10 }}>
              違反チャンネル: <span className="mono">{hotSummary.channels_with_violations.join(", ")}</span>
            </p>
          ) : null}
          {data?.hot_assets_error ? <p className="warning mono">{data.hot_assets_error}</p> : null}
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>CLI（必要ならここで再確認）</h2>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            <li className="mono">./ops storage doctor</li>
            <li className="mono">python3 scripts/ops/hot_assets_doctor.py --all-channels --json</li>
            <li className="mono">./ops ssot audit --strict</li>
          </ul>
          <details style={{ marginTop: 10 }}>
            <summary>YTM環境（.env / runtime）</summary>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 10 }}>
              <div>
                <h3 style={{ margin: "0 0 6px" }}>.env（YTM_*）</h3>
                <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                  {JSON.stringify(data?.dotenv_ytm ?? {}, null, 2)}
                </pre>
              </div>
              <div>
                <h3 style={{ margin: "0 0 6px" }}>runtime（YTM_*）</h3>
                <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                  {JSON.stringify(data?.runtime_ytm ?? {}, null, 2)}
                </pre>
              </div>
            </div>
          </details>
        </div>
      </section>
    </div>
  );
}
