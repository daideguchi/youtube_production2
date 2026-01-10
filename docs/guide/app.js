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
  if (p === "__OVERVIEW__") return true;
  if (p === "__FLOW__") return true;
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
      { title: "Overview", path: "__OVERVIEW__", desc: "目的/成果物/固定ルール（まずここ）" },
      { title: "Flow Map", path: "__FLOW__", desc: "処理フロー（まずここ）" },
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
const overviewPane = $("overviewPane");
const overviewBody = $("overviewBody");
const copyOverviewLink = $("copyOverviewLink");
const backToPortalFromOverview = $("backToPortalFromOverview");
const flowPane = $("flowPane");
const flowBody = $("flowBody");
const copyFlowLink = $("copyFlowLink");
const backToPortalFromFlow = $("backToPortalFromFlow");
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
  overviewPane.hidden = true;
  flowPane.hidden = true;
  docPane.hidden = on;
}

function setOverviewVisible(on) {
  portal.hidden = on;
  overviewPane.hidden = !on;
  flowPane.hidden = true;
  docPane.hidden = true;
}

function setFlowVisible(on) {
  portal.hidden = on;
  overviewPane.hidden = true;
  flowPane.hidden = !on;
  docPane.hidden = true;
}

function createEl(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined && text !== null) el.textContent = String(text);
  return el;
}

const OVERVIEW_SPEC = {
  updatedAt: "2026-01-10",
  purpose: "YouTube量産の「入力→台本→音声→動画→公開」を、SSOT中心で再現性高く回す。",
  what: [
    { title: "Planning（入力SoT）", desc: "タイトル/要件を確定して、台本生成の入力を作る。" },
    { title: "Script（A-text/台本）", desc: "台本を生成し、品質ゲートで止める/通す。" },
    { title: "TTS（B/voicevox_kana）", desc: "誤読ゼロで止めて直す（音声品質を固定）。" },
    { title: "Video（CapCut）", desc: "音声/SRT/画像素材からドラフトを組む。" },
  ],
  deliverables: [
    { title: "台本（A-text）", path: "workspaces/scripts/CHxx/NNN/content/assembled.md" },
    { title: "音声（TTS）", path: "workspaces/audio/final/<CH>/<NNN>/*" },
    { title: "動画（CapCut draft）", path: "workspaces/video/runs/<run_name>/*" },
  ],
  fixedRules: [
    "SSOT-first: 迷ったらSSOTが正。変更はSSOT→実装の順。",
    "Lock: 触る前に lock（並列衝突防止）。",
    "台本（script_*）は LLM API（Fireworks/DeepSeek）固定。Codex/agent は台本を書かない。",
    "tts_* は AIエージェント（Codex）主担当（THINK/AGENTのpending運用）。codex exec（非対話CLI）とは別物。",
    "モデル/プロバイダの自動ローテ禁止（勝手に切り替えない）。",
  ],
  pmBullets: [
    "要件（固定ルール）は SSOT（DECISIONS / Model Routing）に集約し、実装はそれに追従する。",
    "進捗は PLAN の状態と Slack スレッドで追う（コードの途中状態は lock で衝突防止）。",
    "変更手順: lock → SSOT更新 → 実装 → チェック → push → Slack（この順で固定）。",
  ],
  pmLinks: [
    { title: "Plan Status（Active/Draft/Completed）", path: "ssot/plans/PLAN_STATUS.md" },
    { title: "DECISIONS（今の正解）", path: "ssot/DECISIONS.md" },
    { title: "Agent Playbook（運用ルール）", path: "ssot/ops/OPS_AGENT_PLAYBOOK.md" },
  ],
  shortcuts: [
    { title: "Flow Map（処理フロー）", path: "__FLOW__" },
    { title: "Entrypoints（コマンド入口）", path: "ssot/ops/OPS_ENTRYPOINTS_INDEX.md" },
    { title: "Model Routing（どの処理がどのモデルか）", path: "ssot/ops/OPS_CHANNEL_MODEL_ROUTING.md" },
    { title: "Logging Map（ログ/証跡）", path: "ssot/ops/OPS_LOGGING_MAP.md" },
    { title: "DECISIONS（今の正解）", path: "ssot/DECISIONS.md" },
  ],
  examples: [
    {
      title: "台本runbook（API固定）",
      cmd: "./scripts/with_ytm_env.sh --exec-slot 0 python3 scripts/ops/script_runbook.py new --channel CH06 --video 033 --until script_validation --max-iter 6",
      note: "台本は exec-slot=0（API）で実行（THINK/AGENT/Codexに流さない）。",
    },
    {
      title: "TTS（Codex主担当）",
      cmd: "./scripts/think.sh --tts -- python -m script_pipeline.cli audio --channel CH06 --video 033",
      note: "TTSは pending で止めて、AIエージェント（Codex）が output を作って complete → rerun する。",
    },
    {
      title: "THINK MODE（非台本の保留処理）",
      cmd: "./scripts/think.sh --all-text -- <command> [args...]",
      note: "非scriptのテキスト系だけ。台本生成の入口には使わない。",
    },
  ],
};

