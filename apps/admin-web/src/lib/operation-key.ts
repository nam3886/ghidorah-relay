// Regenerate sends one random operation_key per confirmed action. The server builds the new job's idempotency key
// from it and the source job (UNIQUE (key_id, idem_key)), so the same key for the same job never charges twice.
//
// - A new key is saved in sessionStorage (regenerateOperations, {action: key}: the old admin's name and shape) and read
//   back BEFORE it is sent. If it cannot be saved, nothing is sent and OperationKeyStorageError says so: a key held
//   only in memory would be lost on a reload, and the retry after the reload would charge again.
// - One request per action at a time: a call made while the same action is in flight joins it and sends nothing.
// - A success drops the key: the next click is a new action.
// - A 5xx or a network error keeps the key and flags the error retrySafe: the server may have made jobs under it.
// - An answer below 500 drops a key made for this very call: the server refused it before making anything. A key
//   taken over from storage is only there because an earlier send got no clear answer, so jobs may exist under it:
//   only a success drops it (a 401 or a 429 on the retry says nothing about them).
// - Any other failure keeps the key, unflagged.
// - A kept key is never dropped by a 4xx. Only `abandonOperation`, behind an explicit "start a new regenerate" control
//   that warns of a double charge, drops it on purpose.
// - Keys are NOT cleared on sign-out: the server's key is not tied to the user, so a kept key returns the jobs it
//   already made, while clearing it would charge twice after a 503 followed by an expired session.
// sessionStorage is per tab: another tab (unless duplicated from this one) has its own keys.
import { ApiError, NetworkError } from "@/api/client";
import { OPERATIONS_KEY } from "@/lib/session";

// The server's operation_key pattern (routes_jobs.py OPERATION_KEY). The server refuses any other value, so nothing
// can have been made under a stored value that does not match: it is replaced.
const SERVER_KEY = /^[A-Za-z0-9_-]{16,64}$/;

export class OperationKeyStorageError extends Error {
  constructor(cause?: unknown) {
    super("This browser could not save the retry key, so the regenerate was not sent.", { cause });
    this.name = "OperationKeyStorageError";
  }
}

type Operations = Record<string, unknown>;

// Actions dropped in this page whose stored key could not be removed: that stale key is never sent again.
const dropped = new Set<string>();
const inFlight = new Map<string, Promise<unknown>>();

export const bulkAction = (jobIds: readonly string[]): string => `bulk:${[...new Set(jobIds)].sort().join(",")}`;
export const jobAction = (jobId: string): string => `job:${jobId}`;

// The stored record; a record that is not a JSON object counts as empty. Throws when storage cannot be read: a kept
// key may be in there, so neither a new key nor a rewrite of the record is safe.
function load(): Operations {
  let text: string | null;
  try {
    text = sessionStorage.getItem(OPERATIONS_KEY);
  } catch (err) {
    throw new OperationKeyStorageError(err);
  }
  try {
    const parsed: unknown = JSON.parse(text ?? "{}");
    if (typeof parsed === "object" && parsed !== null) return parsed as Operations;
  } catch {
    /* not JSON: counts as empty */
  }
  return {};
}

function save(operations: Operations): void {
  try {
    sessionStorage.setItem(OPERATIONS_KEY, JSON.stringify(operations));
  } catch (err) {
    throw new OperationKeyStorageError(err);
  }
}

// The action's key, and whether this call made it.
function keyFor(action: string): { key: string; made: boolean } {
  const operations = load();
  const stored = operations[action];
  if (!dropped.has(action) && typeof stored === "string" && SERVER_KEY.test(stored)) {
    return { key: stored, made: false };
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  const key = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  save({ ...operations, [action]: key });
  if (load()[action] !== key) throw new OperationKeyStorageError();
  dropped.delete(action);
  return { key, made: true };
}

// Never throws: the answer must still reach the caller. If the stored key cannot be removed, the tombstone keeps it
// from being sent again in this page.
function drop(action: string): void {
  try {
    const operations = load();
    delete operations[action];
    save(operations);
  } catch {
    dropped.add(action);
  }
}

async function attempt<T>(action: string, call: (operationKey: string) => Promise<T>): Promise<T> {
  const { key, made } = keyFor(action);
  try {
    const result = await call(key);
    drop(action);
    return result;
  } catch (err) {
    if (err instanceof ApiError && err.status < 500) {
      if (made) drop(action);
    } else if (err instanceof NetworkError || (err instanceof ApiError && err.status >= 500)) {
      err.retrySafe = true;
    }
    throw err;
  }
}

// Runs `call` with the action's key and rethrows its error as is (see the rules above). A failure before anything
// was sent (storage refused) is an OperationKeyStorageError.
export function regenerateCall<T>(action: string, call: (operationKey: string) => Promise<T>): Promise<T> {
  const running = inFlight.get(action);
  if (running) return running as Promise<T>;
  // `.finally` runs after the set below even when the attempt fails at once, so no settled flight is left behind.
  const run = attempt(action, call).finally(() => inFlight.delete(action));
  inFlight.set(action, run);
  return run;
}

// Drops the action's kept key on the owner's explicit request, so the next click makes a new key: jobs the kept key
// may have made can then be charged again. Refused (false) while the action is in flight: that call's answer would
// still call a retry safe.
export function abandonOperation(action: string): boolean {
  if (inFlight.has(action)) return false;
  drop(action);
  return true;
}
