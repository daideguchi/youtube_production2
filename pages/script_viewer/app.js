/* eslint-disable no-console */

const INDEX_URL = "./data/index.json";
const CHUNK_SIZE = 10_000;

function $(id) {
  const el = document.getElementById(id);
  if (!el) {
    throw new Error(`missing element: ${id}`);
  }
  return el;
}

function normalizeNewlines(text) {
  return String(text || "").replace(/\r\n?/g, "\n");
}

function stripPauseSeparators(raw) {
  const normalized = normalizeNewlines(raw);
  const filtered = normalized
    .split("\n")
    .filter((line) => line.trim() !== "---")
    .join("\n");
  return filtered.replace(/\n{3,}/g, "\n\n").trim();
}

function planChunkCopy(text, chunkIndex, chunkSize = CHUNK_SIZE) {
  const total = text.length;
  if (!total) return null;
  const totalChunks = Math.max(1, Math.ceil(total / chunkSize));
  const safeIndex = chunkIndex * chunkSize >= total ? 0 : Math.max(0, chunkIndex);
  const start = safeIndex * chunkSize;
  const end = Math.min(start + chunkSize, total);
  const nextIndex = end >= total ? 0 : safeIndex + 1;
  return { chunk: text.slice(start, end), start, end, total, totalChunks, currentChunk: safeIndex + 1, nextIndex };
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_err) {
      // fall through
    }
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(textarea);
  return ok;
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

  // local preview: serve repo root via `python3 -m http.server` and open `/pages/script_viewer/`
  return `${window.location.origin}/`;
}

function joinUrl(base, path) {
  const safeBase = base.replace(/\/+$/, "") + "/";
  const safePath = String(path || "").replace(/^\/+/, "");
  return safeBase + safePath;
}

let indexData = null;
let items = [];
let grouped = new Map();
let selected = null;
let loadedText = "";
let chunkIndex = 0;

const channelSelect = $("channelSelect");
const videoSelect = $("videoSelect");
const searchInput = $("searchInput");
const searchResults = $("searchResults");
const metaTitle = $("metaTitle");
const metaPath = $("metaPath");
const openRaw = $("openRaw");
const contentPre = $("contentPre");
const copyStatus = $("copyStatus");
const loading = $("loading");
const footerMeta = $("footerMeta");

function setLoading(on) {
  loading.hidden = !on;
}

function setCopyStatus(text, isError = false) {
  copyStatus.textContent = text || "";
  copyStatus.style.color = isError ? "var(--danger)" : "var(--muted)";
  if (!text) return;
  window.setTimeout(() => {
    if (copyStatus.textContent === text) {
      copyStatus.textContent = "";
    }
  }, 2500);
}

function buildGrouped(itemsList) {
  const map = new Map();
  for (const it of itemsList) {
    if (!map.has(it.channel)) map.set(it.channel, []);
    map.get(it.channel).push(it);
  }
  for (const [ch, arr] of map.entries()) {
    arr.sort((a, b) => Number(a.video) - Number(b.video));
    map.set(ch, arr);
  }
  return map;
}

function renderChannels() {
  const channels = Array.from(grouped.keys()).sort((a, b) => {
    const na = Number(String(a).replace(/^CH/, "")) || 999999;
    const nb = Number(String(b).replace(/^CH/, "")) || 999999;
    if (na !== nb) return na - nb;
    return String(a).localeCompare(String(b));
  });
  channelSelect.innerHTML = "";
  for (const ch of channels) {
    const opt = document.createElement("option");
    opt.value = ch;
    opt.textContent = ch;
    channelSelect.appendChild(opt);
  }
}

function renderVideos(channel) {
  const list = grouped.get(channel) || [];
  videoSelect.innerHTML = "";
  for (const it of list) {
    const opt = document.createElement("option");
    opt.value = it.video;
    opt.textContent = `${it.video} ${it.title ? "· " + it.title : ""}`.trim();
    videoSelect.appendChild(opt);
  }
}

function findItem(channel, video) {
  const list = grouped.get(channel) || [];
  return list.find((it) => it.video === video) || null;
}

function hideSearchResults() {
  searchResults.hidden = true;
  searchResults.innerHTML = "";
}

function showSearchResults(results) {
  searchResults.hidden = false;
  searchResults.innerHTML = "";
  for (const it of results) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "search-results__item";
    btn.innerHTML = `<span class="search-results__id">${it.video_id}</span><span class="search-results__title">${escapeHtml(
      it.title || ""
    )}</span>`;
    btn.addEventListener("click", () => {
      hideSearchResults();
      selectItem(it.channel, it.video);
      searchInput.value = "";
    });
    searchResults.appendChild(btn);
  }
}

