// The images attached in the Test tab are kept in IndexedDB (database relay-admin-test v1, store kv, key files) as the
// list [{name, type, blob}], so they survive a reload. Only image blobs are kept, at most MAX_IMAGES files and
// MAX_BYTES together. The limits are checked when the list is restored AND before every write, so a corrupt or edited
// store can neither be sent nor written back. Every store access is guarded: with IndexedDB blocked or failing the
// tab still works and only forgets on reload.
import type { InjectionKey } from "vue";

export const DB_NAME = "relay-admin-test";
export const DB_VERSION = 1;
export const STORE = "kv";
export const FILES_KEY = "files";
export const MAX_IMAGES = 10;
export const MAX_BYTES = 12 * 1024 * 1024;

export interface StoredFile {
  name: string;
  type: string;
  blob: Blob;
}

// What a stored or about-to-be-stored list may hold. Anything else is dropped, in order, from the first entry that
// breaks a rule: not an object, not a Blob, a type (the blob's own MIME type) that is not image/*, one file too many,
// or one that would take the total over MAX_BYTES. A value that is not an array is dropped whole.
export function validFiles(list: unknown): StoredFile[] {
  const kept: StoredFile[] = [];
  let bytes = 0;
  for (const f of Array.isArray(list) ? list : []) {
    if (!f || !(f.blob instanceof Blob) || !f.blob.type.startsWith("image/")) continue;
    if (kept.length >= MAX_IMAGES || bytes + f.blob.size > MAX_BYTES) continue;
    kept.push({ name: String(f.name || "image"), type: f.blob.type, blob: f.blob });
    bytes += f.blob.size;
  }
  return kept;
}

// Files picked by the user, added after the ones already attached; the names of those that do not fit come back.
export function addFiles(current: StoredFile[], picked: File[]): { files: StoredFile[]; skipped: string[] } {
  const files = [...current];
  let bytes = files.reduce((sum, f) => sum + f.blob.size, 0);
  const skipped: string[] = [];
  for (const file of picked) {
    if (!file.type.startsWith("image/") || files.length >= MAX_IMAGES || bytes + file.size > MAX_BYTES) {
      skipped.push(file.name);
      continue;
    }
    files.push({ name: file.name, type: file.type, blob: file });
    bytes += file.size;
  }
  return { files, skipped };
}

export const LIMITS_TEXT = `images only, ${MAX_IMAGES} files, ${MAX_BYTES / 1048576} MiB together`;
export const NOT_SAVED_NOTE = "The attached images could not be saved in this browser: they are lost on reload.";
export const DROPPED_NOTE = `Saved images that broke the limits (${LIMITS_TEXT}) were dropped.`;

// The one value under `files`. A spec passes an in-memory stand-in; the real one is IndexedDB.
export interface FileStore {
  get(): Promise<unknown>;
  put(files: StoredFile[]): Promise<void>;
  remove(): Promise<void>;
}

// The Test tab takes its store from here, so a component spec can provide an in-memory one.
export const FILE_STORE: InjectionKey<FileStore> = Symbol("relay-test-file-store");

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function withStore<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const req = run(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(req.result);
      tx.onerror = tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

export const idbStore: FileStore = {
  get: () => withStore("readonly", (s) => s.get(FILES_KEY)),
  put: async (files) => void (await withStore("readwrite", (s) => s.put(files, FILES_KEY))),
  remove: async () => void (await withStore("readwrite", (s) => s.delete(FILES_KEY))),
};

// Checks the list, then writes it (an empty one deletes the key). Returns the list that was checked, which is what the
// caller must keep, and whether the store took it.
export async function saveFiles(
  files: StoredFile[],
  store: FileStore = idbStore,
): Promise<{ files: StoredFile[]; saved: boolean }> {
  const checked = validFiles(files);
  try {
    if (checked.length) await store.put(checked);
    else await store.remove();
    return { files: checked, saved: true };
  } catch {
    return { files: checked, saved: false };
  }
}

// What the store holds, checked. If anything was dropped the store is rewritten with what is left and `dropped` is true
// (the tab shows DROPPED_NOTE). A store that cannot be read gives an empty list and `failed: true`, so the tab says at
// once that the images are not kept in this browser (NOT_SAVED_NOTE).
export async function restoreFiles(
  store: FileStore = idbStore,
): Promise<{ files: StoredFile[]; dropped: boolean; failed: boolean }> {
  let stored: unknown;
  try {
    stored = await store.get();
  } catch {
    return { files: [], dropped: false, failed: true };
  }
  if (stored === null || stored === undefined) return { files: [], dropped: false, failed: false };
  const files = validFiles(stored);
  const dropped = !Array.isArray(stored) || files.length !== stored.length;
  if (dropped) await saveFiles(files, store);
  return { files, dropped, failed: false };
}

export async function clearFiles(store: FileStore = idbStore): Promise<void> {
  try {
    await store.remove();
  } catch {
    /* storage blocked: nothing was kept */
  }
}
