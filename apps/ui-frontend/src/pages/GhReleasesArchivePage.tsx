import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  getGhReleasesArchiveLatest,
  getGhReleasesArchiveStatus,
  getGhReleasesArchiveTags,
  searchGhReleasesArchive,
  type GhReleasesArchiveItem,
  type GhReleasesArchiveStatus,
  type GhReleasesArchiveTagCount,
} from "../api/ghReleasesArchive";

function formatDateTime(value?: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP");
}

function formatBytes(value?: number | null): string {
  if (!value || value <= 0) {
    return "";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const digits = unitIndex <= 1 ? 0 : 2;
  return `${size.toFixed(digits)} ${units[unitIndex]}`;
}

function tagValue(tags: string[], key: string): string | null {
  const prefix = `${key}:`;
  const hit = tags.find((t) => t.startsWith(prefix));
  if (!hit) return null;
  const value = hit.slice(prefix.length).trim();
  return value || null;
}

function copyToClipboard(text: string): Promise<boolean> {
  const value = String(text ?? "");
  if (!value) {
    return Promise.resolve(false);
  }
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    return navigator.clipboard
      .writeText(value)
      .then(() => true)
      .catch(() => false);
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return Promise.resolve(Boolean(ok));
  } catch {
    return Promise.resolve(false);
  }
}

function shQuoteDouble(value: string): string {
  const escaped = String(value ?? "").replaceAll("\\", "\\\\").replaceAll('"', '\\"');
  return `"${escaped}"`;
}

function buildRestoreCommand(item: GhReleasesArchiveItem): string {
  const archiveId = item.archive_id;
  const outdir = "/tmp";
  const pull = `./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py pull ${shQuoteDouble(
    archiveId
  )} --outdir ${shQuoteDouble(outdir)}`;

  const type = tagValue(item.tags ?? [], "type");
  if (type !== "episode_asset_pack") {
    return pull;
  }

  const originalName = item.original_name || `${archiveId}.bin`;
  const bundlePath = `${outdir}/${originalName}`;
  return [
    "# 1) bundle を復元（download + sha256 verify）",
    pull,
    "",
    "# 2) workspaces へ展開（{CHxx}/{NNN}/... が作られる）",
    "mkdir -p workspaces/video/assets/episodes",
    `tar -xzf ${shQuoteDouble(bundlePath)} -C workspaces/video/assets/episodes`,
  ].join("\n");
}

