/* eslint-disable no-console */

const INDEX_PATH = "gh_releases_archive/index/latest.json";
const SSOT_DOC_PATH = "ssot/ops/OPS_GH_RELEASES_ARCHIVE.md";
const MANIFEST_PATH = "gh_releases_archive/manifest/manifest.jsonl";

function $(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element: ${id}`);
  return el;
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

function resolveBranch() {
  try {
    const params = new URLSearchParams(window.location.search);
    return String(params.get("branch") || "main").trim() || "main";
  } catch (_err) {
    return "main";
  }
}

function resolveRawBase() {
  try {
    const params = new URLSearchParams(window.location.search);
    const rawBaseOverride = String(params.get("rawBase") || "").trim();
    if (rawBaseOverride) return rawBaseOverride.replace(/\/+$/, "") + "/";
  } catch (_err) {
    // ignore
  }
  const guessed = guessGitHubRepoFromPages();
  if (!guessed) return "";
  const branch = resolveBranch();
  return `https://raw.githubusercontent.com/${guessed.owner}/${guessed.repo}/${branch}/`;
}

function resolveGitTreeBase() {
  try {
    const params = new URLSearchParams(window.location.search);
    const baseOverride = String(params.get("treeBase") || "").trim();
    if (baseOverride) return baseOverride.replace(/\/+$/, "") + "/";
  } catch (_err) {
    // ignore
  }
  const rawBase = resolveRawBase();
  const parsed = parseGitHubRepoFromRawBase(rawBase);
  if (!parsed) return "";
  return `https://github.com/${parsed.owner}/${parsed.repo}/blob/${parsed.branch}/`;
}

function resolveReleaseRoot() {
  const rawBase = resolveRawBase();
  const parsed = parseGitHubRepoFromRawBase(rawBase);
  if (!parsed) return "";
  return `https://github.com/${parsed.owner}/${parsed.repo}/releases`;
}

