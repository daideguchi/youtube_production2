/* eslint-disable no-console */

const INDEX_URL = "./data/index.json";
const CHANNELS_INFO_PATH = "packages/script_pipeline/channels/channels_info.json";
const THUMB_PROJECTS_PATH = "workspaces/thumbnails/projects.json";
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

  // local preview: serve repo root via `python3 -m http.server` and open `/docs/`
  return `${window.location.origin}/`;
}

function joinUrl(base, path) {
  const safeBase = base.replace(/\/+$/, "") + "/";
  const safePath = String(path || "").replace(/^\/+/, "");
  return safeBase + safePath;
}

const rawBase = resolveRawBase();
const channelsInfoUrl = joinUrl(rawBase, CHANNELS_INFO_PATH);
const thumbProjectsUrl = joinUrl(rawBase, THUMB_PROJECTS_PATH);

let channelMetaById = new Map();
let channelMetaPromise = null;
let thumbProjectByVideoId = new Map();
let thumbProjectPromise = null;

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
      console.warn("[script_viewer] failed to load channels_info.json", err);
    }
    return channelMetaById;
  })();
  return channelMetaPromise;
}

function normalizeVideoId(channel, video) {
  const ch = String(channel || "").trim();
  const vv = String(video || "").trim();
  return ch && vv ? `${ch}-${vv}` : "";
}

function loadThumbProjects() {
  if (thumbProjectPromise) return thumbProjectPromise;
  thumbProjectPromise = (async () => {
    try {
      const res = await fetch(thumbProjectsUrl, { cache: "no-store" });
      if (!res.ok) throw new Error(`projects.json fetch failed: ${res.status} ${res.statusText}`);
      const data = await res.json();
      const projects = Array.isArray(data?.projects) ? data.projects : [];
      const next = new Map();
      for (const p of projects) {
        const videoId = normalizeVideoId(p?.channel, p?.video);
        if (!videoId) continue;
        const variantsRaw = Array.isArray(p?.variants) ? p.variants : [];
        const variants = variantsRaw
          .map((v) => ({
            id: String(v?.id || "").trim(),
            label: String(v?.label || "").trim(),
            status: String(v?.status || "").trim(),
            image_url: String(v?.image_url || "").trim(),
            image_path: String(v?.image_path || "").trim(),
          }))
          .filter((v) => v.id || v.label || v.image_url || v.image_path);

        next.set(videoId, {
          channel: String(p?.channel || "").trim(),
          video: String(p?.video || "").trim(),
          title: String(p?.title || "").trim(),
          status: String(p?.status || "").trim(),
          selected_variant_id: String(p?.selected_variant_id || "").trim(),
          variants,
        });
      }
      thumbProjectByVideoId = next;
    } catch (err) {
      console.warn("[script_viewer] failed to load thumbnails projects.json", err);
      thumbProjectByVideoId = new Map();
    }
    return thumbProjectByVideoId;
  })();
  return thumbProjectPromise;
}

let indexData = null;
let items = [];
let grouped = new Map();
let selected = null;
let loadedText = "";
let loadedNoSepText = "";
let audioPrepScriptText = "";
let audioPrepMetaText = "";

const channelSelect = $("channelSelect");
const videoSelect = $("videoSelect");
const searchInput = $("searchInput");
const searchResults = $("searchResults");
const metaTitle = $("metaTitle");
const metaPath = $("metaPath");
const openRaw = $("openRaw");
const contentPre = $("contentPre");
const copyStatus = $("copyStatus");
const copyNoSepChunks = $("copyNoSepChunks");
const loading = $("loading");
const footerMeta = $("footerMeta");
const audioPrepScriptPre = $("audioPrepScriptPre");
const audioPrepMetaPre = $("audioPrepMetaPre");
const openAudioPrepScript = $("openAudioPrepScript");
const openAudioPrepMeta = $("openAudioPrepMeta");
const copyAudioPrepScript = $("copyAudioPrepScript");
const copyAudioPrepMeta = $("copyAudioPrepMeta");
const thumbBody = $("thumbBody");

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

function episodeBasePath(it) {
  return `workspaces/scripts/${it.channel}/${it.video}`;
}

async function fetchTextOptional(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`fetch failed: ${res.status} ${res.statusText}`);
  return await res.text();
}

