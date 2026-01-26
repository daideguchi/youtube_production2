import { useEffect, useState } from "react";
import { AgentBoardPanel } from "../components/AgentBoardPanel";
import { safeLocalStorage } from "../utils/safeStorage";

const ACTOR_STORAGE_KEY = "agent_board_actor";

export function AgentBoardPage() {
  const [actorName, setActorName] = useState(() => safeLocalStorage.getItem(ACTOR_STORAGE_KEY) || "dd");

  useEffect(() => {
    safeLocalStorage.setItem(ACTOR_STORAGE_KEY, actorName);
  }, [actorName]);

  return (
    <div style={{ padding: "16px" }}>
      <h1 style={{ margin: "0 0 10px" }}>共有ボード（Shared Board / 申し送り・進捗）</h1>
      <div style={{ color: "#555", fontSize: 13, marginBottom: 12, lineHeight: 1.6 }}>
        <div>
          ここは <code>board.json</code>（このMacのローカル）をUIで見るためのページです。
        </div>
        <div>
          オススメ: <b>Now（今）→ blocked only（ブロッカーのみ）</b> で「止まってる理由」を確認 → 次に <b>Threads（スレッド）</b>{" "}
          で詳細ログを追う。
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ color: "#555" }}>from（投稿者）</span>
          <input
            value={actorName}
            onChange={(e) => setActorName(e.target.value)}
            style={{ padding: "8px 10px", border: "1px solid #ddd", borderRadius: 8, width: 160 }}
          />
        </label>
        <span style={{ color: "#666", fontSize: 12 }}>
          BEP-1運用は <code>ssot/ops/OPS_AGENT_PLAYBOOK.md</code> を参照
        </span>
      </div>
      <AgentBoardPanel actorName={actorName} />
    </div>
  );
}
