export interface ChannelBranding {
  avatar_url?: string;
  banner_url?: string;
  title?: string;
  subscriber_count?: number;
  view_count?: number;
  video_count?: number;
  custom_url?: string;
  handle?: string;
  url?: string;
  launch_date?: string;
  theme_color?: string;
  updated_at?: string;
}

// --- Domain Enums (matching python core.domain.enums) ---

export enum ChannelCode {
  CH01 = "CH01",
  CH02 = "CH02",
  CH03 = "CH03",
  CH04 = "CH04",
  CH05 = "CH05",
  CH06 = "CH06",
}

export enum GlobalStatus {
  PENDING = "pending",
  SCRIPT_IN_PROGRESS = "script_in_progress",
  SCRIPT_READY = "script_ready",
  SCRIPT_VALIDATED = "script_validated",
  PROCESSING = "processing",
  COMPLETED = "completed",
  FAILED = "failed",
  RERUN_REQUESTED = "rerun_requested",
  RERUN_IN_PROGRESS = "rerun_in_progress",
  RERUN_COMPLETED = "rerun_completed",
}

export enum StageStatus {
  PENDING = "pending",
  PROCESSING = "processing",
  COMPLETED = "completed",
  FAILED = "failed",
  RERUN_REQUESTED = "rerun_requested",
  RERUN_IN_PROGRESS = "rerun_in_progress",
  RERUN_COMPLETED = "rerun_completed",
}

export enum WorkflowStage {
  TOPIC_RESEARCH = "topic_research",
  SCRIPT_OUTLINE = "script_outline",
  SCRIPT_DRAFT = "script_draft",
  SCRIPT_ENHANCEMENT = "script_enhancement",
  SCRIPT_REVIEW = "script_review",
  QUALITY_CHECK = "quality_check",
  SCRIPT_VALIDATION = "script_validation",
  SCRIPT_POLISH_AI = "script_polish_ai",
  SCRIPT_AUDIO_AI = "script_audio_ai",
  SCRIPT_TTS_PREPARE = "script_tts_prepare",
  AUDIO_SYNTHESIS = "audio_synthesis",
  SRT_GENERATION = "srt_generation",
  TIMELINE_COPY = "timeline_copy",
  IMAGE_GENERATION = "image_generation",
}

export enum PublishStatus {
  PENDING = "pending",
  APPROVED = "approved",
  POSTED = "posted",
}

// --- Domain Models (matching python core.domain.task_schema) ---

export interface StageInfo {
  status: StageStatus;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  details?: Record<string, unknown>;
}

export interface ScriptMetadata {
  title?: string;
  theme?: string;
  character_count?: number;
  path?: string;
}

export interface AudioMetadata {
  duration?: number;
  path?: string;
  url?: string;
}

export interface ImageMetadata {
  count?: number;
  path?: string;
}

export interface PlanningMetadata {
  video_number: string;
  title: string;
  target_audience?: string;
  main_tag?: string;
  sub_tag?: string;
  life_scene?: string;
  key_concept?: string;
  benefit?: string;
  metaphor?: string;
  description_lead?: string;
  description_body?: string;
}

export interface TaskMetadata {
  title?: string;
  sheet_title?: string;
  sheet_flag?: string;
  ready_for_audio?: boolean;
  ready_for_audio_at?: string;
  ready_for_audio_note?: string;
  
  // Sub-metadata
  planning?: PlanningMetadata;
  script?: ScriptMetadata;
  audio?: AudioMetadata;
  images?: ImageMetadata;
  
  // Loose fields
  [key: string]: unknown;
}

export interface TaskStatus {
  script_id: string;
  channel: ChannelCode;
  status: GlobalStatus;
  stages: Record<string, StageInfo>; // Key is WorkflowStage
  metadata: TaskMetadata;
  created_at: string;
  updated_at: string;
}

export interface PauseMapEntry {
  section: number;
  pause_sec: number;
  source?: string | null;
  raw_tag?: string | null;
}

export interface ChannelSummary {
  code: string;
  name?: string;
  description?: string;
  video_count: number;
  branding?: ChannelBranding | null;
  spreadsheet_id?: string | null;
  youtube_title?: string | null;
  youtube_handle?: string | null;
  genre?: string | null;
}

