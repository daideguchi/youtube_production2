import { Link } from "react-router-dom";
import { SsotFilePreview } from "../components/SsotFilePreview";
import { SsotWorkspace } from "../components/SsotWorkspace";

export function SsotPortalPage() {
  return (
    <section className="research-workspace research-workspace--wide ssot-portal">
      <header className="research-workspace__header">
        <div>
          <p className="eyebrow">/ssot</p>
          <h2>SSOT Portal（Start Here）</h2>
          <p className="research-workspace__note">
            SSOT = UI（read-only）。目的は「人間/AIの認識ズレをゼロ」にすることです。まずは{" "}
            <span className="mono">System Map</span> を開いて、ノードをクリック→右側の要点（目的/LLM/Prompt/Outputs/SoT）を確認してください。
            SSOT更新時は System Map/Catalog も必ず追随させます（`python3 scripts/ops/pre_push_final_check.py`）。
          </p>
        </div>
      </header>

      <div className="research-quick" style={{ marginTop: 12, lineHeight: 1.7 }}>
        <div style={{ fontWeight: 950 }}>最初の1分（迷ったらここだけ）</div>
        <ol style={{ margin: "8px 0 0 0", paddingLeft: 18 }}>
          <li>
            <Link to="/ssot/map">System Map</Link> を開く（まず <span className="badge subtle">Mainline</span>）
          </li>
          <li>ノードをクリック → 右側のクイックビューで「目的/LLM/Prompt/Outputs/SoT」を確認</li>
          <li>
            詳細は下の <span className="mono">Flow Runbook</span>（全ステップ）で確認（必要なら <span className="badge subtle">Graph Focus</span>）
          </li>
        </ol>
      </div>

      <div className="ssot-portal-grid">
        <section className="ssot-portal-cards">
          <Link className="ssot-portal-card ssot-portal-card--primary" to="/ssot/map">
            <div className="ssot-portal-card__title">System Map</div>
            <div className="ssot-portal-card__desc">全処理を「Flow / Runbook / Trace」まで追える地図</div>
            <div className="ssot-portal-card__meta mono">推奨: ここから開始</div>
          </Link>
          <Link className="ssot-portal-card" to="/ssot/entrypoints">
            <div className="ssot-portal-card__title">Entrypoints</div>
            <div className="ssot-portal-card__desc">API / CLI / LLM 呼び出し箇所の入口索引</div>
            <div className="ssot-portal-card__meta mono">routes / python / shell</div>
          </Link>
          <Link className="ssot-portal-card" to="/ssot/gaps">
            <div className="ssot-portal-card__title">Gaps</div>
            <div className="ssot-portal-card__desc">SSOT ↔ 実装のズレと意思決定ポイント</div>
            <div className="ssot-portal-card__meta mono">P0/P1から潰す</div>
          </Link>
          <Link className="ssot-portal-card" to="/ssot/zombies">
            <div className="ssot-portal-card__title">Zombies</div>
            <div className="ssot-portal-card__desc">削除はしない。棚卸し→隔離→archive-first→log</div>
            <div className="ssot-portal-card__meta mono">cleanup safety</div>
          </Link>
          <Link className="ssot-portal-card" to="/ssot/trace">
            <div className="ssot-portal-card__title">Trace</div>
            <div className="ssot-portal-card__desc">実行ログ（JSONL）から “どのLLM/Prompt” を使ったか追跡</div>
            <div className="ssot-portal-card__meta mono">logs/traces</div>
          </Link>
          <Link className="ssot-portal-card" to="/model-policy">
            <div className="ssot-portal-card__title">Model Policy</div>
            <div className="ssot-portal-card__desc">チャンネル別のモデル方針（画像/LLMスロット）を表で固定</div>
            <div className="ssot-portal-card__meta mono">g-1 / f-4 / slots</div>
          </Link>
        </section>

        <section className="ssot-portal-preview">
          <SsotFilePreview repoPath="ssot/DECISIONS.md" title="DECISIONS（SSOTトップ / 意思決定台帳）" />
        </section>
      </div>

      <details style={{ marginTop: 16 }}>
        <summary style={{ cursor: "pointer", fontWeight: 900 }}>SSOT Docs Browser（詳細）</summary>
        <div style={{ marginTop: 10 }}>
          <SsotWorkspace embedded />
        </div>
      </details>
    </section>
  );
}
