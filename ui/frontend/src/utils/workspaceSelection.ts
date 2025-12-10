const WORKSPACE_SELECTION_STORAGE_KEY = "videoProduction:selectedProject";

export type WorkspaceSelection = {
  channel: string | null;
  projectId: string | null;
};

function getStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function loadWorkspaceSelection(): WorkspaceSelection | null {
  if (typeof window === "undefined") {
    return null;
  }
  const storage = getStorage();
  if (!storage) return null;
  try {
    const raw = storage.getItem(WORKSPACE_SELECTION_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as WorkspaceSelection;
    if (!parsed || typeof parsed !== "object") {
      return null;
    }
    return {
      channel: parsed.channel ? String(parsed.channel).toUpperCase() : null,
      projectId: parsed.projectId ? String(parsed.projectId) : null,
    };
  } catch {
    return null;
  }
}

export function saveWorkspaceSelection(selection: WorkspaceSelection | null): void {
  if (typeof window === "undefined") {
    return;
  }
  const storage = getStorage();
  if (!storage) return;
  try {
    if (!selection || (!selection.channel && !selection.projectId)) {
      storage.removeItem(WORKSPACE_SELECTION_STORAGE_KEY);
      return;
    }
    const payload: WorkspaceSelection = {
      channel: selection.channel ? selection.channel.toUpperCase() : null,
      projectId: selection.projectId ?? null,
    };
    storage.setItem(WORKSPACE_SELECTION_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // ignore quota errors
  }
}