function bytesLabel(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v <= 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let x = v;
  let idx = 0;
  while (x >= 1024 && idx < units.length - 1) {
    x /= 1024;
    idx += 1;
  }
  return `${x.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

function normalizeText(s) {
  return String(s || "").trim();
}

async function copyText(text) {
  const s = String(text || "");
  if (!s.trim()) return false;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(s);
      return true;
    } catch (_err) {
      // fall through
    }
  }
  const ta = document.createElement("textarea");
  ta.value = s;
  ta.style.position = "fixed";
  ta.style.top = "-1000px";
  ta.style.left = "-1000px";
  ta.setAttribute("readonly", "");
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, ta.value.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch (_err) {
    ok = false;
  }
  document.body.removeChild(ta);
  return ok;
}

function resolveRepoSlug() {
  const rawBase = resolveRawBase();
  const parsed = parseGitHubRepoFromRawBase(rawBase);
  if (parsed) return `${parsed.owner}/${parsed.repo}`;
  const guessed = guessGitHubRepoFromPages();
  if (guessed) return `${guessed.owner}/${guessed.repo}`;
  return "";
}

function matchQuery(item, query) {
  const q = normalizeText(query).toLowerCase();
  if (!q) return true;
  const hay = [
    item.archive_id,
    item.created_at,
    item.release_tag,
    item.original_name,
    item.note,
    Array.isArray(item.tags) ? item.tags.join(",") : "",
  ]
    .map((x) => normalizeText(x).toLowerCase())
    .join(" ");
  return hay.includes(q);
}

function matchTag(item, tag) {
  const t = normalizeText(tag);
  if (!t) return true;
  const tags = Array.isArray(item.tags) ? item.tags.map((x) => normalizeText(x)) : [];
  return tags.includes(t);
}

function render(items) {
  const listEl = $("list");
  const emptyEl = $("empty");
  listEl.innerHTML = "";
  const repoSlug = resolveRepoSlug();

  if (!items.length) {
    emptyEl.hidden = false;
    $("countLabel").textContent = "0 件";
    return;
  }
  emptyEl.hidden = true;
  $("countLabel").textContent = `${items.length.toLocaleString("ja-JP")} 件`;

  for (const it of items) {
    const archiveId = normalizeText(it.archive_id) || "-";
    const createdAt = normalizeText(it.created_at) || "-";
    const releaseTag = normalizeText(it.release_tag) || "-";
    const name = normalizeText(it.original_name) || "-";
    const size = bytesLabel(it.original_size_bytes);
    const sha = normalizeText(it.original_sha256);
    const note = normalizeText(it.note);
    const tags = Array.isArray(it.tags) ? it.tags.map((x) => normalizeText(x)).filter(Boolean) : [];

    const item = document.createElement("div");
    item.className = "item";

    const top = document.createElement("div");
    top.className = "item__top";
    const id = document.createElement("div");
    id.className = "item__id";
    id.textContent = archiveId;

    const meta = document.createElement("div");
    meta.className = "item__meta";
    meta.textContent = [createdAt, releaseTag !== "-" ? `tag:${releaseTag}` : "", size !== "-" ? `size:${size}` : ""]
      .filter(Boolean)
      .join(" · ");

    top.appendChild(id);
    top.appendChild(meta);

    const nameEl = document.createElement("div");
    nameEl.className = "item__name";
    nameEl.innerHTML =
      `<div><code>${escapeHtml(name)}</code></div>` +
      (note ? `<div class="muted small" style="margin-top:6px">${escapeHtml(note)}</div>` : "") +
      (sha ? `<div class="muted small" style="margin-top:6px">sha256: <code>${escapeHtml(sha.slice(0, 16))}…</code></div>` : "");

    const badges = document.createElement("div");
    badges.className = "badges";
    for (const t of tags.slice(0, 12)) {
      const b = document.createElement("div");
      b.className = "badge badge--ok";
      b.textContent = t;
      badges.appendChild(b);
    }

    const actions = document.createElement("div");
    actions.className = "item__actions";
    const releaseHref = buildReleaseUrl(releaseTag);
    const openRelease = document.createElement("a");
    openRelease.className = "btn btn--accent";
    openRelease.target = "_blank";
    openRelease.rel = "noreferrer";
    openRelease.href = releaseHref || "#";
    openRelease.textContent = "Release を開く";
    if (!releaseHref) openRelease.setAttribute("aria-disabled", "true");
    actions.appendChild(openRelease);

    const copyPull = document.createElement("button");
    copyPull.className = "btn";
    copyPull.type = "button";
    copyPull.textContent = "復元コマンドをコピー";
    copyPull.addEventListener("click", async () => {
      const outdir = "/tmp/ytm_restore";
      const base = `./ops archive release pull "${archiveId}" --outdir "${outdir}"`;
      const cmd = repoSlug ? `ARCHIVE_REPO="${repoSlug}" ${base}` : base;
      const ok = await copyText(cmd);
      const prev = copyPull.textContent;
      copyPull.textContent = ok ? "コピーしました" : "コピー失敗";
      window.setTimeout(() => {
        copyPull.textContent = prev;
      }, 1200);
    });
    actions.appendChild(copyPull);

    const isTgz = name.endsWith(".tgz") || name.endsWith(".tar.gz");
    if (isTgz && name !== "-") {
      const copyPullExtract = document.createElement("button");
      copyPullExtract.className = "btn";
      copyPullExtract.type = "button";
      copyPullExtract.textContent = "復元+展開コマンドをコピー";
      copyPullExtract.addEventListener("click", async () => {
        const outdir = "/tmp/ytm_restore";
        const filePath = `${outdir}/${name}`;
        const extractDir = `${outdir}/unpacked/${archiveId}`;
        const pull = `./ops archive release pull "${archiveId}" --outdir "${outdir}"`;
        const extract = `mkdir -p "${extractDir}" && tar -xzf "${filePath}" -C "${extractDir}"`;
        const base = `${pull} && ${extract}`;
        const cmd = repoSlug ? `ARCHIVE_REPO="${repoSlug}" ${base}` : base;
        const ok = await copyText(cmd);
        const prev = copyPullExtract.textContent;
        copyPullExtract.textContent = ok ? "コピーしました" : "コピー失敗";
        window.setTimeout(() => {
          copyPullExtract.textContent = prev;
        }, 1200);
      });
      actions.appendChild(copyPullExtract);
    }

    const manifestHref = buildTreeUrl(MANIFEST_PATH);
    const openManifest = document.createElement("a");
    openManifest.className = "btn";
    openManifest.target = "_blank";
    openManifest.rel = "noreferrer";
    openManifest.href = manifestHref || "#";
    openManifest.textContent = "manifest を開く";
    if (!manifestHref) openManifest.setAttribute("aria-disabled", "true");
    actions.appendChild(openManifest);

    item.appendChild(top);
    item.appendChild(nameEl);
    if (tags.length) item.appendChild(badges);
    item.appendChild(actions);
    listEl.appendChild(item);
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

function buildTreeUrl(path) {
  const base = resolveGitTreeBase();
  if (!base) return "";
  return `${base}${String(path || "").replace(/^\/+/, "")}`;
}

function buildRawUrl(path) {
  const base = resolveRawBase();
  if (!base) return "";
  return `${base}${String(path || "").replace(/^\/+/, "")}`;
}

function buildReleaseUrl(releaseTag) {
  const s = normalizeText(releaseTag);
  if (!s || s === "-") return "";
  const rawBase = resolveRawBase();
  const parsed = parseGitHubRepoFromRawBase(rawBase);
  if (!parsed) return "";
  return `https://github.com/${parsed.owner}/${parsed.repo}/releases/tag/${encodeURIComponent(s)}`;
}

