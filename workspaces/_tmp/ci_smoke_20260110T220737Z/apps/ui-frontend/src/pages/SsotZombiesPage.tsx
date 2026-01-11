import { Link } from "react-router-dom";
import { SsotFilePreview } from "../components/SsotFilePreview";

export function SsotZombiesPage() {
  return (
    <section className="research-workspace research-workspace--wide">
      <header className="research-workspace__header">
        <div>
          <p className="eyebrow">/ssot/zombies</p>
          <h2>Zombie / Legacy Candidates</h2>
          <p className="research-workspace__note">
            「確実ゴミ」以外は削除しません。棚卸し台帳を UI 上の正本として固定し、意思決定→archive-first→cleanup log の順で進めます（read-only）。
          </p>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
            <Link className="research-chip" to="/ssot">
              SSOT Portal
            </Link>
            <Link className="research-chip" to="/ssot/map">
              System Map
            </Link>
            <Link className="research-chip" to="/ssot/gaps">
              Gaps
            </Link>
            <Link className="research-chip" to="/ssot/entrypoints">
              Entrypoints
            </Link>
            <Link className="research-chip" to="/ssot/trace">
              Trace
            </Link>
          </div>
        </div>
      </header>

      <div style={{ display: "grid", gap: 16 }}>
        <SsotFilePreview repoPath="ssot/ops/OPS_ZOMBIE_CODE_REGISTER.md" title="Zombie Candidates Register（未確定）" />
        <SsotFilePreview repoPath="ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md" title="Cleanup Execution Log（実行記録）" />
      </div>
    </section>
  );
}

