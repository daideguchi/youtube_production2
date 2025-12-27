import React, { useEffect, useState, useCallback } from 'react';
import { fetchTtsProgress } from "../api/client";
import type { TtsProgressResponse } from "../api/types";

const TtsProgressPage: React.FC = () => {
    const [data, setData] = useState<TtsProgressResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

    const fetchProgress = useCallback(async () => {
        try {
            const result = await fetchTtsProgress();
            setData(result);
            setLastUpdate(new Date());
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Unknown error');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchProgress();
    }, [fetchProgress]);

    const getProgressColor = (percent: number) => {
        if (percent >= 100) return '#22c55e'; // green
        if (percent >= 75) return '#84cc16'; // lime
        if (percent >= 50) return '#eab308'; // yellow
        if (percent >= 25) return '#f97316'; // orange
        return '#ef4444'; // red
    };

    if (loading && !data) {
        return (
            <div className="p-6">
                <div className="animate-pulse">Loading TTS progress...</div>
            </div>
        );
    }

    return (
        <div className="p-6 max-w-6xl mx-auto">
            <div className="flex justify-between items-center mb-6">
                <h1 className="text-2xl font-bold text-gray-800">🎙️ TTS音声生成 進捗モニター</h1>
                <button
                    onClick={fetchProgress}
                    className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 transition"
                >
                    🔄 更新
                </button>
            </div>

            {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded mb-6">
                    ⚠️ {error}
                </div>
            )}

            {lastUpdate && (
                <div className="text-sm text-gray-500 mb-4">
                    最終更新: {lastUpdate.toLocaleTimeString('ja-JP')}
                </div>
            )}

            {data && (
                <>
                    {/* 全体進捗 */}
                    <div className="bg-gradient-to-r from-blue-500 to-purple-600 rounded-xl p-6 mb-6 text-white">
                        <div className="text-lg mb-2">全体進捗</div>
                        <div className="text-4xl font-bold mb-2">{data.overall_progress}%</div>
                        <div className="w-full bg-white/30 rounded-full h-4">
                            <div
                                className="h-4 rounded-full transition-all duration-500"
                                style={{
                                    width: `${data.overall_progress}%`,
                                    backgroundColor: 'white',
                                }}
                            />
                        </div>
                    </div>

                    {/* チャンネル別進捗 */}
                    <div className="grid gap-6">
                        {data.channels.map((channel) => (
                            <div
                                key={channel.channel}
                                className="bg-white rounded-xl shadow-lg border border-gray-100 overflow-hidden"
                            >
                                <div className="p-6">
                                    <div className="flex justify-between items-center mb-4">
                                        <h2 className="text-xl font-semibold text-gray-800">
                                            📁 {channel.channel}
                                        </h2>
                                        <span
                                            className="px-3 py-1 rounded-full text-white font-bold"
                                            style={{ backgroundColor: getProgressColor(channel.progress_percent) }}
                                        >
                                            {channel.progress_percent}%
                                        </span>
                                    </div>

                                    <div className="mb-4">
                                        <div className="flex justify-between text-sm text-gray-600 mb-1">
                                            <span>完了: {channel.completed_episodes} / {channel.total_episodes}</span>
                                            <span>残り: {channel.missing_ids.length}</span>
                                        </div>
                                        <div className="w-full bg-gray-200 rounded-full h-3">
                                            <div
                                                className="h-3 rounded-full transition-all duration-500"
                                                style={{
                                                    width: `${channel.progress_percent}%`,
                                                    backgroundColor: getProgressColor(channel.progress_percent),
                                                }}
                                            />
                                        </div>
                                    </div>

                                    {channel.missing_ids.length > 0 && channel.missing_ids.length <= 20 && (
                                        <div className="mt-4">
                                            <div className="text-sm text-gray-500 mb-2">未完了エピソード:</div>
                                            <div className="flex flex-wrap gap-1">
                                                {channel.missing_ids.map((id) => (
                                                    <span
                                                        key={id}
                                                        className="px-2 py-1 bg-orange-100 text-orange-800 text-xs rounded"
                                                    >
                                                        {id}
                                                    </span>
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    {channel.missing_ids.length > 20 && (
                                        <div className="mt-4 text-sm text-gray-500">
                                            未完了: {channel.missing_ids.slice(0, 10).join(', ')}... (+{channel.missing_ids.length - 10}件)
                                        </div>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </>
            )}
        </div>
    );
};

export default TtsProgressPage;