async function loadLatest() {
  const statusEl = $("status");
  statusEl.textContent = "読み込み中…";
  const url = buildRawUrl(INDEX_PATH);
  if (!url) {
    statusEl.textContent = "GitHub repo を推定できません（rawBase/branch を指定してください）";
    render([]);
    return [];
  }
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (res.status === 404) {
      statusEl.textContent = "index がありません（まだ書庫が初期化されていない可能性）";
      render([]);
      return [];
    }
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const data = await res.json();
    const items = Array.isArray(data) ? data : [];
    statusEl.textContent = `OK（${items.length.toLocaleString("ja-JP")}件）`;
    return items;
  } catch (err) {
    console.error(err);
    statusEl.textContent = `読み込み失敗: ${String(err)}`;
    render([]);
    return [];
  }
}

let latest = [];

function applyUrlParamsToFilters() {
  try {
    const params = new URLSearchParams(window.location.search);
    const q = String(params.get("q") || "").trim();
    const tag = String(params.get("tag") || "").trim();
    if (q) $("queryInput").value = q;
    if (tag) $("tagInput").value = tag;
  } catch (_err) {
    // ignore
  }
}

function updateUrlFilters(query, tag) {
  try {
    const url = new URL(window.location.href);
    const q = String(query || "").trim();
    const t = String(tag || "").trim();
    if (q) url.searchParams.set("q", q);
    else url.searchParams.delete("q");
    if (t) url.searchParams.set("tag", t);
    else url.searchParams.delete("tag");
    window.history.replaceState(null, "", url.toString());
  } catch (_err) {
    // ignore
  }
}

function applyFilters() {
  const query = $("queryInput").value;
  const tag = $("tagInput").value;
  const filtered = latest.filter((it) => matchQuery(it, query) && matchTag(it, tag));
  render(filtered);
  updateUrlFilters(query, tag);
  $("footerMeta").textContent = `source: ${INDEX_PATH} · shown: ${filtered.length.toLocaleString("ja-JP")} / total: ${latest.length.toLocaleString(
    "ja-JP"
  )}`;
}

async function reload() {
  latest = await loadLatest();
  applyFilters();
}

function setupLinks() {
  const ssotHref = buildTreeUrl(SSOT_DOC_PATH);
  const openSsot = $("openSsot");
  if (ssotHref) openSsot.href = ssotHref;
  else openSsot.setAttribute("aria-disabled", "true");

  const releases = resolveReleaseRoot();
  const openReleaseRoot = $("openReleaseRoot");
  if (releases) openReleaseRoot.href = releases;
  else openReleaseRoot.setAttribute("aria-disabled", "true");

  const manifestHref = buildTreeUrl(MANIFEST_PATH);
  const openManifest = $("openManifest");
  if (manifestHref) openManifest.href = manifestHref;
  else openManifest.setAttribute("aria-disabled", "true");
}

document.addEventListener("DOMContentLoaded", () => {
  setupLinks();
  applyUrlParamsToFilters();
  $("reloadBtn").addEventListener("click", () => void reload());
  $("queryInput").addEventListener("input", () => applyFilters());
  $("tagInput").addEventListener("input", () => applyFilters());
  void reload();
});
