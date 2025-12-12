import React, { useState, useEffect, useCallback } from "react";
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
  const [recentLogs, setRecentLogs] = useState<any[]>([]);

  const fetchData = useCallback(async () => {
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
  }, [channelId, videoId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

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
        
        <div style={{ padding: "12px", backgroundColor: "#f3f4f6", borderRadius: "6px", marginTop: "20px" }}>
          <h3 style={{ marginTop: 0 }}>読み辞書 - {kb && kb.words ? Object.keys(kb.words).length : 0}件</h3>
          <p style={{ marginBottom: 0 }}>
            辞書の確認・追加・削除は <a href="/dictionary">辞書ページ</a> で行ってください。
          </p>
        </div>
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

      <div style={{ padding: "12px", backgroundColor: "#f3f4f6", borderRadius: "6px" }}>
        <strong>読み辞書</strong> - {kb && kb.words ? Object.keys(kb.words).length : 0}件
        <div style={{ marginTop: "6px" }}>
          辞書の確認・追加・削除は <a href="/dictionary">辞書ページ</a> で行ってください。
        </div>
      </div>
    </div>
  );
};
