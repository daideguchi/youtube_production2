import { Link } from "react-router-dom";
import { SsotFilePreview } from "../components/SsotFilePreview";

export function SsotGapsPage() {
  return (
    <section className="research-workspace research-workspace--wide">
      <header className="research-workspace__header">
        <div>
          <p className="eyebrow">/ssot/gaps</p>
          <h2>Gaps / Open Questions</h2>
          <p className="research-workspace__note">SSOT↔実装のズレと意思決定ポイントを、UI上の正本として固定します（read-only）。</p>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
            <Link className="research-chip" to="/ssot">
              SSOT Portal
            </Link>
            <Link className="research-chip" to="/ssot/map">
              System Map
            </Link>
            <Link className="research-chip" to="/ssot/entrypoints">
              Entrypoints
            </Link>
            <Link className="research-chip" to="/ssot/zombies">
              Zombies
            </Link>
            <Link className="research-chip" to="/ssot/trace">
              Trace
            </Link>
          </div>
        </div>
      </header>

      <div style={{ display: "grid", gap: 16 }}>
        <SsotFilePreview repoPath="ssot/ops/OPS_GAPS_REGISTER.md" title="Gaps Register（SSOT↔実装）" />
        <SsotFilePreview repoPath="ssot/ops/OPS_OPEN_QUESTIONS.md" title="Open Questions（意思決定が必要）" />
      </div>
    </section>
  );
}
