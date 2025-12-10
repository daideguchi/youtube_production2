import React, { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";

// API Endpoint Base
const API_BASE = "http://127.0.0.1:8000/api";

interface AudioSegment {
  text: string;
  reading: string;
  mecab: string;
  voicevox: string;
  verdict: string;
  heading: boolean;
  pre: number;
  post: number;
  duration: number;
}

interface AudioLog {
  channel: string;
  video: string;
  engine: string;
  timestamp: number;
  segments: AudioSegment[];
}

interface KBData {
  version: number;
  words: Record<string, string>;
  // fallback for v1 migration
  entries?: Record<string, any>;
}

export const AudioIntegrityPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const channelId = searchParams.get("channel");
  const videoId = searchParams.get("video");

  const [log, setLog] = useState<AudioLog | null>(null);
  const [kb, setKb] = useState<KBData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showKB, setShowKB] = useState(false);
  const [recentLogs, setRecentLogs] = useState<any[]>([]);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      if (channelId && videoId) {
        const logRes = await fetch(`${API_BASE}/audio-check/${channelId}/${videoId}`);
        if (logRes.ok) {
           const logData = await logRes.json();
           setLog(logData);
        } else {
           if (logRes.status !== 404) {
             throw new Error(`Log fetch failed: ${logRes.status}`);
           }
           setLog(null);
        }
      } else {
        const listRes = await fetch(`${API_BASE}/audio-check/recent`);
        if (listRes.ok) {
          setRecentLogs(await listRes.json());
        }
      }

      const kbRes = await fetch(`${API_BASE}/kb`);
      if (kbRes.ok) {
        const kbData = await kbRes.json();
        // Fallback for v1
        if (!kbData.words && kbData.entries) {
            setKb({ version: 1, words: {} }); 
        } else {
            setKb(kbData);
        }
      }
    } catch (err: any) {
      console.error(err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [channelId, videoId]);

  const handleDeleteKB = async (key: string) => {
    if (!window.confirm(`「${key}」の登録を削除しますか？`)) return;
    try {
      await fetch(`${API_BASE}/kb/${encodeURIComponent(key)}`, { method: "DELETE" });
      fetchData(); 
    } catch (e) {
      alert("削除に失敗しました");
    }
  };

  const getVerdictLabel = (verdict: string) => {
    if (verdict === "match_kanji") return "一致 (漢字)";
    if (verdict === "llm_match_kanji") return "LLM承認 (漢字)";
    if (verdict === "llm_patched_action_a") return "修正 (一部カナ)";
    if (verdict === "kb_applied") return "辞書適用";
    if (verdict.includes("fallback")) return "全カナ (Fallback)";
    return verdict;
  };

  const getVerdictStyle = (verdict: string) => {
    if (verdict.includes("match")) return { color: "green", borderColor: "green" };
    if (verdict.includes("patched") || verdict.includes("fix")) return { color: "orange", borderColor: "orange", fontWeight: "bold" };
    if (verdict.includes("fallback")) return { color: "red", borderColor: "red" };
    if (verdict.includes("kb")) return { color: "blue", borderColor: "blue" };
    return { color: "gray", borderColor: "gray" };
  };

  if (!channelId || !videoId) {
    return (
      <div style={{ padding: "20px" }}>
        <h2>音声整合性チェック (Audio Integrity)</h2>
        <div style={{ padding: "15px", backgroundColor: "#e3f2fd", borderRadius: "4px", marginBottom: "20px" }}>
          <p>確認したい動画を選択してください。</p>
          {recentLogs.length > 0 ? (
            <ul style={{ listStyle: "none", padding: 0, marginTop: "10px" }}>
              {recentLogs.map((item: any) => (
                <li key={`${item.channel}-${item.video}`} style={{ marginBottom: "8px" }}>
                  <a 
                    href={`/audio-integrity?channel=${item.channel}&video=${item.video}`}
                    style={{ 
                      display: "block", 
                      padding: "10px", 
                      backgroundColor: "white", 
                      border: "1px solid #ddd", 
                      borderRadius: "4px",
                      textDecoration: "none",
                      color: "#333",
                      fontWeight: "bold"
                    }}
                  >
                    ▶ {item.channel} - {item.video} <span style={{ fontWeight: "normal", fontSize: "0.85em", color: "#666", marginLeft: "10px" }}>({new Date(item.updated_at).toLocaleString()})</span>
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p>生成済みのログが見つかりません。</p>
          )}
        </div>
        
        <h3>単語辞書 (GKB) - {kb && kb.words ? Object.keys(kb.words).length : 0}件</h3>
        <button onClick={fetchData} style={{ padding: "5px 10px", cursor: "pointer" }}>辞書更新</button>
        
        {kb && kb.words && (
          <table style={{ width: "100%", marginTop: "20px", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", backgroundColor: "#f5f5f5" }}>
                <th style={{ padding: "8px", borderBottom: "1px solid #ddd" }}>単語</th>
                <th style={{ padding: "8px", borderBottom: "1px solid #ddd" }}>読み (カナ)</th>
                <th style={{ padding: "8px", borderBottom: "1px solid #ddd", width: "100px" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(kb.words).map(([word, reading]) => (
                <tr key={word}>
                  <td style={{ padding: "8px", borderBottom: "1px solid #ddd" }}>{word}</td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #ddd" }}>{reading}</td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #ddd" }}>
                    <button onClick={() => handleDeleteKB(word)} style={{ color: "red", border: "none", background: "none", cursor: "pointer" }}>削除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    );
  }

  if (loading && !log) return <div style={{ padding: "20px" }}>読み込み中...</div>;
  if (error) return <div style={{ padding: "20px", color: "red" }}>{error}</div>;

  return (
    <div style={{ padding: "20px", height: "100%", overflowY: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
        <h2>音声整合性チェック: {channelId}-{videoId}</h2>
        <button onClick={fetchData} style={{ padding: "8px 16px", cursor: "pointer" }}>更新</button>
      </div>

      {!log && (
        <div style={{ padding: "15px", backgroundColor: "#fff3e0", borderRadius: "4px", marginBottom: "20px" }}>
          ログが見つかりません。まだ音声生成（Strict Mode）が実行されていない可能性があります。
        </div>
      )}

      {log && (
        <div style={{ marginBottom: "40px", border: "1px solid #ddd", borderRadius: "8px", padding: "15px", backgroundColor: "white" }}>
           <div style={{ marginBottom: "15px", fontSize: "0.9rem", color: "#666" }}>
             エンジン: <strong>{log.engine}</strong> | 
             生成日時: {new Date(log.timestamp * 1000).toLocaleString()} | 
             セグメント数: {log.segments?.length || 0}
           </div>
           
           <div style={{ maxHeight: "60vh", overflowY: "auto" }}>
             <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
               <thead style={{ position: "sticky", top: 0, backgroundColor: "#f9f9f9", zIndex: 1 }}>
                 <tr style={{ textAlign: "left" }}>
                   <th style={{ padding: "8px", borderBottom: "2px solid #ddd", width: "40px" }}>#</th>
                   <th style={{ padding: "8px", borderBottom: "2px solid #ddd", width: "40%" }}>テキスト (漢字) / MeCab読み</th>
                   <th style={{ padding: "8px", borderBottom: "2px solid #ddd", width: "40%" }}>最終的な読み / Voicevox予測</th>
                   <th style={{ padding: "8px", borderBottom: "2px solid #ddd" }}>判定</th>
                   <th style={{ padding: "8px", borderBottom: "2px solid #ddd" }}>ポーズ</th>
                 </tr>
               </thead>
               <tbody>
                 {(log.segments || []).map((seg, idx) => {
                   const isModified = seg.text !== seg.reading;
                   const verdictStyle = getVerdictStyle(seg.verdict);
                   
                   return (
                     <tr key={idx} style={{ backgroundColor: seg.heading ? "#eef6fc" : "white", borderBottom: "1px solid #eee" }}>
                       <td style={{ padding: "8px" }}>{idx + 1}</td>
                       <td style={{ padding: "8px" }}>
                         <div style={{ fontWeight: seg.heading ? "bold" : "normal" }}>{seg.text}</div>
                         <div style={{ fontSize: "0.75rem", color: "#888", marginTop: "2px" }}>MeCab: {seg.mecab}</div>
                       </td>
                       <td style={{ padding: "8px" }}>
                         <div style={{ 
                           fontWeight: isModified ? "bold" : "normal",
                           color: isModified ? "#d32f2f" : "inherit"
                         }}>
                           {seg.reading}
                         </div>
                         <div style={{ fontSize: "0.75rem", color: "#666", marginTop: "2px" }}>
                           Orig: {seg.voicevox}
                         </div>
                       </td>
                       <td style={{ padding: "8px" }}>
                         <span style={{ 
                           border: `1px solid ${verdictStyle.borderColor}`, 
                           color: verdictStyle.color,
                           padding: "2px 6px",
                           borderRadius: "12px",
                           fontSize: "0.75rem",
                           whiteSpace: "nowrap"
                         }}>
                           {getVerdictLabel(seg.verdict)}
                         </span>
                       </td>
                       <td style={{ padding: "8px" }}>
                         {seg.pre > 0 && <span style={{ display: "block", fontSize: "0.75rem", color: "#666" }}>Pre {seg.pre}s</span>}
                         {seg.post > 0 && <span style={{ display: "block", fontSize: "0.75rem", color: "#666" }}>Post {seg.post}s</span>}
                       </td>
                     </tr>
                   );
                 })}
               </tbody>
             </table>
           </div>
        </div>
      )}

      <div style={{ border: "1px solid #ddd", borderRadius: "8px", overflow: "hidden" }}>
        <div 
          onClick={() => setShowKB(!showKB)} 
          style={{ padding: "10px 15px", backgroundColor: "#f5f5f5", cursor: "pointer", fontWeight: "bold", display: "flex", justifyContent: "space-between" }}
        >
          単語辞書 (GKB) - {kb && kb.words ? Object.keys(kb.words).length : 0}件
          <span>{showKB ? "▲" : "▼"}</span>
        </div>
        
        {showKB && kb && kb.words && (
          <div style={{ maxHeight: "400px", overflowY: "auto", padding: "0" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
              <thead>
                <tr style={{ textAlign: "left", backgroundColor: "#fafafa" }}>
                  <th style={{ padding: "8px", borderBottom: "1px solid #ddd" }}>単語</th>
                  <th style={{ padding: "8px", borderBottom: "1px solid #ddd" }}>読み (カナ)</th>
                  <th style={{ padding: "8px", borderBottom: "1px solid #ddd", width: "100px" }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(kb.words).map(([word, reading]) => (
                  <tr key={word} style={{ borderBottom: "1px solid #eee" }}>
                    <td style={{ padding: "8px" }}>{word}</td>
                    <td style={{ padding: "8px" }}>{reading}</td>
                    <td style={{ padding: "8px" }}>
                      <button onClick={() => handleDeleteKB(word)} style={{ color: "red", border: "none", background: "none", cursor: "pointer" }}>削除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
