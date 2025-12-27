import { API_BASE_URL } from "../api/baseUrl";

const ABSOLUTE_URL_PATTERN = /^(?:https?:)?\/\//i;

export function resolveMediaUrl(
  path?: string | null,
  baseUrl: string = API_BASE_URL,
  protocolFallback: string = "https:"
): string | null {
  if (!path) {
    return null;
  }

  if (ABSOLUTE_URL_PATTERN.test(path)) {
    if (path.startsWith("//")) {
      const protocol =
        typeof window !== "undefined" && window.location && typeof window.location.protocol === "string"
          ? window.location.protocol
          : protocolFallback;
      return `${protocol}${path}`;
    }
    return path;
  }

  try {
    const baseWithSlash = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
    const target = path.startsWith("/") ? path : `./${path}`;
    return new URL(target, baseWithSlash).toString();
  } catch (_error) {
    const trimmedBase = baseUrl.replace(/\/+$/, "");
    const trimmedPath = path.replace(/^\/+/, "");
    return `${trimmedBase}/${trimmedPath}`;
  }
}