async function loadAudioPrep(it) {
  const currentId = it?.video_id || "";
  audioPrepScriptText = "";
  audioPrepMetaText = "";
  copyAudioPrepScript.disabled = true;
  copyAudioPrepMeta.disabled = true;
  audioPrepScriptPre.textContent = "読み込み中…";
  audioPrepMetaPre.textContent = "読み込み中…";

  const base = episodeBasePath(it);
  const scriptPath = `${base}/audio_prep/script_sanitized.txt`;
  const metaPath = `${base}/audio_prep/inference_metadata.txt`;
  const scriptUrl = joinUrl(rawBase, scriptPath);
  const metaUrl = joinUrl(rawBase, metaPath);
  openAudioPrepScript.href = scriptUrl;
  openAudioPrepMeta.href = metaUrl;

  try {
    const [scriptText, metaText] = await Promise.all([fetchTextOptional(scriptUrl), fetchTextOptional(metaUrl)]);
    if (selected?.video_id !== currentId) return;

    if (scriptText == null) {
      audioPrepScriptPre.textContent = "未生成（audio_prep/script_sanitized.txt が見つかりません）";
    } else {
      audioPrepScriptText = normalizeNewlines(scriptText);
      audioPrepScriptPre.textContent = audioPrepScriptText;
      copyAudioPrepScript.disabled = false;
    }

    if (metaText == null) {
      audioPrepMetaPre.textContent = "未生成（audio_prep/inference_metadata.txt が見つかりません）";
    } else {
      audioPrepMetaText = normalizeNewlines(metaText);
      audioPrepMetaPre.textContent = audioPrepMetaText;
      copyAudioPrepMeta.disabled = false;
    }
  } catch (err) {
    if (selected?.video_id !== currentId) return;
    const msg = `読み込みに失敗しました。\n${String(err)}`;
    audioPrepScriptPre.textContent = msg;
    audioPrepMetaPre.textContent = msg;
  }
}

function renderThumbProject(it, proj) {
  thumbBody.innerHTML = "";

  const head = document.createElement("div");
  head.className = "thumb-head";
  const selectedId = String(proj?.selected_variant_id || "").trim();
  const status = String(proj?.status || "").trim();
  head.textContent = `status=${status || "-"} / selected=${selectedId || "-"}`;
  thumbBody.appendChild(head);

  const variants = Array.isArray(proj?.variants) ? proj.variants : [];
  if (!variants.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "variants が空です（projects.json）";
    thumbBody.appendChild(empty);
    return;
  }

  const grid = document.createElement("div");
  grid.className = "thumb-grid";

  for (const v of variants) {
    const card = document.createElement("div");
    card.className = "thumb-card";
    if (selectedId && String(v?.id || "").trim() === selectedId) {
      card.classList.add("thumb-card--selected");
    }

    const title = document.createElement("div");
    title.className = "thumb-card__title";
    const label = String(v?.label || v?.id || "").trim();
    const st = String(v?.status || "").trim();
    title.textContent = `${label || "(no label)"}${st ? " · " + st : ""}`;
    card.appendChild(title);

    const url = String(v?.image_url || "").trim();
    const imagePath = String(v?.image_path || "").trim();
    const pathUrl = imagePath ? joinUrl(rawBase, `workspaces/thumbnails/assets/${imagePath}`) : "";
    const previewUrl = url && /^https?:\\/\\//.test(url) ? url : pathUrl;

    if (previewUrl) {
      const a = document.createElement("a");
      a.href = previewUrl;
      a.target = "_blank";
      a.rel = "noreferrer";
      a.className = "thumb-card__imglink";
      const img = document.createElement("img");
      img.src = previewUrl;
      img.loading = "lazy";
      img.alt = `${it?.video_id || ""} thumbnail`;
      img.onerror = () => {
        try {
          img.remove();
          const code = document.createElement("code");
          code.className = "thumb-card__path";
          code.textContent = imagePath || url || "(no image)";
          a.appendChild(code);
        } catch (_err) {
          // ignore
        }
      };
      a.appendChild(img);
      card.appendChild(a);
    } else if (url || imagePath) {
      const code = document.createElement("code");
      code.className = "thumb-card__path";
      code.textContent = imagePath || url;
      card.appendChild(code);
    } else {
      const none = document.createElement("div");
      none.className = "muted";
      none.textContent = "image_url なし";
      card.appendChild(none);
    }

    grid.appendChild(card);
  }

  thumbBody.appendChild(grid);
}