export function GhReleasesArchivePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const qParam = (searchParams.get("q") ?? "").trim();
  const tagParam = (searchParams.get("tag") ?? "").trim();
  const isSearching = Boolean(qParam || tagParam);

  const [query, setQuery] = useState(qParam);
  const [tag, setTag] = useState(tagParam);

  const [status, setStatus] = useState<GhReleasesArchiveStatus | null>(null);
  const [tagCounts, setTagCounts] = useState<GhReleasesArchiveTagCount[]>([]);
  const [items, setItems] = useState<GhReleasesArchiveItem[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  useEffect(() => {
    setQuery(qParam);
    setTag(tagParam);
  }, [qParam, tagParam]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setCopiedId(null);
    try {
      const [statusResp, tagsResp] = await Promise.all([getGhReleasesArchiveStatus(), getGhReleasesArchiveTags()]);
      setStatus(statusResp);
      setTagCounts(tagsResp.items ?? []);

      if (isSearching) {
        const resp = await searchGhReleasesArchive({ query: qParam, tag: tagParam, limit: 200, offset: 0 });
        setItems(resp.items ?? []);
        setTotal(Number.isFinite(resp.total) ? resp.total : (resp.items ?? []).length);
      } else {
        const latest = await getGhReleasesArchiveLatest(200);
        setItems(latest ?? []);
        setTotal(null);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message || "読み込みに失敗しました。");
      setItems([]);
      setTotal(null);
    } finally {
      setLoading(false);
    }
  }, [isSearching, qParam, tagParam]);

  useEffect(() => {
    void load();
  }, [load]);

  const applyFilters = useCallback(() => {
    const params = new URLSearchParams(searchParams);
    const q = query.trim();
    const t = tag.trim();
    if (q) {
      params.set("q", q);
    } else {
      params.delete("q");
    }
    if (t) {
      params.set("tag", t);
    } else {
      params.delete("tag");
    }
    setSearchParams(params, { replace: true });
  }, [query, searchParams, setSearchParams, tag]);

  const clearFilters = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: true });
  }, [setSearchParams]);

  const rows = useMemo(() => {
    return (items ?? []).map((item) => {
      const tags = item.tags ?? [];
      const type = tagValue(tags, "type") ?? "";
      const channel = tagValue(tags, "channel") ?? "";
      const video = tagValue(tags, "video") ?? "";
      const stage = tagValue(tags, "stage") ?? "";
      const episode = channel && video ? `${channel}-${video}` : channel || "";
      return { item, type, stage, episode };
    });
  }, [items]);

  const copyRestore = useCallback(async (item: GhReleasesArchiveItem) => {
    const ok = await copyToClipboard(buildRestoreCommand(item));
    setCopiedId(ok ? item.archive_id : null);
  }, []);

  return (
    <div className="page">
      <header className="capcut-edit-page__hero">
        <div>
          <p className="page-subtitle">Archive Vault</p>
          <h1>書庫（GitHub Releases Archive）</h1>
          <p className="page-lead">重いアセット（例: Episode Asset Pack）を GitHub Releases に退避した“目録”を確認します。</p>
        </div>
        <div className="capcut-edit-page__actions">
          <Link className="button button--ghost" to="/dashboard">
            ← ダッシュボード
          </Link>
        </div>
      </header>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>状態</h2>
          <p className="shell-panel__subtitle">目録（manifest/index）は repo 内。実体は GitHub Releases assets にあります。</p>
          {status ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
              <div>
                <div className="shell-panel__subtitle">archive_dir</div>
                <code style={{ fontSize: 12 }}>{status.archive_dir}</code>
              </div>
              <div>
                <div className="shell-panel__subtitle">entries</div>
                <div>
                  manifest: {status.manifest_entry_count} / latest: {status.latest_index_count}
                </div>
              </div>
              <div>
                <div className="shell-panel__subtitle">CLI</div>
                <div style={{ display: "grid", gap: 6 }}>
                  <code style={{ fontSize: 12 }}>gh auth status</code>
                  <code style={{ fontSize: 12 }}>./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py list --query "CHxx"</code>
                </div>
              </div>
            </div>
          ) : (
            <div className="shell-panel__subtitle">status: (loading)</div>
          )}
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel shell-panel--placeholder">
          <h2>検索</h2>
          <p className="shell-panel__subtitle">query は archive_id / original_name / tags / note に対して部分一致します。</p>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <label style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
              <span>query</span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    applyFilters();
                  }
                }}
                placeholder='例: "CH27" / "type:episode_asset_pack"'
                style={{ minWidth: 260 }}
              />
            </label>
            <label style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
              <span>tag</span>
              <select value={tag} onChange={(event) => setTag(event.target.value)} style={{ minWidth: 260 }}>
                <option value="">(tag filterなし)</option>
                {tagCounts.map((t) => (
                  <option key={t.tag} value={t.tag}>
                    {t.tag} ({t.count})
                  </option>
                ))}
              </select>
            </label>
            <button className="button" onClick={applyFilters} disabled={loading}>
              検索
            </button>
            <button className="button button--ghost" onClick={clearFilters} disabled={loading}>
              クリア
            </button>
            <button className="button button--ghost" onClick={() => void load()} disabled={loading}>
              再読み込み
            </button>
          </div>
        </div>
      </section>

      <section className="capcut-edit-page__section">
        <div className="shell-panel">
          <h2>一覧</h2>
          {error ? <p style={{ color: "#b00020" }}>{error}</p> : null}
          <p className="shell-panel__subtitle">
            {loading ? "loading..." : isSearching ? `hits: ${total ?? rows.length}` : `latest: ${rows.length}`}
          </p>

          {rows.length === 0 && !loading ? (
            <p className="shell-panel__subtitle">まだ書庫エントリがありません（latest.json が空）。</p>
          ) : null}

          {rows.length > 0 ? (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: "8px 6px" }}>created</th>
                    <th style={{ textAlign: "left", padding: "8px 6px" }}>archive_id</th>
                    <th style={{ textAlign: "left", padding: "8px 6px" }}>type</th>
                    <th style={{ textAlign: "left", padding: "8px 6px" }}>episode</th>
                    <th style={{ textAlign: "left", padding: "8px 6px" }}>name</th>
                    <th style={{ textAlign: "left", padding: "8px 6px" }}>size</th>
                    <th style={{ textAlign: "left", padding: "8px 6px" }}>note</th>
                    <th style={{ textAlign: "left", padding: "8px 6px" }}>actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(({ item, type, episode }) => (
                    <tr key={item.archive_id} style={{ borderTop: "1px solid rgba(0,0,0,0.08)" }}>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>{formatDateTime(item.created_at)}</td>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
                        <code style={{ fontSize: 12 }}>{item.archive_id}</code>
                      </td>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
                        <code style={{ fontSize: 12 }}>{type}</code>
                      </td>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
                        <code style={{ fontSize: 12 }}>{episode}</code>
                      </td>
                      <td style={{ padding: "8px 6px" }}>
                        <div style={{ display: "grid", gap: 2 }}>
                          <span style={{ fontSize: 13 }}>{item.original_name}</span>
                          <span className="shell-panel__subtitle" style={{ fontSize: 11 }}>
                            {item.repo} / {item.release_tag}
                          </span>
                        </div>
                      </td>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>{formatBytes(item.original_size_bytes)}</td>
                      <td style={{ padding: "8px 6px" }}>{item.note}</td>
                      <td style={{ padding: "8px 6px", whiteSpace: "nowrap" }}>
                        <button className="button button--ghost" onClick={() => void copyRestore(item)}>
                          {copiedId === item.archive_id ? "copied" : "Copy restore cmd"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

