import { useCallback, useEffect, useMemo, useState } from "react";
import { API_BASE_URL } from "../api/client";
import type { ChannelSummary } from "../api/types";

interface ChannelProgress {
    total: number;
    completed: number;
    success: number;
    failed: number;
}

interface BatchTtsProgress {
    status: string;
    current_channel: string | null;
    current_video: string | null;
    completed: number;
    total: number;
    success: number;
    failed: number;
    current_step: string | null;
    errors: Array<{ channel: string; video: string; error?: string; issues?: string[] }>;
    updated_at: string | null;
    channels: Record<string, ChannelProgress> | null;
}

async function fetchBatchProgress(): Promise<BatchTtsProgress> {
    const res = await fetch(`${API_BASE_URL}/api/batch-tts/progress`);
    if (!res.ok) throw new Error(`Failed: ${res.status}`);
    return res.json();
}

async function startBatchRegeneration(channels: string[]): Promise<{ status: string; message: string }> {
    const res = await fetch(`${API_BASE_URL}/api/batch-tts/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channels }),
    });
    if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Failed: ${res.status}`);
    }
    return res.json();
}

async function fetchBatchLog(): Promise<string> {
    try {
        const res = await fetch(`${API_BASE_URL}/api/batch-tts/log`);
        if (!res.ok) return "";
        return res.text();
    } catch {
        return "";
    }
}