function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function loadScript(it) {
  selected = it;
  loadedText = "";
  chunkIndex = 0;

  metaTitle.textContent = it.title ? `${it.video_id} · ${it.title}` : it.video_id;
  metaPath.textContent = it.assembled_path;

  const rawBase = resolveRawBase();
  const url = joinUrl(rawBase, it.assembled_path);
  openRaw.href = url;

  setLoading(true);
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`fetch failed: ${res.status} ${res.statusText}`);
    const text = await res.text();
    loadedText = normalizeNewlines(text);
    contentPre.textContent = loadedText;
    footerMeta.textContent = `index: ${indexData?.count || items.length} items · loaded: ${it.video_id} · chars: ${loadedText.length.toLocaleString(
      "ja-JP"
    )}`;
  } catch (err) {
    console.error(err);
    contentPre.textContent = `読み込みに失敗しました。\n\n${String(err)}`;
    footerMeta.textContent = "—";
  } finally {
    setLoading(false);
  }
}

function selectItem(channel, video) {
  channelSelect.value = channel;
  renderVideos(channel);
  videoSelect.value = video;
  const it = findItem(channel, video);
  if (it) void loadScript(it);
}

async function reloadIndex() {
  setLoading(true);
  try {
    const res = await fetch(INDEX_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`index fetch failed: ${res.status} ${res.statusText}`);
    indexData = await res.json();
    items = Array.isArray(indexData?.items) ? indexData.items : [];
    grouped = buildGrouped(items);
    renderChannels();

    // Default selection: first item, or keep current if possible
    const firstChannel = channelSelect.value || Array.from(grouped.keys())[0];
    if (!firstChannel) {
      metaTitle.textContent = "index.json が空です";
      metaPath.textContent = "—";
      contentPre.textContent = "";
      footerMeta.textContent = `generated: ${indexData?.generated_at || "—"}`;
      hideSearchResults();
      return;
    }
    renderVideos(firstChannel);
    const firstVideo = videoSelect.value || (grouped.get(firstChannel)?.[0]?.video ?? null);
    if (firstVideo) {
      selectItem(firstChannel, firstVideo);
    }
    footerMeta.textContent = `generated: ${indexData?.generated_at || "—"} · items: ${items.length.toLocaleString("ja-JP")}`;
    hideSearchResults();
  } catch (err) {
    console.error(err);
    metaTitle.textContent = "index.json の読み込みに失敗しました";
    metaPath.textContent = "—";
    contentPre.textContent = String(err);
    footerMeta.textContent = "—";
  } finally {
    setLoading(false);
  }
}

function setupEvents() {
  $("reloadIndex").addEventListener("click", () => void reloadIndex());

  channelSelect.addEventListener("change", () => {
    const ch = channelSelect.value;
    renderVideos(ch);
    const video = videoSelect.value || (grouped.get(ch)?.[0]?.video ?? null);
    if (video) selectItem(ch, video);
  });

  videoSelect.addEventListener("change", () => {
    const ch = channelSelect.value;
    const video = videoSelect.value;
    const it = findItem(ch, video);
    if (it) void loadScript(it);
  });

  searchInput.addEventListener("input", () => {
    const q = String(searchInput.value || "").trim().toLowerCase();
    if (!q) {
      hideSearchResults();
      return;
    }
    const results = items
      .filter((it) => {
        const id = String(it.video_id || "").toLowerCase();
        const title = String(it.title || "").toLowerCase();
        const video = String(it.video || "").toLowerCase();
        return id.includes(q) || title.includes(q) || video === q;
      })
      .slice(0, 20);
    if (!results.length) {
      hideSearchResults();
      return;
    }
    showSearchResults(results);
  });

  $("copyPath").addEventListener("click", async () => {
    const text = selected?.assembled_path || "";
    if (!text) return;
    const ok = await copyText(text);
    setCopyStatus(ok ? "パスをコピーしました" : "コピーに失敗しました", !ok);
  });

  $("copyRaw").addEventListener("click", async () => {
    if (!loadedText.trim()) {
      setCopyStatus("台本が空です", true);
      return;
    }
    const ok = await copyText(loadedText);
    setCopyStatus(ok ? "コピーしました" : "コピーに失敗しました", !ok);
  });

  $("copyNoSep").addEventListener("click", async () => {
    const cleaned = stripPauseSeparators(loadedText);
    const plan = planChunkCopy(cleaned, chunkIndex);
    if (!plan?.chunk) {
      setCopyStatus("台本が空です", true);
      return;
    }
    const ok = await copyText(plan.chunk);
    if (ok) {
      chunkIndex = plan.nextIndex;
      setCopyStatus(`コピーしました (${plan.currentChunk}/${plan.totalChunks} ${plan.start + 1}-${plan.end})`);
    } else {
      setCopyStatus("コピーに失敗しました", true);
    }
  });
}

setupEvents();
void reloadIndex();

