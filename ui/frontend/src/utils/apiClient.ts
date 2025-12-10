// src/utils/apiClient.ts
export const getApiBaseUrl = (): string => {
  // .env で設定した REACT_APP_API_BASE_URL を見る
  const envBase = process.env.REACT_APP_API_BASE_URL;

  if (envBase && envBase.trim().length > 0) {
    return envBase.replace(/\/$/, ""); // 末尾の / を消す
  }

  // GitHub PagesのURL（youtube_production2）かを判定
  const currentOrigin = window.location.origin;
  const currentPath = window.location.pathname;
  const isGitHubPages = currentOrigin.includes('github.io') && currentPath.startsWith('/youtube_production2');

  if (isGitHubPages) {
    // GitHub Pages環境では相対パスを使用
    return '';
  } else {
    // ローカル環境ではバックエンドのURLを返す
    return 'http://localhost:8000';
  }
};

export const apiUrl = (path: string): string => {
  const base = getApiBaseUrl();
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${base}${p}`;
};

// 共通の fetch ラッパー（必要なら）
export const apiGetJson = async <T = unknown>(path: string): Promise<T> => {
  const url = apiUrl(path);
  const res = await fetch(url, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
    },
  });

  if (!res.ok) {
    // ここでログに出すなど
    throw new Error(`API request failed: ${res.status} ${res.statusText}`);
  }

  return (await res.json()) as T;
};

// POSTリクエスト用のヘルパー
export const apiPostJson = async <T = unknown>(path: string, data: any): Promise<T> => {
  const url = apiUrl(path);
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });

  if (!res.ok) {
    throw new Error(`API request failed: ${res.status} ${res.statusText}`);
  }

  return (await res.json()) as T;
};