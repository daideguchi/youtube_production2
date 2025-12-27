import { useCallback, useEffect, useMemo, useState } from "react";

import {
  cancelScriptPipelineJob,
  fetchScriptPipelineJobs,
  purgeScriptPipelineJobs,
  ScriptPipelineJob,
} from "../api/client";

export function JobsPage() {
  const [raw, setRaw] = useState<string>("");
  const [jobs, setJobs] = useState<ScriptPipelineJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchScriptPipelineJobs();
      setRaw(data.raw || "");
      setJobs(Array.isArray(data.jobs) ? data.jobs : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  const handlePurge = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await purgeScriptPipelineJobs();
      setRaw(data.raw || "");
      await fetchList();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [fetchList]);

  const handleCancel = useCallback(
    async (jobId: string) => {
      setLoading(true);
      setError(null);
      try {
        const data = await cancelScriptPipelineJob(jobId);
        setRaw(data.raw || "");
        await fetchList();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [fetchList]
  );

  const lines = useMemo(() => raw.split("\n").filter(Boolean), [raw]);
  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    jobs.forEach((j) => {
      counts[j.status] = (counts[j.status] || 0) + 1;
    });
    return counts;
  }, [jobs]);

  const sortedJobs = useMemo(() => {
    return [...jobs].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  }, [jobs]);

  return (
    <div className="page jobs-page" style={{ padding: 16, display: "grid", gap: 12 }}>
      <h1>ジョブ管理</h1>
      {error && (
        <div className="error" style={{ color: "red" }}>
          {error}
        </div>
      )}
      <div className="card" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <button onClick={fetchList} disabled={loading}>
          再読み込み
        </button>
        <button onClick={handlePurge} disabled={loading}>
          purge (pending以外削除)
        </button>
        <div>
          {Object.entries(statusCounts).map(([k, v]) => (
            <span key={k} style={{ marginRight: 8 }}>
              {k}: {v}
            </span>
          ))}
        </div>
      </div>

      <div className="card" style={{ marginTop: 8 }}>
        <h3>ジョブ一覧</h3>
        {sortedJobs.length > 0 ? (
          <table className="job-table" style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th>Status</th>
                <th>ID</th>
                <th>Channel</th>
                <th>Video</th>
                <th>Title</th>
                <th>Attempts</th>
                <th>Updated</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {sortedJobs.map((j) => (
                <tr key={j.id}>
                  <td>{j.status}</td>
                  <td>{j.id}</td>
                  <td>{j.channel}</td>
                  <td>{j.video}</td>
                  <td>{j.title}</td>
                  <td>
                    {j.attempts ?? 0}/{j.max_retries ?? 0}
                  </td>
                  <td>{j.updated_at}</td>
                  <td>
                    <button onClick={() => handleCancel(j.id)} disabled={loading}>
                      キャンセル
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div>ジョブなし</div>
        )}
        {loading && <div>読み込み中...</div>}
        <details style={{ marginTop: 8 }}>
          <summary>raw出力</summary>
          <pre style={{ whiteSpace: "pre-wrap", background: "#f7f7f7", padding: 12, borderRadius: 8 }}>
            {lines.length ? lines.join("\n") : raw || "結果なし"}
          </pre>
        </details>
      </div>
    </div>
  );
}