function renderOverview() {
  overviewBody.innerHTML = "";

  const hero = createEl("div", "guide-overview__hero");
  hero.appendChild(createEl("div", "guide-overview__kicker", "1分で「目的/全体像/固定ルール」を掴む"));
  hero.appendChild(createEl("div", "guide-overview__purpose", OVERVIEW_SPEC.purpose));

  const grid = createEl("div", "guide-overview__grid");
  for (const row of OVERVIEW_SPEC.what) {
    const card = createEl("div", "guide-overview__card");
    card.appendChild(createEl("div", "guide-overview__card-title", row.title));
    card.appendChild(createEl("div", "guide-overview__card-desc muted", row.desc));
    grid.appendChild(card);
  }
  hero.appendChild(grid);
  overviewBody.appendChild(hero);

  const deliv = createEl("div", "guide-overview__box");
  deliv.appendChild(createEl("div", "guide-overview__box-title", "成果物（アウトプット）"));
  const ul = createEl("ul", "guide-overview__list mono");
  for (const d of OVERVIEW_SPEC.deliverables) {
    ul.appendChild(createEl("li", "", `${d.title}: ${d.path}`));
  }
  deliv.appendChild(ul);
  overviewBody.appendChild(deliv);

  const rules = createEl("div", "guide-overview__box guide-overview__box--warn");
  rules.appendChild(createEl("div", "guide-overview__box-title", "固定ルール（事故防止）"));
  const rUl = createEl("ul", "guide-overview__list");
  for (const r of OVERVIEW_SPEC.fixedRules) rUl.appendChild(createEl("li", "", r));
  rules.appendChild(rUl);
  overviewBody.appendChild(rules);

  const navBox = createEl("div", "guide-overview__box");
  navBox.appendChild(createEl("div", "guide-overview__box-title", "次に見る場所（迷ったらここ）"));
  const links = createEl("div", "guide-overview__links");
  for (const s of OVERVIEW_SPEC.shortcuts) {
    const btn = createEl("button", "btn btn--ghost btn--small");
    btn.type = "button";
    btn.dataset.doc = s.path;
    btn.textContent = s.title;
    links.appendChild(btn);
  }
  navBox.appendChild(links);
  overviewBody.appendChild(navBox);

  const pm = createEl("div", "guide-overview__box");
  pm.appendChild(createEl("div", "guide-overview__box-title", "進捗/要件管理（PM）"));
  const pmUl = createEl("ul", "guide-overview__list");
  for (const r of OVERVIEW_SPEC.pmBullets || []) pmUl.appendChild(createEl("li", "", r));
  pm.appendChild(pmUl);
  const pmLinks = createEl("div", "guide-overview__links");
  for (const s of OVERVIEW_SPEC.pmLinks || []) {
    const btn = createEl("button", "btn btn--ghost btn--small");
    btn.type = "button";
    btn.dataset.doc = s.path;
    btn.textContent = s.title;
    pmLinks.appendChild(btn);
  }
  pm.appendChild(pmLinks);
  overviewBody.appendChild(pm);

  const ex = createEl("div", "guide-overview__box");
  ex.appendChild(createEl("div", "guide-overview__box-title", "実行例（コピペ）"));
  for (const e of OVERVIEW_SPEC.examples) {
    const row = createEl("div", "guide-overview__cmd");
    row.appendChild(createEl("div", "guide-overview__cmd-title", e.title));
    const pre = document.createElement("pre");
    pre.className = "guide-overview__pre mono";
    pre.textContent = String(e.cmd || "");
    row.appendChild(pre);
    const btn = createEl("button", "btn btn--ghost btn--small");
    btn.type = "button";
    btn.textContent = "コピー";
    btn.addEventListener("click", async () => {
      const ok = await copyTextToClipboard(String(e.cmd || ""));
      setFooter(ok ? "コマンドをコピーしました" : "コピーに失敗しました");
      window.setTimeout(() => setFooter("doc: __OVERVIEW__"), 1200);
    });
    row.appendChild(btn);
    if (e.note) row.appendChild(createEl("div", "guide-overview__cmd-note muted", e.note));
    ex.appendChild(row);
  }
  overviewBody.appendChild(ex);

  overviewBody.appendChild(createEl("div", "guide-overview__footer muted", `updated: ${OVERVIEW_SPEC.updatedAt}  |  doc: __OVERVIEW__`));
}

