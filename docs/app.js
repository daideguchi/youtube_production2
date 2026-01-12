/* eslint-disable no-console */

const INDEX_URL = "./data/index.json";
const CHANNELS_INFO_PATH = "packages/script_pipeline/channels/channels_info.json";
const THUMB_PROJECTS_PATH = "workspaces/thumbnails/projects.json";
const THUMBS_INDEX_URL = "./data/thumbs_index.json";
const VIDEO_IMAGES_INDEX_URL = "./data/video_images_index.json";
const CHUNK_SIZE = 10_000;
const UI_STATE_KEY = "ytm_script_viewer_state_v1";
const SITE_ASSET_VERSION = "20260112_16";

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
  const safeBase = base.replace(/\/+$/, "") + "/";
  const safePath = String(path || "").replace(/^\/+/, "");
  return safeBase + safePath;
}

function siteUrl(relPath) {
  const s = String(relPath || "")
    .trim()
    .replace(/^\/+/, "")
    .replace(/^\.\/+/, "");
  if (!s) return "";
  const base = `./${s}`;
  return SITE_ASSET_VERSION ? `${base}?v=${encodeURIComponent(SITE_ASSET_VERSION)}` : base;
}

function docsRawUrl(relPath) {
  const s = String(relPath || "")
    .trim()
    .replace(/^\/+/, "")
    .replace(/^\.\/+/, "");
  if (!s) return "";
  return joinUrl(rawBase, `docs/${s}`);
}

function normalizeView(raw) {
  const v = String(raw || "").trim().toLowerCase();
  if (v === "audio" || v === "thumb" || v === "images" || v === "script") return v;
  return "script";
}

function normalizeChannelParam(raw) {
  const s = String(raw || "").trim().toUpperCase();
  const m = s.match(/^CH(\d{1,3})$/);
  if (m) {
    const n = Number(m[1]);
    if (Number.isFinite(n)) return `CH${String(n).padStart(2, "0")}`;
  }
  return s;
}

function normalizeVideoParam(raw) {
  const s = String(raw || "").trim();
  if (/^\d{3}$/.test(s)) return s;
  const n = Number(s);
  if (Number.isFinite(n)) return String(n).padStart(3, "0");
  return s;
}

function parseVideoIdParam(raw) {
  const s = String(raw || "").trim().toUpperCase();
  const m = s.match(/^(CH\d{2})-(\d{3})$/);
  if (m) return { channel: m[1], video: m[2] };
  const m2 = s.match(/^CH(\d{1,3})-(\d{1,4})$/);
  if (m2) return { channel: normalizeChannelParam(`CH${m2[1]}`), video: normalizeVideoParam(m2[2]) };
  return null;
}

function urlHasExplicitVideoSelection() {
  try {
    const params = new URLSearchParams(window.location.search);
    const id = params.get("id");
    if (parseVideoIdParam(id)) return true;
    return Boolean(params.get("ch") && params.get("v"));
  } catch (_err) {
    return false;
  }
}

function readUiStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const view = normalizeView(params.get("view"));

  const id = params.get("id");
  const parsed = parseVideoIdParam(id);
  if (parsed) return { channel: parsed.channel, video: parsed.video, view };

  const ch = normalizeChannelParam(params.get("ch"));
  const v = normalizeVideoParam(params.get("v"));
  if (ch && v) return { channel: ch, video: v, view };

  return { channel: "", video: "", view };
}

function readUiStateFromStorage() {
  try {
    const raw = window.localStorage.getItem(UI_STATE_KEY);
    if (!raw) return { channel: "", video: "", view: "script" };
    const obj = JSON.parse(raw);
    const channel = normalizeChannelParam(obj?.channel);
    const video = normalizeVideoParam(obj?.video);
    const view = normalizeView(obj?.view);
    return { channel, video, view };
  } catch (_err) {
    return { channel: "", video: "", view: "script" };
  }
}

function getInitialUiState() {
  const fromUrl = readUiStateFromUrl();
  const fromStorage = readUiStateFromStorage();
  return {
    channel: fromUrl.channel || fromStorage.channel || "",
    video: fromUrl.video || fromStorage.video || "",
    view: fromUrl.view || fromStorage.view || "script",
  };
}

const rawBase = resolveRawBase();
const gitTreeBase = resolveGitTreeBase();
const channelsInfoUrl = joinUrl(rawBase, CHANNELS_INFO_PATH);
const thumbProjectsUrl = joinUrl(rawBase, THUMB_PROJECTS_PATH);

let channelMetaById = new Map();
let channelMetaPromise = null;
let thumbProjectByVideoId = new Map();
let thumbProjectPromise = null;
let thumbIndexByVideoId = new Map();
let thumbIndexPromise = null;
let videoImagesIndexByVideoId = new Map();
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

