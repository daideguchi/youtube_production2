/* eslint-disable no-console */

const INDEX_URL = "./data/index.json";
const CHANNELS_INFO_PATH = "packages/script_pipeline/channels/channels_info.json";
const THUMB_PROJECTS_PATH = "workspaces/thumbnails/projects.json";
const THUMBS_INDEX_URL = "./data/thumbs_index.json";
const THUMBS_ALT_INDEX_URL = "./data/thumbs_alt_index.json";
const VIDEO_IMAGES_INDEX_URL = "./data/video_images_index.json";
const SNAPSHOT_CHANNELS_URL = "./data/snapshot/channels.json";
const CHUNK_SIZE = 10_000;
const UI_STATE_KEY = "ytm_script_viewer_state_v1";
const SITE_ASSET_VERSION = "20260113_06";
const EMBED_MODE = detectEmbedMode();

function detectEmbedMode() {
  try {
    const params = new URLSearchParams(window.location.search);
    const raw = String(params.get("embed") || "").trim().toLowerCase();
    if (raw && raw !== "0" && raw !== "false" && raw !== "off") return true;
  } catch (_err) {
    // ignore
  }
  try {
    return window.self !== window.top;
  } catch (_err) {
    return true;
  }
}

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
  if (EMBED_MODE) return fromUrl;
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
let thumbAltByChannel = new Map();
let thumbAltPromise = null;
let videoImagesIndexByVideoId = new Map();
let videoImagesIndexPromise = null;
let snapshotByChannel = new Map();
let snapshotChannelsPromise = null;
let snapshotEpisodeByVideoId = new Map();
let snapshotEpisodePromiseByChannel = new Map();
let snapshotEpisodesByChannel = new Map();

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

