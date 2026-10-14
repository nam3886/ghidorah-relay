import { OPERATIONS_KEY } from "@/lib/session";

// The same pattern the server enforces (and operation-key.ts replaces a stored value that does not match).
const KEY_PATTERN = /^[A-Za-z0-9_-]{16,64}$/;

// Is a retry key kept for this action in this tab? An earlier attempt got no clear answer, so it may have made jobs;
// the next click would resend that key. Read-only: the record belongs to operation-key.ts, which also drops a key.
export function hasKeptKey(action: string): boolean {
  try {
    const record: unknown = JSON.parse(sessionStorage.getItem(OPERATIONS_KEY) || "{}");
    if (record === null || typeof record !== "object" || Array.isArray(record)) return false;
    const key = (record as Record<string, unknown>)[action];
    return typeof key === "string" && KEY_PATTERN.test(key);
  } catch {
    return false;
  }
}
