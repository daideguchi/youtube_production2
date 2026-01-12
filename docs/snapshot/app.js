/* eslint-disable no-console */

const INDEX_URL = "data/snapshot/channels.json";
const CHANNELS_INFO_PATH = "packages/script_pipeline/channels/channels_info.json";
const VIDEO_IMAGES_INDEX_URL = "data/video_images_index.json";
const SITE_ASSET_VERSION = "20260112_10";

const PAGES_ROOT_URL = new URL("../", window.location.href);

function assetUrl(relPath) {
  const safe = String(relPath || "")
    .trim()
    .replace(/^\/+/, "")
    .replace(/^\.\/+/, "");
  const url = new URL(safe, PAGES_ROOT_URL);
  if (SITE_ASSET_VERSION) url.searchParams.set("v", SITE_ASSET_VERSION);
  return url.toString();
}

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

function parseGitHubRepoFromRawBase(rawBase) {
  const s = String(rawBase || "").trim();
  const m = s.match(/^https:\/\/raw\.githubusercontent\.com\/([^/]+)\/([^/]+)\/([^/]+)\//);
  if (!m) return null;
  return { owner: m[1], repo: m[2], branch: m[3] };
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

function resolveGitTreeBase() {
  const params = new URLSearchParams(window.location.search);
  const branchParam = params.get("branch") || "";
  const rawBaseOverride = params.get("rawBase") || "";

  let owner = params.get("owner") || "";
  let repo = params.get("repo") || "";
  let branch = branchParam || "main";

  if (!owner || !repo) {
    const guessed = guessGitHubRepoFromPages();
    if (guessed) {
      owner = guessed.owner;
      repo = guessed.repo;
    }
  }

  if ((!owner || !repo) && rawBaseOverride) {
    const parsed = parseGitHubRepoFromRawBase(rawBaseOverride);
    if (parsed) {
      owner = parsed.owner;
      repo = parsed.repo;
      if (!branchParam) branch = parsed.branch || branch;
    }
  }

  if (!owner || !repo) return null;
  return `https://github.com/${owner}/${repo}/tree/${branch}/`;
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

function episodeAssetPackPath(channel, video) {
  const ch = normChannel(channel);
  const v = normVideo(video);
  if (!ch || !v) return "";
  return `workspaces/video/assets/episodes/${ch}/${v}`;
}

function episodeAssetPackHref(channel, video) {
  const rel = episodeAssetPackPath(channel, video);
  if (!rel) return "";
  return gitTreeBase ? `${gitTreeBase}${rel}` : joinUrl(rawBase, rel);
}

function stageBadgeLabel(key) {
  switch (key) {
    case "script_outline":
      return "構成";
    case "script_draft":
      return "本文";
    case "script_review":
      return "レビュー";
    case "quality_check":
      return "QC";
    case "script_validation":
      return "確定";
    case "audio_synthesis":
      return "音声";
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

function classifyScriptOverallStatus(status) {
  const s = String(status || "").trim().toLowerCase();
  if (!s) return "off";
  if (s === "script_validated") return "ok";
  if (s.includes("in_progress")) return "run";
  if (s.includes("failed") || s.includes("error") || s.includes("invalid")) return "bad";
  if (s.includes("completed")) return "warn";
  if (s.includes("pending")) return "off";
  return "off";
}

function scriptOverallLabel(status) {
  const raw = String(status || "").trim();
  const s = raw.toLowerCase();
  if (!s) return "—";
  if (s === "script_validated") return "A確定";
  if (s.includes("in_progress")) return "作業中";
  if (s.includes("completed")) return "生成済";
  if (s.includes("pending")) return "未着手";
  if (s.includes("failed") || s.includes("error") || s.includes("invalid")) return "要対応";
  return raw;
}

function classifyPlanningProgress(progress) {
  const s = String(progress || "").trim();
  if (!s) return "off";
  if (s.includes("投稿")) return "ok";
  if (s.includes("完了")) return "ok";
  if (s.includes("失敗") || s.includes("エラー") || s.includes("error") || s.includes("failed")) return "bad";
  if (s.includes("済")) return "run";
  return "warn";
}

function statusSymbol(cls) {
  switch (cls) {
    case "ok":
      return "✓";
    case "run":
      return "…";
    case "warn":
      return "!";
    case "bad":
      return "×";
    default:
      return "—";
  }
}

let indexData = null;
let channels = [];
let selectedChannel = null;
let channelData = null;

const channelSelect = $("channelSelect");
const searchInput = $("searchInput");
const rowsSelect = $("rowsSelect");
const tableBody = $("tableBody");
const cardsBody = $("cardsBody");
const alertBox = $("alertBox");
const metaTitle = $("metaTitle");
const metaSubtitle = $("metaSubtitle");
const summaryBox = $("summaryBox");
const planningCsvLink = $("planningCsvLink");
const openDataJson = $("openDataJson");
const loading = $("loading");
const footerMeta = $("footerMeta");
const rawBase = resolveRawBase();
const gitTreeBase = resolveGitTreeBase();
const channelsInfoUrl = joinUrl(rawBase, CHANNELS_INFO_PATH);

let channelMetaById = new Map();
let channelMetaPromise = null;
let videoImagesCountByVideoId = new Map();
let videoImagesIndexPromise = null;

function pickChannelDisplayName(meta) {
  const yt = meta?.youtube || {};
  const title = String(yt.title || "").trim();
  if (title) return title;
  const name = String(meta?.name || "").trim();
  if (name) return name;
  return "";
}

function channelLabel(channelId) {
  const ch = String(channelId || "").trim();
  const meta = channelMetaById.get(ch);
  const name = pickChannelDisplayName(meta);
  return name ? `${name} (${ch})` : ch;
}

function loadChannelMeta() {
  if (channelMetaPromise) return channelMetaPromise;
  channelMetaPromise = (async () => {
    try {
      const res = await fetch(channelsInfoUrl, { cache: "no-store" });
      if (!res.ok) throw new Error(`channels_info fetch failed: ${res.status} ${res.statusText}`);
      const data = await res.json();
      if (!Array.isArray(data)) return channelMetaById;
      const next = new Map();
      for (const row of data) {
        const id = String(row?.channel_id || "").trim();
        if (!id) continue;
        next.set(id, row);
      }
      channelMetaById = next;
    } catch (err) {
      console.warn("[snapshot] failed to load channels_info.json", err);
    }
    return channelMetaById;
  })();
  return channelMetaPromise;
}

function loadVideoImagesIndex() {
  if (videoImagesIndexPromise) return videoImagesIndexPromise;
  videoImagesIndexPromise = (async () => {
    try {
      const res = await fetch(assetUrl(VIDEO_IMAGES_INDEX_URL), { cache: "no-store" });
      if (!res.ok) throw new Error(`video_images_index fetch failed: ${res.status} ${res.statusText}`);
      const data = await res.json();
      const items = Array.isArray(data?.items) ? data.items : [];
      const next = new Map();
      for (const it of items) {
        const vid = String(it?.video_id || "").trim();
        if (!vid) continue;
        const files = Array.isArray(it?.files) ? it.files : [];
        const count = Number(it?.count) || files.length;
        next.set(vid, count);
      }
      videoImagesCountByVideoId = next;
    } catch (err) {
      console.warn("[snapshot] failed to load video_images_index.json", err);
      videoImagesCountByVideoId = new Map();
    }
    return videoImagesCountByVideoId;
  })();
  return videoImagesIndexPromise;
}

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
    opt.textContent = `${channelLabel(entry.channel)} · scripts ${entry.scripts_count}/${entry.planning_count}`;
    channelSelect.appendChild(opt);
  }
}

function scriptViewerLink(channel, video, view = "") {
  const ch = normChannel(channel);
  const vv = normVideo(video);
  const url = new URL(PAGES_ROOT_URL.toString());
  url.searchParams.set("id", `${ch}-${vv}`);
  const v = String(view || "").trim().toLowerCase();
  if (v && v !== "script") url.searchParams.set("view", v);
  return url.toString();
}

function channelSubtitle(channelId) {
  const ch = String(channelId || "").trim();
  const meta = channelMetaById.get(ch);
  const desc = String(meta?.description || "").trim();
  const handle = String(meta?.youtube?.handle || meta?.youtube_handle || "").trim();
  const parts = [];
  if (desc) parts.push(desc);
  if (handle) parts.push(handle);
  return parts.join(" · ");
}

function renderChannelSummary() {
  if (!channelData?.episodes?.length) {
    summaryBox.hidden = true;
    return;
  }

  const counts = {
    validated: 0,
    in_progress: 0,
    completed: 0,
    pending: 0,
    missing: 0,
    error: 0,
    other: 0,
  };

  for (const ep of channelData.episodes) {
    const script = ep.script || null;
    if (!script) {
      counts.missing += 1;
      continue;
    }
    const s = String(script.status || "").trim().toLowerCase();
    if (!s) {
      counts.other += 1;
      continue;
    }
    if (s === "script_validated") counts.validated += 1;
    else if (s.includes("in_progress")) counts.in_progress += 1;
    else if (s.includes("failed") || s.includes("error") || s.includes("invalid")) counts.error += 1;
    else if (s.includes("completed")) counts.completed += 1;
    else if (s.includes("pending")) counts.pending += 1;
    else counts.other += 1;
  }

  const total = channelData.episodes.length || 0;
  const kpis = [
    { key: "validated", label: "A確定", cls: "ok" },
    { key: "in_progress", label: "作業中", cls: "run" },
    { key: "completed", label: "生成済", cls: "warn" },
    { key: "pending", label: "未着手", cls: "off" },
    { key: "missing", label: "script無し", cls: "bad" },
  ];

  const kpiHtml = kpis
    .map((k) => {
      const n = counts[k.key] || 0;
      const pct = total ? Math.round((n / total) * 100) : 0;
      const title = `${k.label}: ${n}/${total} (${pct}%)`;
      return `<span class="badge badge--${k.cls}" title="${escapeHtml(title)}">${escapeHtml(k.label)} ${n}</span>`;
    })
    .join("");

  const barSegs = [
    { key: "validated", cls: "ok" },
    { key: "in_progress", cls: "run" },
    { key: "completed", cls: "warn" },
    { key: "error", cls: "bad" },
    { key: "pending", cls: "off" },
    { key: "missing", cls: "bad" },
    { key: "other", cls: "off" },
  ]
    .map((seg) => {
      const n = counts[seg.key] || 0;
      const pct = total ? (n / total) * 100 : 0;
      if (pct <= 0) return "";
      const title = `${seg.key}: ${n}/${total}`;
      return `<div class="progressbar__seg progressbar__seg--${seg.cls}" style="width:${pct.toFixed(
        2
      )}%" title="${escapeHtml(title)}"></div>`;
    })
    .filter(Boolean)
    .join("");

  summaryBox.innerHTML = `<div class="summary__kpis">${kpiHtml}</div><div class="progressbar" aria-label="script status summary">${barSegs}</div>`;
  summaryBox.hidden = false;
}

function renderTable() {
  tableBody.innerHTML = "";
  cardsBody.innerHTML = "";
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
    const scriptLabel = scriptOverallLabel(scriptStatus);
    const progressBadge = progress
      ? `<span class="badge badge--${classifyPlanningProgress(progress)} badge--progress" title="${escapeHtml(
          progress
        )}">${escapeHtml(progress)}</span>`
      : `<span class="badge badge--off badge--progress">—</span>`;

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
        const sym = statusSymbol(cls);
        const label = stageBadgeLabel(k);
        const title = st ? `${label}: ${st}` : `${label}: —`;
        return `<span class="badge badge--${cls}" title="${escapeHtml(title)}">${escapeHtml(sym)} ${escapeHtml(label)}</span>`;
      })
      .join("");

    const assembledPath = ep.assembled_path || "";
    const assembledUrl = assembledPath ? joinUrl(rawBase, assembledPath) : "";
    const statusPath = script?.status_path || "";
    const statusUrl = statusPath ? joinUrl(rawBase, statusPath) : "";
    const assetPackHref = episodeAssetPackHref(ep.channel, ep.video);

    const idHtml = `<a class="link mono" href="${escapeHtml(scriptViewerLink(ep.channel, ep.video))}">${escapeHtml(ep.video_id)}</a>`;
    const titleHtml = `<div class="cell-title"><span class="cell-title__title">${escapeHtml(ep.title || "—")}</span><span class="cell-title__sub">${escapeHtml(
      planningUpdated
    )}</span></div>`;

    const links = [
      `<a class="btn btn--ghost" href="${escapeHtml(scriptViewerLink(ep.channel, ep.video, "script"))}">台本</a>`,
      `<a class="btn btn--ghost" href="${escapeHtml(scriptViewerLink(ep.channel, ep.video, "thumb"))}">サムネ</a>`,
      `<a class="btn btn--ghost" href="${escapeHtml(scriptViewerLink(ep.channel, ep.video, "images"))}">画像</a>`,
      assetPackHref ? `<a class="btn btn--ghost" href="${escapeHtml(assetPackHref)}" target="_blank" rel="noreferrer">素材束</a>` : "",
      assembledUrl ? `<a class="btn btn--ghost" href="${escapeHtml(assembledUrl)}" target="_blank" rel="noreferrer">raw</a>` : "",
      statusUrl ? `<a class="btn btn--ghost" href="${escapeHtml(statusUrl)}" target="_blank" rel="noreferrer">status</a>` : "",
    ]
      .filter(Boolean)
      .join("");

    tr.innerHTML = `
      <td class="mono">${idHtml}</td>
      <td>${titleHtml}</td>
      <td>${progressBadge}</td>
      <td><span class="badge badge--${classifyScriptOverallStatus(scriptStatus)}" title="${escapeHtml(
        scriptStatus || "—"
      )}">${escapeHtml(scriptLabel)}</span></td>
      <td><div class="badges">${stageBadges}</div></td>
      <td><div class="links">${links}</div></td>
    `;
    tableBody.appendChild(tr);

    const card = document.createElement("article");
    card.className = "ep-card";
    const thumbSrc = assetUrl(`media/thumbs/${normChannel(ep.channel)}/${normVideo(ep.video)}.jpg`);
    const imgCount = Number(videoImagesCountByVideoId.get(String(ep.video_id || "").trim()) || 0);
    const imgBadge = imgCount
      ? `<span class="badge badge--ok" title="${escapeHtml(String(imgCount))} images">画像 ${escapeHtml(String(imgCount))}</span>`
      : `<span class="badge badge--off">画像 —</span>`;
    card.innerHTML = `
      <div class="ep-card__head">
        <div>
          <div class="ep-card__id">${idHtml}</div>
          <div class="ep-card__title">${escapeHtml(ep.title || "—")}</div>
          <div class="ep-card__sub">${escapeHtml(planningUpdated || "")}</div>
        </div>
        <div class="badges">
          ${progressBadge}
          <span class="badge badge--${classifyScriptOverallStatus(scriptStatus)}" title="${escapeHtml(
            scriptStatus || "—"
          )}">${escapeHtml(scriptLabel)}</span>
          ${imgBadge}
        </div>
      </div>
      <a class="ep-card__thumb" href="${escapeHtml(scriptViewerLink(ep.channel, ep.video, "thumb"))}">
        <img loading="lazy" src="${escapeHtml(thumbSrc)}" alt="${escapeHtml(ep.video_id || "")} thumbnail" />
      </a>
      <div class="badges">${stageBadges}</div>
      <div class="ep-card__links">${links}</div>
    `;
    cardsBody.appendChild(card);
  }

  const extra = filtered.length > sliced.length ? ` (showing ${sliced.length}/${filtered.length})` : ` (${filtered.length})`;
  footerMeta.textContent = `channel: ${channelLabel(selectedChannel)} · episodes: ${channelData.episodes.length}${extra} · generated: ${
    channelData.generated_at || "—"
  }`;
}