async function resetBatch(): Promise<void> {
    const res = await fetch(`${API_BASE_URL}/api/batch-tts/reset`, { method: "POST" });
    if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Failed: ${res.status}`);
    }
}

type BatchTtsProgressPanelProps = {
    channels?: ChannelSummary[];
    channelsLoading?: boolean;
};

function compareChannelCode(a: string, b: string): number {
    const an = Number.parseInt(a.replace(/[^0-9]/g, ""), 10);
    const bn = Number.parseInt(b.replace(/[^0-9]/g, ""), 10);
    const aNum = Number.isFinite(an);
    const bNum = Number.isFinite(bn);
    if (aNum && bNum) return an - bn;
    if (aNum) return -1;
    if (bNum) return 1;
    return a.localeCompare(b, "ja-JP");
}

function loadSavedSelection(): Set<string> {
    try {
        const raw = localStorage.getItem("ui.batch_tts.selected_channels");
        if (!raw) return new Set();
        const arr = JSON.parse(raw);
        if (!Array.isArray(arr)) return new Set();
        const values = arr
            .map((v) => String(v ?? "").trim().toUpperCase())
            .filter((v) => /^CH\\d+$/.test(v));
        return new Set(values);
    } catch {
        return new Set();
    }
}

export function BatchTtsProgressPanel({
    channels: availableChannels = [],
    channelsLoading = false,
}: BatchTtsProgressPanelProps) {
    const [progress, setProgress] = useState<BatchTtsProgress | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [starting, setStarting] = useState(false);
    const [selectedChannels, setSelectedChannels] = useState<Set<string>>(() => loadSavedSelection());
    const [showLog, setShowLog] = useState(false);
    const [logContent, setLogContent] = useState<string>("");
    const [collapsed, setCollapsed] = useState(false);

    useEffect(() => {
        try {
            localStorage.setItem("ui.batch_tts.selected_channels", JSON.stringify(Array.from(selectedChannels)));
        } catch {
            /* ignore */
        }
    }, [selectedChannels]);

    const channelOptions = useMemo(() => {
        const map = new Map<string, ChannelSummary>();
        availableChannels.forEach((c) => map.set(c.code, c));
        const codes = Array.from(map.keys());
        codes.sort(compareChannelCode);
        return codes.map((code) => {
            const c = map.get(code);
            const label = c?.name ?? c?.youtube_title ?? c?.branding?.title ?? code;
            return { code, label };
        });
    }, [availableChannels]);

    useEffect(() => {
        if (channelOptions.length === 0) {
            return;
        }
        const valid = new Set(channelOptions.map((c) => c.code));
        setSelectedChannels((prev) => new Set(Array.from(prev).filter((code) => valid.has(code))));
    }, [channelOptions]);

    const refresh = useCallback(async () => {
        try {
            const data = await fetchBatchProgress();
            setProgress(data);
            setError(null);

            // ログも取得
            if (data.status === "running" && showLog) {
                const log = await fetchBatchLog();
                setLogContent(log);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }, [showLog]);

    useEffect(() => {
        void refresh();
        const interval = setInterval(() => void refresh(), 3000);
        return () => clearInterval(interval);
    }, [refresh]);

    const handleStart = useCallback(async () => {
        if (selectedChannels.size === 0) {
            setError("チャンネルを選択してください");
            return;
        }
        setStarting(true);
        setError(null);
        try {
            await startBatchRegeneration(Array.from(selectedChannels).sort(compareChannelCode));
            await refresh();
            setShowLog(true);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setStarting(false);
        }
    }, [selectedChannels, refresh]);

    const handleReset = useCallback(async () => {
        try {
            await resetBatch();
            setShowLog(false);
            setLogContent("");
            await refresh();
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }, [refresh]);

    const toggleChannel = (code: string) => {
        setSelectedChannels(prev => {
            const next = new Set(prev);
            if (next.has(code)) next.delete(code);
            else next.add(code);
            return next;
        });
    };

    const isRunning = progress?.status === "running";
    const isCompleted = progress?.status === "completed";
    const isIdle = !progress || progress.status === "idle";
    const progressPercent = progress?.total ? Math.round((progress.completed / progress.total) * 100) : 0;

    // 折りたたみ時はコンパクト表示
    if (collapsed) {
        return (
            <div className="batch-panel batch-panel--collapsed" onClick={() => setCollapsed(false)}>
                <div className="batch-panel__collapsed-header">
                    <span className="batch-panel__collapsed-icon">🎙️</span>
                    <span className="batch-panel__collapsed-title">バッチTTS</span>
                    {isRunning && (
                        <span className="batch-panel__collapsed-status batch-panel__collapsed-status--running">
                            {progress?.current_channel}/{progress?.current_video} ({progressPercent}%)
                        </span>
                    )}
                    {isCompleted && (
                        <span className="batch-panel__collapsed-status batch-panel__collapsed-status--done">
                            完了 ✓ {progress?.success}/{progress?.total}
                        </span>
                    )}
                    {isIdle && (
                        <span className="batch-panel__collapsed-status">待機中</span>
                    )}
                    <button className="batch-panel__expand-btn" onClick={(e) => { e.stopPropagation(); setCollapsed(false); }}>
                        展開
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="batch-panel">
            {/* ヘッダー */}
            <header className="batch-panel__header">
                <div className="batch-panel__title-row">
                    <h3 className="batch-panel__title">
                        <span className="batch-panel__icon">🎙️</span>
                        バッチTTS音声生成
                    </h3>
                    <div className="batch-panel__header-actions">
                        <button
                            className="batch-panel__btn batch-panel__btn--ghost"
                            onClick={() => void refresh()}
                            title="更新"
                        >
                            🔄
                        </button>
                        <button
                            className="batch-panel__btn batch-panel__btn--ghost"
                            onClick={() => setCollapsed(true)}
                            title="折りたたむ"
                        >
                            ➖
                        </button>
                    </div>
                </div>
                <p className="batch-panel__description">
                    台本（assembled.md）を確認後、選択したチャンネルの音声を一括生成します
                </p>
            </header>

            {error && (
                <div className="batch-panel__alert batch-panel__alert--error">
                    ⚠️ {error}
                </div>
            )}

            {/* ステップ表示 */}
            <div className="batch-panel__workflow">
                <div className={`batch-panel__step ${isIdle ? "batch-panel__step--active" : "batch-panel__step--done"}`}>
                    <span className="batch-panel__step-number">1</span>
                    <span className="batch-panel__step-label">チャンネル選択</span>
                </div>
                <div className="batch-panel__step-arrow">→</div>
                <div className={`batch-panel__step ${isRunning ? "batch-panel__step--active" : isCompleted ? "batch-panel__step--done" : ""}`}>
                    <span className="batch-panel__step-number">2</span>
                    <span className="batch-panel__step-label">音声生成中</span>
                </div>
                <div className="batch-panel__step-arrow">→</div>
                <div className={`batch-panel__step ${isCompleted ? "batch-panel__step--active" : ""}`}>
                    <span className="batch-panel__step-number">3</span>
                    <span className="batch-panel__step-label">完了</span>
                </div>
            </div>

            {/* チャンネル選択（待機中のみ） */}
            {isIdle && (
                <section className="batch-panel__section">
                    <h4 className="batch-panel__section-title">対象チャンネルを選択</h4>
                    {channelsLoading ? <div className="muted small-text">チャンネルを読み込み中…</div> : null}
                    {!channelsLoading && channelOptions.length === 0 ? (
                        <div className="muted small-text">チャンネルが見つかりません（先に「チャンネル設定」から登録してください）</div>
                    ) : null}
                    <div className="batch-panel__channel-grid">
                        {channelOptions.map((ch) => {
                            const stats = progress?.channels?.[ch.code] ?? null;
                            const countLabel = stats ? `${stats.total} 本 (完了 ${stats.completed})` : "—";
                            return (
                            <label
                                key={ch.code}
                                className={`batch-panel__channel-card ${selectedChannels.has(ch.code) ? "batch-panel__channel-card--selected" : ""}`}
                            >
                                <input
                                    type="checkbox"
                                    checked={selectedChannels.has(ch.code)}
                                    onChange={() => toggleChannel(ch.code)}
                                />
                                <div className="batch-panel__channel-info">
                                    <span className="batch-panel__channel-code">{ch.code}</span>
                                    <span className="batch-panel__channel-name">{ch.label}</span>
                                    <span className="batch-panel__channel-count">{countLabel}</span>
                                </div>
                            </label>
                            );
                        })}
                    </div>
                    <div className="batch-panel__action-row">
                        <button
                            className="batch-panel__btn batch-panel__btn--primary batch-panel__btn--large"
                            onClick={() => void handleStart()}
                            disabled={starting || selectedChannels.size === 0}
                        >
                            {starting ? "開始中..." : `🚀 ${selectedChannels.size}チャンネルの音声生成を開始`}
                        </button>
                    </div>
                </section>
            )}

            {/* 進捗表示（実行中） */}
            {isRunning && progress && (
                <section className="batch-panel__section">
                    <div className="batch-panel__current-task">
                        <span className="batch-panel__current-label">処理中:</span>
                        <span className="batch-panel__current-target">
                            {progress.current_channel}/{progress.current_video}
                        </span>
                        <span className="batch-panel__current-step">
                            {progress.current_step || "処理中..."}
                        </span>
                    </div>

                    <div className="batch-panel__progress-main">
                        <div className="batch-panel__progress-bar">
                            <div
                                className="batch-panel__progress-fill"
                                style={{ width: `${progressPercent}%` }}
                            />
                        </div>
                        <div className="batch-panel__progress-stats">
                            <span>{progress.completed} / {progress.total} 完了 ({progressPercent}%)</span>
                            <span className="batch-panel__stats-detail">
                                <span className="batch-panel__stat-success">✓ {progress.success}</span>
                                <span className="batch-panel__stat-fail">✗ {progress.failed}</span>
                            </span>
                        </div>
                    </div>

                    {/* チャンネル別進捗 */}
                    {progress.channels && (
                        <div className="batch-panel__channel-progress">
                            {Object.entries(progress.channels).map(([code, ch]) => {
                                const pct = ch.total > 0 ? Math.round((ch.completed / ch.total) * 100) : 0;
                                const isActive = progress.current_channel === code;
                                return (
                                    <div key={code} className={`batch-panel__ch-row ${isActive ? "batch-panel__ch-row--active" : ""}`}>
                                        <span className="batch-panel__ch-code">{code}</span>
                                        <div className="batch-panel__ch-bar">
                                            <div className="batch-panel__ch-fill" style={{ width: `${pct}%` }} />
                                        </div>
                                        <span className="batch-panel__ch-text">{ch.completed}/{ch.total}</span>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </section>
            )}

            {/* 完了表示 */}
            {isCompleted && progress && (
                <section className="batch-panel__section batch-panel__section--completed">
                    <div className="batch-panel__completed-header">
                        <span className="batch-panel__completed-icon">✅</span>
                        <span className="batch-panel__completed-text">バッチ処理完了</span>
                    </div>
                    <div className="batch-panel__completed-stats">
                        <div className="batch-panel__stat-box batch-panel__stat-box--success">
                            <span className="batch-panel__stat-value">{progress.success}</span>
                            <span className="batch-panel__stat-label">成功</span>
                        </div>
                        <div className="batch-panel__stat-box batch-panel__stat-box--fail">
                            <span className="batch-panel__stat-value">{progress.failed}</span>
                            <span className="batch-panel__stat-label">失敗</span>
                        </div>
                        <div className="batch-panel__stat-box">
                            <span className="batch-panel__stat-value">{progress.total}</span>
                            <span className="batch-panel__stat-label">合計</span>
                        </div>
                    </div>
                    {progress.failed > 0 && (
                        <details className="batch-panel__errors">
                            <summary>エラー詳細 ({progress.errors.length}件)</summary>
                            <ul>
                                {progress.errors.map((e, i) => (
                                    <li key={i}>
                                        <strong>{e.channel}/{e.video}</strong>: {e.error || e.issues?.join(", ")}
                                    </li>
                                ))}
                            </ul>
                        </details>
                    )}
                    <button
                        className="batch-panel__btn batch-panel__btn--primary"
                        onClick={() => void handleReset()}
                    >
                        新しいバッチを開始
                    </button>
                </section>
            )}

            {/* ログ表示 */}
            {(isRunning || showLog) && (
                <section className="batch-panel__section">
                    <div className="batch-panel__log-header">
                        <h4 className="batch-panel__section-title">処理ログ</h4>
                        <button
                            className="batch-panel__btn batch-panel__btn--ghost"
                            onClick={() => setShowLog(!showLog)}
                        >
                            {showLog ? "ログを隠す" : "ログを表示"}
                        </button>
                    </div>
                    {showLog && (
                        <pre className="batch-panel__log">
                            {logContent || "ログを読み込み中..."}
                        </pre>
                    )}
                </section>
            )}

            {/* 最終更新時刻 */}
            {progress?.updated_at && (
                <footer className="batch-panel__footer">
                    最終更新: {new Date(progress.updated_at).toLocaleString("ja-JP")}
                </footer>
            )}
        </div>
    );
}
