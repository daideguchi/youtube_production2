/* eslint-disable no-console */

const INDEX_URL = "../data/snapshot/channels.json";

function $(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element: ${id}`);
  return el;
}

function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function guessGitHubRepoFromPages() {
  const host = window.location.hostname;
  if (!host.endsWith(".github.io")) return null;
  const owner = host.replace(/\.github\.io$/, "");
  const pathParts = window.location.pathname.split("/").filter(Boolean);
  const repo = pathParts.length ? pathParts[0] : null;
  return repo ? { owner, repo } : null;
}

function resolveRawBase() {
  const params = new URLSearchParams(window.location.search);
  const rawBaseOverride = params.get("rawBase");
  if (rawBaseOverride) return rawBaseOverride.replace(/\/+$/, "") + "/";

  const branch = params.get("branch") || "main";
  const owner = params.get("owner");
  const repo = params.get("repo");
  if (owner && repo) {
    return `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/`;
  }

  const guessed = guessGitHubRepoFromPages();
  if (guessed) {
    return `https://raw.githubusercontent.com/${guessed.owner}/${guessed.repo}/${branch}/`;
  }

  // local preview: serve repo root via `python3 -m http.server` and open `/docs/`
  return `${window.location.origin}/`;
}

function joinUrl(base, path) {
  const safeBase = String(base || "").replace(/\/+$/, "") + "/";
  const safePath = String(path || "").replace(/^\/+/, "");
  return safeBase + safePath;
}

function normChannel(raw) {
  const s = String(raw || "").trim().toUpperCase();
  const m = s.match(/^CH(\d+)$/);
  if (m) return `CH${String(Number(m[1])).padStart(2, "0")}`;
  return s;
}

function normVideo(raw) {
  const s = String(raw || "").trim();
  const n = Number(s);
  if (!Number.isFinite(n)) return s.padStart(3, "0");
  return String(Math.trunc(n)).padStart(3, "0");
}

function stageBadgeLabel(key) {
  switch (key) {
    case "script_outline":
      return "outline";
    case "script_draft":
      return "draft";
    case "script_review":
      return "review";
    case "quality_check":
      return "qc";
    case "script_validation":
      return "validate";
    case "audio_synthesis":
      return "audio";
    default:
      return key;
  }
}

function classifyStatus(status) {
  const s = String(status || "").trim().toLowerCase();
  if (!s) return "off";
  if (["completed", "ok", "passed", "success", "done", "script_validated"].includes(s)) return "ok";
  if (["running", "in_progress", "working"].includes(s)) return "run";
  if (["skipped", "skip", "ignored", "n/a", "na"].includes(s)) return "off";
  if (["failed", "error", "invalid"].includes(s)) return "bad";
  if (["warning", "warn", "minor"].includes(s)) return "warn";
  return "off";
}

let indexData = null;
let channels = [];
let selectedChannel = null;
let channelData = null;

const channelSelect = $("channelSelect");
const searchInput = $("searchInput");
const rowsSelect = $("rowsSelect");
const tableBody = $("tableBody");
const alertBox = $("alertBox");
const metaTitle = $("metaTitle");
const planningCsvLink = $("planningCsvLink");
const openDataJson = $("openDataJson");
const loading = $("loading");
const footerMeta = $("footerMeta");
const rawBase = resolveRawBase();

function setLoading(on) {
  loading.hidden = !on;
}

function setAlert(text) {
  alertBox.textContent = text || "";
  alertBox.hidden = !text;
}

function parseQuery() {
  const params = new URLSearchParams(window.location.search);
  const ch = params.get("channel");
  const q = params.get("q");
  return { channel: ch ? normChannel(ch) : null, q: q ? String(q) : "" };
}

function writeQuery(next) {
  const url = new URL(window.location.href);
  if (next.channel) url.searchParams.set("channel", next.channel);
  else url.searchParams.delete("channel");
  if (next.q) url.searchParams.set("q", next.q);
  else url.searchParams.delete("q");
  window.history.replaceState({}, "", url);
}

function renderChannelSelect() {
  channelSelect.innerHTML = "";
  for (const entry of channels) {
    const opt = document.createElement("option");
    opt.value = entry.channel;
    opt.textContent = `${entry.channel} (${entry.scripts_count}/${entry.planning_count})`;
    channelSelect.appendChild(opt);
  }
}

function scriptViewerLink(channel, video) {
  const url = new URL("../", window.location.href);
  url.searchParams.set("channel", channel);
  url.searchParams.set("video", video);
  return url.toString();
}

