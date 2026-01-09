/* eslint-disable no-console */

function $(id) {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element: ${id}`);
  return el;
}

function escapeAttr(text) {
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

function resolveRepoInfo() {
  const params = new URLSearchParams(window.location.search);
  const branch = params.get("branch") || "main";
  const owner = params.get("owner");
  const repo = params.get("repo");
  if (owner && repo) return { owner, repo, branch };
  const guessed = guessGitHubRepoFromPages();
  if (guessed) return { ...guessed, branch };
  return null;
}

function resolveRawBase() {
  const params = new URLSearchParams(window.location.search);
  const rawBaseOverride = params.get("rawBase");
  if (rawBaseOverride) return rawBaseOverride.replace(/\/+$/, "") + "/";

  const info = resolveRepoInfo();
  if (info) return `https://raw.githubusercontent.com/${info.owner}/${info.repo}/${info.branch}/`;

  // local preview: serve repo root via `python3 -m http.server` and open `/docs/guide/`
  return `${window.location.origin}/`;
}

function resolveGitHubBlobBase() {
  const info = resolveRepoInfo();
  if (!info) return null;
  return `https://github.com/${info.owner}/${info.repo}/blob/${info.branch}/`;
}

function joinUrl(base, path) {
  const safeBase = String(base || "").replace(/\/+$/, "") + "/";
  const safePath = String(path || "").replace(/^\/+/, "");
  return safeBase + safePath;
}

function normalizeDocPath(raw) {
  const s = String(raw || "").trim();
  if (!s) return "";
  if (s.includes("..")) return "";
  if (s.startsWith("/")) return "";
  if (s.startsWith("file:")) return "";
  return s;
}

function isAllowedDocPath(path) {
  const p = String(path || "").trim();
  if (!p) return false;
  if (p === "START_HERE.md") return true;
  if (p.startsWith("ssot/")) return true;
  if (p.startsWith("packages/script_pipeline/channels/") && p.endsWith("/script_prompt.txt")) return true;
  if (p.startsWith("workspaces/planning/personas/") && p.endsWith(".md")) return true;
  return false;
}

