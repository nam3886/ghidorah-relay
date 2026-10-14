// The API key typed in the Test tab. It is kept in sessionStorage only (relay_test_key): gone when the browser tab
// closes, never written to localStorage, IndexedDB, the URL or a log. Every access is guarded: with storage blocked
// the key lives in memory only.
import { TEST_KEY } from "@/lib/session";

export function readKey(): string {
  try {
    return sessionStorage.getItem(TEST_KEY) || "";
  } catch {
    return "";
  }
}

export function saveKey(key: string): void {
  try {
    sessionStorage.setItem(TEST_KEY, key);
  } catch {
    /* private mode: the key is only in memory */
  }
}

export function forgetKey(): void {
  try {
    sessionStorage.removeItem(TEST_KEY);
  } catch {
    /* nothing was stored */
  }
}