function openOverview() {
  currentDocPath = "__OVERVIEW__";
  currentDocHash = "";
  setOverviewVisible(true);
  closeNav();
  renderOverview();

  const url = new URL(window.location.href);
  url.searchParams.set("doc", "__OVERVIEW__");
  url.hash = "";
  window.history.replaceState(null, "", url.toString());
  setFooter("doc: __OVERVIEW__");
  window.scrollTo({ top: 0, behavior: "auto" });
}

const FLOW_SPEC = {
  updatedAt: "2026-01-10",
  purpose: "YouTube量産の「入力→台本→音声→動画→公開」を SSOT中心で再現性高く回す。",
  rails: [
    { title: "SSOT-first", desc: "ルール/運用/フローはSSOTが正。変更はSSOT→実装の順。" },
    { title: "Lock", desc: "複数AI並列運用の衝突防止。触る前に lock を置く。" },
    { title: "No Drift", desc: "勝手なモデル切替/フォールバック禁止。台本はAPI固定。" },
  ],
  steps: [
    {
      n: 1,
      title: "Planning（入力SoT）",
      summary: "動画タイトル/タグ/要件を確定して、台本生成の入力を作る。",
      artifacts: ["workspaces/planning/channels/CHxx.csv", "packages/script_pipeline/channels/CHxx-*/channel_info.json"],
      docs: [
        { title: "入口（START_HERE）", path: "START_HERE.md" },
        { title: "確定フロー（Pipeline）", path: "ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md" },
        { title: "入口索引（Entrypoints）", path: "ssot/ops/OPS_ENTRYPOINTS_INDEX.md" },
      ],
      stop: ["入力不足/欠損があれば Planning を直す（SSOTに戻る）。"],
    },
    {
      n: 2,
      title: "Script Pipeline（A-text/台本）",
      summary: "台本を生成し、品質ゲートで止める/通す。止まったら修正して resume。",
      rails: ["台本（script_*）は Fireworks/DeepSeek 固定", "Codex/AGENT は台本を書かない（遮断済み）"],
      docs: [
        { title: "台本パイプラインSSOT", path: "ssot/ops/OPS_SCRIPT_PIPELINE_SSOT.md" },
        { title: "モデル固定ルール", path: "ssot/ops/OPS_LLM_MODEL_CHEATSHEET.md" },
      ],
      stop: ["quality gate / fact check などで fail → 指摘に沿って修正 → 同じコマンドで resume。"],
    },
    {
      n: 3,
      title: "Audio/TTS（Bテキスト/voicevox_kana）",
      summary: "TTS用の整形/読み監査/VOICEVOX合成。誤読ゼロで止めて直す。",
      rails: ["`tts_*` は AIエージェント（Codex）主担当（pending運用）", "codex exec（非対話CLI）とは別物。TTSはcodex execへ寄せない"],
      docs: [
        { title: "VOICEVOX Reading Reform（SSOT）", path: "ssot/plans/PLAN_OPS_VOICEVOX_READING_REFORM.md" },
        { title: "TTS 手動監査（誤読ゼロ）", path: "ssot/ops/OPS_TTS_MANUAL_READING_AUDIT.md" },
      ],
      stop: ["mismatch 検出→停止→辞書/パッチ修正→再合成（混入を許さない）。"],
    },
    {
      n: 4,
      title: "Video Pipeline（CapCut）",
      summary: "音声/SRT/画像素材から動画を組む（不足があれば止めて補完）。",
      docs: [
        { title: "確定フロー（Pipeline）", path: "ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md" },
        { title: "モデル/実行モード（@xN）", path: "ssot/ops/OPS_CHANNEL_MODEL_ROUTING.md" },
        { title: "ログ配置（証跡）", path: "ssot/ops/OPS_LOGGING_MAP.md" },
      ],
      stop: ["pending（THINK/agent）や素材欠損があれば、該当runbook/ログに従って解消して再実行。"],
    },
    {
      n: 5,
      title: "Publish / Evidence（証跡）",
      summary: "成果物とログを揃えて、いつでも再現できる状態にする。",
      docs: [
        { title: "ログ配置（証跡）", path: "ssot/ops/OPS_LOGGING_MAP.md" },
        { title: "意思決定（DECISIONS）", path: "ssot/DECISIONS.md" },
      ],
      stop: ["迷ったら「どのSoTが正か」を先に確認してから動く。"],
    },
  ],
};

