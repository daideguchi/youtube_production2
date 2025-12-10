import { useEffect, useMemo, useState } from "react";
import { mockPages } from "./mockPages";
import "./mockGallery.css";

interface MockPageState {
  html: string;
  status: "idle" | "loading" | "loaded" | "error";
  errorMessage?: string;
}

const ensureExternalAssets = () => {
  if (typeof document === "undefined") {
    return;
  }

  const ensureLink = (id: string, href: string) => {
    if (document.getElementById(id)) {
      return;
    }
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
  };

  const ensureScript = (id: string, src: string, before?: () => void) => {
    if (document.getElementById(id)) {
      return;
    }
    if (before) {
      before();
    }
    const script = document.createElement("script");
    script.id = id;
    script.src = src;
    script.async = true;
    document.head.appendChild(script);
  };

  const ensureConfig = () => {
    if (document.getElementById("mock-tailwind-config")) {
      return;
    }
    const config = document.createElement("script");
    config.id = "mock-tailwind-config";
    config.innerHTML = `
      tailwind.config = {
        darkMode: "class",
        theme: {
          extend: {
            colors: {
              primary: "#135bec",
              "background-light": "#f6f6f8",
              "background-dark": "#101622",
              "surface-light": "#ffffff",
              "surface-dark": "#1a202c",
            }
          }
        }
      };
    `;
    document.head.appendChild(config);
  };

  ensureLink(
    "mock-font-spacegrotesk",
    "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300..700&family=Noto+Sans+JP:wght@400;500;700&display=swap"
  );
  ensureLink(
    "mock-font-material",
    "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined"
  );
  ensureConfig();
  ensureScript(
    "mock-tailwind-cdn",
    "https://cdn.tailwindcss.com?plugins=forms,container-queries"
  );
};

export const MockGallery = () => {
  const [selectedId, setSelectedId] = useState<string>(mockPages[0]?.id ?? "");
  const [state, setState] = useState<MockPageState>({ html: "", status: "idle" });
  const [paletteOpen, setPaletteOpen] = useState(false);

  const selectedPage = useMemo(
    () => mockPages.find((page) => page.id === selectedId) ?? mockPages[0],
    [selectedId]
  );

  useEffect(() => {
    ensureExternalAssets();
    document.documentElement.classList.add("dark");
    return () => {
      document.documentElement.classList.add("dark");
    };
  }, []);

  useEffect(() => {
    if (!selectedPage) {
      return;
    }

    setState({ html: "", status: "loading" });

    fetch(selectedPage.assetPath)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`${response.status} ${response.statusText}`);
        }
        return response.text();
      })
      .then((html) => {
        setState({ html, status: "loaded" });
      })
      .catch((error: Error) => {
        setState({
          html: "",
          status: "error",
          errorMessage: error.message || "モックを読み込めませんでした。",
        });
      });
  }, [selectedPage]);

  useEffect(() => {
    if (!paletteOpen) {
      return;
    }
    const handleKeydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPaletteOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [paletteOpen]);

  const handleSelectPage = (id: string) => {
    setSelectedId(id);
    setPaletteOpen(false);
  };

  return (
    <div className="mock-gallery">
      {paletteOpen ? <button type="button" className="mock-gallery__backdrop" onClick={() => setPaletteOpen(false)} /> : null}
      <main className="mock-gallery__main">
        {state.status === "loading" ? (
          <div className="mock-gallery__status">モックを読み込み中です…</div>
        ) : null}
        {state.status === "error" ? (
          <div className="mock-gallery__status mock-gallery__status--error">
            {selectedPage ? `${selectedPage.label} の読み込みに失敗しました。` : "モックの読み込みに失敗しました。"}
            <br />
            <span className="mock-gallery__status-detail">{state.errorMessage}</span>
          </div>
        ) : null}
        {state.status === "loaded" ? (
          <div
            key={selectedPage?.id}
            className="mock-gallery__preview"
            dangerouslySetInnerHTML={{ __html: state.html }}
          />
        ) : null}
      </main>

      <div className="mock-gallery__switcher">
        <button
          type="button"
          className="mock-gallery__toggle"
          onClick={() => setPaletteOpen((prev) => !prev)}
          aria-expanded={paletteOpen}
        >
          <span className="mock-gallery__toggle-label">{selectedPage?.label ?? "モック切替"}</span>
          <span className="mock-gallery__toggle-icon" aria-hidden>
            {paletteOpen ? "▲" : "▼"}
          </span>
        </button>
        <div className={paletteOpen ? "mock-gallery__palette mock-gallery__palette--open" : "mock-gallery__palette"}>
          <header className="mock-gallery__header">
            <span className="mock-gallery__badge">モック表示</span>
            <h1 className="mock-gallery__title">台本・音声制作ダッシュボード</h1>
            <p className="mock-gallery__subtitle">React 内で閲覧できるモックプレビュー</p>
          </header>
          <nav className="mock-gallery__nav" aria-label="モックページ一覧">
            {mockPages.map((page) => {
              const isActive = page.id === selectedId;
              return (
                <button
                  key={page.id}
                  type="button"
                  className={isActive ? "mock-gallery__nav-item mock-gallery__nav-item--active" : "mock-gallery__nav-item"}
                  onClick={() => handleSelectPage(page.id)}
                >
                  <span className="mock-gallery__nav-label">{page.label}</span>
                  {page.description ? (
                    <span className="mock-gallery__nav-description">{page.description}</span>
                  ) : null}
                </button>
              );
            })}
          </nav>
          <footer className="mock-gallery__footer">
            <p>URL 末尾に <code>?mock=1</code> を付けてアクセスすると、このモードを表示します。</p>
          </footer>
        </div>
      </div>
    </div>
  );
};

export default MockGallery;
