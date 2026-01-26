import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { fetchVideoJobs, fetchVideoProjects } from "../api/client";
import type { VideoJobRecord, VideoProjectSummary } from "../api/types";
import { VideoLiveAssetsPanel } from "../components/VideoLiveAssetsPanel";
import { safeLocalStorage } from "../utils/safeStorage";

const IMAGE_WAREHOUSE_LAST_PROJECT_ID_KEY = "imageWarehouse:lastProjectId";

function formatProjectLabel(project: VideoProjectSummary): string {
  const planningChannel = (project.planning?.channel ?? "").trim();
  const planningVideo = (project.planning?.videoNumber ?? "").trim();
  const planning = [planningChannel, planningVideo].filter(Boolean).join(" ");
  const title = (project.title ?? project.planning?.title ?? "").trim();
  if (planning && title) return `${planning} — ${title}`;
  if (planning) return planning;
  if (title) return `${project.id} — ${title}`;
  return project.id;
}

function sortProjects(projects: VideoProjectSummary[]): VideoProjectSummary[] {
  const copy = [...projects];
  copy.sort((a, b) => {
    const at = Date.parse(a.last_updated ?? "") || Date.parse(a.created_at ?? "") || 0;
    const bt = Date.parse(b.last_updated ?? "") || Date.parse(b.created_at ?? "") || 0;
    if (at !== bt) return bt - at;
    return a.id.localeCompare(b.id);
  });
  return copy;
}

export function ImageWarehousePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedProjectId = (searchParams.get("project") ?? "").trim();

  const [projects, setProjects] = useState<VideoProjectSummary[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState<string | null>(null);

  const [jobs, setJobs] = useState<VideoJobRecord[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState<string | null>(null);

  const sortedProjects = useMemo(() => sortProjects(projects), [projects]);
  const selectedProject = useMemo(
    () => sortedProjects.find((project) => project.id === selectedProjectId) ?? null,
    [selectedProjectId, sortedProjects]
  );
  const requiredImages = selectedProject?.imageProgress?.requiredTotal ?? undefined;

  const selectProject = useCallback(
    (projectId: string, opts?: { replace?: boolean }) => {
      const params = new URLSearchParams(searchParams);
      const normalized = projectId.trim();
      if (normalized) {
        params.set("project", normalized);
        safeLocalStorage.setItem(IMAGE_WAREHOUSE_LAST_PROJECT_ID_KEY, normalized);
      } else {
        params.delete("project");
        safeLocalStorage.removeItem(IMAGE_WAREHOUSE_LAST_PROJECT_ID_KEY);
      }
      setSearchParams(params, { replace: opts?.replace ?? true });
    },
    [searchParams, setSearchParams]
  );

  const refreshProjects = useCallback(async () => {
    setProjectsLoading(true);
    setProjectsError(null);
    try {
      const data = await fetchVideoProjects();
      setProjects(data);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      setProjectsError(msg);
    } finally {
      setProjectsLoading(false);
    }
  }, []);

  const refreshJobs = useCallback(async (projectId: string) => {
    setJobsLoading(true);
    setJobsError(null);
    try {
      const data = await fetchVideoJobs(projectId, 200);
      setJobs(data);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      setJobsError(msg);
      setJobs([]);
    } finally {
      setJobsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshProjects();
  }, [refreshProjects]);

  useEffect(() => {
    if (selectedProjectId) return;
    if (projectsLoading) return;
    const last = (safeLocalStorage.getItem(IMAGE_WAREHOUSE_LAST_PROJECT_ID_KEY) ?? "").trim();
    if (!last) return;
    if (!projects.some((project) => project.id === last)) return;
    selectProject(last, { replace: true });
  }, [projects, projectsLoading, selectProject, selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId) {
      setJobs([]);
      setJobsError(null);
      setJobsLoading(false);
      return;
    }
    void refreshJobs(selectedProjectId);
  }, [refreshJobs, selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId) return;
    const id = selectedProjectId;
    const handle = window.setInterval(() => {
      void fetchVideoJobs(id, 200)
        .then((data) => setJobs(data))
        .catch(() => {
          /* keep previous jobs on transient failures */
        });
    }, 4000);
    return () => {
      window.clearInterval(handle);
    };
  }, [selectedProjectId]);

  return (
    <div style={{ display: "grid", gap: 12, padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 18 }}>画像倉庫（Live）</h1>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" onClick={() => void refreshProjects()}>
            プロジェクト更新
          </button>
          {selectedProjectId ? (
            <button type="button" onClick={() => void refreshJobs(selectedProjectId)}>
              ジョブ更新
            </button>
          ) : null}
        </div>
      </div>

      {projectsError ? (
        <div style={{ padding: 10, border: "1px solid #fecaca", background: "#fef2f2", color: "#7f1d1d" }}>
          プロジェクト一覧の取得に失敗しました: {projectsError}
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <label style={{ fontWeight: 700 }}>プロジェクト</label>
        <select
          value={selectedProjectId}
          onChange={(e) => selectProject(e.target.value, { replace: true })}
          style={{ minWidth: 420, maxWidth: "100%" }}
          disabled={projectsLoading}
        >
          <option value="">{projectsLoading ? "読込中…" : "選択してください"}</option>
          {sortedProjects.map((project) => (
            <option key={project.id} value={project.id}>
              {formatProjectLabel(project)}
            </option>
          ))}
        </select>
        {selectedProject ? (
          <span style={{ fontSize: 12, color: "#475569" }}>
            status: {selectedProject.status}
            {requiredImages ? ` · required: ${requiredImages}` : ""}
          </span>
        ) : null}
      </div>

      {selectedProjectId ? (
        <div style={{ display: "grid", gap: 10 }}>
          {jobsError ? (
            <div style={{ padding: 10, border: "1px solid #fecaca", background: "#fef2f2", color: "#7f1d1d" }}>
              ジョブ一覧の取得に失敗しました: {jobsError}
            </div>
          ) : null}
          {jobsLoading ? <div style={{ fontSize: 12, color: "#475569" }}>ジョブ読込中…</div> : null}
          <VideoLiveAssetsPanel projectId={selectedProjectId} jobs={jobs} requiredImages={requiredImages} />
        </div>
      ) : (
        <div style={{ padding: 12, border: "1px solid #e2e8f0", background: "#f8fafc", color: "#334155" }}>
          まずはプロジェクトを選択してください。画像生成中なら、生成されるたびにここに追加されます。
        </div>
      )}
    </div>
  );
}

