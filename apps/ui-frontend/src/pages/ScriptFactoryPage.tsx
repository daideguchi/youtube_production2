import { useCallback, useEffect, useMemo, useState } from "react";
import { useOutletContext } from "react-router-dom";
import {
  cancelBatchQueueEntry,
  enqueueBatchWorkflow,
  fetchBatchQueue,
  fetchBatchWorkflowLog,
  fetchBatchWorkflowTask,
  fetchChannelProfile,
  fetchLlmSettings,
  fetchPlanningRows,
  fetchPlanningSpreadsheet,
  updatePlanning,
} from "../api/client";
import type {
  BatchQueueEntry,
  BatchWorkflowTask,
  ChannelProfileResponse,
  PlanningCsvRow,
  PlanningInfo,
  PlanningSpreadsheetResponse,
} from "../api/types";
import type { ShellOutletContext } from "../layouts/AppShell";

const FLAG_LABELS: Record<string, string> = {
  "": "未設定",
  "0": "未着手",
  "1": "進行中",
  "2": "ボツ",
  "3": "保留",
  "9": "ロック",
};

const TASK_STATUS_LABELS: Record<string, string> = {
  pending: "待機中",
  running: "実行中",
  succeeded: "完了",
  failed: "失敗",
};

const QUEUE_STATUS_LABELS: Record<string, string> = {
  queued: "待機",
  running: "実行中",
  succeeded: "完了",
  failed: "失敗",
  cancelled: "キャンセル",
};

const DEFAULT_FORM = {
  minCharacters: 8000,
  maxCharacters: 12000,
  llmModel: "",
  scriptPrompt: "",
  qualityTemplate: "",
  loopMode: true,
  autoRetry: true,
  debugLog: false,
};

const FALLBACK_LLM_MODEL = "qwen/qwen3-14b:free";

type FormState = typeof DEFAULT_FORM;
type FormFieldName = keyof FormState;
type SpreadsheetRow = { rowIndex: number; key: string | null; cells: string[] };

type CellOverlay = {
  content: string;
  header: string;
  position: { top: number; left: number };
};

function formatDate(value?: string | null): string {
  if (!value) return "―";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ja-JP", { hour12: false });
}

function queueStatusClass(status: string): string {
  switch (status) {
    case "queued":
      return "status-chip status-chip--warning";
    case "running":
      return "status-chip status-chip--warning";
    case "failed":
      return "status-chip status-chip--danger";
    default:
      return "status-chip";
  }
}

function queueProgressPercent(entry: BatchQueueEntry): number | null {
  if (!entry.total_count || entry.total_count <= 0) {
    return null;
  }
  const processed = entry.processed_count ?? 0;
  return Math.max(0, Math.min(100, Math.round((processed / entry.total_count) * 100)));
}

function queueProgressLabel(entry: BatchQueueEntry): string {
  if (!entry.total_count || entry.total_count <= 0) {
    return "進捗不明";
  }
  const processed = entry.processed_count ?? 0;
  return `${processed} / ${entry.total_count}`;
}

function isBlockedFlag(flag?: string | null): boolean {
  return flag === "2" || flag === "9";
}

function buildRowKey(row: PlanningCsvRow): string {
  return `${row.channel}-${row.video_number}`;
}

const HEADER_LABELS = {
  title: "タイトル",
  videoNumber: "動画番号",
  scriptId: "動画ID",
  creationFlag: "作成フラグ",
  updatedAt: "更新日時",
  progress: "進捗",
};

const HEADER_ORDER: string[] = [
  HEADER_LABELS.videoNumber,
  HEADER_LABELS.title,
  HEADER_LABELS.creationFlag,
  HEADER_LABELS.progress,
  HEADER_LABELS.scriptId,
  HEADER_LABELS.updatedAt,
];

const COMPLETED_STAGE_THRESHOLD = 9;

function extractStageNumber(value?: string | null): number | null {
  if (!value) {
    return null;
  }
  const match = value.match(/(\d+)\s*\/\s*13/);
  if (!match) {
    return null;
  }
  const stage = Number(match[1]);
  return Number.isNaN(stage) ? null : stage;
}

function normalizeVideoNumber(value?: string | null): string | null {
  if (!value) return null;
  const digits = value.replace(/[^0-9]/g, "");
  if (!digits) return null;
  return digits.padStart(3, "0");
}