async function loadChannel(channel) {
  const entry = channels.find((c) => c.channel === channel);
  if (!entry) return;
  selectedChannel = channel;
  setAlert("");
  setLoading(true);
  try {
    const url = assetUrl(entry.data_path);
    openDataJson.href = url;

    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`channel fetch failed: ${res.status} ${res.statusText}`);
    channelData = await res.json();

    const planningCsv = channelData?.planning_csv || "";
    planningCsvLink.textContent = planningCsv || "—";
    planningCsvLink.href = planningCsv ? joinUrl(rawBase, planningCsv) : "#";

    metaTitle.textContent = `${channelLabel(channel)} · planning ${channelData?.planning_count || 0} · scripts ${channelData?.scripts_count || 0}`;
    const subtitle = channelSubtitle(channel);
    metaSubtitle.textContent = subtitle || "—";
    metaSubtitle.hidden = !subtitle;
    renderChannelSummary();
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
    metaSubtitle.textContent = "—";
    metaSubtitle.hidden = true;
    summaryBox.hidden = true;
    setAlert(`スナップショットの読み込みに失敗しました。\n\n${String(err)}\n\n※ GitHub Pages 側では workflow が data を生成します。ローカルでは:\npython3 scripts/ops/pages_snapshot_export.py --write`);
  } finally {
    setLoading(false);
  }
}

async function reloadIndex() {
  setLoading(true);
  setAlert("");
  try {
    const [res] = await Promise.all([
      fetch(assetUrl(INDEX_URL), { cache: "no-store" }),
      loadChannelMeta(),
      loadVideoImagesIndex(),
    ]);
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