export interface VideoSummary {
  video: string;
  script_id?: string | null;
  title?: string | null;
  status: string;
  ready_for_audio: boolean;
  stages: Record<string, string>;
  updated_at?: string | null;
  character_count?: number | null;
  planning?: PlanningInfo | null;
  youtube_description?: string | null;
}

export interface PlanningField {
  key: string;
  column: string;
  label: string;
  value?: string | null;
}

export interface PlanningInfo {
  creation_flag?: string | null;
  fields: PlanningField[];
}

export interface PlanningCreatePayload {
  channel: string;
  video_number: string;
  title: string;
  no?: string | null;
  creation_flag?: string | null;
  progress?: string | null;
  fields?: Record<string, string | null>;
}

export interface PlanningCsvRow {
  channel: string;
  video_number: string;
  script_id?: string | null;
  title?: string | null;
  script_path?: string | null;
  progress?: string | null;
  quality_check?: string | null;
  character_count?: number | null;
  updated_at?: string | null;
  planning?: PlanningInfo;
  columns?: Record<string, string | null | undefined>;
}

export interface PlanningSpreadsheetResponse {
  channel: string;
  headers: string[];
  rows: (string | null)[][];
}

export interface PromptSyncTarget {
  path: string;
  exists: boolean;
  checksum?: string | null;
  updated_at?: string | null;
}

export interface PromptDocumentSummary {
  id: string;
  label: string;
  description?: string | null;
  relative_path: string;
  size_bytes: number;
  updated_at?: string | null;
  checksum: string;
  sync_targets: PromptSyncTarget[];
}

export interface PromptDocumentDetail extends PromptDocumentSummary {
  content: string;
}

export interface PromptUpdatePayload {
  content: string;
  expectedChecksum?: string | null;
}

export interface LlmMetric {
  name: string;
  value: number | string | null;
  source?: string | null;
}

export interface LlmModelInfo {
  id: string;
  label: string;
  provider: string;
  model_id: string;
  iq?: number | null;
  knowledge_metric?: LlmMetric | null;
  specialist_metric?: LlmMetric | null;
  notes?: string | null;
  last_updated?: string | null;
}

export interface VideoDetail {
  channel: string;
  video: string;
  script_id?: string | null;
  title?: string | null;
  status: string;
  ready_for_audio: boolean;
  stages: Record<string, string>;
  stage_details?: Record<string, Record<string, unknown>> | null;
  alignment_status?: string | null;
  alignment_reason?: string | null;
  assembled_path?: string | null;
  assembled_content?: string | null;
  assembled_human_path?: string | null;
  assembled_human_content?: string | null;
  tts_path?: string | null;
  tts_content?: string | null;
  tts_plain_content?: string | null;
  tts_tagged_path?: string | null;
  tts_tagged_content?: string | null;
  script_audio_path?: string | null;
  script_audio_content?: string | null;
  script_audio_human_path?: string | null;
  script_audio_human_content?: string | null;
  srt_path?: string | null;
  srt_content?: string | null;
  audio_path?: string | null;
  audio_url?: string | null;
  audio_duration_seconds?: number | null;
  audio_updated_at?: string | null;
  audio_quality_status?: string | null;
  audio_quality_summary?: string | null;
  audio_quality_report?: string | null;
  audio_metadata?: Record<string, unknown> | null;
  tts_pause_map?: PauseMapEntry[] | null;
  audio_reviewed?: boolean;
  updated_at?: string | null;
  completed_at?: string | null;
  ui_session_token?: string | null;
  planning?: PlanningInfo | null;
  youtube_description?: string | null;
  warnings?: string[];
  redo_script?: boolean;
  redo_audio?: boolean;
  redo_note?: string | null;
  artifacts?: ArtifactsSummary | null;
}

export interface HumanScriptResponse {
  assembled_path?: string | null;
  assembled_content?: string | null;
  assembled_human_path?: string | null;
  assembled_human_content?: string | null;
  script_audio_path?: string | null;
  script_audio_content?: string | null;
  script_audio_human_path?: string | null;
  script_audio_human_content?: string | null;
  audio_reviewed: boolean;
  updated_at?: string | null;
  warnings?: string[];
}

export interface HumanScriptUpdatePayload {
  assembled_human?: string | null;
  script_audio_human?: string | null;
  audio_reviewed?: boolean;
  expectedUpdatedAt?: string | null;
}

export type ScriptManifest = Record<string, unknown>;