async function loadThumb(it) {
  const currentId = it?.video_id || "";
  thumbBody.textContent = "読み込み中…";
  try {
    const map = await loadThumbProjects();
    if (selected?.video_id !== currentId) return;
    const proj = map.get(currentId);
    if (!proj) {
      thumbBody.textContent = "projects.json に未登録（thumb未作成 or 未同期）";
      return;
    }
    renderThumbProject(it, proj);
  } catch (err) {
    if (selected?.video_id !== currentId) return;
    thumbBody.textContent = `読み込みに失敗しました: ${String(err)}`;
  }
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
    opt.textContent = channelLabel(ch);
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

function renderNoSepChunkButtons() {
  const cleaned = loadedNoSepText;
  copyNoSepChunks.innerHTML = "";

  const total = cleaned.length;
  if (!total) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn--ghost btn--chunk";
    btn.textContent = "1";
    btn.disabled = true;
    copyNoSepChunks.appendChild(btn);
    return;
  }

  const totalChunks = Math.max(1, Math.ceil(total / CHUNK_SIZE));
  for (let idx = 0; idx < totalChunks; idx += 1) {
    const start = idx * CHUNK_SIZE;
    const end = Math.min(start + CHUNK_SIZE, total);
    const label = `${idx + 1}/${totalChunks}`;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn--ghost btn--chunk";
    btn.textContent = label;
    btn.title = `${label} (${start + 1}-${end})`;
    btn.addEventListener("click", async () => {
      const chunk = cleaned.slice(start, end);
      if (!chunk.trim()) {
        setCopyStatus("台本が空です", true);
        return;
      }
      const ok = await copyText(chunk);
      setCopyStatus(ok ? `コピーしました (${label} ${start + 1}-${end})` : "コピーに失敗しました", !ok);
    });
    copyNoSepChunks.appendChild(btn);
  }
}

async function loadScript(it) {
  selected = it;
  loadedText = "";
  loadedNoSepText = "";
  renderNoSepChunkButtons();
  void loadAudioPrep(it);
  void loadThumb(it);

  const chLabel = channelLabel(it.channel);
  metaTitle.textContent = it.title ? `${chLabel} · ${it.video} · ${it.title}` : `${chLabel} · ${it.video}`;
  metaPath.textContent = it.assembled_path;

  const url = joinUrl(rawBase, it.assembled_path);
  openRaw.href = url;

  setLoading(true);
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`fetch failed: ${res.status} ${res.statusText}`);
    const text = await res.text();
    loadedText = normalizeNewlines(text);
    loadedNoSepText = stripPauseSeparators(loadedText);
    renderNoSepChunkButtons();
    contentPre.textContent = loadedText;
    footerMeta.textContent = `index: ${indexData?.count || items.length} items · loaded: ${it.video_id} · chars: ${loadedText.length.toLocaleString(
      "ja-JP"
    )}`;
  } catch (err) {
    console.error(err);
    contentPre.textContent = `読み込みに失敗しました。\n\n${String(err)}`;
    footerMeta.textContent = "—";
    loadedNoSepText = "";
    renderNoSepChunkButtons();
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
    const [res] = await Promise.all([fetch(INDEX_URL, { cache: "no-store" }), loadChannelMeta()]);
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
        const ch = String(it.channel || "").toLowerCase();
        const chLabel = String(channelLabel(it.channel) || "").toLowerCase();
        const id = String(it.video_id || "").toLowerCase();
        const title = String(it.title || "").toLowerCase();
        const video = String(it.video || "").toLowerCase();
        return id.includes(q) || title.includes(q) || video === q || ch === q || ch.includes(q) || chLabel.includes(q);
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

  copyAudioPrepScript.addEventListener("click", async () => {
    if (!audioPrepScriptText.trim()) {
      setCopyStatus("audio_prep が空です", true);
      return;
    }
    const ok = await copyText(audioPrepScriptText);
    setCopyStatus(ok ? "audio_prep（script_sanitized）をコピーしました" : "コピーに失敗しました", !ok);
  });

  copyAudioPrepMeta.addEventListener("click", async () => {
    if (!audioPrepMetaText.trim()) {
      setCopyStatus("audio_prep metadata が空です", true);
      return;
    }
    const ok = await copyText(audioPrepMetaText);
    setCopyStatus(ok ? "audio_prep（metadata）をコピーしました" : "コピーに失敗しました", !ok);
  });
}

setupEvents();
void reloadIndex();