function channelAvatarUrl(channelId) {
  const ch = String(channelId || "").trim();
  const meta = channelMetaById.get(ch) || {};
  const branding = meta?.branding || {};
  const url = String(branding?.avatar_url || "").trim();
  if (url && /^https?:\/\//.test(url)) return url;
  return "";
}

function channelShortName(channelId) {
  const ch = String(channelId || "").trim();
  const label = channelLabel(ch);
  const m = label.match(/^(.*)\\s+\\(CH\\d+\\)$/);
  const out = (m ? m[1] : label).trim();
  return out || ch;
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

function loadThumbIndex() {
  if (thumbIndexPromise) return thumbIndexPromise;
  thumbIndexPromise = (async () => {
    try {
      const res = await fetch(siteUrl(THUMBS_INDEX_URL), { cache: "no-store" });
      if (res.status === 404) {
        thumbIndexByVideoId = new Map();
        return thumbIndexByVideoId;
      }
      if (!res.ok) throw new Error(`thumbs_index fetch failed: ${res.status} ${res.statusText}`);
      const data = await res.json();
      const items = Array.isArray(data?.items) ? data.items : [];
      const next = new Map();
      for (const it of items) {
        const vid = String(it?.video_id || "").trim();
        if (!vid) continue;
        next.set(vid, {
          preview_rel: String(it?.preview_rel || "").trim(),
          preview_exists: Boolean(it?.preview_exists),
        });
      }
      thumbIndexByVideoId = next;
    } catch (err) {
      console.warn("[script_viewer] failed to load thumbs_index.json", err);
      thumbIndexByVideoId = new Map();
    }
    return thumbIndexByVideoId;
  })();
  return thumbIndexPromise;
}

function loadVideoImagesIndex() {
  if (videoImagesIndexPromise) return videoImagesIndexPromise;
  videoImagesIndexPromise = (async () => {
    try {
      const res = await fetch(siteUrl(VIDEO_IMAGES_INDEX_URL), { cache: "no-store" });
      if (res.status === 404) {
        videoImagesIndexByVideoId = new Map();
        return videoImagesIndexByVideoId;
      }
      if (!res.ok) throw new Error(`video_images_index fetch failed: ${res.status} ${res.statusText}`);
      const data = await res.json();
      const items = Array.isArray(data?.items) ? data.items : [];
      const next = new Map();
      for (const it of items) {
        const vid = String(it?.video_id || "").trim();
        if (!vid) continue;
        const filesRaw = Array.isArray(it?.files) ? it.files : [];
        const files = filesRaw
          .map((f) => ({
            file: String(f?.file || "").trim(),
            rel: String(f?.rel || "").trim(),
            summary: String(f?.summary || "").trim(),
          }))
          .filter((f) => f.file && f.rel);
        next.set(vid, {
          video_id: vid,
          channel: String(it?.channel || "").trim(),
          video: String(it?.video || "").trim(),
          run_id: String(it?.run_id || "").trim(),
          count: Number(it?.count) || files.length,
          files,
        });
      }
      videoImagesIndexByVideoId = next;
    } catch (err) {
      console.warn("[script_viewer] failed to load video_images_index.json", err);
      videoImagesIndexByVideoId = new Map();
    }
    return videoImagesIndexByVideoId;
  })();
  return videoImagesIndexPromise;
}

let indexData = null;
let items = [];
let grouped = new Map();
let channelsSorted = [];
let selected = null;
let loadedText = "";
let loadedNoSepText = "";
let audioPrepScriptText = "";
let audioPrepMetaText = "";
const initialUiState = getInitialUiState();
const initialUrlHasSelection = urlHasExplicitVideoSelection();
let initialChannelWanted = initialUiState.channel || "";
let initialVideoWanted = initialUiState.video || "";
let currentView = initialUiState.view || "script";
let scriptState = "idle"; // idle | loading | ok | error
let audioState = "idle"; // idle | loading | ok | partial | missing | error
let thumbState = "idle"; // idle | loading | ok | partial | missing | error
let videoImagesState = "idle"; // idle | loading | ok | partial | missing | error
let videoImagesCount = 0;

const channelSelect = $("channelSelect");
const videoSelect = $("videoSelect");
const channelChips = $("channelChips");
const videoList = $("videoList");
const searchInput = $("searchInput");
const searchResults = $("searchResults");
const browseDetails = $("browseDetails");
const browseSummary = $("browseSummary");
const reloadIndexButton = $("reloadIndex");
const metaTitle = $("metaTitle");
const metaPath = $("metaPath");
const heroMedia = $("heroMedia");
const heroThumbButton = $("heroThumbButton");
const heroThumbImg = $("heroThumbImg");
const heroThumbFallback = $("heroThumbFallback");
const heroToThumb = $("heroToThumb");
const heroToImages = $("heroToImages");
const openRaw = $("openRaw");
const openAssetPack = $("openAssetPack");
const openBrowse = $("openBrowse");
const openSnapshot = $("openSnapshot");
const openFixedLogic = $("openFixedLogic");
const openContactBox = $("openContactBox");
const contentPre = $("contentPre");
const copyStatus = $("copyStatus");
const copyNoSepChunks = $("copyNoSepChunks");
const loading = $("loading");
const footerMeta = $("footerMeta");
const appRoot = $("appRoot");
const viewTabs = $("viewTabs");
const tabScript = $("tabScript");
const tabAudio = $("tabAudio");
const tabThumb = $("tabThumb");
const tabImages = $("tabImages");
const badgeScript = $("badgeScript");
const badgeAudio = $("badgeAudio");
const badgeThumb = $("badgeThumb");
const badgeImages = $("badgeImages");
const audioPrepDetails = $("audioPrepDetails");
const thumbDetails = $("thumbDetails");
const videoImagesDetails = $("videoImagesDetails");
const audioPrepScriptPre = $("audioPrepScriptPre");
const audioPrepMetaPre = $("audioPrepMetaPre");
const openAudioPrepScript = $("openAudioPrepScript");
const openAudioPrepMeta = $("openAudioPrepMeta");
const copyAudioPrepScript = $("copyAudioPrepScript");
const copyAudioPrepMeta = $("copyAudioPrepMeta");
const thumbBody = $("thumbBody");
const videoImagesBody = $("videoImagesBody");

function setLoading(on) {
  loading.hidden = !on;
}

function updateHeroMedia(it) {
  const videoId = String(it?.video_id || "").trim();
  if (!videoId) {
    heroMedia.hidden = true;
    return;
  }

  heroMedia.hidden = false;
  heroThumbImg.hidden = true;
  heroThumbFallback.hidden = false;
  heroThumbFallback.textContent = "サムネ読み込み中…";

  const idx = thumbIndexByVideoId.get(videoId) || null;
  const rel = String(idx?.preview_rel || `media/thumbs/${it.channel}/${it.video}.jpg`).trim();
  const url = siteUrl(rel);
  const currentId = videoId;

  heroThumbImg.onload = () => {
    if (String(selected?.video_id || "").trim() !== currentId) return;
    heroThumbImg.hidden = false;
    heroThumbFallback.hidden = true;
  };
  heroThumbImg.onerror = () => {
    if (String(selected?.video_id || "").trim() !== currentId) return;
    heroThumbImg.hidden = true;
    heroThumbFallback.hidden = false;
    heroThumbFallback.textContent = "サムネ未（プレビューなし）";
  };
  heroThumbImg.alt = `${videoId} thumbnail`;
  heroThumbImg.src = url;
}

function updateBrowseSummary() {
  const ch = normalizeChannelParam(channelSelect?.value || "") || String(selected?.channel || "").trim();
  const v = normalizeVideoParam(videoSelect?.value || "") || String(selected?.video || "").trim();
  const id = ch && v ? `${ch}-${v}` : "";
  browseSummary.textContent = id ? `Browse（${id}）` : "Browse（Channel/Videoで選ぶ）";
  updateSnapshotLink();
}

function updateStaticLinks() {
  const fixedLogicPath = "ssot/reference/【消さないで！人間用】確定ロジック.md";
  const contactBoxPath = "ssot/reference/CONTACT_BOX.md";
  if (gitTreeBase) {
    const editBase = gitTreeBase.replace("/tree/", "/edit/");
    openFixedLogic.href = encodeURI(`${editBase}${fixedLogicPath}`);
    openContactBox.href = encodeURI(`${editBase}${contactBoxPath}`);
  } else {
    openFixedLogic.href = joinUrl(rawBase, fixedLogicPath);
    openContactBox.href = joinUrl(rawBase, contactBoxPath);
  }
}

function updateSnapshotLink() {
  const ch = normalizeChannelParam(channelSelect?.value || "") || String(selected?.channel || "").trim();
  const v = normalizeVideoParam(videoSelect?.value || "") || String(selected?.video || "").trim();
  if (!ch) {
    openSnapshot.href = "./snapshot/";
    return;
  }
  const url = new URL("./snapshot/", window.location.href);
  url.searchParams.set("channel", ch);
  if (v) url.searchParams.set("q", `${ch}-${v}`);
  openSnapshot.href = url.toString();
}

function setControlsDisabled(on) {
  const disabled = Boolean(on);
  channelSelect.disabled = disabled;
  videoSelect.disabled = disabled;
  searchInput.disabled = disabled;
  reloadIndexButton.disabled = disabled;
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

function isNarrowView() {
  try {
    return Boolean(window.matchMedia && window.matchMedia("(max-width: 720px)").matches);
  } catch (_err) {
    return false;
  }
}

function closeBrowseIfNarrow() {
  if (!isNarrowView()) return;
  try {
    browseDetails.open = false;
  } catch (_err) {
    // ignore
  }
}

function setBadge(el, text, kind) {
  el.textContent = text || "—";
  el.dataset.kind = kind || "neutral";
}

function makeMiniBadge(text, kind) {
  const el = document.createElement("span");
  el.className = "mini-badge";
  el.dataset.kind = kind || "neutral";
  el.textContent = text || "—";
  return el;
}

function updateBadges() {
  if (scriptState === "loading") {
    setBadge(badgeScript, "…", "neutral");
  } else if (scriptState === "error") {
    setBadge(badgeScript, "ERR", "error");
  } else if (scriptState === "ok") {
    const k = Math.round((loadedText || "").length / 1000);
    setBadge(badgeScript, k ? `${k}k` : "OK", "ok");
  } else {
    setBadge(badgeScript, "—", "neutral");
  }

  if (audioState === "loading") {
    setBadge(badgeAudio, "…", "neutral");
  } else if (audioState === "error") {
    setBadge(badgeAudio, "ERR", "error");
  } else if (audioState === "ok") {
    setBadge(badgeAudio, "OK", "ok");
  } else if (audioState === "partial") {
    setBadge(badgeAudio, "一部", "warn");
  } else if (audioState === "missing") {
    setBadge(badgeAudio, "未", "neutral");
  } else {
    setBadge(badgeAudio, "—", "neutral");
  }

  if (thumbState === "loading") {
    setBadge(badgeThumb, "…", "neutral");
  } else if (thumbState === "error") {
    setBadge(badgeThumb, "ERR", "error");
  } else if (thumbState === "ok") {
    setBadge(badgeThumb, "OK", "ok");
  } else if (thumbState === "partial") {
    setBadge(badgeThumb, "META", "warn");
  } else if (thumbState === "missing") {
    setBadge(badgeThumb, "未", "neutral");
  } else {
    setBadge(badgeThumb, "—", "neutral");
  }

  if (videoImagesState === "loading") {
    setBadge(badgeImages, "…", "neutral");
  } else if (videoImagesState === "error") {
    setBadge(badgeImages, "ERR", "error");
  } else if (videoImagesState === "ok") {
    setBadge(badgeImages, videoImagesCount ? String(videoImagesCount) : "OK", "ok");
  } else if (videoImagesState === "partial") {
    setBadge(badgeImages, videoImagesCount ? String(videoImagesCount) : "一部", "warn");
  } else if (videoImagesState === "missing") {
    setBadge(badgeImages, "未", "neutral");
  } else {
    setBadge(badgeImages, "—", "neutral");
  }
}

function persistUiState() {
  try {
    const stored = readUiStateFromStorage();
    // Prefer `selected` as the source of truth (prevents accidental CH01 resets if selects desync).
    const selCh = normalizeChannelParam(selected?.channel || "");
    const selV = normalizeVideoParam(selected?.video || "");
    const hasSelected = Boolean(selCh && selV);
    const chUi = normalizeChannelParam(channelSelect?.value || "");
    const vUi = normalizeVideoParam(videoSelect?.value || "");
    const channel = (hasSelected ? selCh : chUi) || stored.channel || "";
    const video = (hasSelected ? selV : vUi) || stored.video || "";
    const view = normalizeView(currentView);
    window.localStorage.setItem(
      UI_STATE_KEY,
      JSON.stringify({ channel, video, view, updated_at: new Date().toISOString() })
    );
  } catch (_err) {
    // ignore
  }

  try {
    const params = new URLSearchParams(window.location.search);
    const chosenCh = normalizeChannelParam(selected?.channel || "") || normalizeChannelParam(channelSelect?.value || "");
    const chosenV = normalizeVideoParam(selected?.video || "") || normalizeVideoParam(videoSelect?.value || "");
    if (chosenCh && chosenV) {
      params.set("id", `${chosenCh}-${chosenV}`);
      params.delete("ch");
      params.delete("v");
    }
    const viewParam = normalizeView(currentView);
    if (viewParam && viewParam !== "script") {
      params.set("view", viewParam);
    } else {
      params.delete("view");
    }
    const qs = params.toString();
    const next = `${window.location.pathname}${qs ? "?" + qs : ""}${window.location.hash || ""}`;
    window.history.replaceState(null, "", next);
  } catch (_err) {
    // ignore
  }
}

function setActiveView(view) {
  const v = normalizeView(view);
  currentView = v;
  appRoot.dataset.view = v;
  tabScript.classList.toggle("view-tab--active", v === "script");
  tabAudio.classList.toggle("view-tab--active", v === "audio");
  tabThumb.classList.toggle("view-tab--active", v === "thumb");
  tabImages.classList.toggle("view-tab--active", v === "images");

  // Open only the relevant details for the selected view.
  if (v === "script") {
    audioPrepDetails.open = false;
    thumbDetails.open = false;
    videoImagesDetails.open = false;
  } else if (v === "audio") {
    audioPrepDetails.open = true;
    thumbDetails.open = false;
    videoImagesDetails.open = false;
  } else if (v === "thumb") {
    thumbDetails.open = true;
    audioPrepDetails.open = false;
    videoImagesDetails.open = false;
  } else if (v === "images") {
    videoImagesDetails.open = true;
    audioPrepDetails.open = false;
    thumbDetails.open = false;
  }

  persistUiState();
}

function scrollToEl(el) {
  try {
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (_err) {
    // Fallback without smooth scroll.
    try {
      el.scrollIntoView(true);
    } catch (_err2) {
      // ignore
    }
  }
}

function goToView(view) {
  const v = normalizeView(view);
  setActiveView(v);

  if (v === "audio") {
    scrollToEl(audioPrepDetails);
  } else if (v === "thumb") {
    scrollToEl(thumbDetails);
  } else if (v === "images") {
    scrollToEl(videoImagesDetails);
  } else {
    scrollToEl(contentPre);
  }
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

function episodeScriptDirPath(it) {
  const ch = normalizeChannelParam(it?.channel);
  const v = normalizeVideoParam(it?.video);
  if (!ch || !v) return "";
  return `workspaces/scripts/${ch}/${v}`;
}

function runImagesTreeUrl(runId) {
  if (!gitTreeBase) return "";
  const rid = String(runId || "").trim();
  if (!rid) return "";
  return `${gitTreeBase}workspaces/video/runs/${rid}/images`;
}

function episodeAssetPackPath(it) {
  const ch = normalizeChannelParam(it?.channel);
  const v = normalizeVideoParam(it?.video);
  if (!ch || !v) return "";
  return `workspaces/video/assets/episodes/${ch}/${v}`;
}

function episodeAssetPackImagesTreeUrl(it) {
  if (!gitTreeBase) return "";
  const rel = episodeAssetPackPath(it);
  if (!rel) return "";
  return `${gitTreeBase}${rel}/images`;
}

function episodeAssetPackManifestBlobUrl(it) {
  if (!gitTreeBase) return "";
  const rel = episodeAssetPackPath(it);
  if (!rel) return "";
  const blobBase = gitTreeBase.replace("/tree/", "/blob/");
  return `${blobBase}${rel}/manifest.json`;
}

function updateAssetPackLink(it) {
  const rel = episodeScriptDirPath(it);
  if (!rel) {
    openAssetPack.removeAttribute("href");
    return;
  }
  openAssetPack.href = gitTreeBase ? `${gitTreeBase}${rel}` : joinUrl(rawBase, rel);
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
  audioState = "loading";
  updateBadges();
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

    const hasScript = scriptText != null;
    const hasMeta = metaText != null;

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

    if (hasScript && hasMeta) {
      audioState = "ok";
    } else if (hasScript || hasMeta) {
      audioState = "partial";
    } else {
      audioState = "missing";
    }
    updateBadges();
  } catch (err) {
    if (selected?.video_id !== currentId) return;
    const msg = `読み込みに失敗しました。\n${String(err)}`;
    audioPrepScriptPre.textContent = msg;
    audioPrepMetaPre.textContent = msg;
    audioState = "error";
    updateBadges();
  }
}

function renderThumbProject(it, proj) {
  thumbBody.innerHTML = "";

  const videoId = String(it?.video_id || "").trim();
  const selectedId = String(proj?.selected_variant_id || "").trim();
  const status = String(proj?.status || "").trim();
  const variants = Array.isArray(proj?.variants) ? proj.variants : [];

  const head = document.createElement("div");
  head.className = "thumb-head";
  head.textContent = `status=${status || "-"} / selected=${selectedId || "-"} / variants=${variants.length}`;
  thumbBody.appendChild(head);

  const selectedVar = variants.find((v) => String(v?.id || "").trim() === selectedId) || null;

  const selectedCard = document.createElement("div");
  selectedCard.className = "thumb-selected";

  const selectedTitle = document.createElement("div");
  selectedTitle.className = "thumb-selected__title";
  selectedTitle.textContent = "選択サムネ（タップで拡大）";
  selectedCard.appendChild(selectedTitle);

  const selectedMeta = document.createElement("div");
  selectedMeta.className = "thumb-selected__meta muted";
  const label = String(selectedVar?.label || selectedId || "").trim();
  const vStatus = String(selectedVar?.status || "").trim();
  selectedMeta.textContent = `${label || "(no label)"}${vStatus ? " · " + vStatus : ""}`;
  selectedCard.appendChild(selectedMeta);

  const previewWrap = document.createElement("div");
  previewWrap.className = "thumb-selected__preview";

  const idx = videoId ? thumbIndexByVideoId.get(videoId) : null;
  const publishedRel = String(idx?.preview_rel || `media/thumbs/${it.channel}/${it.video}.jpg`).trim();
  const publishedUrl = siteUrl(publishedRel);
  const publishedKnownMissing = idx && idx.preview_rel && idx.preview_exists === false;

  const rawUrl = String(selectedVar?.image_url || "").trim();
  const remoteUrl = rawUrl && /^https?:\/\//.test(rawUrl) ? rawUrl : "";
  const keepInfoAfterLoad = !selectedVar;

  const message = document.createElement("div");
  message.className = "thumb-selected__message muted";

  if (!selectedVar) {
    message.textContent = "selected_variant_id のvariantが見つかりません（projects.json）。published preview を表示します。";
  }

  if (!remoteUrl && publishedKnownMissing) {
    message.textContent = [
      "プレビュー未公開（元画像はgitignoreなので、Pages用プレビュー生成が必要です）。",
      `次: python3 scripts/ops/pages_thumb_previews.py --channel ${it.channel} --video ${it.video} --write`,
      "→ commit/push で Pages から表示できます。",
    ].join("\n");
    previewWrap.appendChild(message);
  } else if (!remoteUrl && !publishedUrl) {
    message.textContent = "プレビューURLを決定できません。";
    previewWrap.appendChild(message);
  } else {
    const primaryUrl = remoteUrl || publishedUrl;
    const fallbackUrl = remoteUrl ? publishedUrl : "";

    const a = document.createElement("a");
    a.className = "thumb-selected__imglink";
    a.target = "_blank";
    a.rel = "noreferrer";
    a.href = primaryUrl;

    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = `${videoId} thumbnail`;
    img.hidden = true;

    let triedFallback = false;
    img.onload = () => {
      img.hidden = false;
      if (!keepInfoAfterLoad) {
        try {
          message.remove();
        } catch (_err) {
          // ignore
        }
      } else {
        message.textContent = "projects.json未同期（published preview）";
      }
    };
    img.onerror = () => {
      if (!triedFallback && fallbackUrl) {
        triedFallback = true;
        a.href = fallbackUrl;
        img.src = fallbackUrl;
        return;
      }
      try {
        img.remove();
      } catch (_err) {
        // ignore
      }
      message.textContent = [
        "画像プレビューを読み込めませんでした。",
        "（元画像はgitignoreだが、Pages用プレビューは docs/media/thumbs に出せます）",
        `次: python3 scripts/ops/pages_thumb_previews.py --channel ${it.channel} --video ${it.video} --write`,
      ].join("\n");
      previewWrap.appendChild(message);
    };
    img.src = primaryUrl;

    a.appendChild(img);
    previewWrap.appendChild(a);
    previewWrap.appendChild(message);
  }

  const srcInfo = document.createElement("div");
  srcInfo.className = "thumb-selected__src muted";
  const imagePath = String(selectedVar?.image_path || "").trim();
  if (imagePath || rawUrl) {
    const code = document.createElement("code");
    code.textContent = imagePath || rawUrl;
    srcInfo.appendChild(document.createTextNode("source: "));
    srcInfo.appendChild(code);
  } else {
    srcInfo.textContent = "source: (none)";
  }

  selectedCard.appendChild(previewWrap);
  selectedCard.appendChild(srcInfo);
  thumbBody.appendChild(selectedCard);

  const det = document.createElement("details");
  det.className = "details thumb-variants";
  det.open = false;
  const sum = document.createElement("summary");
  sum.textContent = `候補一覧（${variants.length}）`;
  det.appendChild(sum);
  const body = document.createElement("div");
  body.className = "details__body";

  if (!variants.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "variants が空です（projects.json）";
    body.appendChild(empty);
  } else {
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
      const vLabel = String(v?.label || v?.id || "").trim();
      const st2 = String(v?.status || "").trim();
      title.textContent = `${vLabel || "(no label)"}${st2 ? " · " + st2 : ""}`;
      card.appendChild(title);

      const u = String(v?.image_url || "").trim();
      const p = String(v?.image_path || "").trim();
      const code = document.createElement("code");
      code.className = "thumb-card__path";
      code.textContent = p || u || "(no image)";
      card.appendChild(code);

      grid.appendChild(card);
    }
    body.appendChild(grid);
  }

  det.appendChild(body);
  thumbBody.appendChild(det);
}

function renderThumbPreviewOnly(it) {
  thumbBody.innerHTML = "";

  const videoId = String(it?.video_id || "").trim();
  const idx = videoId ? thumbIndexByVideoId.get(videoId) : null;
  const publishedRel = String(idx?.preview_rel || `media/thumbs/${it.channel}/${it.video}.jpg`).trim();
  const publishedUrl = siteUrl(publishedRel);
  const publishedKnownMissing = idx && idx.preview_rel && idx.preview_exists === false;

  const head = document.createElement("div");
  head.className = "thumb-head";
  head.textContent = `selected=(unknown) / preview=${publishedRel || "-"}`;
  thumbBody.appendChild(head);

  const card = document.createElement("div");
  card.className = "thumb-selected";
  const title = document.createElement("div");
  title.className = "thumb-selected__title";
  title.textContent = "サムネ（プレビュー）";
  card.appendChild(title);

  const previewWrap = document.createElement("div");
  previewWrap.className = "thumb-selected__preview";

  const message = document.createElement("div");
  message.className = "thumb-selected__message muted";

  if (!publishedUrl) {
    message.textContent = "プレビューURLを決定できません。";
    previewWrap.appendChild(message);
  } else if (publishedKnownMissing) {
    message.textContent = [
      "プレビュー未公開（まだPagesに生成されていません）。",
      `次: python3 scripts/ops/pages_thumb_previews.py --channel ${it.channel} --video ${it.video} --write`,
      "→ commit/push で Pages から表示できます。",
    ].join("\n");
    previewWrap.appendChild(message);
  } else {
    const a = document.createElement("a");
    a.className = "thumb-selected__imglink";
    a.target = "_blank";
    a.rel = "noreferrer";
    a.href = publishedUrl;

    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = `${videoId} thumbnail`;
    img.hidden = true;
    img.onload = () => {
      img.hidden = false;
      try {
        message.remove();
      } catch (_err) {
        // ignore
      }
    };
    img.onerror = () => {
      try {
        img.remove();
      } catch (_err) {
        // ignore
      }
      message.textContent = [
        "画像プレビューを読み込めませんでした。",
        `次: python3 scripts/ops/pages_thumb_previews.py --channel ${it.channel} --video ${it.video} --write`,
      ].join("\n");
      previewWrap.appendChild(message);
    };
    img.src = publishedUrl;

    a.appendChild(img);
    previewWrap.appendChild(a);
    previewWrap.appendChild(message);
  }

  const note = document.createElement("div");
  note.className = "thumb-selected__src muted";
  note.textContent = "※ projects.json 未登録のため、候補一覧/選択IDは表示できません（プレビューのみ）。";

  card.appendChild(previewWrap);
  card.appendChild(note);
  thumbBody.appendChild(card);
}

async function loadThumb(it) {
  const currentId = it?.video_id || "";
  thumbState = "loading";
  updateBadges();
  thumbBody.textContent = "読み込み中…";
  try {
    const [map] = await Promise.all([loadThumbProjects(), loadThumbIndex()]);
    if (selected?.video_id !== currentId) return;
    const proj = map.get(currentId);
    if (!proj) {
      renderThumbPreviewOnly(it);
      const idx = thumbIndexByVideoId.get(currentId);
      if (idx && idx.preview_exists === true) {
        thumbState = "ok";
      } else if (idx && idx.preview_exists === false) {
        thumbState = "missing";
      } else {
        thumbState = "partial";
      }
      updateBadges();
      return;
    }
    renderThumbProject(it, proj);
    const variants = Array.isArray(proj?.variants) ? proj.variants : [];
    const selectedId = String(proj?.selected_variant_id || "").trim();
    const selectedVar = variants.find((v) => String(v?.id || "").trim() === selectedId) || null;
    const rawUrl = String(selectedVar?.image_url || "").trim();
    const hasRemote = rawUrl && /^https?:\/\//.test(rawUrl);
    const idx = thumbIndexByVideoId.get(currentId);
    const hasPublished = Boolean(idx?.preview_exists);
    thumbState = hasRemote || hasPublished ? "ok" : "partial";
    updateBadges();
  } catch (err) {
    if (selected?.video_id !== currentId) return;
    thumbBody.textContent = `読み込みに失敗しました: ${String(err)}`;
    thumbState = "error";
    updateBadges();
  }
}

function renderVideoImagesEntry(it, entry) {
  videoImagesBody.innerHTML = "";

  const files = Array.isArray(entry?.files) ? entry.files : [];
  const count = Number(entry?.count) || files.length;
  const runId = String(entry?.run_id || "").trim();

  const head = document.createElement("div");
  head.className = "video-images-head";
  head.textContent = `count=${count || files.length || 0}${runId ? " / run=" + runId : ""}`;
  videoImagesBody.appendChild(head);

  const tools = document.createElement("div");
  tools.className = "video-images-tools";

  const runTreeUrl = runImagesTreeUrl(runId);
  if (runTreeUrl) {
    const a = document.createElement("a");
    a.className = "btn btn--ghost";
    a.target = "_blank";
    a.rel = "noreferrer";
    a.href = runTreeUrl;
    a.textContent = "runs/images をGitHubで開く";
    tools.appendChild(a);
  }

  const imagesTreeUrl = episodeAssetPackImagesTreeUrl(it);
  if (imagesTreeUrl) {
    const a = document.createElement("a");
    a.className = "btn btn--ghost";
    a.target = "_blank";
    a.rel = "noreferrer";
    a.href = imagesTreeUrl;
    a.textContent = "素材束(images)をGitHubで開く";
    tools.appendChild(a);
  }

  const manifestUrl = episodeAssetPackManifestBlobUrl(it);
  if (manifestUrl) {
    const a = document.createElement("a");
    a.className = "btn btn--ghost";
    a.target = "_blank";
    a.rel = "noreferrer";
    a.href = manifestUrl;
    a.textContent = "manifest.json";
    tools.appendChild(a);
  }

  if (files.length) {
    const copyUrls = document.createElement("button");
    copyUrls.type = "button";
    copyUrls.className = "btn btn--ghost";
    copyUrls.textContent = "raw URL一覧をコピー";
    copyUrls.addEventListener("click", async () => {
      const urls = files
        .map((f) => docsRawUrl(String(f?.rel || "").trim()))
        .filter(Boolean)
        .join("\n");
      const ok = await copyText(urls);
      setCopyStatus(ok ? "画像URLをコピーしました" : "コピーに失敗しました", !ok);
    });
    tools.appendChild(copyUrls);
  }

  if (tools.childNodes.length) videoImagesBody.appendChild(tools);

  if (!files.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "プレビュー画像がありません（indexはあるが files が空）。";
    videoImagesBody.appendChild(empty);
    return;
  }

  const grid = document.createElement("div");
  grid.className = "video-images-grid";
  videoImagesBody.appendChild(grid);

  const maxInitial = 12;
  let shown = 0;
  const total = files.length;

  function addMore(nextN) {
    const end = Math.min(total, shown + nextN);
    for (let i = shown; i < end; i += 1) {
      const f = files[i] || {};
      const rel = String(f?.rel || "").trim();
      const url = siteUrl(rel);
      const summary = String(f?.summary || "").trim();

      const card = document.createElement("div");
      card.className = "video-image-card";

      const a = document.createElement("a");
      a.className = "video-image-card__imglink";
      a.target = "_blank";
      a.rel = "noreferrer";
      a.href = url;

      const img = document.createElement("img");
      img.loading = "lazy";
      img.alt = summary ? `${it?.video_id || ""} ${summary}` : `${it?.video_id || ""} image`;
      img.src = url;
      img.onerror = () => {
        try {
          img.remove();
        } catch (_err) {
          // ignore
        }
        const msg = document.createElement("div");
        msg.className = "muted";
        msg.textContent = "画像を読み込めません";
        a.appendChild(msg);
      };
      a.appendChild(img);
      card.appendChild(a);

      if (summary) {
        const cap = document.createElement("div");
        cap.className = "video-image-card__caption muted";
        cap.textContent = summary;
        card.appendChild(cap);
      }

      grid.appendChild(card);
    }
    shown = end;
  }

  addMore(maxInitial);

  if (shown < total) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "btn btn--ghost";
    more.textContent = `さらに表示（残り ${total - shown}）`;
    more.addEventListener("click", () => {
      addMore(24);
      if (shown >= total) {
        try {
          more.remove();
        } catch (_err) {
          // ignore
        }
      } else {
        more.textContent = `さらに表示（残り ${total - shown}）`;
      }
    });
    videoImagesBody.appendChild(more);
  }
}

async function loadVideoImages(it) {
  const currentId = it?.video_id || "";
  videoImagesState = "loading";
  videoImagesCount = 0;
  updateBadges();
  videoImagesBody.textContent = "読み込み中…";
  try {
    const map = await loadVideoImagesIndex();
    if (selected?.video_id !== currentId) return;
    const entry = map.get(currentId);
    if (!entry) {
      videoImagesState = "missing";
      videoImagesCount = 0;
      updateBadges();
      videoImagesBody.innerHTML = "";
      const msg = document.createElement("div");
      msg.className = "muted";
      msg.textContent = "未公開（Pages用プレビューがまだ生成されていません）。";
      videoImagesBody.appendChild(msg);

      const tools = document.createElement("div");
      tools.className = "video-images-tools";
      const imagesTreeUrl = episodeAssetPackImagesTreeUrl(it);
      if (imagesTreeUrl) {
        const a = document.createElement("a");
        a.className = "btn btn--ghost";
        a.target = "_blank";
        a.rel = "noreferrer";
        a.href = imagesTreeUrl;
        a.textContent = "素材束(images)をGitHubで開く";
        tools.appendChild(a);
      }
      const manifestUrl = episodeAssetPackManifestBlobUrl(it);
      if (manifestUrl) {
        const a = document.createElement("a");
        a.className = "btn btn--ghost";
        a.target = "_blank";
        a.rel = "noreferrer";
        a.href = manifestUrl;
        a.textContent = "manifest.json";
        tools.appendChild(a);
      }
      if (tools.childNodes.length) videoImagesBody.appendChild(tools);

      const next = document.createElement("pre");
      next.className = "pre pre--small muted";
      next.textContent = [
        `次: python3 scripts/ops/pages_video_images_previews.py --channel ${it.channel} --video ${it.video} --write`,
        "→ commit/push で Pages から表示できます。",
      ].join("\n");
      videoImagesBody.appendChild(next);
      return;
    }
    renderVideoImagesEntry(it, entry);
    videoImagesCount = Array.isArray(entry?.files) ? entry.files.length : 0;
    videoImagesState = videoImagesCount ? "ok" : "partial";
    updateBadges();
  } catch (err) {
    if (selected?.video_id !== currentId) return;
    videoImagesBody.textContent = `読み込みに失敗しました: ${String(err)}`;
    videoImagesState = "error";
    videoImagesCount = 0;
    updateBadges();
  }
}

function renderChannelChips(channels, activeChannel) {
  const active = String(activeChannel || "").trim();
  channelChips.innerHTML = "";
  for (const ch of channels) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "channel-chip";
    if (active && String(ch) === active) btn.classList.add("channel-chip--active");

    const avatar = document.createElement("div");
    avatar.className = "channel-chip__avatar";
    const url = channelAvatarUrl(ch);
    if (url) {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.alt = `${ch} avatar`;
      img.src = url;
      img.onerror = () => {
        try {
          img.remove();
        } catch (_err) {
          // ignore
        }
        avatar.textContent = String(ch).replace(/^CH/, "");
      };
      avatar.appendChild(img);
    } else {
      avatar.textContent = String(ch).replace(/^CH/, "");
    }

    const text = document.createElement("div");
    text.className = "channel-chip__text";
    const name = document.createElement("div");
    name.className = "channel-chip__name";
    name.textContent = channelShortName(ch);
    const code = document.createElement("div");
    code.className = "channel-chip__code muted";
    code.textContent = String(ch);
    text.appendChild(name);
    text.appendChild(code);

    btn.appendChild(avatar);
    btn.appendChild(text);
    btn.addEventListener("click", () => {
      channelSelect.value = String(ch);
      renderVideos(String(ch));
      const video = defaultVideoForChannel(String(ch));
      if (video) {
        selectItem(String(ch), String(video));
      } else {
        clearSelectionForChannel(String(ch));
      }
    });
    channelChips.appendChild(btn);
  }
}

function clearSelectionForChannel(channel) {
  const ch = String(channel || "").trim();
  selected = null;
  heroMedia.hidden = true;
  loadedText = "";
  loadedNoSepText = "";
  scriptState = "idle";
  audioState = "idle";
  thumbState = "idle";
  videoImagesState = "idle";
  updateBadges();
  renderNoSepChunkButtons();
  metaTitle.textContent = ch ? `${channelLabel(ch)}（台本なし）` : "—";
  metaPath.textContent = "—";
  openRaw.removeAttribute("href");
  openAssetPack.removeAttribute("href");
  contentPre.textContent = "このチャンネルには台本がありません。";
  initialChannelWanted = ch;
  initialVideoWanted = "";
  updateBrowseSummary();
  persistUiState();
}

function renderChannels() {
  const channels = Array.from(grouped.keys()).sort((a, b) => {
    const na = Number(String(a).replace(/^CH/, "")) || 999999;
    const nb = Number(String(b).replace(/^CH/, "")) || 999999;
    if (na !== nb) return na - nb;
    return String(a).localeCompare(String(b));
  });
  channelsSorted = channels;
  channelSelect.innerHTML = "";
  for (const ch of channels) {
    const opt = document.createElement("option");
    opt.value = ch;
    opt.textContent = channelLabel(ch);
    channelSelect.appendChild(opt);
  }
  renderChannelChips(channels, channelSelect.value || channels[0] || "");
}

function defaultVideoForChannel(channel) {
  const list = grouped.get(String(channel || "")) || [];
  if (!list.length) return null;
  return isNarrowView() ? (list[list.length - 1]?.video ?? null) : (list[0]?.video ?? null);
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
  renderVideoList(channel, videoSelect.value || "");
}

function renderVideoList(channel, activeVideo) {
  const list0 = grouped.get(channel) || [];
  const active = String(activeVideo || "").trim() || String(videoSelect.value || "").trim();
  videoList.innerHTML = "";

  if (!list0.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "このチャンネルには台本がありません。";
    videoList.appendChild(empty);
    return;
  }

  // Mobile: show latest first + chunked rendering (performance).
  const narrow = isNarrowView();
  const list = narrow ? [...list0].slice().reverse() : list0;
  const maxInitial = narrow ? 60 : 180;
  let shown = 0;

  function appendItems(nextN) {
    const end = Math.min(list.length, shown + nextN);
    const frag = document.createDocumentFragment();
    for (let i = shown; i < end; i += 1) {
      const it = list[i];

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "video-list__item";
      if (active && String(it.video || "") === active) {
        btn.classList.add("video-list__item--active");
      }

      const left = document.createElement("div");
      left.className = "video-list__thumb";

      const videoId = String(it?.video_id || "").trim();
      const idx = videoId ? thumbIndexByVideoId.get(videoId) : null;
      const rel = String(idx?.preview_rel || `media/thumbs/${it.channel}/${it.video}.jpg`).trim();
      const canShow = idx ? idx.preview_exists !== false : true;
      const thumbUrl = canShow ? siteUrl(rel) : "";

      if (thumbUrl) {
        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = `${videoId} thumb`;
        img.src = thumbUrl;
        img.onerror = () => {
          try {
            img.remove();
          } catch (_err) {
            // ignore
          }
          left.textContent = "—";
        };
        left.appendChild(img);
      } else {
        left.textContent = "—";
      }

      const right = document.createElement("div");
      right.className = "video-list__meta";

      const id = document.createElement("div");
      id.className = "video-list__id";
      id.textContent = String(it.video_id || "").trim();
      right.appendChild(id);

      const title = document.createElement("div");
      title.className = "video-list__title";
      title.textContent = String(it.title || "").trim();
      right.appendChild(title);

      const badges = document.createElement("div");
      badges.className = "mini-badges";
      const vid = String(it?.video_id || "").trim();

      const thumbIdx = vid ? thumbIndexByVideoId.get(vid) : null;
      if (thumbIdx && thumbIdx.preview_exists === true) {
        badges.appendChild(makeMiniBadge("サムネ✓", "ok"));
      } else if (thumbIdx && thumbIdx.preview_exists === false) {
        badges.appendChild(makeMiniBadge("サムネ未", "bad"));
      } else {
        badges.appendChild(makeMiniBadge("サムネ?", "neutral"));
      }

      const imgs = vid ? videoImagesIndexByVideoId.get(vid) : null;
      const imgCount = Array.isArray(imgs?.files) ? imgs.files.length : 0;
      if (imgs && imgCount > 0) {
        badges.appendChild(makeMiniBadge(`画像${imgCount}`, "ok"));
      } else if (imgs) {
        badges.appendChild(makeMiniBadge("画像一部", "warn"));
      } else {
        badges.appendChild(makeMiniBadge("画像未", "neutral"));
      }
      right.appendChild(badges);

      btn.appendChild(left);
      btn.appendChild(right);
      btn.addEventListener("click", () => {
        selectItem(it.channel, it.video);
        searchInput.value = "";
        hideSearchResults();
        closeBrowseIfNarrow();
      });
      frag.appendChild(btn);
    }
    videoList.appendChild(frag);
    shown = end;
  }

  appendItems(maxInitial);

  if (shown < list.length) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "btn btn--ghost";
    function updateText() {
      more.textContent = `さらに表示（残り ${list.length - shown}）`;
    }
    updateText();
    more.addEventListener("click", () => {
      appendItems(narrow ? 80 : 200);
      if (shown >= list.length) {
        try {
          more.remove();
        } catch (_err) {
          // ignore
        }
      } else {
        updateText();
      }
    });
    videoList.appendChild(more);
  }
}