export interface LlmArtifactListItem {
  name: string;
  status: string;
  stage?: string | null;
  task?: string | null;
  generated_at?: string | null;
  output_path?: string | null;
  output_sha1?: string | null;
  content_chars?: number | null;
  error?: string | null;
}

export interface LlmTextArtifactSourceFile {
  path: string;
  sha1: string;
}

export interface LlmTextArtifact {
  schema: string;
  generated_at: string;
  status: string;
  stage: string;
  task: string;
  channel?: string | null;
  video?: string | null;
  output: {
    path: string;
    sha1?: string | null;
  };
  content: string;
  sources: LlmTextArtifactSourceFile[];
  llm_meta?: Record<string, unknown>;
  notes?: string;
}

export interface LlmTextArtifactUpdatePayload {
  status: "pending" | "ready";
  content: string;
  notes?: string | null;
  applyOutput?: boolean;
}

export interface ScriptTextResponse {
  path?: string | null;
  content: string;
  updated_at?: string | null;
}

export interface PlanningUpdateResponse {
  status: string;
  updated_at: string;
  planning: PlanningInfo;
}

export interface PlanningUpdatePayload {
  creationFlag?: string | null;
  fields?: Record<string, string | null>;
  expectedUpdatedAt?: string | null;
}

export interface PersonaDocumentResponse {
  channel: string;
  path: string;
  content: string;
}

export interface PersonaDocumentUpdatePayload {
  content: string;
}

export interface PlanningTemplateResponse {
  channel: string;
  path: string;
  content: string;
  headers: string[];
  sample: string[];
}

export interface PlanningTemplateUpdatePayload {
  content: string;
}

export interface RedoUpdatePayload {
  redo_script?: boolean;
  redo_audio?: boolean;
  redo_note?: string | null;
}

export interface RedoUpdateResponse {
  status: string;
  redo_script: boolean;
  redo_audio: boolean;
  redo_note?: string | null;
  updated_at: string;
}

export interface ThumbnailOverridePayload {
  thumbnail_url: string;
  thumbnail_path?: string | null;
}

export interface ThumbnailOverrideResponse {
  status: string;
  thumbnail_url: string;
  thumbnail_path?: string | null;
  updated_at: string;
}

export interface PublishLockPayload {
  force_complete?: boolean;
  published_at?: string | null;
}

export interface PublishLockResponse {
  status: string;
  channel: string;
  video: string;
  published_at: string;
  updated_at: string;
}

export interface RedoSummaryItem {
  channel: string;
  redo_script: number;
  redo_audio: number;
  redo_both: number;
}

export interface ThumbnailLookupItem {
  path: string;
  url: string;
  name?: string;
}

export interface ThumbnailLookupResponse {
  items: ThumbnailLookupItem[];
}

export interface ChannelProfileResponse {
  channel_code: string;
  channel_name?: string | null;
  audience_profile?: string | null;
  persona_summary?: string | null;
  script_prompt?: string | null;
  description?: string | null;
  default_tags?: string[] | null;
  youtube_title?: string | null;
  youtube_description?: string | null;
  youtube_handle?: string | null;
  audio_default_voice_key?: string | null;
  audio_section_voice_rules?: Record<string, string>;
  default_min_characters: number;
  default_max_characters: number;
  llm_model?: string | null;
  quality_check_template?: string | null;
  planning_persona?: string | null;
  planning_persona_path?: string | null;
  planning_required_fieldsets?: {
    min_no?: number | null;
    required_keys: string[];
    required_columns: string[];
  }[] | null;
  planning_description_defaults?: Record<string, string> | null;
  planning_template_path?: string | null;
  planning_template_headers?: string[] | null;
  planning_template_sample?: string[] | null;
}

export interface ChannelProfileUpdateAudioPayload {
  default_voice_key?: string | null;
  section_voice_rules?: Record<string, string> | null;
}

export interface ChannelProfileUpdatePayload {
  script_prompt?: string | null;
  description?: string | null;
  youtube_title?: string | null;
  youtube_description?: string | null;
  youtube_handle?: string | null;
  default_tags?: string[] | null;
  audio?: ChannelProfileUpdateAudioPayload | null;
}

export interface ApiErrorShape {
  detail?: string;
  status?: string;
}

export interface TtsValidationIssue {
  type: string;
  line?: number | null;
  detail?: string | null;
}