function loadThumbAltIndex() {
  if (thumbAltPromise) return thumbAltPromise;
  thumbAltPromise = (async () => {
    try {
      const res = await fetch(siteUrl(THUMBS_ALT_INDEX_URL), { cache: "no-store" });
      if (res.status === 404) {
        thumbAltByChannel = new Map();
        return thumbAltByChannel;
      }
      if (!res.ok) throw new Error(`thumbs_alt_index fetch failed: ${res.status} ${res.statusText}`);
      const data = await res.json();
      const channels = data?.channels && typeof data.channels === "object" ? data.channels : {};
      const next = new Map();
      for (const [chRaw, variantsRaw] of Object.entries(channels)) {
        const ch = normalizeChannelParam(chRaw);
        if (!ch) continue;
        const vmap = new Map();
        if (variantsRaw && typeof variantsRaw === "object") {
          for (const [variantRaw, videosRaw] of Object.entries(variantsRaw)) {
            const variant = String(variantRaw || "").trim();
            if (!variant) continue;
            const set = new Set();
            if (Array.isArray(videosRaw)) {
              for (const vvRaw of videosRaw) {
                const vv = normalizeVideoParam(vvRaw);
                if (vv) set.add(vv);
              }
            }
            if (set.size) vmap.set(variant, set);
          }
        }
        if (vmap.size) next.set(ch, vmap);
      }
      thumbAltByChannel = next;
    } catch (err) {
      console.warn("[script_viewer] failed to load thumbs_alt_index.json", err);
      thumbAltByChannel = new Map();
    }
    return thumbAltByChannel;
  })();
  return thumbAltPromise;
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

function loadSnapshotChannels() {
  if (snapshotChannelsPromise) return snapshotChannelsPromise;
  snapshotChannelsPromise = (async () => {
    try {
      const res = await fetch(siteUrl(SNAPSHOT_CHANNELS_URL), { cache: "no-store" });
      if (res.status === 404) {
        snapshotByChannel = new Map();
        return snapshotByChannel;
      }
      if (!res.ok) throw new Error(`snapshot channels fetch failed: ${res.status} ${res.statusText}`);
      const data = await res.json();
      const rows = Array.isArray(data?.channels) ? data.channels : [];
      const next = new Map();
      for (const row of rows) {
        const ch = String(row?.channel || "").trim();
        if (!ch) continue;
        next.set(ch, {
          planning_count: Number(row?.planning_count) || 0,
          scripts_count: Number(row?.scripts_count) || 0,
          data_path: String(row?.data_path || "").trim(),
        });
      }
      snapshotByChannel = next;
    } catch (err) {
      console.warn("[script_viewer] failed to load snapshot channels.json", err);
      snapshotByChannel = new Map();
    }
    return snapshotByChannel;
  })();
  return snapshotChannelsPromise;
}

function snapshotChannelDataPath(channelId) {
  const ch = String(channelId || "").trim();
  if (!ch) return "";
  const row = snapshotByChannel.get(ch);
  const p = String(row?.data_path || "").trim();
  return p || `data/snapshot/${ch}.json`;
}

async function loadSnapshotChannel(channelId) {
  const ch = String(channelId || "").trim();
  if (!ch) return null;
  if (snapshotEpisodePromiseByChannel.has(ch)) return snapshotEpisodePromiseByChannel.get(ch);

  const promise = (async () => {
    try {
      await loadSnapshotChannels();
      const dataPath = snapshotChannelDataPath(ch);
      if (!dataPath) return null;
      const res = await fetch(siteUrl(dataPath), { cache: "no-store" });
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`snapshot fetch failed: ${res.status} ${res.statusText}`);
      const data = await res.json();
      const eps = Array.isArray(data?.episodes) ? data.episodes : [];
      snapshotEpisodesByChannel.set(ch, eps);
      for (const ep of eps) {
        const vid = String(ep?.video_id || "").trim();
        if (!vid) continue;
        snapshotEpisodeByVideoId.set(vid, ep);
      }
      return data;
    } catch (err) {
      console.warn("[script_viewer] failed to load snapshot channel", ch, err);
      return null;
    }
  })();

  snapshotEpisodePromiseByChannel.set(ch, promise);
  return promise;
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
let scriptState = "idle"; // idle | loading | ok | missing | error
let audioState = "idle"; // idle | loading | ok | partial | missing | error
let thumbState = "idle"; // idle | loading | ok | partial | missing | error
let videoImagesState = "idle"; // idle | loading | ok | partial | missing | error
let videoImagesCount = 0;

const channelSelect = $("channelSelect");
const videoSelect = $("videoSelect");
const channelChips = $("channelChips");
const channelFilter = $("channelFilter");
const videoList = $("videoList");
const searchInput = $("searchInput");
const searchResults = $("searchResults");
const browseDetails = $("browseDetails");
const browseSummary = $("browseSummary");
const reloadIndexButton = $("reloadIndex");
const metaTitle = $("metaTitle");
const metaSub = $("metaSub");
const metaPath = $("metaPath");
const heroMedia = $("heroMedia");
const heroThumbButton = $("heroThumbButton");
const heroThumbImg = $("heroThumbImg");
const heroThumbFallback = $("heroThumbFallback");
const heroOpenThumb = $("heroOpenThumb");
const heroOpenThumbFallback = $("heroOpenThumbFallback");
const heroToThumb = $("heroToThumb");
const heroToImages = $("heroToImages");
const openRaw = $("openRaw");
const openAssetPack = $("openAssetPack");
const openBrowse = $("openBrowse");
const openSnapshot = $("openSnapshot");
const openFixedLogic = $("openFixedLogic");
const openContactBox = $("openContactBox");
const contentPre = $("contentPre");
const contentNav = $("contentNav");
const contentPrev = $("contentPrev");
const contentNext = $("contentNext");
const copyStatus = $("copyStatus");
const copyNoSep = $("copyNoSep");
const copyNoSepChunks = $("copyNoSepChunks");
const loading = $("loading");
const footerMeta = $("footerMeta");
const appRoot = $("appRoot");
if (EMBED_MODE) {
  appRoot.dataset.embed = "1";
}
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
const youtubeMetaDetails = $("youtubeMetaDetails");
const ytChannelInfoPre = $("ytChannelInfoPre");
const openYtStudio = $("openYtStudio");
const openYtChannel = $("openYtChannel");
const openVoiceConfig = $("openVoiceConfig");
const openChannelPrompt = $("openChannelPrompt");
const ytTitlePre = $("ytTitlePre");
const ytTagsPre = $("ytTagsPre");
const ytFullDescPre = $("ytFullDescPre");
const ytEpisodeDescPre = $("ytEpisodeDescPre");
const ytChannelDescPre = $("ytChannelDescPre");
const copyYtTitle = $("copyYtTitle");
const copyYtTags = $("copyYtTags");
const copyYtFullDesc = $("copyYtFullDesc");
const copyYtEpisodeDesc = $("copyYtEpisodeDesc");
const copyYtChannelDesc = $("copyYtChannelDesc");

function setLoading(on) {
  loading.hidden = !on;
}

function thumbAltVariantsForEpisode(it) {
  const ch = normalizeChannelParam(it?.channel || "");
  const v = normalizeVideoParam(it?.video || "");
  if (!ch || !v) return [];
  const per = thumbAltByChannel.get(ch) || null;
  if (!per) return [];
  const out = [];
  for (const [variant, vids] of per.entries()) {
    if (vids && vids.has(v)) out.push(String(variant || "").trim());
  }
  out.sort((a, b) => a.localeCompare(b));
  return out.filter(Boolean);
}

function preferredThumbAltVariant(it) {
  const variants = thumbAltVariantsForEpisode(it);
  if (!variants.length) return "";
  if (variants.includes("illust_v1")) return "illust_v1";
  return variants[0];
}

function thumbAltVariantLabel(variant) {
  const v = String(variant || "").trim();
  if (!v) return "";
  if (v === "illust_v1") return "イラストサムネ（縦長）";
  if (v.toLowerCase().includes("illust")) return `イラストサムネ（${v}）`;
  return `イラストサムネ（${v}）`;
}

function pickThumbUrls(it) {
  const videoId = String(it?.video_id || "").trim();
  const ch = normalizeChannelParam(it?.channel || "");
  const v = normalizeVideoParam(it?.video || "");
  if (!videoId || !ch || !v) return { primaryUrl: "", fallbackUrl: "" };

  const altVariant = preferredThumbAltVariant(it);
  const altRel = altVariant ? `media/thumbs_alt/${altVariant}/${ch}/${v}.jpg` : "";
  const altUrl = altRel ? siteUrl(altRel) : "";

  const idx = thumbIndexByVideoId.get(videoId) || null;
  const rel = String(idx?.preview_rel || `media/thumbs/${ch}/${v}.jpg`).trim();
  const canShow = idx ? idx.preview_exists !== false : true;
  const fallbackUrl = canShow ? siteUrl(rel) : "";

  return altUrl ? { primaryUrl: altUrl, fallbackUrl } : { primaryUrl: fallbackUrl, fallbackUrl: "" };
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
  const hasAlt = Boolean(preferredThumbAltVariant(it));
  heroThumbFallback.textContent = hasAlt ? "イラストサムネ読み込み中…" : "サムネ読み込み中…";

  const { primaryUrl, fallbackUrl } = pickThumbUrls(it);
  const currentId = videoId;
  let triedFallback = false;
  if (!primaryUrl) {
    heroThumbImg.hidden = true;
    heroThumbFallback.hidden = false;
    heroThumbFallback.textContent = "サムネ未（プレビューなし）";
    heroOpenThumb.hidden = true;
    heroOpenThumb.removeAttribute("href");
    heroOpenThumbFallback.hidden = true;
    heroOpenThumbFallback.removeAttribute("href");
    return;
  }

  const altVariant = preferredThumbAltVariant(it);
  heroOpenThumb.hidden = false;
  heroOpenThumb.href = primaryUrl;
  heroOpenThumb.textContent = hasAlt ? `${thumbAltVariantLabel(altVariant)}を開く` : "サムネを開く";

  if (fallbackUrl) {
    heroOpenThumbFallback.hidden = false;
    heroOpenThumbFallback.href = fallbackUrl;
    heroOpenThumbFallback.textContent = "通常サムネ（横長）を開く";
  } else {
    heroOpenThumbFallback.hidden = true;
    heroOpenThumbFallback.removeAttribute("href");
  }

  heroThumbImg.onload = () => {
    if (String(selected?.video_id || "").trim() !== currentId) return;
    heroThumbImg.hidden = false;
    heroThumbFallback.hidden = true;
  };
  heroThumbImg.onerror = () => {
    if (String(selected?.video_id || "").trim() !== currentId) return;
    if (!triedFallback && fallbackUrl) {
      triedFallback = true;
      heroThumbImg.src = fallbackUrl;
      return;
    }
    heroThumbImg.hidden = true;
    heroThumbFallback.hidden = false;
    heroThumbFallback.textContent = "サムネ未（プレビューなし）";
  };
  heroThumbImg.alt = `${videoId} thumbnail`;
  heroThumbImg.src = primaryUrl;
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

function sharePathForCurrentView(it) {
  const ch = normalizeChannelParam(it?.channel || "");
  const v = normalizeVideoParam(it?.video || "");
  if (!ch || !v) return "";
  const view = normalizeView(currentView);
  let path = `/ep/${ch}/${v}/`;
  if (view && view !== "script") path += `${view}/`;
  return path;
}

function updateMetaSub(it) {
  const ch = normalizeChannelParam(it?.channel || "");
  const v = normalizeVideoParam(it?.video || "");
  if (!ch || !v) {
    metaSub.textContent = "—";
    return;
  }

  const parts = [];

  const planning = effectivePlanning(it);
  const status = cleanText(planning?.status || planning?.["ステータス"] || planning?.state);
  if (status) parts.push(`企画:${status}`);

  const snap = snapshotByChannel.get(ch) || null;
  const planN = Number(snap?.planning_count) || 0;
  const snapScripts = Number(snap?.scripts_count);
  const scriptsN = snap && Number.isFinite(snapScripts) ? snapScripts : 0;
  if (planN) parts.push(`進捗:台本 ${scriptsN}/${planN}`);

  const altVariants = thumbAltVariantsForEpisode(it);
  if (altVariants.length) parts.push(`イラスト:${altVariants.join(",")}`);

  const share = sharePathForCurrentView(it);
  if (share) parts.push(`共有:${share}`);

  metaSub.textContent = parts.join(" · ") || "—";
}

function cleanText(raw) {
  return normalizeNewlines(String(raw || "")).trim();
}

function effectivePlanning(it) {
  const p = it?.planning;
  if (p && typeof p === "object") return p;
  const videoId = String(it?.video_id || "").trim();
  const ep = videoId ? snapshotEpisodeByVideoId.get(videoId) : null;
  const p2 = ep?.planning;
  return p2 && typeof p2 === "object" ? p2 : {};
}

function planningText(planning, keys) {
  const p = planning && typeof planning === "object" ? planning : {};
  for (const k of keys) {
    if (!k) continue;
    const v = p[k];
    const t = cleanText(v);
    if (t) return t;
  }
  return "";
}

function buildEpisodeDescription(it) {
  const planning = effectivePlanning(it);
  const lead = planningText(planning, ["description_lead", "説明文_リード"]);
  const body = planningText(planning, ["description_body", "説明文_この動画でわかること", "説明文_本文"]);
  return [lead, body].filter(Boolean).join("\n");
}

function buildFullDescription(it) {
  const episodeDesc = buildEpisodeDescription(it);
  const channelDesc = buildChannelDescription(it?.channel);
  if (episodeDesc && channelDesc) return `${episodeDesc}\n\n${channelDesc}`.trim();
  return (episodeDesc || channelDesc || "").trim();
}

function buildChannelDescription(channelId) {
  const ch = String(channelId || "").trim();
  if (!ch) return "";
  const meta = channelMetaById.get(ch) || {};
  return cleanText(meta?.youtube_description || meta?.description || "");
}

function uniquePush(list, raw) {
  const s = cleanText(raw);
  if (!s) return;
  if (!list.includes(s)) list.push(s);
}

function buildYtTags(it) {
  const out = [];

  const planning = effectivePlanning(it);
  const tagsRaw = Array.isArray(planning?.tags) ? planning.tags : [];
  if (tagsRaw.length) {
    for (const t of tagsRaw) uniquePush(out, t);
  } else {
    uniquePush(out, planning?.main_tag);
    uniquePush(out, planning?.sub_tag);
    uniquePush(out, planning["悩みタグ_メイン"]);
    uniquePush(out, planning["悩みタグ_サブ"]);
  }

  const ch = String(it?.channel || "").trim();
  const meta = channelMetaById.get(ch) || {};
  const defaultsRaw = Array.isArray(meta?.default_tags) ? meta.default_tags : meta?.default_tags ? [meta.default_tags] : [];
  for (const t of defaultsRaw) uniquePush(out, t);

  return out;
}

function renderYoutubeMeta(it) {
  const title = cleanText(it?.title);
  const tags = buildYtTags(it);
  const episodeDesc = buildEpisodeDescription(it);
  const channelDesc = buildChannelDescription(it?.channel);
  const fullDesc = buildFullDescription(it);

  const ch = normalizeChannelParam(it?.channel || "");
  const meta = ch ? channelMetaById.get(ch) || {} : {};
  const yt = meta?.youtube || {};
  const branding = meta?.branding || {};

  function setLink(el, url) {
    const u = String(url || "").trim();
    if (!u) {
      el.hidden = true;
      el.removeAttribute("href");
      return;
    }
    el.hidden = false;
    el.href = u;
  }

  const channelName = pickChannelDisplayName(meta) || String(meta?.name || "").trim() || ch;
  const handle = cleanText(yt?.handle || branding?.handle || "");
  const youtubeChannelId = cleanText(yt?.channel_id || "");
  const youtubeUrl = cleanText(yt?.url || branding?.url || (handle ? `https://www.youtube.com/${handle.replace(/^@?/, "@")}` : ""));
  const youtubeStudioUrl = youtubeChannelId ? `https://studio.youtube.com/channel/${youtubeChannelId}/videos` : "";
  const voiceConfigPath = cleanText(meta?.production_sources?.voice_config_path);
  const promptPath = cleanText(meta?.template_path);
  const voiceConfigUrl = voiceConfigPath ? (gitTreeBase ? `${gitTreeBase}${voiceConfigPath}` : joinUrl(rawBase, voiceConfigPath)) : "";
  const promptUrl = promptPath ? (gitTreeBase ? `${gitTreeBase}${promptPath}` : joinUrl(rawBase, promptPath)) : "";

  ytChannelInfoPre.textContent =
    (channelName ? `${channelName}${ch ? ` (${ch})` : ""}` : ch) +
    (handle ? `\nhandle: ${handle}` : "") +
    (youtubeChannelId ? `\nyoutube_channel_id: ${youtubeChannelId}` : "") +
    (voiceConfigPath ? `\nvoice_config: ${voiceConfigPath}` : "") +
    (promptPath ? `\nscript_prompt: ${promptPath}` : "") ||
    "—";
  setLink(openYtStudio, youtubeStudioUrl);
  setLink(openYtChannel, youtubeUrl);
  setLink(openVoiceConfig, encodeURI(voiceConfigUrl));
  setLink(openChannelPrompt, encodeURI(promptUrl));

  ytTitlePre.textContent = title || "（未設定）";
  ytTagsPre.textContent = tags.length ? tags.join(", ") : "（未設定）";
  ytFullDescPre.textContent = fullDesc || "（未設定）";
  ytEpisodeDescPre.textContent = episodeDesc || "（未設定）";
  ytChannelDescPre.textContent = channelDesc || "（未設定）";

  copyYtTitle.disabled = !title;
  copyYtTags.disabled = !tags.length;
  copyYtFullDesc.disabled = !fullDesc;
  copyYtEpisodeDesc.disabled = !episodeDesc;
  copyYtChannelDesc.disabled = !channelDesc;
}

function clearYoutubeMeta() {
  renderYoutubeMeta(null);
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
  } else if (scriptState === "missing") {
    setBadge(badgeScript, "未", "neutral");
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
  if (!EMBED_MODE) {
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
  if (selected && String(selected?.channel || "").trim() && String(selected?.video || "").trim()) {
    updateMetaSub(selected);
  }
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
  thumbBody.prepend(det);
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
  title.textContent = "通常サムネ（横長・プレビュー）";
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

function appendThumbAltPreviews(it) {
  const variants = thumbAltVariantsForEpisode(it);
  if (!variants.length) return;

  const ch = normalizeChannelParam(it?.channel || "");
  const v = normalizeVideoParam(it?.video || "");
  if (!ch || !v) return;

  const det = document.createElement("details");
  det.className = "details";
  det.open = true;

  const sum = document.createElement("summary");
  sum.textContent =
    variants.length === 1
      ? `${thumbAltVariantLabel(variants[0]) || "イラストサムネ"}`
      : `イラストサムネ（${variants.map((v) => thumbAltVariantLabel(v) || v).join(" / ")}）`;
  det.appendChild(sum);

  const body = document.createElement("div");
  body.className = "details__body";

  for (const variant of variants) {
    const rel = `media/thumbs_alt/${variant}/${ch}/${v}.jpg`;
    const url = siteUrl(rel);

    const card = document.createElement("div");
    card.className = "thumb-selected";

    const title = document.createElement("div");
    title.className = "thumb-selected__title";
    title.textContent = thumbAltVariantLabel(variant) || `イラストサムネ（${variant}）`;
    card.appendChild(title);

    const tools = document.createElement("div");
    tools.className = "video-images-tools";

    const epUrl = encodeURI(`./ep/${ch}/${v}/thumb/${variant}/`);
    const galUrl = encodeURI(`./ep/${ch}/thumb/${variant}/`);

    const aEp = document.createElement("a");
    aEp.className = "btn btn--ghost";
    aEp.target = "_blank";
    aEp.rel = "noreferrer";
    aEp.href = epUrl;
    aEp.textContent = "共有ページで見る（/ep）";
    tools.appendChild(aEp);

    const aGal = document.createElement("a");
    aGal.className = "btn btn--ghost";
    aGal.target = "_blank";
    aGal.rel = "noreferrer";
    aGal.href = galUrl;
    aGal.textContent = "一覧で見る（30枚など）";
    tools.appendChild(aGal);

    const aImg = document.createElement("a");
    aImg.className = "btn btn--ghost";
    aImg.target = "_blank";
    aImg.rel = "noreferrer";
    aImg.href = url;
    aImg.textContent = "画像を開く（DL）";
    tools.appendChild(aImg);

    card.appendChild(tools);

    const previewWrap = document.createElement("div");
    previewWrap.className = "thumb-selected__preview";

    const a = document.createElement("a");
    a.className = "thumb-selected__imglink";
    a.target = "_blank";
    a.rel = "noreferrer";
    a.href = url;

    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = `${ch}-${v} thumb_alt:${variant}`;
    img.src = url;
    img.onerror = () => {
      try {
        img.remove();
      } catch (_err) {
        // ignore
      }
      const msg = document.createElement("div");
      msg.className = "thumb-selected__message muted";
      msg.textContent = `画像が見つかりません: ${rel}`;
      previewWrap.appendChild(msg);
    };

    a.appendChild(img);
    previewWrap.appendChild(a);
    card.appendChild(previewWrap);

    body.appendChild(card);
  }

  det.appendChild(body);
  thumbBody.appendChild(det);
}

async function loadThumb(it) {
  const currentId = it?.video_id || "";
  thumbState = "loading";
  updateBadges();
  thumbBody.textContent = "読み込み中…";
  try {
    const [map] = await Promise.all([loadThumbProjects(), loadThumbIndex(), loadThumbAltIndex()]);
    if (selected?.video_id !== currentId) return;
    const proj = map.get(currentId);
    if (!proj) {
      renderThumbPreviewOnly(it);
      appendThumbAltPreviews(it);
      const hasAlt = thumbAltVariantsForEpisode(it).length > 0;
      const idx = thumbIndexByVideoId.get(currentId);
      if (hasAlt) {
        thumbState = "ok";
      } else if (idx && idx.preview_exists === true) {
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
    appendThumbAltPreviews(it);
    const variants = Array.isArray(proj?.variants) ? proj.variants : [];
    const selectedId = String(proj?.selected_variant_id || "").trim();
    const selectedVar = variants.find((v) => String(v?.id || "").trim() === selectedId) || null;
    const rawUrl = String(selectedVar?.image_url || "").trim();
    const hasRemote = rawUrl && /^https?:\/\//.test(rawUrl);
    const idx = thumbIndexByVideoId.get(currentId);
    const hasPublished = Boolean(idx?.preview_exists);
    const hasAlt = thumbAltVariantsForEpisode(it).length > 0;
    thumbState = hasRemote || hasPublished || hasAlt ? "ok" : "partial";
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

function getChannelFilterQuery() {
  return String(channelFilter?.value || "")
    .trim()
    .toLowerCase();
}

function filterChannelsForChips(channels, activeChannel) {
  const q = getChannelFilterQuery();
  if (!q) return channels;

  const out = [];
  for (const ch of channels) {
    const chId = String(ch || "").trim();
    if (!chId) continue;
    const label = channelLabel(chId).toLowerCase();
    const short = channelShortName(chId).toLowerCase();
    if (chId.toLowerCase().includes(q) || label.includes(q) || short.includes(q)) {
      out.push(chId);
    }
  }

  const active = String(activeChannel || "").trim();
  if (active && !out.includes(active) && channels.includes(active)) out.unshift(active);
  return out;
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
    const snap = snapshotByChannel.get(String(ch)) || null;
    const plan = Number(snap?.planning_count) || 0;
    const snapScripts = Number(snap?.scripts_count);
    const scriptsN = snap && Number.isFinite(snapScripts) ? snapScripts : (grouped.get(String(ch)) || []).length;
    code.textContent = plan > 0 ? `${ch} · ${scriptsN}/${plan}` : scriptsN > 0 ? `${ch} · ${scriptsN}` : String(ch);
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
  const snap = snapshotByChannel.get(ch) || null;
  const plan = Number(snap?.planning_count) || 0;
  const snapScripts = Number(snap?.scripts_count);
  const scriptsN = snap && Number.isFinite(snapScripts) ? snapScripts : (grouped.get(ch) || []).length;
  metaTitle.textContent = ch ? (plan > 0 ? `${channelLabel(ch)}（台本 ${scriptsN}/${plan}）` : `${channelLabel(ch)}（台本なし）`) : "—";
  metaSub.textContent = ch && plan > 0 ? `進捗:台本 ${scriptsN}/${plan}` : "—";
  metaPath.textContent = "—";
  renderYoutubeMeta({ channel: ch });
  openRaw.removeAttribute("href");
  openAssetPack.removeAttribute("href");
  contentPre.textContent =
    plan > 0
      ? `このチャンネルには台本がありません（${scriptsN}/${plan}）。\n→ 企画/進捗は snapshot を確認してください。`
      : "このチャンネルには台本がありません。";
  initialChannelWanted = ch;
  initialVideoWanted = "";
  updateBrowseSummary();
  persistUiState();
}

function renderChannels() {
  const set = new Set();
  for (const ch of grouped.keys()) set.add(ch);
  for (const ch of channelMetaById.keys()) set.add(ch);
  for (const ch of snapshotByChannel.keys()) set.add(ch);
  const channels = Array.from(set).sort((a, b) => {
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
    const snap = snapshotByChannel.get(String(ch)) || null;
    const plan = Number(snap?.planning_count) || 0;
    const snapScripts = Number(snap?.scripts_count);
    const scriptsN = snap && Number.isFinite(snapScripts) ? snapScripts : (grouped.get(String(ch)) || []).length;
    opt.textContent = plan > 0 ? `${channelLabel(ch)} · ${scriptsN}/${plan}` : channelLabel(ch);
    channelSelect.appendChild(opt);
  }
  const active = channelSelect.value || channels[0] || "";
  renderChannelChips(filterChannelsForChips(channels, active), active);
}

function _snapshotEpisodeToPseudoItem(ep, fallbackChannel) {
  const parsedVid = parseVideoIdParam(String(ep?.video_id || "").trim());
  const ch = normalizeChannelParam(ep?.channel) || normalizeChannelParam(fallbackChannel) || parsedVid?.channel || "";
  const v = normalizeVideoParam(ep?.video) || (parsedVid?.video ? normalizeVideoParam(parsedVid.video) : "");
  const videoId = ch && v ? `${ch}-${v}` : String(ep?.video_id || "").trim();
  return {
    channel: ch || normalizeChannelParam(fallbackChannel) || "",
    video: v,
    video_id: videoId,
    title: cleanText(ep?.title),
    planning: ep?.planning || {},
    assembled_path: cleanText(ep?.assembled_path),
  };
}

function mergedEpisodesForChannel(channel) {
  const ch = String(channel || "").trim();
  const scripts = grouped.get(ch) || [];
  const snapRaw = snapshotEpisodesByChannel.get(ch);
  const snap = Array.isArray(snapRaw) ? snapRaw : [];
  if (!snap.length) return scripts;

  const byVideo = new Map();
  for (const it of scripts) {
    const vv = String(it?.video || "").trim();
    if (!vv) continue;
    byVideo.set(vv, it);
  }
  for (const ep of snap) {
    const pseudo = _snapshotEpisodeToPseudoItem(ep, ch);
    const vv = String(pseudo?.video || "").trim();
    if (!vv) continue;
    if (!byVideo.has(vv)) byVideo.set(vv, pseudo);
  }
  const out = Array.from(byVideo.values());
  out.sort((a, b) => {
    const na = Number(String(a?.video || "").trim());
    const nb = Number(String(b?.video || "").trim());
    if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return na - nb;
    return String(a?.video || "").localeCompare(String(b?.video || ""));
  });
  return out;
}

function defaultVideoForChannel(channel) {
  const list = mergedEpisodesForChannel(String(channel || ""));
  if (!list.length) return null;
  return isNarrowView() ? (list[list.length - 1]?.video ?? null) : (list[0]?.video ?? null);
}

function renderVideos(channel) {
  const list = mergedEpisodesForChannel(channel);
  videoSelect.innerHTML = "";
  for (const it of list) {
    const opt = document.createElement("option");
    opt.value = it.video;
    const hasScript = Boolean(String(it?.assembled_path || "").trim());
    opt.textContent = `${it.video} ${it.title ? "· " + it.title : ""}${hasScript ? "" : "（台本未）"}`.trim();
    videoSelect.appendChild(opt);
  }
  renderVideoList(channel, videoSelect.value || "");
}

function renderVideoList(channel, activeVideo) {
  const list0 = mergedEpisodesForChannel(channel);
  const active = String(activeVideo || "").trim() || String(videoSelect.value || "").trim();
  videoList.innerHTML = "";

  if (!list0.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    const snap = snapshotByChannel.get(String(channel || "").trim()) || null;
    const plan = Number(snap?.planning_count) || 0;
    const snapScripts = Number(snap?.scripts_count);
    const scriptsN = snap && Number.isFinite(snapScripts) ? snapScripts : 0;
    if (plan > 0) {
      empty.textContent = `企画を読み込み中…（台本 ${scriptsN}/${plan}）`;
      void (async () => {
        const ch = normalizeChannelParam(channel);
        if (!ch) return;
        await loadSnapshotChannel(ch);
        if (String(channelSelect.value || "").trim() !== ch) return;
        renderVideos(ch);
        renderVideoList(ch, active);
      })();
    } else {
      empty.textContent = "このチャンネルには台本がありません。";
    }
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
      const { primaryUrl, fallbackUrl } = pickThumbUrls(it);
      const thumbUrl = primaryUrl;
      let triedFallback = false;

      if (thumbUrl) {
        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = `${videoId} thumb`;
        img.src = thumbUrl;
        img.onerror = () => {
          if (!triedFallback && fallbackUrl) {
            triedFallback = true;
            img.src = fallbackUrl;
            return;
          }
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

      const hasScript = Boolean(String(it?.assembled_path || "").trim());
      badges.appendChild(makeMiniBadge(hasScript ? "台本✓" : "台本未", hasScript ? "ok" : "neutral"));

      const thumbIdx = vid ? thumbIndexByVideoId.get(vid) : null;
      if (thumbIdx && thumbIdx.preview_exists === true) {
        badges.appendChild(makeMiniBadge("サムネ✓", "ok"));
      } else if (thumbIdx && thumbIdx.preview_exists === false) {
        badges.appendChild(makeMiniBadge("サムネ未", "bad"));
      } else {
        badges.appendChild(makeMiniBadge("サムネ?", "neutral"));
      }

      const altVariants = thumbAltVariantsForEpisode(it);
      if (altVariants.length) {
        badges.appendChild(makeMiniBadge(`イラスト`, "warn"));
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
  const ch = String(channel || "").trim();
  const v = normalizeVideoParam(video);
  const list = grouped.get(ch) || [];
  const found = list.find((it) => String(it?.video || "").trim() === v) || null;
  if (found) return found;
  const vid = ch && v ? `${ch}-${v}` : "";
  const ep = vid ? snapshotEpisodeByVideoId.get(vid) : null;
  return ep ? _snapshotEpisodeToPseudoItem(ep, ch) : null;
}

function _sortedVideosForChannel(channel) {
  const ch = normalizeChannelParam(channel);
  if (!ch) return [];

  const out = [];
  const seen = new Set();

  const snap = snapshotEpisodesByChannel.get(ch);
  if (Array.isArray(snap) && snap.length) {
    for (const ep of snap) {
      const v = normalizeVideoParam(ep?.video || "");
      if (!v || seen.has(v)) continue;
      seen.add(v);
      out.push(v);
    }
  } else {
    const list = grouped.get(ch) || [];
    for (const it of list) {
      const v = normalizeVideoParam(it?.video || "");
      if (!v || seen.has(v)) continue;
      seen.add(v);
      out.push(v);
    }
  }

  out.sort((a, b) => {
    const na = Number(a);
    const nb = Number(b);
    if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
    return String(a).localeCompare(String(b));
  });
  return out;
}

function _setContentNavButton(btn, channel, video) {
  const ch = normalizeChannelParam(channel);
  const v = normalizeVideoParam(video);
  if (!ch || !v) {
    btn.disabled = true;
    delete btn.dataset.channel;
    delete btn.dataset.video;
    btn.title = "";
    return;
  }
  const it = findItem(ch, v);
  const vid = `${ch}-${v}`;
  const title = String(it?.title || "").trim();
  btn.disabled = false;
  btn.dataset.channel = ch;
  btn.dataset.video = v;
  btn.title = title ? `${vid} · ${title}` : vid;
}

function updateContentNav(it) {
  if (EMBED_MODE) {
    contentNav.hidden = true;
    return;
  }
  const ch = normalizeChannelParam(it?.channel || "");
  const v = normalizeVideoParam(it?.video || "");
  if (!ch || !v) {
    contentNav.hidden = true;
    return;
  }
  const list = _sortedVideosForChannel(ch);
  const idx = list.indexOf(v);
  if (idx < 0) {
    contentNav.hidden = true;
    return;
  }
  const prev = idx > 0 ? list[idx - 1] : "";
  const next = idx < list.length - 1 ? list[idx + 1] : "";
  if (!prev && !next) {
    contentNav.hidden = true;
    return;
  }
  contentNav.hidden = false;
  _setContentNavButton(contentPrev, ch, prev);
  _setContentNavButton(contentNext, ch, next);
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
  updateContentNav(it);
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
  const assembledPath = String(it?.assembled_path || "").trim();
  metaPath.textContent = assembledPath || "（台本未生成/未公開）";
  updateMetaSub(it);
  renderYoutubeMeta(it);
  void (async () => {
    const currentId = String(it?.video_id || "").trim();
    const ch = String(it?.channel || "").trim();
    if (!currentId || !ch) return;
    await loadSnapshotChannel(ch);
    if (String(selected?.video_id || "").trim() !== currentId) return;
    const ep = snapshotEpisodeByVideoId.get(currentId);
    if (!ep) return;
    const hydrated = { ...it, title: cleanText(it?.title) || cleanText(ep?.title), planning: ep?.planning || it?.planning };
    renderYoutubeMeta(hydrated);
    updateMetaSub(hydrated);
    updateContentNav(hydrated);
  })();

  if (!assembledPath) {
    openRaw.removeAttribute("href");
    updateAssetPackLink(it);
    contentPre.textContent = [
      "台本本文はまだありません（assembled.md が git にありません）。",
      "- 企画（タイトル/概要欄/タグ）は表示できます。",
      "- 台本を公開したら「索引を再読み込み」してください。",
    ].join("\n");
    scriptState = "missing";
    updateBadges();
    footerMeta.textContent = `generated: ${indexData?.generated_at || "—"} · items: ${items.length.toLocaleString("ja-JP")} · selected: ${it.video_id}`;
    return;
  }

  const url = joinUrl(rawBase, assembledPath);
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
  renderChannelChips(filterChannelsForChips(channelsSorted, channel), channel);
  renderVideos(channel);
  videoSelect.value = video;
  renderVideoList(channel, video);
  updateBrowseSummary();
  // After snapshot hydration, re-render Browse so Planning-only episodes appear (and counts match).
  void (async () => {
    const ch = normalizeChannelParam(channel);
    if (!ch) return;
    await loadSnapshotChannel(ch);
    if (String(channelSelect.value || "").trim() !== ch) return;
    renderVideos(ch);
    videoSelect.value = normalizeVideoParam(video);
    renderVideoList(ch, normalizeVideoParam(video));
  })();
  const it = findItem(channel, video);
  if (it) {
    void loadScript(it);
  } else {
    void (async () => {
      const ch = normalizeChannelParam(channel);
      const v = normalizeVideoParam(video);
      const vid = ch && v ? `${ch}-${v}` : "";
      if (!vid) {
        clearSelectionForChannel(channel);
        return;
      }
      await loadSnapshotChannel(ch);
      const ep = snapshotEpisodeByVideoId.get(vid);
      if (!ep) {
        clearSelectionForChannel(channel);
        return;
      }
      void loadScript({
        channel: ch,
        video: v,
        video_id: vid,
        title: cleanText(ep?.title),
        planning: ep?.planning || {},
        assembled_path: cleanText(ep?.assembled_path),
      });
    })();
  }
}

async function reloadIndex() {
  setLoading(true);
  setControlsDisabled(true);
  try {
    // Force refresh for avatar/channel metadata and asset indexes (browser cache + in-memory cache).
    channelMetaPromise = null;
    channelMetaById = new Map();
    thumbProjectPromise = null;
    thumbProjectByVideoId = new Map();
    thumbIndexPromise = null;
    thumbIndexByVideoId = new Map();
    thumbAltPromise = null;
    thumbAltByChannel = new Map();
    videoImagesIndexPromise = null;
    videoImagesIndexByVideoId = new Map();
    snapshotChannelsPromise = null;
    snapshotByChannel = new Map();
    snapshotEpisodeByVideoId = new Map();
    snapshotEpisodePromiseByChannel = new Map();
    snapshotEpisodesByChannel = new Map();

    const [res] = await Promise.all([
      fetch(siteUrl(INDEX_URL), { cache: "no-store" }),
      loadChannelMeta(),
      loadThumbProjects(),
      loadThumbIndex(),
      loadThumbAltIndex(),
      loadVideoImagesIndex(),
      loadSnapshotChannels(),
    ]);
    if (!res.ok) throw new Error(`index fetch failed: ${res.status} ${res.statusText}`);
    indexData = await res.json();
    items = Array.isArray(indexData?.items) ? indexData.items : [];
    grouped = buildGrouped(items);
    renderChannels();

    // If the page was opened with an explicit deep link, but the script does not exist in index.json,
    // do NOT silently fall back to CH01. Show a clear message instead.
    if (initialUrlHasSelection && initialChannelWanted && initialVideoWanted) {
      const reqCh = String(initialChannelWanted || "").trim();
      const reqV = String(initialVideoWanted || "").trim();
      const reqId = reqCh && reqV ? `${reqCh}-${reqV}` : "";
      const itRequested = reqCh && reqV ? findItem(reqCh, reqV) : null;
      if (reqId && !itRequested) {
        // Try to show Planning metadata even if the script is not in index.json.
        selectItem(reqCh, reqV);
        hideSearchResults();
        if (isNarrowView()) {
          try {
            browseDetails.open = true;
          } catch (_err) {
            // ignore
          }
        }
        return;
      }
    }

    // Default selection: first item, or keep current if possible
    const preferredChannel =
      (initialChannelWanted && grouped.has(initialChannelWanted) ? initialChannelWanted : "") ||
      channelSelect.value ||
      Array.from(grouped.keys())[0];
    if (!preferredChannel) {
      metaTitle.textContent = "index.json が空です";
      metaPath.textContent = "—";
      clearYoutubeMeta();
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
    clearYoutubeMeta();
    contentPre.textContent = String(err);
    footerMeta.textContent = "—";
  } finally {
    setLoading(false);
    setControlsDisabled(false);
  }
}

function setupEvents() {
  $("reloadIndex").addEventListener("click", () => void reloadIndex());
  contentPrev.addEventListener("click", () => {
    const ch = String(contentPrev.dataset.channel || "").trim();
    const v = String(contentPrev.dataset.video || "").trim();
    if (ch && v) selectItem(ch, v);
  });
  contentNext.addEventListener("click", () => {
    const ch = String(contentNext.dataset.channel || "").trim();
    const v = String(contentNext.dataset.video || "").trim();
    if (ch && v) selectItem(ch, v);
  });

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
    renderChannelChips(filterChannelsForChips(channelsSorted, ch), ch);
    const fast = (grouped.get(String(ch)) || []).slice();
    const fastVideo = fast.length ? (isNarrowView() ? fast[fast.length - 1]?.video : fast[0]?.video) : null;
    if (fastVideo) {
      selectItem(ch, fastVideo);
    } else {
      clearSelectionForChannel(ch);
    }
    void (async () => {
      const norm = normalizeChannelParam(ch);
      if (!norm) return;
      await loadSnapshotChannel(norm);
      if (String(channelSelect.value || "").trim() !== norm) return;
      if (!fastVideo && (!selected || String(selected?.channel || "").trim() !== norm)) {
        const dv = defaultVideoForChannel(norm);
        if (dv) selectItem(norm, dv);
        return;
      }
      renderVideos(norm);
      renderVideoList(norm, selected?.video || fastVideo || "");
    })();
  });

  channelFilter?.addEventListener("input", () => {
    const ch = channelSelect.value || channelsSorted[0] || "";
    renderChannelChips(filterChannelsForChips(channelsSorted, ch), ch);
  });

  channelFilter?.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    channelFilter.value = "";
    const ch = channelSelect.value || channelsSorted[0] || "";
    renderChannelChips(filterChannelsForChips(channelsSorted, ch), ch);
  });

  videoSelect.addEventListener("change", () => {
    const ch = channelSelect.value;
    const video = videoSelect.value;
    selectItem(ch, video);
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
      hideSearchResults();
      selectItem(parsed.channel, parsed.video);
      closeBrowseIfNarrow();
      return;
    }

    // If only a channel is provided (e.g. CH27), jump to that channel's default/planned video.
    const chOnly = normalizeChannelParam(raw);
    if (chOnly && /^CH\d{2}$/.test(chOnly)) {
      hideSearchResults();
      void (async () => {
        await loadSnapshotChannel(chOnly);
        const video = defaultVideoForChannel(chOnly);
        if (video) {
          selectItem(chOnly, video);
        } else {
          clearSelectionForChannel(chOnly);
        }
        closeBrowseIfNarrow();
      })();
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

  copyYtTitle.addEventListener("click", async () => {
    const text = cleanText(selected?.title);
    if (!text) return;
    const ok = await copyText(text);
    setCopyStatus(ok ? "コピーしました → YouTube Studio の「タイトル」に貼り付け" : "コピーに失敗しました", !ok);
  });

  copyYtTags.addEventListener("click", async () => {
    const tags = buildYtTags(selected);
    if (!tags.length) return;
    const ok = await copyText(tags.join(", "));
    setCopyStatus(ok ? "コピーしました → YouTube Studio の「タグ」に貼り付け" : "コピーに失敗しました", !ok);
  });

  copyYtFullDesc.addEventListener("click", async () => {
    const text = buildFullDescription(selected);
    if (!text) return;
    const ok = await copyText(text);
    setCopyStatus(ok ? "コピーしました → YouTube Studio の「説明」に貼り付け" : "コピーに失敗しました", !ok);
  });

  copyYtEpisodeDesc.addEventListener("click", async () => {
    const text = buildEpisodeDescription(selected);
    if (!text) return;
    const ok = await copyText(text);
    setCopyStatus(ok ? "コピーしました（この動画だけ）→ YouTube Studio の「説明」に貼り付け" : "コピーに失敗しました", !ok);
  });

  copyYtChannelDesc.addEventListener("click", async () => {
    const text = buildChannelDescription(selected?.channel);
    if (!text) return;
    const ok = await copyText(text);
    setCopyStatus(ok ? "コピーしました（定型）→ YouTube Studio の「説明」に追記" : "コピーに失敗しました", !ok);
  });

  $("copyPath").addEventListener("click", async () => {
    const text = selected?.assembled_path || "";
    if (!text) return;
    const ok = await copyText(text);
    setCopyStatus(ok ? "パスをコピーしました" : "コピーに失敗しました", !ok);
  });

  $("copyLink").addEventListener("click", async () => {
    let url = String(window.location.href || "").trim();
    try {
      const ch = normalizeChannelParam(selected?.channel || "");
      const v = normalizeVideoParam(selected?.video || "");
      if (ch && v) {
        const base = new URL(".", window.location.href);
        const view = normalizeView(currentView);
        let path = `ep/${ch}/${v}/`;
        if (view && view !== "script") path += `${view}/`;
        url = new URL(path, base).href;
      }
    } catch (_err) {
      // ignore (fallback to current URL)
    }
    if (!url) return;
    const ok = await copyText(url);
    setCopyStatus(ok ? "リンクをコピーしました" : "コピーに失敗しました", !ok);
  });

  copyNoSep.addEventListener("click", async () => {
    const cleaned = loadedNoSepText.trim() ? loadedNoSepText : stripPauseSeparators(loadedText);
    if (!cleaned.trim()) {
      setCopyStatus("台本が空です", true);
      return;
    }
    const ok = await copyText(cleaned);
    setCopyStatus(ok ? "コピーしました（---なし）" : "コピーに失敗しました（分割コピーを試してください）", !ok);
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
clearYoutubeMeta();
void reloadIndex();
