// src/utils/safeStorage.ts
export interface SafeStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
  clear(): void;
  isAvailable: boolean;
}

type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
  clear(): void;
};

const warnStorageUnavailable = (error: unknown): void => {
  try {
    (globalThis as any)?.console?.warn?.("Storage not available in this context", error);
  } catch {
    // ignore
  }
};

const resolveStorage = (key: "localStorage" | "sessionStorage"): StorageLike | null => {
  try {
    const candidate = (globalThis as any)?.[key];
    if (!candidate) {
      return null;
    }
    if (typeof candidate.getItem !== "function") {
      return null;
    }
    return candidate as StorageLike;
  } catch {
    return null;
  }
};

const createSafeStorage = (backing: StorageLike | null): SafeStorage => {
  if (!backing) {
    return {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
      clear: () => {},
      isAvailable: false,
    };
  }

  const safeCall = <T>(fn: () => T, fallback: T): T => {
    try {
      return fn();
    } catch (e) {
      warnStorageUnavailable(e);
      return fallback;
    }
  };

  return {
    getItem: (key: string) => safeCall(() => backing.getItem(key), null),
    setItem: (key: string, value: string) =>
      safeCall(() => {
        backing.setItem(key, value);
      }, undefined),
    removeItem: (key: string) =>
      safeCall(
        () => {
          backing.removeItem(key);
        },
        undefined
      ),
    clear: () =>
      safeCall(
        () => {
          backing.clear();
        },
        undefined
      ),
    isAvailable: true,
  };
};

export const safeLocalStorage: SafeStorage = createSafeStorage(
  resolveStorage("localStorage")
);

export const safeSessionStorage: SafeStorage = createSafeStorage(
  resolveStorage("sessionStorage")
);