export interface TtsValidationResponse {
  sanitized_content: string;
  issues: TtsValidationIssue[];
  valid: boolean;
}

export interface SrtVerifyIssue {
  type: string;
  detail: string;
  block?: number | null;
  start?: number | null;
  end?: number | null;
}

export interface SrtVerifyResponse {
  valid: boolean;
  audio_duration_seconds?: number | null;
  srt_duration_seconds?: number | null;
  diff_ms?: number | null;
  issues: SrtVerifyIssue[];
}

export interface WorkflowPrecheckItem {
  script_id: string;
  video_number: string;
  progress?: string | null;
  title?: string | null;
  flag?: string | null;
}

export interface WorkflowPrecheckPendingSummary {
  channel: string;
  count: number;
  items: WorkflowPrecheckItem[];
}

export interface WorkflowPrecheckReadyEntry {
  channel: string;
  video_number: string;
  script_id: string;
  audio_status?: string | null;
}

export interface WorkflowPrecheckResponse {
  generated_at: string;
  pending: WorkflowPrecheckPendingSummary[];
  ready: WorkflowPrecheckReadyEntry[];
}

export interface LockMetricSample {
  timestamp: string;
  type: string;
  timeout: number;
  unexpected: number;
}

export interface LockMetrics {
  timeout: number;
  unexpected: number;
  history: LockMetricSample[];
}

export interface DashboardChannelSummary {
  code: string;
  total: number;
  script_completed: number;
  audio_completed: number;
  srt_completed: number;
  blocked: number;
  ready_for_audio: number;
  pending_sync: number;
}

export interface DashboardAlert {
  type: string;
  channel: string;
  video: string;
  message: string;
  updated_at?: string | null;
}

export type StageMatrix = Record<string, Record<string, Record<string, number>>>;

export interface DashboardOverview {
  generated_at: string;
  channels: DashboardChannelSummary[];
  stage_matrix: StageMatrix;
  alerts: DashboardAlert[];
}

export interface TtsReplaceRequestPayload {
  original: string;
  replacement: string;
  scope?: "first" | "all";
  update_assembled?: boolean;
  regenerate_audio?: boolean;
  expected_updated_at?: string | null;
}

export interface TtsReplaceResponse {
  replaced: number;
  content: string;
  plain_content?: string | null;
  tagged_content?: string | null;
  pause_map?: PauseMapEntry[] | null;
  audio_regenerated: boolean;
  message?: string | null;
}

export interface TtsSaveResponse {
  status: string;
  updated_at: string;
  diff?: string[];
  audio_regenerated?: boolean;
  message?: string | null;
  plain_content?: string | null;
  tagged_content?: string | null;
  pause_map?: PauseMapEntry[] | null;
}

export type NaturalCommandAction =
  | {
      type: "replace";
      target?: "tts" | "assembled" | "srt";
      original?: string | null;
      replacement?: string | null;
      scope?: "first" | "all";
      update_assembled?: boolean;
      regenerate_audio?: boolean;
    }
  | {
      type: "insert_pause";
      pause_seconds?: number | null;
      pause_scope?: "cursor" | "line_end" | "section_end";
    };

export interface NaturalCommandResponse {
  actions: NaturalCommandAction[];
  message?: string | null;
}

export interface AudioReviewItem {
  channel: string;
  video: string;
  status: string;
  title?: string | null;
  channel_title?: string | null;
  workspace_path: string;
  audio_stage: string;
  audio_stage_updated_at?: string | null;
  subtitle_stage: string;
  subtitle_stage_updated_at?: string | null;
  audio_quality_status?: string | null;
  audio_quality_summary?: string | null;
  audio_updated_at?: string | null;
  audio_duration_seconds?: number | null;
  audio_url: string;
  srt_url?: string | null;
  audio_waveform_image?: string | null;
  audio_waveform_url?: string | null;
  audio_message?: string | null;
  audio_error?: string | null;
  manual_pause_count?: number | null;
  ready_for_audio?: boolean;
  tts_input_path?: string | null;
  audio_log_url?: string | null;
  audio_engine?: string | null;
  audio_log_summary?: {
    engine?: string | null;
    duration_sec?: number | null;
    chunk_count?: number | null;
  } | null;
}