function findItem(channel, video) {
  const list = grouped.get(channel) || [];
  return list.find((it) => it.video === video) || null;
}

function findItemByVideoId(videoId) {
  const vid = String(videoId || "").trim().toUpperCase();
  for (const it of items) {
    if (String(it.video_id || "").toUpperCase() === vid) return it;
  }
  return null;
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
    const left = document.createElement("div");
    left.className = "search-results__thumb";

    const videoId = String(it?.video_id || "").trim();
    const idx = videoId ? thumbIndexByVideoId.get(videoId) : null;
    const rel = String(idx?.preview_rel || `media/thumbs/${it.channel}/${it.video}.jpg`).trim();
    const canShow = idx ? idx.preview_exists !== false : true;
    const thumbUrl = canShow ? siteUrl(rel) : "";

    if (thumbUrl) {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.alt = `${videoId} thumb`;
      img.src = thumbUrl;
      img.onerror = () => {
        try {
          img.remove();
        } catch (_err) {
          // ignore
        }
        left.textContent = "—";
      };
      left.appendChild(img);
    } else {
      left.textContent = "—";
    }

    const right = document.createElement("div");
    right.className = "search-results__meta";

    const id = document.createElement("div");
    id.className = "search-results__id";
    id.textContent = String(it.video_id || "").trim();
    right.appendChild(id);

    const title = document.createElement("div");
    title.className = "search-results__title";
    title.textContent = String(it.title || "").trim();
    right.appendChild(title);

    const sub = document.createElement("div");
    sub.className = "search-results__sub muted";
    sub.textContent = channelLabel(it.channel);
    right.appendChild(sub);

    btn.appendChild(left);
    btn.appendChild(right);
    btn.addEventListener("click", () => {
      hideSearchResults();
      selectItem(it.channel, it.video);
      searchInput.value = "";
      closeBrowseIfNarrow();
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
  initialChannelWanted = String(it?.channel || "").trim();
  initialVideoWanted = String(it?.video || "").trim();
  updateBrowseSummary();
  persistUiState();
  updateHeroMedia(it);
  loadedText = "";
  loadedNoSepText = "";
  scriptState = "loading";
  updateBadges();
  renderNoSepChunkButtons();
  void loadAudioPrep(it);
  void loadThumb(it);
  void loadVideoImages(it);

  const chLabel = channelLabel(it.channel);
  metaTitle.textContent = it.title ? `${chLabel} · ${it.video} · ${it.title}` : `${chLabel} · ${it.video}`;
  metaPath.textContent = it.assembled_path;

  const url = joinUrl(rawBase, it.assembled_path);
  openRaw.href = url;
  updateAssetPackLink(it);

  setLoading(true);
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`fetch failed: ${res.status} ${res.statusText}`);
    const text = await res.text();
    loadedText = normalizeNewlines(text);
    loadedNoSepText = stripPauseSeparators(loadedText);
    renderNoSepChunkButtons();
    contentPre.textContent = loadedText;
    scriptState = "ok";
    updateBadges();
    footerMeta.textContent = `index: ${indexData?.count || items.length} items · loaded: ${it.video_id} · chars: ${loadedText.length.toLocaleString(
      "ja-JP"
    )}`;
  } catch (err) {
    console.error(err);
    contentPre.textContent = `読み込みに失敗しました。\n\n${String(err)}`;
    footerMeta.textContent = "—";
    loadedNoSepText = "";
    scriptState = "error";
    updateBadges();
    renderNoSepChunkButtons();
  } finally {
    setLoading(false);
  }
}

