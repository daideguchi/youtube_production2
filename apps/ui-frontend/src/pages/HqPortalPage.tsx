import { useEffect, useMemo } from "react";
import { Link, useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import type { ShellOutletContext } from "../layouts/AppShell";
import "./HqPortalPage.css";

type GoTarget =
  | "dashboard"
  | "ui"
  | "files"
  | "fleet"
  | "pages"
  | "pages-mobile"
  | "pages-ep"
  | "pages-ep-audio";

function pad3(value: string | null): string | null {
  if (!value) return null;
  const raw = value.trim();
  if (!raw) return null;
  if (/^\d{3}$/.test(raw)) return raw;
  if (/^\d+$/.test(raw)) return String(Number(raw)).padStart(3, "0");
  return raw;
}

function detectBasePath(pathname: string): string {
  const candidates = ["/ui", "/youtube_production2"];
  for (const base of candidates) {
    if (pathname === base || pathname.startsWith(`${base}/`)) {
      return base;
    }
  }
  return "";
}

function normalizePagesBase(raw: string): string {
  const s = String(raw || "").trim();
  return s.replace(/\/+$/, "");
}

export function HqPortalPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { selectedChannel, selectedVideo } = useOutletContext<ShellOutletContext>();

  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const basePath = typeof window === "undefined" ? "" : detectBasePath(window.location.pathname);

  const uiRootUrl = `${origin}${basePath}/`;
  const filesUrl = `${origin}/files/`;
  const fleetUrl = `${origin}/fleet/`;

  const pagesBase = useMemo(() => {
    const fromEnv = process.env.REACT_APP_SCRIPT_VIEWER_PAGES_BASE_URL;
    return normalizePagesBase(fromEnv || "https://daideguchi.github.io/youtube_production2");
  }, []);

  const pagesViewerUrl = `${pagesBase}/`;
  const pagesMobileUrl = `${pagesBase}/m/`;

  const channel = (selectedChannel || "").trim().toUpperCase();
  const video3 = pad3(selectedVideo);
  const pagesEpisodeUrl = channel && video3 ? `${pagesBase}/ep/${encodeURIComponent(channel)}/${encodeURIComponent(video3)}/` : null;
  const pagesEpisodeAudioUrl = pagesEpisodeUrl ? `${pagesEpisodeUrl}audio/` : null;

  const quickSwitchUrls = useMemo(() => {
    const hqUrl = `${uiRootUrl.replace(/\/+$/, "")}/hq`;
    const make = (go: GoTarget) => `${hqUrl}?go=${encodeURIComponent(go)}`;
    return [
      { label: "このUI", url: make("ui") },
      { label: "ファイル", url: make("files") },
      { label: "稼働状況", url: make("fleet") },
      { label: "Script Viewer", url: make("pages") },
      { label: "Script Viewer (Mobile)", url: make("pages-mobile") },
      ...(channel && video3
        ? [
            { label: `Episode ${channel}-${video3}`, url: make("pages-ep") },
            { label: `Audio ${channel}-${video3}`, url: make("pages-ep-audio") },
          ]
        : []),
    ];
  }, [uiRootUrl, channel, video3]);

  useEffect(() => {
    const go = (searchParams.get("go") || "").trim() as GoTarget;
    if (!go) return;

    const targets: Record<GoTarget, string | null> = {
      dashboard: "/dashboard",
      ui: "/dashboard",
      files: filesUrl,
      fleet: fleetUrl,
      pages: pagesViewerUrl,
      "pages-mobile": pagesMobileUrl,
      "pages-ep": pagesEpisodeUrl ?? pagesViewerUrl,
      "pages-ep-audio": pagesEpisodeAudioUrl ?? pagesViewerUrl,
    };

    const dest = targets[go] ?? null;
    if (!dest) return;

    if (dest.startsWith("/")) {
      navigate(dest, { replace: true });
      return;
    }
    window.location.assign(dest);
  }, [
    searchParams,
    navigate,
    filesUrl,
    fleetUrl,
    pagesViewerUrl,
    pagesMobileUrl,
    pagesEpisodeUrl,
    pagesEpisodeAudioUrl,
  ]);

  return (
    <section className="main-content main-content--workspace hq-portal">
      <header className="hq-portal__header">
        <div>
          <h1 className="hq-portal__title">HQ ポータル</h1>
          <p className="hq-portal__subtitle">
            URLで切り替え（<code>?go=...</code>）もできるリンク集。モバイル前提。
          </p>
        </div>
        <div className="hq-portal__header-actions">
          <Link className="workspace-button" to="/dashboard">
            ダッシュボード
          </Link>
        </div>
      </header>

      <div className="hq-portal__grid">
        <div className="hq-portal__card">
          <h2>このサーバー</h2>
          <div className="hq-portal__links">
            <a className="workspace-button workspace-button--primary" href={uiRootUrl}>
              UI
            </a>
            <a className="workspace-button" href={filesUrl} target="_blank" rel="noreferrer">
              /files
            </a>
            <a className="workspace-button" href={fleetUrl} target="_blank" rel="noreferrer">
              /fleet
            </a>
            <a className="workspace-button" href={`${origin}/api/healthz`} target="_blank" rel="noreferrer">
              /api/healthz
            </a>
          </div>
          <div className="hq-portal__meta mono">
            origin: {origin}
            {basePath ? ` · base=${basePath}` : ""}
          </div>
        </div>

        <div className="hq-portal__card">
          <h2>Script Viewer（GitHub Pages）</h2>
          <div className="hq-portal__links">
            <a className="workspace-button workspace-button--primary" href={pagesViewerUrl} target="_blank" rel="noreferrer">
              Viewer
            </a>
            <a className="workspace-button" href={pagesMobileUrl} target="_blank" rel="noreferrer">
              Mobile Start
            </a>
            {pagesEpisodeUrl ? (
              <a className="workspace-button" href={pagesEpisodeUrl} target="_blank" rel="noreferrer">
                Episode（{channel}-{video3}）
              </a>
            ) : (
              <span className="hq-portal__hint">（チャンネル/動画を選ぶと Episode の直リンクが出ます）</span>
            )}
            {pagesEpisodeAudioUrl ? (
              <a className="workspace-button" href={pagesEpisodeAudioUrl} target="_blank" rel="noreferrer">
                Audio
              </a>
            ) : null}
          </div>
          <div className="hq-portal__meta mono">pages: {pagesBase}</div>
        </div>

        <div className="hq-portal__card hq-portal__card--wide">
          <h2>URLスイッチ（コピペ用）</h2>
          <p className="hq-portal__subtitle">
            <code>/ui/hq?go=files</code> みたいに叩くと、そのまま飛びます（固定キーのみ）。
          </p>
          <div className="hq-portal__switch-grid">
            {quickSwitchUrls.map((item) => (
              <a
                key={item.label}
                className="hq-portal__switch"
                href={item.url}
                target={item.url.includes("?go=ui") || item.url.includes("?go=dashboard") ? undefined : "_blank"}
                rel="noreferrer"
              >
                <span className="hq-portal__switch-label">{item.label}</span>
                <span className="hq-portal__switch-url mono">{item.url}</span>
              </a>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