export function ScriptFactoryPage() {
  const {
    channels,
    channelsLoading,
    channelsError,
    selectedChannel,
    selectChannel,
    selectedChannelSummary,
  } = useOutletContext<ShellOutletContext>();

  const [channelProfile, setChannelProfile] = useState<ChannelProfileResponse | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);

  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
  const [formValues, setFormValues] = useState<FormState>(DEFAULT_FORM);
  const [isEnqueuing, setIsEnqueuing] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [taskInfo, setTaskInfo] = useState<BatchWorkflowTask | null>(null);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [planningOverrides, setPlanningOverrides] = useState<Record<string, PlanningInfo>>({});
  const [flagSaving, setFlagSaving] = useState<Record<string, boolean>>({});
  const [tableMessage, setTableMessage] = useState<string | null>(null);
  const [planningRows, setPlanningRows] = useState<PlanningCsvRow[]>([]);
  const [planningLoading, setPlanningLoading] = useState(false);
  const [planningError, setPlanningError] = useState<string | null>(null);
  const [spreadsheetData, setSpreadsheetData] = useState<PlanningSpreadsheetResponse | null>(null);
  const [spreadsheetLoading, setSpreadsheetLoading] = useState(false);
  const [spreadsheetError, setSpreadsheetError] = useState<string | null>(null);
  const [cellOverlay, setCellOverlay] = useState<CellOverlay | null>(null);
  const [dataReloadKey, setDataReloadKey] = useState(0);
  const [queueEntries, setQueueEntries] = useState<BatchQueueEntry[]>([]);
  const [queueLoading, setQueueLoading] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [queueMessage, setQueueMessage] = useState<string | null>(null);
  const [queueReloadKey, setQueueReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchLlmSettings()
      .then((settings) => {
        if (cancelled) return;
        const phase = settings.llm.phase_models?.script_rewrite;
        const defaultModel = phase?.model ? `${phase.provider}:${phase.model}` : null;
        if (defaultModel) {
          setFormValues((prev) => {
            if (!prev.llmModel || prev.llmModel === FALLBACK_LLM_MODEL) {
              return { ...prev, llmModel: defaultModel };
            }
            return prev;
          });
        }
      })
      .catch((err) => console.warn("Failed to load LLM settings", err));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedChannel) {
      setChannelProfile(null);
      setProfileError(null);
      setSelectedRows(new Set());
      setTaskId(null);
      setTaskInfo(null);
      setLogLines([]);
      setPlanningOverrides({});
      setFlagSaving({});
      setTableMessage(null);
      setFormValues(DEFAULT_FORM);
      return;
    }
    setSelectedRows(new Set());
    let cancelled = false;
    setProfileLoading(true);
    setProfileError(null);
    fetchChannelProfile(selectedChannel)
      .then((profile) => {
        if (cancelled) return;
        setChannelProfile(profile);
        setFormValues((prev) => {
          const profileModel = (profile.llm_model ?? "").trim();
          const prevModel = (prev.llmModel ?? "").trim();
          const useProfileModel =
            profileModel.length > 0 &&
            (profileModel !== FALLBACK_LLM_MODEL || prevModel.length === 0 || prevModel === FALLBACK_LLM_MODEL);

          return {
            ...prev,
            minCharacters: profile.default_min_characters ?? prev.minCharacters,
            maxCharacters: profile.default_max_characters ?? prev.maxCharacters,
            llmModel: useProfileModel ? profileModel : prev.llmModel,
            scriptPrompt: profile.script_prompt ?? prev.scriptPrompt,
            qualityTemplate: profile.quality_check_template ?? prev.qualityTemplate,
          };
        });
      })
      .catch((error) => {
        if (cancelled) return;
        setProfileError(error instanceof Error ? error.message : String(error));
        setChannelProfile(null);
      })
      .finally(() => {
        if (!cancelled) {
          setProfileLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedChannel]);

  useEffect(() => {
    setQueueMessage(null);
  }, [selectedChannel]);

  useEffect(() => {
    let cancelled = false;
    setPlanningLoading(true);
    setPlanningError(null);
    fetchPlanningRows()
      .then((data) => {
        if (cancelled) return;
        setPlanningRows(data);
      })
      .catch((error) => {
        if (cancelled) return;
        setPlanningError(error instanceof Error ? error.message : String(error));
        setPlanningRows([]);
      })
      .finally(() => {
        if (!cancelled) {
          setPlanningLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [dataReloadKey]);

useEffect(() => {
  let cancelled = false;
  setQueueLoading(true);
  setQueueError(null);
  // Allow disabling noisy polling via env (default: disabled).
  const pollingEnabled = process.env.REACT_APP_DISABLE_BATCH_QUEUE_POLLING === "0";
  if (!pollingEnabled) {
    setQueueLoading(false);
    return () => {
      cancelled = true;
    };
  }
  fetchBatchQueue()
    .then((data) => {
      if (cancelled) return;
      setQueueEntries(data);
    })
    .catch((error) => {
      if (cancelled) return;
      setQueueError(error instanceof Error ? error.message : String(error));
      setQueueEntries([]);
    })
    .finally(() => {
      if (!cancelled) {
        setQueueLoading(false);
      }
    });
  return () => {
    cancelled = true;
  };
}, [queueReloadKey]);

useEffect(() => {
  const pollingEnabled = process.env.REACT_APP_DISABLE_BATCH_QUEUE_POLLING === "0";
  if (!pollingEnabled) {
    return () => undefined;
  }
  const interval = setInterval(() => {
    setQueueReloadKey((prev) => prev + 1);
  }, 7000);
  return () => clearInterval(interval);
}, []);

  useEffect(() => {
    if (taskId) {
      return;
    }
    const preferred = queueEntries.find(
      (entry) => entry.status === "running" && entry.task_id && (!selectedChannel || entry.channel_code === selectedChannel)
    );
    const fallback = queueEntries.find((entry) => entry.status === "running" && entry.task_id);
    const target = preferred ?? fallback;
    if (target?.task_id) {
      setTaskId(target.task_id);
      setLogLines([]);
    }
  }, [queueEntries, selectedChannel, taskId]);

  const spreadsheetHeaders = useMemo(() => spreadsheetData?.headers ?? [], [spreadsheetData]);
  const headerIndexMap = useMemo(() => {
    const map: Record<string, number> = {};
    spreadsheetHeaders.forEach((header, index) => {
      map[header] = index;
    });
    return map;
  }, [spreadsheetHeaders]);
  const orderedHeaders = useMemo(() => {
    const existing = new Set(spreadsheetHeaders);
    const orderedKnown = HEADER_ORDER.filter((header) => existing.has(header));
    const remaining = spreadsheetHeaders.filter((header) => !orderedKnown.includes(header));
    return [...orderedKnown, ...remaining];
  }, [spreadsheetHeaders]);
  const orderedHeaderIndexMap = useMemo(() => {
    const map: Record<string, number> = {};
    orderedHeaders.forEach((header, index) => {
      map[header] = index;
    });
    return map;
  }, [orderedHeaders]);
  const creationFlagIndex = orderedHeaderIndexMap[HEADER_LABELS.creationFlag] ?? -1;
  const progressIndex = orderedHeaderIndexMap[HEADER_LABELS.progress] ?? -1;

  const tableRows = useMemo(() => {
    if (!selectedChannel || !spreadsheetData) {
      return [] as SpreadsheetRow[];
    }
    const videoIdx = orderedHeaderIndexMap[HEADER_LABELS.videoNumber] ?? -1;
    const scriptIdIdx = orderedHeaderIndexMap[HEADER_LABELS.scriptId] ?? -1;
    const mapped = spreadsheetData.rows.map((cellsRaw, idx) => {
      const normalizedCells = orderedHeaders.map((header) => {
        const rawIndex = headerIndexMap[header];
        const value = rawIndex == null ? "" : cellsRaw[rawIndex];
        return value == null ? "" : String(value);
      });
      const videoValue = videoIdx >= 0 ? normalizedCells[videoIdx] : "";
      const scriptValue = scriptIdIdx >= 0 ? normalizedCells[scriptIdIdx] : "";
      const fallbackVideo = scriptValue && scriptValue.includes("-") ? scriptValue.split("-").pop() : undefined;
      const videoNumber = normalizeVideoNumber(videoValue || fallbackVideo);
      const rowKey = videoNumber ? `${selectedChannel}-${videoNumber}` : null;
      return { rowIndex: idx, key: rowKey, cells: normalizedCells, sortValue: videoNumber ? Number(videoNumber) : Number.MAX_SAFE_INTEGER };
    });
    return mapped.sort((a, b) => a.sortValue - b.sortValue).map(({ sortValue, ...rest }) => rest);
  }, [selectedChannel, spreadsheetData, headerIndexMap, orderedHeaders, orderedHeaderIndexMap]);

  const planningMap = useMemo(() => {
    const map = new Map<string, PlanningInfo | undefined>();
    planningRows.forEach((row) => {
      const key = buildRowKey(row);
      map.set(key, planningOverrides[key] ?? row.planning);
    });
    return map;
  }, [planningRows, planningOverrides]);

  const progressByKey = useMemo(() => {
    const map = new Map<string, number>();
    planningRows.forEach((row) => {
      const key = buildRowKey(row);
      if (!key) {
        return;
      }
      const stage = extractStageNumber(row.progress);
      if (stage != null) {
        map.set(key, stage);
      }
    });
    return map;
  }, [planningRows]);

  const channelNameMap = useMemo(() => {
    const map = new Map<string, string>();
    channels.forEach((channel) => {
      const name = channel.name ?? channel.branding?.title ?? channel.youtube_title ?? channel.code;
      map.set(channel.code, name);
    });
    return map;
  }, [channels]);

  const isRowCompleted = useCallback(
    (row: SpreadsheetRow) => {
      if (row.key) {
        const stageFromPlanning = progressByKey.get(row.key);
        if (stageFromPlanning != null) {
          return stageFromPlanning >= COMPLETED_STAGE_THRESHOLD;
        }
      }
      if (progressIndex < 0) {
        return false;
      }
      const value = row.cells[progressIndex];
      const stageNumber = extractStageNumber(value);
      if (stageNumber == null) {
        return false;
      }
      return stageNumber >= COMPLETED_STAGE_THRESHOLD;
    },
    [progressByKey, progressIndex]
  );

  useEffect(() => {
    if (!planningRows.length) {
      setSelectedRows(new Set());
      setPlanningOverrides({});
      setFlagSaving({});
      return;
    }
    const available = new Set(planningRows.map((row) => buildRowKey(row)));
    setSelectedRows((prev) => {
      const next = new Set<string>();
      prev.forEach((key) => {
        if (available.has(key)) {
          next.add(key);
        }
      });
      return next;
    });
    setPlanningOverrides((prev) => {
      const next: Record<string, PlanningInfo> = {};
      Object.entries(prev).forEach(([key, planning]) => {
        if (available.has(key)) {
          next[key] = planning;
        }
      });
      return next;
    });
    setFlagSaving((prev) => {
      const next: Record<string, boolean> = {};
      Object.entries(prev).forEach(([key, saving]) => {
        if (available.has(key)) {
          next[key] = saving;
        }
      });
      return next;
    });
  }, [planningRows]);

  useEffect(() => {
    if (!selectedChannel) {
      return;
    }
    const channelPrefix = `${selectedChannel}-`;
    const validKeys = new Set(tableRows.map((row) => row.key).filter((key): key is string => Boolean(key)));
    setSelectedRows((prev) => {
      const next = new Set(prev);
      Array.from(next).forEach((key) => {
        if (key.startsWith(channelPrefix) && !validKeys.has(key)) {
          next.delete(key);
        }
      });
      return next;
    });
  }, [tableRows, selectedChannel]);

  useEffect(() => {
    if (!selectedChannel) {
      return;
    }
    const channelPrefix = `${selectedChannel}-`;
    const autoKeys = new Set<string>();
    tableRows.forEach((row) => {
      if (!row.key || !row.key.startsWith(channelPrefix)) {
        return;
      }
      if (!isRowCompleted(row)) {
        autoKeys.add(row.key);
      }
    });
    setSelectedRows((prev) => {
      const next = new Set(prev);
      Array.from(next).forEach((key) => {
        if (key.startsWith(channelPrefix)) {
          next.delete(key);
        }
      });
      autoKeys.forEach((key) => next.add(key));
      return next;
    });
  }, [selectedChannel, tableRows, isRowCompleted]);

  useEffect(() => {
    if (!selectedChannel) {
      setSpreadsheetData(null);
      setSpreadsheetError(null);
      setSpreadsheetLoading(false);
      setCellOverlay(null);
      return;
    }
    let cancelled = false;
    setSpreadsheetLoading(true);
    setSpreadsheetError(null);
    fetchPlanningSpreadsheet(selectedChannel)
      .then((data) => {
        if (cancelled) return;
        setSpreadsheetData(data);
      })
      .catch((error) => {
        if (cancelled) return;
        setSpreadsheetError(error instanceof Error ? error.message : String(error));
        setSpreadsheetData(null);
      })
      .finally(() => {
        if (!cancelled) {
          setSpreadsheetLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedChannel, dataReloadKey]);

  useEffect(() => {
    if (!taskId) {
      return;
    }
    let cancelled = false;
    let statusInterval: ReturnType<typeof setInterval> | undefined;
    let logInterval: ReturnType<typeof setInterval> | undefined;

    const pollStatus = async () => {
      try {
        const data = await fetchBatchWorkflowTask(taskId);
        if (cancelled) return;
        setTaskInfo(data);
        if (data.status === "succeeded" || data.status === "failed") {
          if (statusInterval) {
            clearInterval(statusInterval);
          }
          if (logInterval) {
            clearInterval(logInterval);
          }
          setQueueReloadKey((prev) => prev + 1);
        }
      } catch (error) {
        if (cancelled) return;
        setTaskError(error instanceof Error ? error.message : String(error));
      }
    };

    const pollLog = async () => {
      try {
        const data = await fetchBatchWorkflowLog(taskId, 400);
        if (cancelled) return;
        setLogLines(data.lines);
      } catch (error) {
        if (cancelled) return;
        setTaskError(error instanceof Error ? error.message : String(error));
      }
    };

    pollStatus();
    pollLog();
    statusInterval = setInterval(pollStatus, 4000);
    logInterval = setInterval(pollLog, 3500);

    return () => {
      cancelled = true;
      if (statusInterval) {
        clearInterval(statusInterval);
      }
      if (logInterval) {
        clearInterval(logInterval);
      }
    };
  }, [taskId]);

  const selectedRowList = useMemo(() => Array.from(selectedRows), [selectedRows]);

  const selectableCount = useMemo(() => {
    return tableRows.filter((row) => {
      if (!row.key) {
        return false;
      }
      const planning = planningMap.get(row.key);
      return !isBlockedFlag(planning?.creation_flag);
    }).length;
  }, [tableRows, planningMap]);

  const handleToggleRow = useCallback(
    (row: SpreadsheetRow) => {
      if (!selectedChannel) {
        setTableMessage("チャンネルを選択してください。");
        return;
      }
      if (!row.key) {
        setTableMessage("動画番号が未設定の行は選択できません。");
        return;
      }
      setSelectedRows((prev) => {
        const next = new Set(prev);
        if (next.has(row.key as string)) {
          next.delete(row.key as string);
        } else {
          next.add(row.key as string);
        }
        return next;
      });
    },
    [selectedChannel]
  );

  const selectableRowKeys = useMemo(() => {
    return tableRows
      .filter((row) => {
        if (!row.key) {
          return false;
        }
        if (isRowCompleted(row)) {
          return false;
        }
        return !isBlockedFlag(planningMap.get(row.key)?.creation_flag);
      })
      .map((row) => row.key as string);
  }, [tableRows, planningMap, isRowCompleted]);

  const queueForSelectedChannel = useMemo(() => {
    if (!selectedChannel) {
      return queueEntries;
    }
    return queueEntries.filter((entry) => entry.channel_code === selectedChannel);
  }, [queueEntries, selectedChannel]);

  const queueDisplayEntries = queueForSelectedChannel.length ? queueForSelectedChannel : queueEntries;
  const showingFallbackQueue = Boolean(selectedChannel && !queueForSelectedChannel.length && queueEntries.length);
  const runningQueueTasks = useMemo(() => {
    return queueEntries
      .filter((entry) => entry.status === "running" && entry.task_id)
      .map((entry) => ({
        taskId: entry.task_id as string,
        label: `${entry.channel_code} (${queueProgressLabel(entry)})`,
        channel: entry.channel_code,
        status: entry.status,
      }));
  }, [queueEntries]);

  const handleSelectAll = useCallback(() => {
    if (!selectedChannel) {
      setTableMessage("チャンネルを選択するとまとめて選択できます。");
      return;
    }
    setSelectedRows(new Set(tableRows.map((row) => row.key).filter((key): key is string => Boolean(key))));
  }, [tableRows, selectedChannel]);

  const handleSelectPending = useCallback(() => {
    if (!selectedChannel) {
      setTableMessage("チャンネルを選択するとまとめて選択できます。");
      return;
    }
    setSelectedRows(new Set(selectableRowKeys));
  }, [selectableRowKeys, selectedChannel]);

  const handleClearSelection = useCallback(() => {
    setSelectedRows(new Set());
  }, []);

  const handleReloadData = useCallback(() => {
    setDataReloadKey((prev) => prev + 1);
    setCellOverlay(null);
  }, []);

  const handleOpenCellOverlay = useCallback((header: string, content: string, target: HTMLDivElement) => {
    const rect = target.getBoundingClientRect();
    const scrollX = window.scrollX || document.documentElement.scrollLeft;
    const scrollY = window.scrollY || document.documentElement.scrollTop;
    const overlayWidth = 420;
    const margin = 16;
    let left = rect.left + scrollX;
    if (left + overlayWidth + margin > scrollX + window.innerWidth) {
      left = scrollX + window.innerWidth - overlayWidth - margin;
    }
    if (left < margin) {
      left = margin;
    }
    const top = rect.bottom + scrollY + 8;
    setCellOverlay({
      header,
      content,
      position: { top, left },
    });
  }, []);

  const handleCloseOverlay = useCallback(() => {
    setCellOverlay(null);
  }, []);

  const handleRefreshQueue = useCallback(() => {
    setQueueReloadKey((prev) => prev + 1);
  }, []);

  const handleCancelQueue = useCallback(
    async (entryId: number) => {
      try {
        await cancelBatchQueueEntry(entryId);
        handleRefreshQueue();
      } catch (error) {
        setQueueError(error instanceof Error ? error.message : String(error));
      }
    },
    [handleRefreshQueue]
  );

  const handleViewLog = useCallback((taskIdValue: string) => {
    setTaskId(taskIdValue);
    setLogLines([]);
    setTaskError(null);
  }, []);

  const handleFormChange = (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { name, value } = event.target;
    const field = name as FormFieldName;
    const isCheckbox = event.target instanceof HTMLInputElement && event.target.type === "checkbox";
    const isNumber = event.target instanceof HTMLInputElement && event.target.type === "number";
    setFormValues((prev) => ({
      ...prev,
      [field]: isCheckbox ? (event.target as HTMLInputElement).checked : isNumber ? Number(value) : value,
    }));
  };

  const handleFlagChange = async (row: SpreadsheetRow, nextFlag: string) => {
    if (!row.key) {
      return;
    }
    const [channelCode, videoNumber] = row.key.split("-");
    setTableMessage(null);
    setFlagSaving((prev) => ({ ...prev, [row.key as string]: true }));
    try {
      const response = await updatePlanning(channelCode, videoNumber, {
        creationFlag: nextFlag,
        expectedUpdatedAt: null,
      });
      setPlanningOverrides((prev) => ({ ...prev, [row.key as string]: response.planning }));
      setPlanningRows((prev) =>
        prev.map((item) =>
          item.channel === channelCode && item.video_number === videoNumber
            ? { ...item, planning: response.planning }
            : item
        )
      );
      if (isBlockedFlag(response.planning.creation_flag)) {
        setSelectedRows((prev) => {
          if (!row.key || !prev.has(row.key)) {
            return prev;
          }
          const cloned = new Set(prev);
          cloned.delete(row.key);
          return cloned;
        });
      }
      if (creationFlagIndex >= 0) {
        setSpreadsheetData((prev) => {
          if (!prev) {
            return prev;
          }
          const updatedRows = prev.rows.map((cells, idx) => {
            if (idx !== row.rowIndex) {
              return cells;
            }
            const cloned = [...cells];
            cloned[creationFlagIndex] = nextFlag;
            return cloned;
          });
          return { ...prev, rows: updatedRows };
        });
      }
      setTableMessage("作成フラグを更新しました。");
    } catch (error) {
      setTableMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setFlagSaving((prev) => ({ ...prev, [row.key as string]: false }));
    }
  };

  const handleEnqueue = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selectedChannel) {
      setTaskError("チャンネルを選択してください。");
      return;
    }
    if (selectedRows.size === 0) {
      setTaskError("台本を1件以上選択してください。");
      return;
    }
    setIsEnqueuing(true);
    setTaskError(null);
    setQueueMessage(null);
    try {
      const payload = {
        channel_code: selectedChannel,
        video_numbers: Array.from(selectedRows)
          .filter((key) => key.startsWith(`${selectedChannel}-`))
          .map((key) => key.split("-", 2)[1])
          .sort((a, b) => Number(a) - Number(b)),
        config: {
          min_characters: Number(formValues.minCharacters) || DEFAULT_FORM.minCharacters,
          max_characters: Number(formValues.maxCharacters) || DEFAULT_FORM.maxCharacters,
          script_prompt_template: formValues.scriptPrompt || undefined,
          quality_check_template: formValues.qualityTemplate || undefined,
          llm_model: formValues.llmModel || undefined,
          loop_mode: !!formValues.loopMode,
          auto_retry: !!formValues.autoRetry,
          debug_log: !!formValues.debugLog,
        },
      };
      await enqueueBatchWorkflow(payload);
      setQueueMessage(`${selectedChannel} に ${payload.video_numbers.length} 件の台本を登録しました。`);
      handleRefreshQueue();
    } catch (error) {
      setTaskError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsEnqueuing(false);
    }
  };

  return (
    <div className="script-factory">
      <section className="script-factory__intro">
        <div>
          <h1>台本作成ワークスペース</h1>
          <p>
            SSOT → CSV → Sheets の一貫ルールを守りつつ、量産対象の企画をチェックしてパラメータを整え、Qwen 無料モデルで連続生成します。
          </p>
        </div>
        <div className="script-factory__stats">
          <span>選択済み {selectedRows.size} 件</span>
          <span>選択候補 {selectableCount} / {tableRows.length}</span>
        </div>
      </section>

      <section className="script-factory__channel-picker">
        <div className="script-factory__channel-picker-header">
          <div>
            <h2>対象チャンネル</h2>
            <p className="muted small-text">CSV ビューで操作するチャンネルを選択してください。</p>
          </div>
          {selectedChannelSummary ? (
            <span className="script-factory__channel-pill" title={selectedChannelSummary.name ?? selectedChannelSummary.code}>
              {selectedChannelSummary.name ?? selectedChannelSummary.code}
            </span>
          ) : null}
        </div>
        {channelsLoading ? (
          <p className="script-factory__empty">チャンネルを読み込み中です…</p>
        ) : channelsError ? (
          <p className="script-factory__empty script-factory__empty--error">{channelsError}</p>
        ) : (
          <div className="channel-chip-compact-grid">
            {channels.map((channel) => {
              const displayName = channel.name ?? channel.branding?.title ?? channel.youtube_title ?? channel.code;
              const avatarUrl = channel.branding?.avatar_url ?? null;
              const themeColor = channel.branding?.theme_color ?? "#e2e8f0";
              const isActive = selectedChannel === channel.code;
              return (
                <button
                  key={channel.code}
                  type="button"
                  className={`channel-chip channel-chip--compact${isActive ? " channel-chip--active" : ""}`}
                  onClick={() => selectChannel(channel.code)}
                  aria-label={`${displayName} を選択`}
                >
                  <div
                    className={`channel-chip__avatar${avatarUrl ? " channel-chip__avatar--image" : ""}`}
                    style={avatarUrl ? { backgroundImage: `url(${avatarUrl})` } : { background: themeColor }}
                    aria-hidden
                  >
                    {!avatarUrl ? displayName.slice(0, 2) : null}
                  </div>
                  <div className="channel-chip__info channel-chip__info--compact">
                    <p className="channel-chip__name" title={displayName}>
                      {displayName}
                    </p>
                    <span className="channel-chip__code">{channel.code}</span>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </section>

      <section className="script-factory__workspace">
        <div className="script-factory__grid">
          <article className="script-factory__panel script-factory__panel--csv">
            <header className="script-factory__panel-header">
              <div>
                <h2>CSV ビュー（企画一覧）</h2>
                <p className="muted small-text">
                  作成フラグと任意列は workspaces/planning/channels/CHxx.csv（planning_store）を参照しています。チェックボックスは量産 API へそのまま渡されます。
                </p>
                {selectedChannelSummary ? (
                  <p className="muted small-text">
                    対象チャンネル: {selectedChannelSummary.name ?? selectedChannelSummary.code}
                  </p>
                ) : null}
              </div>
              <div className="script-factory__actions">
                <button type="button" onClick={handleReloadData} disabled={planningLoading || spreadsheetLoading}>
                  CSV再読み込み
                </button>
                <button type="button" onClick={handleSelectPending} disabled={!tableRows.length}>
                  ペンディング以外を選択
                </button>
                <button type="button" onClick={handleSelectAll} disabled={!tableRows.length}>
                  全件選択
                </button>
                <button type="button" onClick={handleClearSelection} disabled={selectedRows.size === 0}>
                  クリア
                </button>
              </div>
            </header>

            {planningError ? (
              <p className="script-factory__empty script-factory__empty--error">
                企画メタデータの読み込みに失敗しました: {planningError}
              </p>
            ) : null}

            {!selectedChannel ? (
              <p className="script-factory__empty">チャンネルを選択すると最新の CSV を表示します。</p>
            ) : spreadsheetLoading ? (
              <p className="script-factory__empty">CSV を読み込み中です…</p>
            ) : spreadsheetError ? (
              <p className="script-factory__empty script-factory__empty--error">{spreadsheetError}</p>
            ) : tableRows.length === 0 ? (
              <p className="script-factory__empty">CSV に該当データがありません。</p>
            ) : (
              <div className="script-factory__table-wrapper">
                {tableMessage ? <p className="script-factory__message">{tableMessage}</p> : null}
                <table className="script-factory__table">
                  <thead>
                    <tr>
                      <th aria-label="select" />
                      {orderedHeaders.map((header) => {
                        let headerClass: string | undefined;
                        if (header === HEADER_LABELS.title) {
                          headerClass = "script-factory__th-title";
                        } else if (header === HEADER_LABELS.progress) {
                          headerClass = "script-factory__th-progress";
                        }
                        return (
                          <th key={header} className={headerClass}>
                            {header}
                          </th>
                        );
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {tableRows.map((row) => {
                      const rowKey = row.key ?? `row-${row.rowIndex}`;
                      const isSelected = !!row.key && selectedRows.has(row.key);
                      const flagValue = row.key ? planningMap.get(row.key)?.creation_flag ?? (creationFlagIndex >= 0 ? row.cells[creationFlagIndex] : "") : "";
                      return (
                        <tr
                          key={rowKey}
                          className={isSelected ? "script-factory__row--selected" : undefined}
                          onClick={() => handleToggleRow(row)}
                        >
                          <td>
                            <input
                              type="checkbox"
                              checked={isSelected}
                              disabled={!row.key}
                              onChange={() => handleToggleRow(row)}
                              onClick={(event) => event.stopPropagation()}
                              aria-label={`${rowKey} を選択`}
                            />
                          </td>
                          {row.cells.map((cell, colIdx) => {
                            const header = orderedHeaders[colIdx] ?? `col-${colIdx}`;
                            if (colIdx === creationFlagIndex && row.key) {
                              return (
                                <td key={`${rowKey}-${header}`}>
                                  <div className="script-factory__flag-cell">
                                    <span className={`flag-chip flag-${(flagValue || "default").trim()}`}>
                                      {FLAG_LABELS[flagValue ?? ""] ?? (flagValue || "—")}
                                    </span>
                                    <select
                                      value={flagValue ?? ""}
                                      disabled={flagSaving[row.key]}
                                      onChange={(event) => handleFlagChange(row, event.target.value)}
                                      onClick={(event) => event.stopPropagation()}
                                    >
                                      {Object.entries(FLAG_LABELS).map(([value, label]) => (
                                        <option key={value} value={value}>
                                          {label}
                                        </option>
                                      ))}
                                    </select>
                                  </div>
                                </td>
                              );
                            }
                            let cellClass: string | undefined;
                            if (header === HEADER_LABELS.title) {
                              cellClass = "script-factory__td-title";
                            } else if (header === HEADER_LABELS.progress) {
                              cellClass = "script-factory__td-progress";
                            }
                            if (!cell) {
                              return (
                                <td key={`${rowKey}-${header}`} className={cellClass}>
                                  <span className="muted">—</span>
                                </td>
                              );
                            }
                            const isTruncated = cell.length > 80;
                            return (
                              <td key={`${rowKey}-${header}`} className={cellClass}>
                                <div
                                  className={`script-factory__cell-text${isTruncated ? " script-factory__cell-text--truncated" : ""}`}
                                  role="button"
                                  tabIndex={0}
                                  onClick={(event) => handleOpenCellOverlay(header, cell, event.currentTarget)}
                                  onKeyDown={(event) => {
                                    if (event.key === "Enter" || event.key === " ") {
                                      event.preventDefault();
                                      handleOpenCellOverlay(header, cell, event.currentTarget);
                                    }
                                  }}
                                  title="クリックで全文を表示"
                                >
                                  {cell}
                                </div>
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </article>

          <article className="script-factory__panel">
            <header className="script-factory__panel-header">
              <div>
                <h2>台本量産パラメータ</h2>
                <p className="muted small-text">
                  ChannelProfile からペルソナ／台本プロンプトを取得し、実行前にカスタマイズできます。
                </p>
              </div>
            </header>

            <form className="script-factory__form" onSubmit={handleEnqueue}>
              <div className="form-grid">
                <label>
                  最小文字数
                  <input
                    type="number"
                    name="minCharacters"
                    min={1000}
                    value={formValues.minCharacters}
                    onChange={handleFormChange}
                  />
                </label>
                <label>
                  最大文字数
                  <input
                    type="number"
                    name="maxCharacters"
                    min={1000}
                    value={formValues.maxCharacters}
                    onChange={handleFormChange}
                  />
                </label>
                <label>
                  LLMモデル
                  <input type="text" name="llmModel" value={formValues.llmModel} onChange={handleFormChange} />
                </label>
                <label className="checkbox-field">
                  <input
                    type="checkbox"
                    name="loopMode"
                    checked={formValues.loopMode}
                    onChange={handleFormChange}
                  />
                  ループモード（完了後に次へ自動遷移）
                </label>
                <label className="checkbox-field">
                  <input
                    type="checkbox"
                    name="autoRetry"
                    checked={formValues.autoRetry}
                    onChange={handleFormChange}
                  />
                  自動リトライ
                </label>
              </div>

              <label>
                台本プロンプト
                <textarea
                  name="scriptPrompt"
                  rows={8}
                  value={formValues.scriptPrompt}
                  onChange={handleFormChange}
                  placeholder="channel_info の script_prompt を自動投入"
                />
              </label>

              <label>
                品質チェックテンプレート（任意）
                <textarea
                  name="qualityTemplate"
                  rows={4}
                  value={formValues.qualityTemplate}
                  onChange={handleFormChange}
                />
              </label>

              <div className="script-factory__selection-summary">
                <strong>対象台本:</strong> {selectedRowList.length ? selectedRowList.join(", ") : "(未選択)"}
              </div>

              {taskError ? <p className="script-factory__error">{taskError}</p> : null}

              <button
                type="submit"
                className="primary-button"
                disabled={isEnqueuing || !selectedChannel || selectedRows.size === 0}
              >
                {isEnqueuing ? "登録中…" : "バッチ登録（キューに追加）"}
              </button>
            </form>

            <section className="script-factory__profile">
              <header>
                <h3>チャンネルペルソナ</h3>
                {profileLoading ? <span className="status-chip">読み込み中…</span> : null}
                {profileError ? <span className="status-chip status-chip--danger">{profileError}</span> : null}
              </header>
              {channelProfile ? (
                <div>
                  <p className="muted small-text">{channelProfile.channel_name ?? channelProfile.channel_code}</p>
                  <pre className="script-factory__persona-block">
                    {channelProfile.persona_summary || "ペルソナ情報が登録されていません。"}
                  </pre>
                </div>
              ) : (
                <p className="muted">チャンネル選択後にペルソナを表示します。</p>
              )}
            </section>
          </article>

          <article className="script-factory__panel script-factory__panel--queue">
            <header className="script-factory__panel-header">
              <div>
                <h2>バッチ登録キュー</h2>
                <p className="muted small-text">
                  チャンネル単位で自動的に直列実行します。複数チャンネルを同時に登録しても、それぞれ独立に進行します。
                </p>
              </div>
              <div className="script-factory__actions">
                <button type="button" onClick={handleRefreshQueue} disabled={queueLoading}>
                  最新化
                </button>
              </div>
            </header>
            {queueMessage ? <p className="script-factory__message">{queueMessage}</p> : null}
            {queueLoading ? <p className="script-factory__empty">キューを読み込み中です…</p> : null}
            {queueError ? <p className="script-factory__empty script-factory__empty--error">{queueError}</p> : null}
            {!queueLoading && !queueError && queueDisplayEntries.length === 0 ? (
              <p className="script-factory__empty">登録済みのバッチはありません。</p>
            ) : null}
            {!queueLoading && queueDisplayEntries.length > 0 ? (
              <>
                {showingFallbackQueue ? (
                  <p className="muted small-text">
                    選択チャンネル {selectedChannel ?? " ― "} の登録はありません。全チャンネルのキューを表示しています。
                  </p>
                ) : null}
                <div className="script-factory__queue-table-wrapper">
                  <table className="script-factory__queue-table">
                    <thead>
                      <tr>
                        <th>チャンネル</th>
                        <th>対象台本</th>
                        <th>進捗</th>
                        <th>状態</th>
                        <th>更新</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {queueDisplayEntries.map((entry) => {
                        const displayName = channelNameMap.get(entry.channel_code) ?? entry.channel_code;
                        const preview = entry.video_numbers.slice(0, 5).join(", ");
                        const remaining = entry.video_numbers.length - 5;
                        const isActiveChannel = !!selectedChannel && entry.channel_code === selectedChannel;
                        return (
                          <tr key={entry.id} className={isActiveChannel ? "queue-row--active" : undefined}>
                            <td>
                              <div className="script-factory__queue-channel">
                                <span className="script-factory__queue-channel-code">{entry.channel_code}</span>
                                <span className="script-factory__queue-channel-name" title={displayName}>
                                  {displayName}
                                </span>
                              </div>
                            </td>
                            <td>
                              <div className="script-factory__queue-videos" title={entry.video_numbers.join(", ")}>
                                <strong>{entry.video_numbers.length} 件</strong>
                                <span>
                                  {preview}
                                  {remaining > 0 ? ` …ほか${remaining}件` : null}
                                </span>
                              </div>
                            </td>
                            <td>
                              <div className="script-factory__queue-progress">
                                <div className="script-factory__queue-progress-count">{queueProgressLabel(entry)}</div>
                                {(() => {
                                  const percent = queueProgressPercent(entry);
                                  if (percent === null) {
                                    return null;
                                  }
                                  return (
                                    <div className="script-factory__queue-progress-bar" aria-label={`進捗 ${percent}%`}>
                                      <span style={{ width: `${percent}%` }} />
                                    </div>
                                  );
                                })()}
                                {entry.current_video ? (
                                  <p className="script-factory__queue-current">処理中: {entry.current_video}</p>
                                ) : null}
                              </div>
                            </td>
                            <td>
                              <span className={queueStatusClass(entry.status)}>
                                {QUEUE_STATUS_LABELS[entry.status] ?? entry.status}
                              </span>
                            </td>
                            <td>
                              <div className="script-factory__queue-timestamps">
                                <span>{formatDate(entry.created_at)}</span>
                                <span className="muted small-text">{formatDate(entry.updated_at)}</span>
                              </div>
                            </td>
                            <td>
                              <div className="script-factory__queue-actions">
                                {entry.status === "queued" ? (
                                  <button type="button" className="link-button" onClick={() => handleCancelQueue(entry.id)}>
                                    取消
                                  </button>
                                ) : null}
                                {entry.task_id ? (
                                  <button type="button" className="link-button" onClick={() => handleViewLog(entry.task_id!)}>
                                    ログ表示
                                  </button>
                                ) : (
                                  <span className="muted small-text">待機中</span>
                                )}
                              </div>
                              {entry.issues ? (
                                <div className="script-factory__queue-issues">
                                  {Object.entries(entry.issues).map(([video, reason]) => (
                                    <p key={video}>
                                      <strong>{video}</strong>: {reason}
                                    </p>
                                  ))}
                                </div>
                              ) : null}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}
          </article>

          <article className="script-factory__panel script-factory__panel--log">
            <section className="script-factory__log">
              <header>
                <h3>リアルタイムログ</h3>
                {taskInfo ? (
                  <span className="status-chip">{TASK_STATUS_LABELS[taskInfo.status] ?? taskInfo.status}</span>
                ) : runningQueueTasks.length ? (
                  <span className="status-chip status-chip--warning">実行中 {runningQueueTasks.length} 件</span>
                ) : null}
              </header>
              {runningQueueTasks.length ? (
                <div className="script-factory__log-tabs">
                  {runningQueueTasks.map((task) => (
                    <button
                      key={task.taskId}
                      type="button"
                      className={`script-factory__log-tab${taskId === task.taskId ? " script-factory__log-tab--active" : ""}`}
                      onClick={() => handleViewLog(task.taskId)}
                    >
                      <span className="script-factory__log-tab-channel">{task.channel}</span>
                      <span className="script-factory__log-tab-label">{task.label}</span>
                    </button>
                  ))}
                </div>
              ) : null}
              {!taskId ? (
                <p className="muted">量産ジョブを開始するとログが表示されます。</p>
              ) : (
                <pre className="script-factory__log-output">
                  {logLines.length ? logLines.join("\n") : "(ログを待機しています…)"}
                </pre>
              )}
            </section>
          </article>
        </div>
      </section>
      {cellOverlay ? (
        <div className="script-factory__cell-overlay" style={{ top: cellOverlay.position.top, left: cellOverlay.position.left }}>
          <div className="script-factory__cell-overlay-header">
            <strong>{cellOverlay.header}</strong>
            <button type="button" onClick={handleCloseOverlay} aria-label="閉じる">
              ✕
            </button>
          </div>
          <pre className="script-factory__cell-overlay-content">{cellOverlay.content}</pre>
        </div>
      ) : null}
    </div>
  );
}