function renderTable() {
  tableBody.innerHTML = "";
  if (!channelData?.episodes?.length) return;

  const limit = Number(rowsSelect.value) || 100;
  const q = String(searchInput.value || "").trim().toLowerCase();

  const filtered = channelData.episodes.filter((ep) => {
    if (!q) return true;
    const id = String(ep.video_id || "").toLowerCase();
    const title = String(ep.title || "").toLowerCase();
    const video = String(ep.video || "").toLowerCase();
    return id.includes(q) || title.includes(q) || video === q;
  });

  const sliced = filtered.slice(0, limit);
  for (const ep of sliced) {
    const tr = document.createElement("tr");

    const planning = ep.planning || {};
    const progress = planning["進捗"] || "";
    const planningUpdated = planning["更新日時"] || "";

    const script = ep.script || null;
    const scriptStatus = script?.status || "";

    const stages = script?.stages || {};
    const stageKeys = [
      "script_outline",
      "script_draft",
      "script_review",
      "quality_check",
      "script_validation",
      "audio_synthesis",
    ];
    const stageBadges = stageKeys
      .map((k) => {
        const st = stages?.[k] || "";
        const cls = classifyStatus(st);
        const label = stageBadgeLabel(k);
        const title = st ? `${label}: ${st}` : `${label}: —`;
        return `<span class="badge badge--${cls}" title="${escapeHtml(title)}">${escapeHtml(label)}</span>`;
      })
      .join("");

    const assembledPath = ep.assembled_path || "";
    const assembledUrl = assembledPath ? joinUrl(rawBase, assembledPath) : "";
    const statusPath = script?.status_path || "";
    const statusUrl = statusPath ? joinUrl(rawBase, statusPath) : "";

    const idHtml = `<a class="link mono" href="${escapeHtml(scriptViewerLink(ep.channel, ep.video))}">${escapeHtml(ep.video_id)}</a>`;
    const titleHtml = `<div class="cell-title"><span class="cell-title__title">${escapeHtml(ep.title || "—")}</span><span class="cell-title__sub">${escapeHtml(
      planningUpdated
    )}</span></div>`;

    const scriptBadge = `<span class="badge badge--${classifyStatus(scriptStatus)}" title="${escapeHtml(
      scriptStatus || "—"
    )}">${escapeHtml(scriptStatus || "—")}</span>`;

    const links = [
      `<a class="btn btn--ghost" href="${escapeHtml(scriptViewerLink(ep.channel, ep.video))}">open</a>`,
      assembledUrl ? `<a class="btn btn--ghost" href="${escapeHtml(assembledUrl)}" target="_blank" rel="noreferrer">raw</a>` : "",
      statusUrl ? `<a class="btn btn--ghost" href="${escapeHtml(statusUrl)}" target="_blank" rel="noreferrer">status</a>` : "",
    ]
      .filter(Boolean)
      .join("");

    tr.innerHTML = `
      <td class="mono">${idHtml}</td>
      <td>${titleHtml}</td>
      <td>${escapeHtml(progress || "—")}</td>
      <td>${scriptBadge}</td>
      <td><div class="badges">${stageBadges}</div></td>
      <td><div class="links">${links}</div></td>
    `;
    tableBody.appendChild(tr);
  }

  const extra = filtered.length > sliced.length ? ` (showing ${sliced.length}/${filtered.length})` : ` (${filtered.length})`;
  footerMeta.textContent = `channel: ${selectedChannel} · episodes: ${channelData.episodes.length}${extra} · generated: ${channelData.generated_at || "—"}`;
}

async function loadChannel(channel) {
  const entry = channels.find((c) => c.channel === channel);
  if (!entry) return;
  selectedChannel = channel;
  setAlert("");
  setLoading(true);
  try {
    const url = joinUrl(new URL("../", window.location.href).toString(), entry.data_path);
    openDataJson.href = url;

    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`channel fetch failed: ${res.status} ${res.statusText}`);
    channelData = await res.json();

    const planningCsv = channelData?.planning_csv || "";
    planningCsvLink.textContent = planningCsv || "—";
    planningCsvLink.href = planningCsv ? joinUrl(rawBase, planningCsv) : "#";

    metaTitle.textContent = `${channel} · planning ${channelData?.planning_count || 0} · scripts ${channelData?.scripts_count || 0}`;
    renderTable();
    writeQuery({ channel, q: String(searchInput.value || "") });
  } catch (err) {
    console.error(err);
    channelData = null;
    tableBody.innerHTML = "";
    metaTitle.textContent = `${channel} · 読み込み失敗`;
    planningCsvLink.textContent = "—";
    planningCsvLink.href = "#";
    footerMeta.textContent = "—";
    setAlert(`スナップショットの読み込みに失敗しました。\n\n${String(err)}\n\n※ GitHub Pages 側では workflow が data を生成します。ローカルでは:\npython3 scripts/ops/pages_snapshot_export.py --write`);
  } finally {
    setLoading(false);
  }
}

async function reloadIndex() {
  setLoading(true);
  setAlert("");
  try {
    const res = await fetch(INDEX_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`index fetch failed: ${res.status} ${res.statusText}`);
    indexData = await res.json();
    channels = Array.isArray(indexData?.channels) ? indexData.channels : [];

    if (!channels.length) {
      setAlert("channels.json が空です。workflow で snapshot export が実行されているか確認してください。");
      channelSelect.innerHTML = "";
      tableBody.innerHTML = "";
      metaTitle.textContent = "—";
      footerMeta.textContent = `generated: ${indexData?.generated_at || "—"}`;
      return;
    }

    renderChannelSelect();
    const { channel, q } = parseQuery();
    if (q) searchInput.value = q;

    const target = channel && channels.some((c) => c.channel === channel) ? channel : channels[0].channel;
    channelSelect.value = target;
    await loadChannel(target);
  } catch (err) {
    console.error(err);
    setAlert(`channels.json の読み込みに失敗しました。\n\n${String(err)}\n\n※ GitHub Pages 側では workflow が data を生成します。ローカルでは:\npython3 scripts/ops/pages_snapshot_export.py --write`);
  } finally {
    setLoading(false);
  }
}

function setupEvents() {
  $("reload").addEventListener("click", () => void reloadIndex());

  channelSelect.addEventListener("change", () => {
    const ch = channelSelect.value;
    writeQuery({ channel: ch, q: String(searchInput.value || "") });
    void loadChannel(ch);
  });

  searchInput.addEventListener("input", () => {
    writeQuery({ channel: selectedChannel, q: String(searchInput.value || "") });
    renderTable();
  });

  rowsSelect.addEventListener("change", () => renderTable());
}

setupEvents();
void reloadIndex();

