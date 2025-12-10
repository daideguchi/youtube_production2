// src/utils/safeStorage.ts
export interface SafeStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
  clear(): void;
  isAvailable: boolean;
}

const createSafeStorage = (backing: Storage | null): SafeStorage => {
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
      console.warn("Storage not available in this context", e);
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
  typeof window !== "undefined" ? window.localStorage : null
);

export const safeSessionStorage: SafeStorage = createSafeStorage(
  typeof window !== "undefined" ? window.sessionStorage : null
);