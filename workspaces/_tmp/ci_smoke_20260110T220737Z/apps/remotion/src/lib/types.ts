export type ImageCue = {
  path: string;
  start_sec: number;
  end_sec: number;
};

export type ImageCues = {
  cues: ImageCue[];
};

export type Belt = {
  text: string;
  start: number;
  end: number;
};

export type BeltConfig = {
  belts: Belt[];
  opening_offset?: number;
  total_duration?: number;
  episode?: string;
  main_title?: string;
};

export type EpisodeInfo = {
  title?: string;
  episode?: string;
};

export type Chapters = {
  chapters: { title: string; start: number }[];
};

export type RunData = {
  runDir: string;
  imageCues: ImageCues;
  belt: BeltConfig;
  episode?: EpisodeInfo;
  chapters?: Chapters;
  srtText: string;
  srtCues: SubtitleCue[];
  position?: Position;
};

export type SubtitleCue = {
  start: number;
  end: number;
  text: string;
};

export type Position = {
  tx: number;
  ty: number;
  scale: number;
};

export type LayoutConfig = {
  beltTopPct?: number; // default 82
  beltHeightPct?: number; // default 16
  beltInsetPx?: number; // when beltTopPct is near 0 (top-left anchor)
  subtitleBottomPx?: number; // default 120
  subtitleMaxWidthPct?: number; // default 80
  subtitleFontSize?: number; // default 34
  beltMainScale?: number; // optional scale multiplier for main belt size
  beltSubScale?: number; // optional scale multiplier for sub belt size
  beltGapScale?: number; // optional scale for sub belt gap
};
