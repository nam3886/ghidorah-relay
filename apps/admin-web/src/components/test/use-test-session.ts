import { ref, shallowRef } from "vue";
import { getPoints, listModels, type PublicModel, TestApiError } from "@/api/test";
import { useLatest } from "@/composables/use-latest";
import { forgetKey, readKey, saveKey } from "./test-key";

export const KEY_MISSING = "Enter an API key";
export const PICK_A_MODEL = "Pick a model";

// The key and the model list of the Test tab. The typed key is kept (in sessionStorage, by test-key.ts) only after a
// model load with it succeeded, so a mistyped or refused key is never stored. Calls use the key that was loaded, not
// whatever is typed in the box now. A public 401 means that key is no longer valid: it is forgotten at once.
export function useTestSession() {
  const typedKey = ref("");
  const models = shallowRef<PublicModel[]>([]);
  const points = ref("");
  // The one status line of the tab: errors read `code: message`.
  const status = ref("Idle");
  let activeKey = "";
  const latest = useLatest();

  const messageOf = (err: unknown): string => (err instanceof Error ? err.message : String(err));

  function forget(): void {
    latest.invalidate();
    forgetKey();
    activeKey = "";
    typedKey.value = "";
  }

  // Sign-out and a 401 of the admin session: nothing of the session stays, not even the loaded models.
  function reset(): void {
    forget();
    models.value = [];
    points.value = "";
    status.value = "Idle";
  }

  // Runs a call with the loaded key. A 401 forgets the key; the error is rethrown for the caller.
  async function call<T>(run: (key: string) => Promise<T>): Promise<T> {
    const key = activeKey;
    if (!key) throw new TestApiError(0, "", KEY_MISSING);
    try {
      return await run(key);
    } catch (err) {
      if (err instanceof TestApiError && err.status === 401 && key === activeKey) forget();
      throw err;
    }
  }

  // True when the models were loaded and the key kept. The newest load wins; an answer that is no longer wanted (another
  // load, Forget key, a sign-out) is dropped without touching the stored key.
  async function loadModels(): Promise<boolean> {
    const key = typedKey.value.trim();
    if (!key) {
      status.value = KEY_MISSING;
      return false;
    }
    const ticket = latest.next();
    try {
      const out = await listModels(key);
      if (!latest.isCurrent(ticket)) return false;
      activeKey = key;
      models.value = out.data;
      saveKey(key);
      try {
        const answer = await getPoints(key);
        if (latest.isCurrent(ticket)) points.value = JSON.stringify(answer, null, 2);
      } catch (err) {
        if (latest.isCurrent(ticket)) points.value = messageOf(err);
      }
      if (!latest.isCurrent(ticket)) return false;
      status.value = PICK_A_MODEL;
      return true;
    } catch (err) {
      if (!latest.isCurrent(ticket)) return false;
      if (err instanceof TestApiError && err.status === 401 && readKey() === key) forgetKey();
      status.value = messageOf(err);
      return false;
    }
  }

  // The tab was opened again in this browser tab: the key kept for it is loaded.
  async function restore(): Promise<boolean> {
    const key = readKey();
    if (!key) return false;
    typedKey.value = key;
    return loadModels();
  }

  return { typedKey, models, points, status, loadModels, restore, forget, reset, call };
}

export type TestSession = ReturnType<typeof useTestSession>;