export interface LlmMeta {
  request_id?: string | null;
  model?: string | null;
  provider?: string | null;
  latency_ms?: number | null;
  usage?: {
    prompt_tokens?: number | null;
    completion_tokens?: number | null;
    total_tokens?: number | null;
  } | null;
}

export interface RunTtsV2Response {
  engine?: string | null;
  wav_path: string;
  srt_path?: string | null;
  log?: string | null;
  stdout?: string | null;
  final_wav?: string | null;
  final_srt?: string | null;
  llm_meta?: LlmMeta | null;
}

export type ThumbnailProjectStatus = "draft" | "in_progress" | "review" | "approved" | "published" | "archived";

export type ThumbnailVariantStatus = "draft" | "candidate" | "review" | "approved" | "archived";

export interface ThumbnailVariant {
  id: string;
  label?: string | null;
  status: ThumbnailVariantStatus;
  image_url?: string | null;
  image_path?: string | null;
  preview_url?: string | null;
  notes?: string | null;
  tags?: string[] | null;
  is_selected?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ThumbnailProject {
  channel: string;
  video: string;
  script_id?: string | null;
  title?: string | null;
  sheet_title?: string | null;
  status: ThumbnailProjectStatus;
  owner?: string | null;
  summary?: string | null;
  notes?: string | null;
  tags?: string[] | null;
  variants: ThumbnailVariant[];
  ready_for_publish?: boolean;
  updated_at?: string | null;
  status_updated_at?: string | null;
  due_at?: string | null;
  selected_variant_id?: string | null;
  audio_stage?: string | null;
  script_stage?: string | null;
}

export interface ThumbnailChannelVideo {
  video_id: string;
  title: string;
  url: string;
  thumbnail_url?: string | null;
  published_at?: string | null;
  view_count?: number | null;
  duration_seconds?: number | null;
  estimated_ctr?: number | null;
}

export interface ThumbnailChannelSummary {
  total: number;
  subscriber_count?: number | null;
  view_count?: number | null;
  video_count?: number | null;
}

export interface ThumbnailChannelBlock {
  channel: string;
  channel_title?: string | null;
  summary: ThumbnailChannelSummary;
  projects: ThumbnailProject[];
  videos: ThumbnailChannelVideo[];
  library_path?: string | null;
}

export interface ThumbnailOverview {
  generated_at?: string | null;
  channels: ThumbnailChannelBlock[];
}

export interface ThumbnailLibraryAsset {
  id: string;
  file_name: string;
  size_bytes: number;
  updated_at: string;
  public_url: string;
  relative_path: string;
}

export interface ThumbnailQuickHistoryEntry {
  channel: string;
  video: string;
  label?: string | null;
  asset_name: string;
  image_path?: string | null;
  public_url: string;
  timestamp: string;
}

export interface ThumbnailLibraryAssignResponse {
  file_name: string;
  image_path: string;
  public_url: string;
}

export interface ThumbnailDescriptionResponse {
  description: string;
  model?: string | null;
  source: "openai" | "openrouter" | "heuristic";
}

export interface LlmConfig {
  caption_provider: "openai" | "openrouter";
  openai_caption_model?: string | null;
  openrouter_caption_model?: string | null;
  openai_key_configured: boolean;
  openrouter_key_configured: boolean;
  openai_models: string[];
  openrouter_models: string[];
  openai_key_preview?: string | null;
  openrouter_key_preview?: string | null;
  openai_models_error?: string | null;
  openrouter_models_error?: string | null;
  phase_models: Record<string, PhaseModel>;
  phase_details?: Record<string, PhaseDetail>;
}

export interface LlmSettings {
  llm: LlmConfig;
}

export interface LlmSettingsUpdate {
  caption_provider?: "openai" | "openrouter";
  openai_api_key?: string;
  openai_caption_model?: string | null;
  openrouter_api_key?: string;
  openrouter_caption_model?: string | null;
  phase_models?: Record<string, PhaseModel>;
}

export interface PhaseModel {
  label: string;
  provider: "openai" | "openrouter" | "gemini";
  model: string | null;
}

export interface PhaseDetail extends PhaseModel {
  endpoint?: string | null;
  prompt_source?: string | null;
  path?: string | null;
  role?: string | null;
}

export interface BatchWorkflowConfigPayload {
  min_characters?: number;
  max_characters?: number;
  script_prompt_template?: string | null;
  quality_check_template?: string | null;
  llm_model?: string | null;
  loop_mode?: boolean;
  auto_retry?: boolean;
  debug_log?: boolean;
}

export interface BatchWorkflowRequestPayload {
  channel_code: string;
  video_numbers: string[];
  config?: BatchWorkflowConfigPayload;
}

export interface BatchWorkflowTask {
  task_id: string;
  channel_code: string;
  video_numbers: string[];
  status: string;
  log_path?: string | null;
  config_path?: string | null;
  created_at?: string | null;
  queue_entry_id?: number | null;
}

export interface BatchWorkflowLogResponse {
  task_id: string;
  lines: string[];
}

export interface BatchQueueEntry {
  id: number;
  channel_code: string;
  video_numbers: string[];
  status: string;
  task_id?: string | null;
  created_at: string;
  updated_at: string;
  processed_count?: number | null;
  total_count?: number | null;
  current_video?: string | null;
  issues?: Record<string, string> | null;
}

export interface VideoProjectSummary {
  id: string;
  title?: string | null;
  status: string;
  next_action?: string | null;
  template_used?: string | null;
  image_count: number;
  log_count: number;
  created_at?: string | null;
  last_updated?: string | null;
  srt_file?: string | null;
  draft_path?: string | null;
  channel_id?: string | null;
  channelId?: string | null;
  source_status?: SourceStatus | null;
  sourceStatus?: SourceStatus | null;
}

export interface VideoProjectImageAsset {
  path: string;
  url: string;
  size_bytes?: number;
  modified_at?: string;
}

export interface VideoProjectImageSample {
  path: string;
  url: string;
}

export interface VideoProjectCue {
  index: number;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  summary?: string | null;
  text?: string | null;
  visual_focus?: string | null;
  role_tag?: string | null;
  role_asset?: {
    path?: string;
    kind?: string;
    role_tag?: string;
    note?: string;
  } | null;
  emotional_tone?: string | null;
  prompt?: string | null;
  context_reason?: string | null;
}

export interface VideoProjectBeltEntry {
  text: string;
  start: number;
  end: number;
}

export interface VideoProjectChapterEntry {
  key: string;
  title: string;
}

export interface VideoProjectLayerSegment {
  id: string;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  material_id?: string | null;
  material_name?: string | null;
  material_path?: string | null;
  transition_name?: string | null;
  transition_duration_sec?: number | null;
}

export interface VideoProjectLayer {
  id: string;
  name: string;
  type: string;
  segment_count: number;
  duration_sec: number;
  has_fade: boolean;
  segments: VideoProjectLayerSegment[];
}

export interface ArtifactEntry {
  key: string;
  label: string;
  path: string;
  kind: "file" | "dir";
  exists: boolean;
  size_bytes?: number | null;
  modified_time?: string | null;
  meta?: Record<string, unknown>;
}

export interface ArtifactsSummary {
  project_dir?: string | null;
  items: ArtifactEntry[];
}

export type VideoProjectArtifactEntry = ArtifactEntry;
export type VideoProjectArtifacts = ArtifactsSummary;

export interface SrtSegment {
  index: number;
  start_sec: number;
  end_sec: number;
  text: string;
}

export interface SrtSegmentsArtifact {
  schema: string;
  generated_at?: string | null;
  episode?: string | null;
  source_srt: {
    path: string;
    sha1: string;
  };
  segments: SrtSegment[];
  meta?: Record<string, unknown>;
}

export interface VisualCuesPlanSection {
  start_segment: number;
  end_segment: number;
  summary: string;
  visual_focus: string;
  emotional_tone: string;
  persona_needed: boolean;
  role_tag: string;
  section_type: string;
}

export interface VisualCuesPlanArtifact {
  schema: string;
  generated_at: string;
  status: "pending" | "ready";
  source_srt: {
    path: string;
    sha1: string;
  };
  segment_count: number;
  base_seconds: number;
  sections: VisualCuesPlanSection[];
  episode?: string | null;
  style_hint?: string;
  llm_task?: Record<string, unknown>;
  meta?: Record<string, unknown>;
}

export interface VisualCuesPlanUpdatePayload {
  status: "pending" | "ready";
  sections: VisualCuesPlanSection[];
  styleHint?: string | null;
}

export interface VideoProjectDetail {
  summary: VideoProjectSummary;
  images?: VideoProjectImageAsset[];
  image_samples: VideoProjectImageSample[];
  log_excerpt: string[];
  cues: VideoProjectCue[];
  belt: VideoProjectBeltEntry[];
  chapters: VideoProjectChapterEntry[];
  srt_preview: string[];
  warnings: string[];
  layers: VideoProjectLayer[];
  guard?: VideoProjectGuard | null;
  sourceStatus?: SourceStatus | null;
  generationOptions?: VideoGenerationOptions | null;
  capcut?: VideoProjectCapcutSettings | null;
  artifacts?: VideoProjectArtifacts | null;
}

export interface VideoGenerationOptions {
  imgdur: number;
  crossfade: number;
  fps: number;
  style: string;
  size: string;
  fit: "cover" | "contain" | "fill";
  margin: number;
}

export interface VideoProjectGuardIssue {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface VideoProjectGuard {
  status: "ok" | "fail";
  cueCount: number | null;
  imageCount: number | null;
  minImageBytes: number | null;
  personaRequired: boolean;
  missingProfiles: number[];
  tinyImages: string[];
  recommendedCommands: string[];
  issues: VideoProjectGuardIssue[];
  projectDir?: string | null;
  imageDir?: string | null;
}

export interface VideoProjectCapcutSettings {
  channelId?: string | null;
  templateUsed?: string | null;
  draftName?: string | null;
  draftPath?: string | null;
  transform: {
    tx: number;
    ty: number;
    scale: number;
  };
  crossfadeSec: number;
  fadeDurationSec: number;
  openingOffset: number;
}

export interface SourceStatus {
  channel?: string | null;
  videoNumber?: string | null;
  srtReady?: boolean;
  audioReady?: boolean;
  srtPath?: string | null;
  audioPath?: string | null;
}

export interface VideoJobRecord {
  id: string;
  project_id: string;
  action: string;
  options?: Record<string, unknown> | null;
  status: "queued" | "running" | "succeeded" | "failed";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  exit_code?: number | null;
  error?: string | null;
  command?: string[] | null;
  note?: string | null;
  summary?: string | null;
  log_path?: string | null;
  log_excerpt?: string[] | null;
}

export interface VideoJobCreatePayload {
  action: string;
  options?: Record<string, unknown>;
  note?: string;
}

export interface VideoProjectCreatePayload {
  projectId: string;
  channelId?: string;
  targetSections?: number;
  existingSrtPath?: string;
  srtFile?: File | null;
}

export interface VideoProjectCreateResponse {
  project_id: string;
  output_dir: string;
  srt_file?: string | null;
  channel_id?: string | null;
  target_sections?: number | null;
}

export interface CapcutInstallResult {
  status: string;
  source: string;
  target: string;
  overwrite: boolean;
}

export interface VideoProductionSrtFile {
  channelId: string;
  name: string;
  relativePath: string;
  size?: number;
  modifiedTimeIso?: string;
}

export interface AutoDraftSrtItem {
  name: string;
  path: string;
}

export interface AutoDraftListResponse {
  items: AutoDraftSrtItem[];
  inputRoot: string;
}

export interface AutoDraftSrtContent {
  name: string;
  path: string;
  content: string;
  sizeBytes?: number | null;
  modifiedTime?: number | null;
}

export interface ProjectSrtContent {
  name: string;
  path: string;
  content: string;
  sizeBytes?: number | null;
  modifiedTime?: number | null;
}

export interface AutoDraftCreateResponse {
  ok: boolean;
  stdout: string;
  stderr: string;
  runName: string;
  title: string;
  channel: string;
  runDir: string;
}

export interface AutoDraftCreatePayload {
  srtPath: string;
  channel?: string | null;
  runName?: string | null;
  title?: string | null;
  labels?: string | null;
  template?: string | null;
  promptTemplate?: string | null;
  beltMode?: "llm" | "grouped" | "equal" | "existing" | null;
  chaptersJson?: string | null;
  episodeInfoJson?: string | null;
  imgDuration?: number | null;
}

export interface PromptTemplateItem {
  name: string;
  path: string;
}

export interface PromptTemplateListResponse {
  items: PromptTemplateItem[];
  templateRoot: string;
}

export interface PromptTemplateContentResponse {
  name: string;
  path: string;
  content: string;
  templateRoot: string;
}

export interface VideoProductionChannelPreset {
  channelId: string;
  name: string;
  promptTemplate?: string | null;
  style?: string | null;
  capcutTemplate?: string | null;
  personaRequired?: boolean;
  imageMinBytes?: number | null;
  position?: {
    tx?: number;
    ty?: number;
    scale?: number;
  };
  belt?: {
    enabled?: boolean;
    opening_offset?: number;
    requires_config?: boolean;
  };
  beltLabels?: string | null;
  notes?: string;
  status?: string;
  srtFiles?: VideoProductionSrtFile[];
}

export interface ChannelPresetUpdatePayload {
  name?: string;
  promptTemplate?: string | null;
  style?: string | null;
  capcutTemplate?: string | null;
  personaRequired?: boolean;
  imageMinBytes?: number | null;
  position?: {
    tx?: number | null;
    ty?: number | null;
    scale?: number | null;
  } | null;
  belt?: {
    enabled?: boolean | null;
    opening_offset?: number | null;
    requires_config?: boolean | null;
  } | null;
  notes?: string | null;
  status?: string | null;
}

export interface CapcutDraftSummary {
  name: string;
  path: string;
  title: string;
  duration: number;
  imageCount: number;
  modifiedTime: number;
  modifiedTimeIso?: string;
  channelId?: string | null;
  channelName?: string | null;
  videoNumber?: string | null;
  projectId?: string | null;
  projectExists?: boolean;
  projectHint?: string | null;
}

export interface CapcutDraftSegment {
  materialId: string;
  path: string;
  filename: string;
  startSec: number;
  endSec: number;
  durationSec: number;
}

export interface CapcutDraftDetail {
  draft: Record<string, unknown>;
  segments: CapcutDraftSegment[];
}

export interface RemotionAssetStatus {
  label: string;
  path?: string | null;
  exists: boolean;
  type: "file" | "directory";
  sizeBytes?: number | null;
  modifiedTime?: string | null;
}

export interface RemotionRenderOutput {
  path: string;
  fileName: string;
  sizeBytes?: number | null;
  modifiedTime?: string | null;
}

export interface RemotionDriveUpload {
  uploadedAt?: string | null;
  fileId?: string | null;
  fileName?: string | null;
  webViewLink?: string | null;
  folderPath?: string | null;
}

export interface RemotionProjectSummary {
  projectId: string;
  channelId?: string | null;
  title?: string | null;
  durationSec?: number | null;
  status: "missing_assets" | "assets_ready" | "scaffolded" | "rendered";
  issues: string[];
  metrics: {
    imageCount: number;
    assetReady: number;
    assetTotal: number;
  };
  assets: RemotionAssetStatus[];
  outputs: RemotionRenderOutput[];
  remotionDir?: string | null;
  timelinePath?: string | null;
  lastRendered?: string | null;
  driveUpload?: RemotionDriveUpload | null;
}

export interface ResearchFileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number;
  modified?: string;
}

export interface ResearchListResponse {
  base: string;
  path: string;
  entries: ResearchFileEntry[];
}

export interface ResearchFileResponse {
  base: string;
  path: string;
  size?: number;
  modified?: string;
  content: string;
}

// UI params (image/belt defaults)
export interface UiParams {
  image_track_target_count: number;
  belt_segments: number;
  belt_text_limit: number;
  start_offset_sec: number;
  max_duration_sec: number;
  allow_extra_video_tracks: boolean;
}

export interface UiParamsResponse {
  params: UiParams;
}

export interface AudioIntegrityItem {
  channel: string;
  video: string;
  missing: string[];
  audio_path?: string | null;
  srt_path?: string | null;
  b_text_path?: string | null;
  audio_duration?: number | null;
  srt_duration?: number | null;
  duration_diff?: number | null;
}

export interface PauseEntry {
  section: number;
  pause_sec: number;
}

export interface VoicevoxKanaDiff {
  engine_kana: string;
  llm_kana: string;
  diff: unknown[];
}

export interface AudioAnalysis {
  channel: string;
  video: string;
  b_text_with_pauses?: string | null;
  pause_map?: PauseEntry[] | null;
  voicevox_kana?: string | null;
  voicevox_kana_corrected?: string | null;
  voicevox_kana_diff?: VoicevoxKanaDiff | null;
  voicevox_kana_llm_ref?: unknown | null;
  voicevox_accent_phrases?: unknown | null;
  warnings: string[];
}
