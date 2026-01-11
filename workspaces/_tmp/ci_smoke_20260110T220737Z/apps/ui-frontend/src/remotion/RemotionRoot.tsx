import { AbsoluteFill, Audio, useCurrentFrame, useVideoConfig } from "remotion";
import { interpolate } from "remotion";

type Belt = { text: string; start: number; end: number };
type ImageCue = { path: string; start?: number; end?: number; duration?: number; text?: string };
type SubtitleCue = { start: number; end: number; text: string };

type Props = {
  runId: string;
  episode: string;
  belts: Belt[];
  imageCues: ImageCue[];
  audioPath?: string;
  subtitles?: SubtitleCue[];
};

// Very lightweight composition for in-app preview (not the full production render).
export const RemotionRoot: React.FC<Props> = ({ belts, imageCues, episode, audioPath, subtitles }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const t = frame / fps;

  const beltProgress = belts.map((b, idx) => ({
    ...b,
    idx,
    isActive: t >= (b.start ?? 0) && t < (b.end ?? 0),
  }));
  const total = beltProgress.length;
  const activeImage = imageCues.find((c) => {
    const st = c.start ?? 0;
    const end = c.end ?? (c.duration ? st + c.duration : st + 4);
    return t >= st && t < end;
  });
  const activeSubtitle = subtitles?.find((c) => t >= c.start && t < c.end);

  const opacity = interpolate(frame, [0, 20], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ backgroundColor: "#0b0c10", color: "#fff", fontFamily: "Noto Sans JP, sans-serif" }}>
      {audioPath && <Audio src={audioPath} />}

      {/* 背景画像＋グラデーション */}
      <AbsoluteFill>
        <div style={{ position: "absolute", inset: 0, opacity, overflow: "hidden" }}>
          {activeImage ? (
            <img src={activeImage.path} style={{ width: "100%", height: "100%", objectFit: "cover" }} alt="" />
          ) : (
            <div
              style={{
                fontSize: 32,
                opacity: 0.5,
                display: "flex",
                height: "100%",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              No image
            </div>
          )}
          <div
            style={{
              position: "absolute",
              inset: 0,
              background:
                "linear-gradient(180deg, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0.25) 40%, rgba(0,0,0,0.70) 100%)",
            }}
          />
        </div>

        {/* 帯エリア（トップに揃える） */}
        <div style={{ position: "absolute", top: 22, left: 24, right: 24 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-start" }}>
            <div
              style={{
                background: "linear-gradient(135deg, #c0392b, #e74c3c)",
                border: "2px solid #f6d365",
                color: "#fff",
                padding: "9px 14px",
                borderRadius: 12,
                fontWeight: 900,
                fontSize: 17,
                letterSpacing: 0.2,
                boxShadow: "0 4px 12px rgba(0,0,0,0.35)",
              }}
            >
              {episode || "タイトル未設定"}
            </div>

            {total > 0 && (
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 6,
                  maxWidth: 540,
                  alignItems: "center",
                }}
              >
                {beltProgress.map((b) => (
                  <div
                    key={b.idx}
                    style={{
                      background: b.isActive ? "#f6d365" : "#d74c3c",
                      color: b.isActive ? "#1a1a1a" : "#fff",
                      padding: "6px 11px",
                      borderRadius: 12,
                      fontWeight: 800,
                      fontSize: 12.5,
                      textAlign: "center",
                      border: b.isActive ? "1.5px solid #f1c40f" : "1.5px solid #f6d365",
                      boxShadow: b.isActive ? "0 4px 10px rgba(0,0,0,0.35)" : "0 2px 6px rgba(0,0,0,0.25)",
                      transition: "all 140ms ease",
                      minHeight: 28,
                      minWidth: 105,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      opacity: b.isActive ? 1 : 0.92,
                      whiteSpace: "nowrap",
                      textShadow: b.isActive ? "none" : "0 1px 2px rgba(0,0,0,0.35)",
                    }}
                  >
                    {b.text}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </AbsoluteFill>

      {/* 字幕ボックス（下部センター、黒帯ピル型） */}
      <AbsoluteFill style={{ padding: 32, justifyContent: "flex-end", alignItems: "center" }}>
        <div
          style={{
            minHeight: 56,
            maxWidth: "90%",
            background: "rgba(0,0,0,0.78)",
            padding: "12px 18px",
            borderRadius: 18,
            fontSize: 19,
            fontWeight: 700,
            textAlign: "center",
            lineHeight: 1.38,
            boxShadow: "0 6px 16px rgba(0,0,0,0.45)",
            border: "1.5px solid rgba(255,255,255,0.22)",
          }}
        >
          {activeSubtitle?.text ?? activeImage?.text ?? ""}
        </div>
      </AbsoluteFill>

      {/* 進捗バー */}
      <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "stretch" }}>
        <div style={{ height: 6, background: "rgba(255,255,255,0.12)", margin: "0 16px 14px" }}>
          <div
            style={{
              height: "100%",
              width: `${(frame / Math.max(1, durationInFrames - 1)) * 100}%`,
              background: "#2ecc71",
              transition: "width 50ms linear",
            }}
          />
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
