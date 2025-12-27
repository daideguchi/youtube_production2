// src/api/baseUrl.ts

const DEFAULT_API_BASE_URL = ""; // relative path (same-origin / dev proxy)

export const getApiBaseUrl = (): string => {
  const envBase = process.env.REACT_APP_API_BASE_URL;
  if (envBase && envBase.trim().length > 0) {
    return envBase.replace(/\/$/, "");
  }
  return DEFAULT_API_BASE_URL;
};

export const API_BASE_URL = getApiBaseUrl();

export const apiUrl = (path: string): string => {
  const base = getApiBaseUrl();
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${base}${normalized}`;
};
