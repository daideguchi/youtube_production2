import React, { useState, useEffect, useCallback } from "react";
import { runAudioTtsV2FromScript } from "../api/client";

interface ChannelProgress {
  channel: string;
  total_episodes: number;
  completed_episodes: number;
  completed_ids: string[];
  missing_ids: string[];
  progress_percent: number;
}

interface TtsProgressResponse {
  channels: ChannelProgress[];
  overall_progress: number;
}

const CHANNELS = [
  { id: "CH06", name: "闇の雑学", engine: "voicevox" },
  { id: "CH02", name: "人生の道標", engine: "voicevox" },
  { id: "CH04", name: "世界のミステリー", engine: "voicevox" },
];

export const AudioTtsV2Page: React.FC = () => {
  const [progress, setProgress] = useState<TtsProgressResponse | null>(null);
  const [selectedChannel, setSelectedChannel] = useState<string>("CH06");
  const [isGenerating, setIsGenerating] = useState(false);
  const [currentEpisode, setCurrentEpisode] = useState<string | null>(null);
  const [generationLog, setGenerationLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  // 進捗を取得
  const fetchProgress = useCallback(async () => {
    try {
      const response = await fetch("/api/tts-progress");
      if (response.ok) {
        const data = await response.json();
        setProgress(data);
      }
    } catch (e) {
      console.error("Failed to fetch progress:", e);
    }
  }, []);

  useEffect(() => {
    fetchProgress();
    const interval = setInterval(fetchProgress, 10000); // 10秒ごとに更新
    return () => clearInterval(interval);
  }, [fetchProgress]);

  // 単一エピソード生成
  const generateSingle = async (channel: string, video: string) => {
    setCurrentEpisode(`${channel}-${video}`);
    setGenerationLog((prev) => [...prev, `🎙️ ${channel}-${video} 生成開始...`]);

    try {
      const channelConfig = CHANNELS.find(c => c.id === channel);
      await runAudioTtsV2FromScript({
        channel,
        video,
        engine_override: channelConfig?.engine,
      });
      setGenerationLog((prev) => [...prev, `✅ ${channel}-${video} 完了`]);
      return true;
    } catch (e: any) {
      setGenerationLog((prev) => [...prev, `❌ ${channel}-${video} 失敗: ${e.message}`]);
      return false;
    }
  };

  // チャンネル全体を再生成
  const regenerateChannel = async (channel: string) => {
    setIsGenerating(true);
    setError(null);
    setGenerationLog([`📁 ${channel} の全エピソードを再生成します...`]);

    const channelProgress = progress?.channels.find(c => c.channel === channel);
    if (!channelProgress) {
      setError("チャンネル情報が取得できません");
      setIsGenerating(false);
      return;
    }

    // 全エピソードを取得（完了+未完了）
    const allEpisodes = Array.from(new Set([...channelProgress.completed_ids, ...channelProgress.missing_ids])).sort();

    let successCount = 0;
    let failCount = 0;

    for (const ep of allEpisodes) {
      const success = await generateSingle(channel, ep);
      if (success) successCount++;
      else failCount++;
      await fetchProgress(); // 進捗更新
    }

    setGenerationLog((prev) => [
      ...prev,
      ``,
      `📊 完了: 成功 ${successCount} / 失敗 ${failCount}`,
    ]);
    setCurrentEpisode(null);
    setIsGenerating(false);
  };

  // 未生成分のみ生成
  const generateMissing = async (channel: string) => {
    setIsGenerating(true);
    setError(null);

    const channelProgress = progress?.channels.find(c => c.channel === channel);
    if (!channelProgress || channelProgress.missing_ids.length === 0) {
      setGenerationLog(["✅ 未生成のエピソードはありません"]);
      setIsGenerating(false);
      return;
    }

    setGenerationLog([`📁 ${channel} の未生成分 ${channelProgress.missing_ids.length}本 を生成します...`]);

    let successCount = 0;
    let failCount = 0;

    for (const ep of channelProgress.missing_ids) {
      const success = await generateSingle(channel, ep);
      if (success) successCount++;
      else failCount++;
      await fetchProgress();
    }

    setGenerationLog((prev) => [
      ...prev,
      ``,
      `📊 完了: 成功 ${successCount} / 失敗 ${failCount}`,
    ]);
    setCurrentEpisode(null);
    setIsGenerating(false);
  };

  const getProgressColor = (percent: number) => {
    if (percent >= 100) return "#22c55e";
    if (percent >= 75) return "#84cc16";
    if (percent >= 50) return "#eab308";
    return "#f97316";
  };

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
      {/* ヘッダー */}
      <div style={{ marginBottom: 32 }}>
        <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 8 }}>
          🎙️ TTS音声生成
        </h1>
        <p style={{ color: "#666", margin: 0 }}>
          修正済みBテキストから音声を生成します
        </p>
      </div>

      {/* チャンネル選択カード */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 20, marginBottom: 32 }}>
        {CHANNELS.map((ch) => {
          const channelProgress = progress?.channels.find(c => c.channel === ch.id);
          const percent = channelProgress?.progress_percent ?? 0;
          const total = channelProgress?.total_episodes ?? 0;
          const completed = channelProgress?.completed_episodes ?? 0;
          const missing = channelProgress?.missing_ids.length ?? 0;
          const isSelected = selectedChannel === ch.id;

          return (
            <div
              key={ch.id}
              onClick={() => !isGenerating && setSelectedChannel(ch.id)}
              style={{
                background: isSelected ? "linear-gradient(135deg, #667eea 0%, #764ba2 100%)" : "#fff",
                color: isSelected ? "#fff" : "#333",
                borderRadius: 16,
                padding: 24,
                cursor: isGenerating ? "not-allowed" : "pointer",
                boxShadow: isSelected ? "0 8px 32px rgba(102, 126, 234, 0.3)" : "0 2px 8px rgba(0,0,0,0.1)",
                transition: "all 0.2s ease",
                border: isSelected ? "none" : "1px solid #e5e7eb",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                <div>
                  <div style={{ fontSize: 20, fontWeight: 600 }}>{ch.id}</div>
                  <div style={{ fontSize: 14, opacity: 0.8 }}>{ch.name}</div>
                </div>
                <div
                  style={{
                    fontSize: 24,
                    fontWeight: 700,
                    padding: "4px 12px",
                    borderRadius: 8,
                    background: isSelected ? "rgba(255,255,255,0.2)" : getProgressColor(percent),
                    color: isSelected ? "#fff" : "#fff",
                  }}
                >
                  {percent}%
                </div>
              </div>

              {/* プログレスバー */}
              <div style={{ background: isSelected ? "rgba(255,255,255,0.2)" : "#e5e7eb", borderRadius: 4, height: 8, marginBottom: 12 }}>
                <div
                  style={{
                    width: `${percent}%`,
                    height: "100%",
                    borderRadius: 4,
                    background: isSelected ? "#fff" : getProgressColor(percent),
                    transition: "width 0.5s ease",
                  }}
                />
              </div>

              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 14, opacity: 0.8 }}>
                <span>完了: {completed} / {total}</span>
                <span>未生成: {missing}</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* アクションパネル */}
      <div style={{ background: "#fff", borderRadius: 16, padding: 24, boxShadow: "0 2px 8px rgba(0,0,0,0.1)", marginBottom: 24 }}>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 20 }}>
          <button
            onClick={() => regenerateChannel(selectedChannel)}
            disabled={isGenerating}
            style={{
              padding: "16px 32px",
              fontSize: 16,
              fontWeight: 600,
              background: isGenerating ? "#9ca3af" : "linear-gradient(135deg, #ef4444 0%, #dc2626 100%)",
              color: "#fff",
              border: "none",
              borderRadius: 12,
              cursor: isGenerating ? "not-allowed" : "pointer",
              boxShadow: isGenerating ? "none" : "0 4px 16px rgba(239, 68, 68, 0.3)",
            }}
          >
            {isGenerating ? "生成中..." : `🔄 ${selectedChannel} 全て再生成`}
          </button>

          <button
            onClick={() => generateMissing(selectedChannel)}
            disabled={isGenerating}
            style={{
              padding: "16px 32px",
              fontSize: 16,
              fontWeight: 600,
              background: isGenerating ? "#9ca3af" : "linear-gradient(135deg, #22c55e 0%, #16a34a 100%)",
              color: "#fff",
              border: "none",
              borderRadius: 12,
              cursor: isGenerating ? "not-allowed" : "pointer",
              boxShadow: isGenerating ? "none" : "0 4px 16px rgba(34, 197, 94, 0.3)",
            }}
          >
            {isGenerating ? "生成中..." : `➕ ${selectedChannel} 未生成分のみ`}
          </button>

          <button
            onClick={fetchProgress}
            disabled={isGenerating}
            style={{
              padding: "16px 24px",
              fontSize: 16,
              background: "#fff",
              color: "#333",
              border: "1px solid #e5e7eb",
              borderRadius: 12,
              cursor: "pointer",
            }}
          >
            🔄 進捗更新
          </button>
        </div>

        {/* 現在の生成状況 */}
        {currentEpisode && (
          <div style={{ padding: 16, background: "#fef3c7", borderRadius: 8, marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div className="spinner" style={{ width: 16, height: 16, border: "2px solid #f59e0b", borderTop: "2px solid transparent", borderRadius: "50%", animation: "spin 1s linear infinite" }} />
              <span style={{ fontWeight: 600 }}>生成中: {currentEpisode}</span>
            </div>
          </div>
        )}

        {error && (
          <div style={{ padding: 16, background: "#fee2e2", color: "#dc2626", borderRadius: 8, marginBottom: 16 }}>
            ⚠️ {error}
          </div>
        )}
      </div>

      {/* 生成ログ */}
      {generationLog.length > 0 && (
        <div style={{ background: "#1e293b", borderRadius: 16, padding: 20, color: "#e2e8f0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <span style={{ fontWeight: 600 }}>📋 生成ログ</span>
            <button
              onClick={() => setGenerationLog([])}
              style={{ background: "transparent", border: "none", color: "#94a3b8", cursor: "pointer" }}
            >
              クリア
            </button>
          </div>
          <div style={{ fontFamily: "monospace", fontSize: 13, lineHeight: 1.6, maxHeight: 300, overflowY: "auto" }}>
            {generationLog.map((log, i) => (
              <div key={i}>{log}</div>
            ))}
          </div>
        </div>
      )}

      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
};

export default AudioTtsV2Page;