function selectItem(channel, video) {
  channelSelect.value = channel;
  renderChannelChips(channelsSorted, channel);
  renderVideos(channel);
  videoSelect.value = video;
  renderVideoList(channel, video);
  updateBrowseSummary();
  const it = findItem(channel, video);
  if (it) {
    void loadScript(it);
  } else {
    clearSelectionForChannel(channel);
  }
}

async function reloadIndex() {
  setLoading(true);
  setControlsDisabled(true);
  try {
    const [res] = await Promise.all([
      fetch(siteUrl(INDEX_URL), { cache: "no-store" }),
      loadChannelMeta(),
      loadThumbIndex(),
      loadVideoImagesIndex(),
    ]);
    if (!res.ok) throw new Error(`index fetch failed: ${res.status} ${res.statusText}`);
    indexData = await res.json();
    items = Array.isArray(indexData?.items) ? indexData.items : [];
    grouped = buildGrouped(items);
    renderChannels();

    // Default selection: first item, or keep current if possible
    const preferredChannel =
      (initialChannelWanted && grouped.has(initialChannelWanted) ? initialChannelWanted : "") ||
      channelSelect.value ||
      Array.from(grouped.keys())[0];
    if (!preferredChannel) {
      metaTitle.textContent = "index.json が空です";
      metaPath.textContent = "—";
      contentPre.textContent = "";
      footerMeta.textContent = `generated: ${indexData?.generated_at || "—"}`;
      hideSearchResults();
      return;
    }
    channelSelect.value = preferredChannel;
    renderVideos(preferredChannel);

    const preferredVideoCandidate = initialVideoWanted || "";
    const preferredVideo =
      (preferredVideoCandidate && findItem(preferredChannel, preferredVideoCandidate) ? preferredVideoCandidate : "") ||
      defaultVideoForChannel(preferredChannel);
    if (preferredVideo) {
      selectItem(preferredChannel, preferredVideo);
      // Mobile UX: if user opened the viewer without an explicit deep link, keep Browse open
      // so they can pick a channel/video without hunting for the toggle.
      if (initialUrlHasSelection) {
        closeBrowseIfNarrow();
      } else if (isNarrowView()) {
        try {
          browseDetails.open = true;
        } catch (_err) {
          // ignore
        }
      }
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
    setControlsDisabled(false);
  }
}

function setupEvents() {
  $("reloadIndex").addEventListener("click", () => void reloadIndex());

  viewTabs.addEventListener("click", (ev) => {
    const target = ev.target instanceof Element ? ev.target.closest("[data-view]") : null;
    const view = target?.getAttribute("data-view") || "";
    if (!view) return;
    goToView(view);
  });

  heroThumbButton.addEventListener("click", () => goToView("thumb"));
  heroToThumb.addEventListener("click", () => goToView("thumb"));
  heroToImages.addEventListener("click", () => goToView("images"));

  channelSelect.addEventListener("change", () => {
    const ch = channelSelect.value;
    renderChannelChips(channelsSorted, ch);
    renderVideos(ch);
    const video = defaultVideoForChannel(ch);
    if (video) {
      selectItem(ch, video);
    } else {
      clearSelectionForChannel(ch);
    }
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

  searchInput.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter") return;
    const raw = String(searchInput.value || "").trim();
    if (!raw) return;

    const parsed = parseVideoIdParam(raw);
    if (parsed) {
      const it = findItem(parsed.channel, parsed.video);
      if (it) {
        hideSearchResults();
        selectItem(it.channel, it.video);
        closeBrowseIfNarrow();
        return;
      }
    }

    // If only a channel is provided (e.g. CH27), jump to that channel's default video.
    const chOnly = normalizeChannelParam(raw);
    if (chOnly && grouped.has(chOnly)) {
      hideSearchResults();
      const video = defaultVideoForChannel(chOnly);
      if (video) {
        selectItem(chOnly, video);
      } else {
        clearSelectionForChannel(chOnly);
      }
      closeBrowseIfNarrow();
      return;
    }

    // If a channel exists in Planning but has no scripts yet, jump to snapshot instead.
    if (chOnly && /^CH\d{2}$/.test(chOnly) && !grouped.has(chOnly)) {
      hideSearchResults();
      const url = new URL("./snapshot/", window.location.href);
      url.searchParams.set("channel", chOnly);
      url.searchParams.set("q", chOnly);
      window.location.href = url.toString();
      return;
    }

    // If only a video number is provided, show candidates across channels.
    if (/^\d{1,4}$/.test(raw)) {
      const v = normalizeVideoParam(raw);
      const matches = items.filter((it) => String(it.video || "") === v).slice(0, 20);
      if (!matches.length) {
        setCopyStatus("見つかりません（CHxx-NNN 形式推奨）", true);
        return;
      }
      if (matches.length === 1) {
        hideSearchResults();
        selectItem(matches[0].channel, matches[0].video);
        closeBrowseIfNarrow();
        return;
      }
      showSearchResults(matches);
      setCopyStatus(`候補が複数あります（${matches.length}件）。選んでください`, false);
      return;
    }

    // Fallback: if user typed an exact video_id-like string.
    const it2 = findItemByVideoId(raw);
    if (it2) {
      hideSearchResults();
      selectItem(it2.channel, it2.video);
      closeBrowseIfNarrow();
      return;
    }

    setCopyStatus("見つかりません（CHxx-NNN 形式か検索結果から選択）", true);
  });

  openBrowse.addEventListener("click", () => {
    try {
      browseDetails.open = true;
    } catch (_err) {
      // ignore
    }
    scrollToEl(browseDetails);
  });

  $("copyPath").addEventListener("click", async () => {
    const text = selected?.assembled_path || "";
    if (!text) return;
    const ok = await copyText(text);
    setCopyStatus(ok ? "パスをコピーしました" : "コピーに失敗しました", !ok);
  });

  $("copyLink").addEventListener("click", async () => {
    const url = String(window.location.href || "").trim();
    if (!url) return;
    const ok = await copyText(url);
    setCopyStatus(ok ? "リンクをコピーしました" : "コピーに失敗しました", !ok);
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
try {
  browseDetails.open = isNarrowView();
} catch (_err) {
  // ignore
}
updateStaticLinks();
updateBrowseSummary();
setActiveView(currentView);
updateBadges();
void reloadIndex();
