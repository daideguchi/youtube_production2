import type { Belt, SubtitleCue } from "../types/remotionTypes";

type BeltConfig = { episode?: string; total_duration?: number; belts?: Belt[] };
type ImageCue = {
  path: string;
  start?: number;
  end?: number;
  duration?: number;
  text?: string;
  start_sec?: number;
  end_sec?: number;
  duration_sec?: number;
};
type ImageCueFile = { cues?: ImageCue[] } | ImageCue[];

type RemotionInput = {
  episode?: string;
  totalDuration?: number;
  belts?: Belt[];
  imageCues?: ImageCue[];
  audioPath?: string;
  subtitles?: SubtitleCue[];
  mp4Url?: string;
};

export async function loadRemotionInput(runId: string): Promise<RemotionInput> {
  const base = `/remotion/input/${runId}`;
  const mp4Candidates = [
    `/remotion/out/remotion_${runId}_preview.mp4`,
    `/remotion/out/${runId}.mp4`,
    `/remotion/out/${runId}_test.mp4`,
  ];

  const [beltRes, cuesRes, srtRes, mp4Url] = await Promise.all([
    fetch(`${base}/belt_config.json`),
    fetch(`${base}/image_cues.json`),
    fetch(`${base}/${runId}.srt`).catch(() => null),
    (async () => {
      for (const url of mp4Candidates) {
        try {
          const res = await fetch(url, { method: "HEAD" });
          if (res.ok) return url;
        } catch (e) {
          continue;
        }
      }
      return undefined;
    })(),
  ]);

  if (!beltRes.ok) {
    throw new Error(`belt_config.json 読み込み失敗 (${beltRes.status})`);
  }
  if (!cuesRes.ok) {
    throw new Error(`image_cues.json 読み込み失敗 (${cuesRes.status})`);
  }

  const beltJson = (await beltRes.json()) as BeltConfig;
  const cuesJsonRaw = (await cuesRes.json()) as ImageCueFile;
  const cuesArray: ImageCue[] = Array.isArray(cuesJsonRaw) ? cuesJsonRaw : Array.isArray(cuesJsonRaw?.cues) ? cuesJsonRaw.cues : [];

  // image pathをpublic配下相対パスにする
  const imageCues = cuesArray
    .map((c) => {
      const start = c.start ?? c.start_sec ?? 0;
      const end = c.end ?? c.end_sec ?? (c.duration_sec ? start + c.duration_sec : c.duration ? start + c.duration : start + 4);
      const duration = c.duration ?? c.duration_sec ?? (end - start);
      const raw = c.path || "";
      if (raw.startsWith("http")) {
        return { ...c, path: raw, start, end, duration };
      }
      // 絶対パスの場合はファイル名だけ拾って remotion/input/<runId>/images/<basename> に張り替える
      const basename = raw.split("/").filter(Boolean).pop() ?? raw;
      return { ...c, path: `${base}/images/${basename}`, start, end, duration };
    })
    .sort((a, b) => (a.start ?? 0) - (b.start ?? 0));

  // オーディオは存在チェックは後段（プレイヤー側）に任せる
  const audioPath = `${base}/${runId}.wav`;

  // 総尺はベルトが無ければ imageCues から推定
  const derivedDuration = beltJson.total_duration ?? Math.max(0, ...imageCues.map((c) => c.end ?? 0));

  // SRT を字幕として読み込む
  let subtitles: SubtitleCue[] | undefined;
  if (srtRes && srtRes.ok) {
    try {
      const srtText = await srtRes.text();
      subtitles = parseSrt(srtText);
    } catch {
      subtitles = undefined;
    }
  }

  return {
    episode: beltJson.episode,
    totalDuration: derivedDuration,
    belts: beltJson.belts ?? [],
    imageCues,
    audioPath,
    subtitles,
    mp4Url,
  };
}

// 簡易SRTパーサ
function parseSrt(text: string): SubtitleCue[] {
  const blocks = text.split(/\r?\n\r?\n/);
  const cues: SubtitleCue[] = [];
  for (const b of blocks) {
    const lines = b.trim().split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) continue;
    const tm = (lines[1] || lines[0]).match(/(.+)\s-->\s(.+)/);
    if (!tm) continue;
    const start = parseTime(tm[1].trim());
    const end = parseTime(tm[2].trim());
    const content = lines.slice(2).join(" ").trim();
    if (!Number.isFinite(start) || !Number.isFinite(end) || !content) continue;
    cues.push({ start, end, text: content });
  }
  return cues.sort((a, b) => a.start - b.start);
}

function parseTime(t: string): number {
  const m = t.match(/(\d+):(\d+):(\d+),(\d+)/);
  if (!m) return 0;
  const [, h, mnt, s, ms] = m;
  return Number(h) * 3600 + Number(mnt) * 60 + Number(s) + Number(ms) / 1000;
}
