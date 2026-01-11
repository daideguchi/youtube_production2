import React, { useCallback, useEffect, useState } from "react";
import { getLlmUsageLogs, getLlmOverrides, saveLlmOverrides, getLlmModels } from "../api/llmUsage";

export const LlmUsagePage: React.FC = () => {
  const [limit, setLimit] = useState<number>(() => {
    const saved = localStorage.getItem("llmUsage.limit");
    return saved ? Number(saved) : 200;
  });
  const [logs, setLogs] = useState<any[]>([]);
  const [overrides, setOverrides] = useState<any>({ tasks: {} });
  const [taskFilter, setTaskFilter] = useState<string>("");
  const [modelFilter, setModelFilter] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modelKeys, setModelKeys] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const models = await getLlmModels().catch(() => []);
      setModelKeys(models || []);
      const logRes = await getLlmUsageLogs(limit);
      setLogs(logRes.records || []);
      const ovRes = await getLlmOverrides();
      setOverrides(ovRes || { tasks: {} });
    } catch (e: any) {
      setError(e?.message || "failed to load");
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    localStorage.setItem("llmUsage.limit", String(limit));
  }, [limit]);

  const handleSave = async () => {
    // Validate model keys from backend list
    const allowed = new Set<string>(modelKeys);
    for (const [task, conf] of Object.entries(overrides.tasks || {})) {
      const models = (conf as any)?.models || [];
      for (const m of models) {
        if (allowed.size && !allowed.has(m)) {
          setError(`Unknown model selector in overrides: ${m} (task=${task})`);
          return;
        }
      }
    }
    setSaving(true);
    setError(null);
    try {
      await saveLlmOverrides({ tasks: overrides?.tasks || {} });
      await load();
    } catch (e: any) {
      setError(e?.message || "failed to save");
    } finally {
      setSaving(false);
    }
  };

  const updateTask = (task: string, field: string, value: any) => {
    setOverrides((prev: any) => {
      const next = { ...prev, tasks: { ...prev.tasks } };
      next.tasks[task] = { ...(prev.tasks?.[task] || {}) };
      if (value === "") {
        delete next.tasks[task][field];
      } else {
        next.tasks[task][field] = value;
      }
      return next;
    });
  };

  const taskList = Object.keys(overrides.tasks || {});
  const tasksFromLogs = Array.from(new Set(logs.map((r) => r.task).filter(Boolean)));
  const filteredLogs = logs.filter((r) => {
    if (taskFilter && r.task !== taskFilter) return false;
    if (modelFilter && r.model !== modelFilter) return false;
    return true;
  });

  return (
    <div style={{ padding: 16 }}>
      <h2>LLM Usage & Overrides</h2>
      <div style={{ marginBottom: 12, padding: 10, border: "1px solid #f59e0b", background: "#fff7ed", color: "#7c2d12" }}>
        <b>注意:</b> ここはデバッグ用です。保存される override は <code>configs/llm_task_overrides.local.yaml</code>（ローカル専用）に書かれます。
        SSOT（<code>configs/llm_task_overrides.yaml</code>）は書き換えません。通常運用は <code>/model-policy</code> と数字スロットを使ってください。
      </div>
      <div style={{ display: "flex", gap: 16 }}>
        <div style={{ flex: 2 }}>
          <h3>Logs (latest {limit})</h3>
          <button onClick={load} disabled={loading}>Reload</button>
          <input
            type="number"
            value={limit}
            min={1}
            max={2000}
            onChange={(e) => setLimit(Number(e.target.value))}
            style={{ marginLeft: 8, width: 80 }}
          />
          <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center" }}>
            <label>Task</label>
            <select value={taskFilter} onChange={(e) => setTaskFilter(e.target.value)}>
              <option value="">(all)</option>
              {tasksFromLogs.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <label>Model</label>
            <select value={modelFilter} onChange={(e) => setModelFilter(e.target.value)}>
              <option value="">(all)</option>
              {modelKeys.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <button onClick={() => { setTaskFilter(""); setModelFilter(""); }}>Clear</button>
          </div>
          {error && <div style={{ color: "red" }}>{error}</div>}
          <div style={{ maxHeight: 400, overflow: "auto", marginTop: 8, border: "1px solid #ddd", padding: 8 }}>
            {filteredLogs.slice().reverse().map((r, idx) => (
              <div key={idx} style={{ marginBottom: 8, borderBottom: "1px solid #eee", paddingBottom: 4 }}>
                <div><b>{r.task}</b> [{r.model}] via {r.provider}</div>
                <div>latency: {r.latency_ms} ms | chain: {(r.chain || []).join(" -> ")} | status: {r.status}</div>
                <div>usage: {r.usage ? JSON.stringify(r.usage) : "-"} | req_id: {r.request_id || "-"}</div>
                {r.error && <div style={{ color: "red" }}>error: {r.error} ({r.error_class}) status={r.status_code}</div>}
              </div>
            ))}
          </div>
        </div>
        <div style={{ flex: 1 }}>
          <h3>Task Overrides</h3>
          <button onClick={handleSave} disabled={saving}>Save</button>
          <div style={{ marginTop: 8 }}>
            {(taskList.length ? taskList : ["(add task key below)"]).map((t) => (
              <div key={t} style={{ marginBottom: 12, borderBottom: "1px solid #eee", paddingBottom: 8 }}>
                <div><b>{t}</b></div>
                <label>tier </label>
                <input
                  type="text"
                  value={overrides.tasks?.[t]?.tier || ""}
                  onChange={(e) => updateTask(t, "tier", e.target.value)}
                />
                <div>models (comma-separated)</div>
                <input
                  type="text"
                  value={(overrides.tasks?.[t]?.models || []).join(",")}
                  onChange={(e) => updateTask(t, "models", e.target.value.split(",").map(x => x.trim()).filter(Boolean))}
                />
                <div>system_prompt_override</div>
                <textarea
                  value={overrides.tasks?.[t]?.system_prompt_override || ""}
                  onChange={(e) => updateTask(t, "system_prompt_override", e.target.value)}
                  style={{ width: "100%", height: 60 }}
                />
                <div>options (JSON)</div>
                <textarea
                  value={overrides.tasks?.[t]?.options ? JSON.stringify(overrides.tasks[t].options, null, 2) : ""}
                  onChange={(e) => {
                    try {
                      const val = e.target.value ? JSON.parse(e.target.value) : {};
                      updateTask(t, "options", val);
                    } catch {
                      // ignore parse errors for now
                    }
                  }}
                  style={{ width: "100%", height: 80 }}
                />
              </div>
            ))}
            <div>
              <h4>Add / edit task key</h4>
              <input
                type="text"
                placeholder="task key"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    const key = (e.target as HTMLInputElement).value.trim();
                    if (!key) return;
                    setOverrides((prev: any) => ({ ...prev, tasks: { ...(prev.tasks || {}), [key]: prev.tasks?.[key] || {} } }));
                    (e.target as HTMLInputElement).value = "";
                  }
                }}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LlmUsagePage;