function renderFlow() {
  flowBody.innerHTML = "";

  const hero = createEl("div", "guide-flow__hero");
  hero.appendChild(createEl("div", "guide-flow__kicker", "まずは全体の処理フローだけ掴む"));
  hero.appendChild(createEl("div", "guide-flow__purpose", FLOW_SPEC.purpose));

  const rails = createEl("div", "guide-flow__rails");
  for (const r of FLOW_SPEC.rails) {
    const card = createEl("div", "guide-flow__rail");
    card.appendChild(createEl("div", "guide-flow__rail-title", r.title));
    card.appendChild(createEl("div", "guide-flow__rail-desc muted", r.desc));
    rails.appendChild(card);
  }
  hero.appendChild(rails);
  flowBody.appendChild(hero);

  const stepsWrap = createEl("div", "guide-flow__steps");
  for (const st of FLOW_SPEC.steps) {
    const step = document.createElement("details");
    step.className = "guide-flow__step";
    step.open = false;

    const head = document.createElement("summary");
    head.className = "guide-flow__step-head";
    head.appendChild(createEl("div", "guide-flow__step-num mono", String(st.n)));
    const headMain = createEl("div", "guide-flow__step-head-main");
    headMain.appendChild(createEl("div", "guide-flow__step-title", st.title));
    headMain.appendChild(createEl("div", "guide-flow__step-summary muted", st.summary));
    head.appendChild(headMain);
    head.appendChild(createEl("div", "guide-flow__step-disclosure muted", "詳細"));
    step.appendChild(head);

    const body = createEl("div", "guide-flow__step-body");

    if (st.rails && st.rails.length) {
      const pills = createEl("div", "guide-flow__pills");
      for (const raw of st.rails) {
        pills.appendChild(createEl("div", "guide-flow__pill", raw));
      }
      body.appendChild(pills);
    }

    if (st.artifacts && st.artifacts.length) {
      const box = createEl("div", "guide-flow__box");
      box.appendChild(createEl("div", "guide-flow__box-title", "SoT / artifacts"));
      const ul = createEl("ul", "guide-flow__list");
      for (const a of st.artifacts) ul.appendChild(createEl("li", "", a));
      box.appendChild(ul);
      body.appendChild(box);
    }

    if (st.stop && st.stop.length) {
      const box = createEl("div", "guide-flow__box guide-flow__box--warn");
      box.appendChild(createEl("div", "guide-flow__box-title", "停止条件 / 次にやること"));
      const ul = createEl("ul", "guide-flow__list");
      for (const s of st.stop) ul.appendChild(createEl("li", "", s));
      box.appendChild(ul);
      body.appendChild(box);
    }

    if (st.docs && st.docs.length) {
      const links = createEl("div", "guide-flow__links");
      for (const d of st.docs) {
        const btn = createEl("button", "btn btn--ghost btn--small");
        btn.type = "button";
        btn.dataset.doc = d.path;
        btn.textContent = d.title;
        links.appendChild(btn);
      }
      body.appendChild(links);
    }

    step.appendChild(body);

    stepsWrap.appendChild(step);
  }
  flowBody.appendChild(stepsWrap);

  const footer = createEl("div", "guide-flow__footer muted", `updated: ${FLOW_SPEC.updatedAt}  |  doc: __FLOW__`);
  flowBody.appendChild(footer);
}

function openFlow() {
  currentDocPath = "__FLOW__";
  currentDocHash = "";
  setFlowVisible(true);
  closeNav();
  renderFlow();

  const url = new URL(window.location.href);
  url.searchParams.set("doc", "__FLOW__");
  url.hash = "";
  window.history.replaceState(null, "", url.toString());
  setFooter(`doc: __FLOW__`);
  window.scrollTo({ top: 0, behavior: "auto" });
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
  if (normalized === "__OVERVIEW__") {
    openOverview();
    return;
  }
  if (normalized === "__FLOW__") {
    openFlow();
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
  backToPortalFromOverview.addEventListener("click", clearDocSelection);
  backToPortalFromFlow.addEventListener("click", clearDocSelection);

  copyLink.addEventListener("click", async () => {
    if (!currentDocPath) return;
    const url = toDocUrl(currentDocPath, currentDocHash);
    const ok = await copyTextToClipboard(url);
    docStatus.textContent = ok ? "リンクをコピーしました" : "コピーに失敗しました";
    window.setTimeout(() => {
      if (docStatus.textContent.includes("リンク")) docStatus.textContent = "";
    }, 1800);
  });

  copyOverviewLink.addEventListener("click", async () => {
    const url = toDocUrl("__OVERVIEW__", "");
    const ok = await copyTextToClipboard(url);
    setFooter(ok ? "リンクをコピーしました" : "コピーに失敗しました");
    window.setTimeout(() => {
      if (footerMeta.textContent.includes("リンク")) setFooter("doc: __OVERVIEW__");
    }, 1800);
  });

  copyFlowLink.addEventListener("click", async () => {
    const url = toDocUrl("__FLOW__", "");
    const ok = await copyTextToClipboard(url);
    setFooter(ok ? "リンクをコピーしました" : "コピーに失敗しました");
    window.setTimeout(() => {
      if (footerMeta.textContent.includes("リンク")) setFooter("doc: __FLOW__");
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
