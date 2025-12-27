import { useState } from "react";
import { apiUrl } from "../api/baseUrl";

// iframe は 3000 上のページに埋め込むだけなので、スタジオ自体は 3100 を直接参照する。
// 必要なら REACT_APP_REMOTION_STUDIO_URL で上書き。
const STUDIO_SRC_BASE = process.env.REACT_APP_REMOTION_STUDIO_URL || "http://localhost:3100";
// backend側に追加した再起動API（同一オリジン /api 配下）
const RESTART_URL = "/api/remotion/restart_preview";

export function RemotionStudioPage() {
  const [channel, setChannel] = useState("CH01");
  const [runId, setRunId] = useState("192");
  const [subtitleFs, setSubtitleFs] = useState<string>("");
  const [subtitleBottom, setSubtitleBottom] = useState<string>("");
  const [subtitleWidth, setSubtitleWidth] = useState<string>("");
  const [beltMainScale, setBeltMainScale] = useState<string>("");
  const [beltSubScale, setBeltSubScale] = useState<string>("");
  const [beltGap, setBeltGap] = useState<string>("");
  const [cacheBust, setCacheBust] = useState(0);

  const src = `${STUDIO_SRC_BASE}?run=${encodeURIComponent(runId)}${channel ? `&channel=${encodeURIComponent(channel)}` : ""}`
    + `${subtitleFs ? `&subtitle_fs=${encodeURIComponent(subtitleFs)}` : ""}`
    + `${subtitleBottom ? `&subtitle_bottom=${encodeURIComponent(subtitleBottom)}` : ""}`
    + `${subtitleWidth ? `&subtitle_width=${encodeURIComponent(subtitleWidth)}` : ""}`
    + `${beltMainScale ? `&belt_main_scale=${encodeURIComponent(beltMainScale)}` : ""}`
    + `${beltSubScale ? `&belt_sub_scale=${encodeURIComponent(beltSubScale)}` : ""}`
    + `${beltGap ? `&belt_gap=${encodeURIComponent(beltGap)}` : ""}`
    + `&cb=${cacheBust}`;

  const presets = [
    { label: "CH01-192", channel: "CH01", run: "192" },
    { label: "CH01-193", channel: "CH01", run: "193" },
    { label: "TEST-001", channel: "TEST", run: "001" },
  ];

  const applyPreset = (c: string, r: string) => {
    setChannel(c);
    setRunId(r);
  };

  const applyVisual = () => {
    // cache bust to force iframe reload with updated query params
    setCacheBust((v) => v + 1);
  };

  const handleRestart = async () => {
    // このエンドポイントは存在しない場合があるので、叩けるときだけ使う。
    try {
      const res = await fetch(apiUrl(RESTART_URL), { method: "POST" });
      if (!res.ok) {
        alert("Remotion preview の再起動に失敗しました (endpoint missing)");
      } else {
        alert("再起動リクエストを送信しました。少し待って再読込してください。");
      }
    } catch (e) {
      alert("再起動エンドポイントに到達できませんでした。preview を手動で再起動してください。");
    }
  };

  return (
    <div className="remotion-preview">
      <div className="remotion-preview__header">
        <div>
          <h1>Remotion Studio (タイムライン)</h1>
          <p>別プロセスで `cd apps/remotion && npx remotion preview --entry src/index.ts --port 3100` を起動し、このiframeでタイムライン編集・レンダリングを確認します。</p>
        </div>
        <div className="remotion-preview__controls">
          <label>
            Channel:
            <input value={channel} onChange={(e) => setChannel(e.target.value.trim())} placeholder="例: CH01" />
          </label>
          <label>
            Run ID:
            <input value={runId} onChange={(e) => setRunId(e.target.value.trim())} placeholder="例: 192" />
          </label>
          <div className="remotion-preview__presets">
            {presets.map((p) => (
              <button key={p.label} type="button" onClick={() => applyPreset(p.channel, p.run)}>
                {p.label}
              </button>
            ))}
            <button type="button" onClick={handleRestart} title="preview再起動（エンドポイントがあれば）">
              Studio再起動
            </button>
            <button type="button" onClick={applyVisual} title="iframeを再読み込み（クエリ変更を反映）">
              反映/リロード
            </button>
          </div>
          <div className="remotion-preview__controls remotion-preview__controls--grid">
            <label>
              subtitle_fs(px):
              <input value={subtitleFs} onChange={(e) => setSubtitleFs(e.target.value)} placeholder="例: 52" />
            </label>
            <label>
              subtitle_bottom(px):
              <input value={subtitleBottom} onChange={(e) => setSubtitleBottom(e.target.value)} placeholder="例: 60" />
            </label>
            <label>
              subtitle_width(%):
              <input value={subtitleWidth} onChange={(e) => setSubtitleWidth(e.target.value)} placeholder="例: 92" />
            </label>
            <label>
              belt_main_scale:
              <input value={beltMainScale} onChange={(e) => setBeltMainScale(e.target.value)} placeholder="例: 1.2" />
            </label>
            <label>
              belt_sub_scale:
              <input value={beltSubScale} onChange={(e) => setBeltSubScale(e.target.value)} placeholder="例: 1.3" />
            </label>
            <label>
              belt_gap:
              <input value={beltGap} onChange={(e) => setBeltGap(e.target.value)} placeholder="例: 1.2" />
            </label>
          </div>
        </div>
      </div>
      <div className="remotion-preview__player" style={{ height: "80vh" }}>
        <iframe
          title="Remotion Studio"
          src={src}
          style={{ width: "100%", height: "100%", border: "1px solid #e2e8f0", borderRadius: 8, background: "#000" }}
        />
      </div>
    </div>
  );
}