function resolveRelativePath(basePath, href) {
  const base = String(basePath || "").trim();
  const rel = String(href || "").trim();
  if (!base || !rel) return "";
  if (rel.startsWith("#")) return rel;
  if (/^[a-z]+:/i.test(rel)) return rel;
  // Resolve as URL path without relying on the current origin.
  const baseDir = base.includes("/") ? base.slice(0, base.lastIndexOf("/") + 1) : "";
  const u = new URL(rel, `https://example.invalid/${baseDir}`);
  const pathname = u.pathname.replace(/^\//, "");
  const hash = String(u.hash || "");
  return pathname + hash;
}

const DOC_SECTIONS = [
  {
    title: "Start",
    items: [
      { title: "START_HERE", path: "START_HERE.md", desc: "入口（最優先）" },
      { title: "DECISIONS", path: "ssot/DECISIONS.md", desc: "意思決定台帳（SSOTトップ）" },
      { title: "SSOT Docs Index", path: "ssot/DOCS_INDEX.md", desc: "SSOTドキュメント索引" },
      { title: "System Overview", path: "ssot/OPS_SYSTEM_OVERVIEW.md", desc: "プロジェクト全貌（概説）" },
    ],
  },
  {
    title: "Flow / Ops",
    items: [
      { title: "Confirmed Pipeline Flow", path: "ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md", desc: "確定フロー（正本）" },
      { title: "Entrypoints Index", path: "ssot/ops/OPS_ENTRYPOINTS_INDEX.md", desc: "CLI/API/入口索引" },
      { title: "Logging Map", path: "ssot/ops/OPS_LOGGING_MAP.md", desc: "ログ配置/証跡" },
      { title: "Agent Playbook", path: "ssot/ops/OPS_AGENT_PLAYBOOK.md", desc: "並列AI運用（lock/削除/SSOT）" },
      { title: "SSOT System Map (how-to)", path: "ssot/ops/OPS_SSOT_SYSTEM_MAP.md", desc: "UIで“全処理”可視化（SSOT=UI）" },
    ],
  },
  {
    title: "Model / LLM",
    items: [
      { title: "Channel→Model Routing", path: "ssot/ops/OPS_CHANNEL_MODEL_ROUTING.md", desc: "どの処理がどのモデルか" },
      { title: "LLM Model Cheatsheet", path: "ssot/ops/OPS_LLM_MODEL_CHEATSHEET.md", desc: "モデル/スロット/固定ルール" },
      { title: "A-text LLM Quality Gate", path: "ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md", desc: "品質ゲート（台本）" },
    ],
  },
  {
    title: "Prompts",
    items: [
      {
        title: "CH01 Prompt (人生の道標)",
        path: "packages/script_pipeline/channels/CH01-人生の道標/script_prompt.txt",
        desc: "CH01はこの1つに固定",
      },
      { title: "CH01 Persona", path: "workspaces/planning/personas/CH01_PERSONA.md", desc: "視聴者/トーンの正本" },
    ],
  },
];

const navToggle = $("navToggle");
const nav = $("nav");
const navOverlay = $("navOverlay");
const navSearch = $("navSearch");
const navList = $("navList");
const reloadDoc = $("reloadDoc");
const portal = $("portal");
const docPane = $("docPane");
const docTitle = $("docTitle");
const docPath = $("docPath");
const docStatus = $("docStatus");
const openRaw = $("openRaw");
const openGitHub = $("openGitHub");
const copyLink = $("copyLink");
const backToPortal = $("backToPortal");
const tocInline = $("tocInline");
const docBody = $("docBody");
const footerMeta = $("footerMeta");

const rawBase = resolveRawBase();
const githubBlobBase = resolveGitHubBlobBase();

let currentDocPath = "";
let currentDocHash = "";
const docCache = new Map();

function setFooter(text) {
  footerMeta.textContent = text || "";
}

function openNav() {
  nav.classList.add("is-open");
  navOverlay.hidden = false;
}

function closeNav() {
  nav.classList.remove("is-open");
  navOverlay.hidden = true;
}

function renderNav(filterText) {
  const q = String(filterText || "").trim().toLowerCase();
  navList.innerHTML = "";

  for (const section of DOC_SECTIONS) {
    const sectionEl = document.createElement("div");
    sectionEl.className = "guide-nav__section";
    sectionEl.textContent = section.title;
    navList.appendChild(sectionEl);

    for (const item of section.items) {
      const hay = `${item.title} ${item.desc || ""} ${item.path}`.toLowerCase();
      if (q && !hay.includes(q)) continue;

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "guide-nav__item";
      btn.dataset.doc = item.path;
      btn.dataset.title = item.title;

      const t = document.createElement("div");
      t.className = "guide-nav__item-title";
      t.textContent = item.title;
      btn.appendChild(t);

      const m = document.createElement("div");
      m.className = "guide-nav__item-meta mono";
      m.textContent = item.path;
      btn.appendChild(m);

      if (item.desc) {
        const d = document.createElement("div");
        d.className = "guide-nav__item-meta";
        d.textContent = item.desc;
        btn.appendChild(d);
      }

      btn.addEventListener("click", () => {
        openDoc(item.path, item.title);
      });
      navList.appendChild(btn);
    }
  }
}

function configureMarked() {
  const markedLib = window.marked;
  if (!markedLib) throw new Error("marked is not loaded");

  const renderer = new markedLib.Renderer();
  renderer.html = () => "";
  renderer.image = (href, title, text) => {
    const alt = String(text || "").trim() || "image";
    const safeHref = String(href || "").trim();
    if (!safeHref) return `<span class=\"muted\">[image: ${escapeAttr(alt)}]</span>`;
    return `<a href=\"${escapeAttr(safeHref)}\" target=\"_blank\" rel=\"noreferrer\">[image: ${escapeAttr(alt)}]</a>`;
  };
  renderer.link = (href, title, text) => {
    const raw = String(href || "").trim();
    const low = raw.toLowerCase();
    const safeHref = !raw || low.startsWith("javascript:") || low.startsWith("data:") ? "#" : raw;
    const t = title ? ` title=\"${escapeAttr(title)}\"` : "";
    const isHash = safeHref.startsWith("#");
    const isRelative = !isHash && !/^[a-z]+:/i.test(safeHref);
    const target = isHash || isRelative ? "" : ' target=\"_blank\" rel=\"noreferrer\"';
    return `<a href=\"${escapeAttr(safeHref)}\"${t}${target}>${text}</a>`;
  };

  markedLib.setOptions({
    renderer,
    gfm: true,
    breaks: false,
    headerIds: true,
    mangle: false,
  });
}

function toDocUrl(path, hash) {
  const base = new URL(window.location.href);
  base.searchParams.set("doc", path);
  base.hash = String(hash || "");
  return base.toString();
}

async function copyTextToClipboard(text) {
  const t = String(text || "");
  if (!t) return false;
  try {
    await navigator.clipboard.writeText(t);
    return true;
  } catch (err) {
    const textarea = document.createElement("textarea");
    textarea.value = t;
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
}

function setPortalVisible(on) {
  portal.hidden = !on;
  docPane.hidden = on;
}

function buildToc() {
  tocInline.innerHTML = "";
  const headings = docBody.querySelectorAll("h1, h2, h3");
  const items = [];
  for (const h of headings) {
    const id = h.id || "";
    const title = (h.textContent || "").trim();
    if (!id || !title) continue;
    const level = Number(String(h.tagName || "").replace(/^H/i, "")) || 2;
    items.push({ id, title, level });
  }
  if (!items.length) {
    tocInline.innerHTML = "<div class=\"muted\">—</div>";
    return;
  }
  for (const it of items) {
    const a = document.createElement("a");
    a.href = `#${it.id}`;
    a.textContent = it.title;
    a.dataset.level = String(it.level);
    tocInline.appendChild(a);
  }
}

function installDocLinkInterceptor() {
  docBody.addEventListener("click", (ev) => {
    const a = ev.target?.closest?.("a");
    if (!a) return;
    const href = a.getAttribute("href") || "";
    if (!href || href.startsWith("#")) return;
    if (/^[a-z]+:/i.test(href)) return;

    ev.preventDefault();
    const resolved = resolveRelativePath(currentDocPath, href);
    const hashIdx = resolved.indexOf("#");
    const resolvedPath = hashIdx >= 0 ? resolved.slice(0, hashIdx) : resolved;
    const resolvedHash = hashIdx >= 0 ? resolved.slice(hashIdx) : "";

    const normalized = normalizeDocPath(resolvedPath);
    if (!normalized) {
      docStatus.textContent = "（リンク解決に失敗しました）";
      return;
    }
    if (!isAllowedDocPath(normalized)) {
      docStatus.textContent = `（閲覧対象外: ${normalized}）`;
      return;
    }
    openDoc(normalized + resolvedHash);
  });
}

async function openDoc(path, titleOverride) {
  const rawRef = String(path || "").trim();
  const hashIdx = rawRef.indexOf("#");
  const refPath = hashIdx >= 0 ? rawRef.slice(0, hashIdx) : rawRef;
  const refHash = hashIdx >= 0 ? rawRef.slice(hashIdx) : "";

  const normalized = normalizeDocPath(refPath);
  if (!normalized || !isAllowedDocPath(normalized)) {
    docStatus.textContent = "（このパスは閲覧対象外）";
    return;
  }

  currentDocPath = normalized;
  currentDocHash = String(refHash || "");
  const title = String(titleOverride || "").trim() || normalized.split("/").slice(-1)[0] || normalized;
  docTitle.textContent = title;
  docPath.textContent = normalized;
  docStatus.textContent = "";

  const rawUrl = joinUrl(rawBase, normalized);
  openRaw.href = rawUrl;

  if (githubBlobBase) {
    openGitHub.href = joinUrl(githubBlobBase, normalized);
    openGitHub.classList.remove("btn--disabled");
  } else {
    openGitHub.href = rawUrl;
  }

  setPortalVisible(false);

  // Close nav on mobile to maximize reading area.
  closeNav();

  let text = docCache.get(normalized);
  if (text === undefined) {
    docStatus.textContent = "読み込み中…";
    try {
      const res = await fetch(rawUrl, { cache: "no-store" });
      if (!res.ok) throw new Error(`fetch failed: ${res.status} ${res.statusText}`);
      text = await res.text();
      docCache.set(normalized, text);
    } catch (err) {
      console.warn("[ssot_guide] doc fetch failed", err);
      docStatus.textContent = "（読み込み失敗）";
      docBody.innerHTML =
        `<p class=\"muted\">読み込みに失敗しました。</p>` +
        `<p><a href=\"${escapeAttr(rawUrl)}\" target=\"_blank\" rel=\"noreferrer\">raw を開く</a></p>`;
      buildToc();
      return;
    }
  }

  docStatus.textContent = "";
  const isMarkdown = normalized.endsWith(".md") || normalized.endsWith(".txt");
  if (isMarkdown) {
    docBody.innerHTML = window.marked.parse(String(text || ""));
  } else {
    docBody.innerHTML = `<pre><code>${escapeAttr(String(text || ""))}</code></pre>`;
  }

  buildToc();
  setFooter(`rawBase: ${rawBase}  |  doc: ${normalized}`);
  if (currentDocHash) {
    const id = currentDocHash.replace(/^#/, "");
    const target = document.getElementById(id);
    if (target) {
      target.scrollIntoView({ behavior: "auto", block: "start" });
    } else {
      window.scrollTo({ top: 0, behavior: "auto" });
    }
  } else {
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  const url = new URL(window.location.href);
  url.searchParams.set("doc", normalized);
  url.hash = currentDocHash;
  window.history.replaceState(null, "", url.toString());
}

function clearDocSelection() {
  currentDocPath = "";
  currentDocHash = "";
  setPortalVisible(true);
  docBody.innerHTML = "";
  tocInline.innerHTML = "";
  const url = new URL(window.location.href);
  url.searchParams.delete("doc");
  url.hash = "";
  window.history.replaceState(null, "", url.toString());
  setFooter(`rawBase: ${rawBase}`);
}

function boot() {
  configureMarked();
  renderNav("");
  installDocLinkInterceptor();

  navToggle.addEventListener("click", () => {
    if (nav.classList.contains("is-open")) closeNav();
    else openNav();
  });
  navOverlay.addEventListener("click", closeNav);

  navSearch.addEventListener("input", (ev) => {
    renderNav(ev.target.value);
  });

  reloadDoc.addEventListener("click", () => {
    if (!currentDocPath) return;
    docCache.delete(currentDocPath);
    openDoc(currentDocPath, docTitle.textContent);
  });

  backToPortal.addEventListener("click", clearDocSelection);

  copyLink.addEventListener("click", async () => {
    if (!currentDocPath) return;
    const url = toDocUrl(currentDocPath, currentDocHash);
    const ok = await copyTextToClipboard(url);
    docStatus.textContent = ok ? "リンクをコピーしました" : "コピーに失敗しました";
    window.setTimeout(() => {
      if (docStatus.textContent.includes("リンク")) docStatus.textContent = "";
    }, 1800);
  });

  document.body.addEventListener("click", (ev) => {
    const el = ev.target?.closest?.("[data-doc]");
    if (!el) return;
    const p = el.getAttribute("data-doc");
    if (!p) return;
    ev.preventDefault();
    openDoc(p);
  });

  // Deep link support.
  const params = new URLSearchParams(window.location.search);
  const initial = normalizeDocPath(params.get("doc") || "");
  const initialHash = String(window.location.hash || "");
  if (initial && isAllowedDocPath(initial)) {
    openDoc(initial + initialHash);
  } else {
    setFooter(`rawBase: ${rawBase}`);
  }
}

boot();
