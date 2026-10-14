import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/api/test";
import { KEY_MISSING, PICK_A_MODEL, useTestSession } from "@/components/test/use-test-session";

vi.mock("@/api/test", async (original) => ({
  ...(await original<typeof import("@/api/test")>()),
  listModels: vi.fn(),
  getPoints: vi.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const models = [{ id: "veo-3", kind: "video", owned_by: "flow" }];
const unauthorized = () => new api.TestApiError(401, "auth.invalid_key", "bad key");

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  vi.mocked(api.listModels).mockReset().mockResolvedValue({ data: models });
  vi.mocked(api.getPoints).mockReset().mockResolvedValue({ points: 5 });
});

describe("the API key", () => {
  it("goes to sessionStorage only after a successful model load, and nowhere else", async () => {
    const pending = deferred<{ data: typeof models }>();
    vi.mocked(api.listModels).mockReturnValue(pending.promise);
    const s = useTestSession();
    s.typedKey.value = "rk-secret-1";
    const loading = s.loadModels();
    expect(sessionStorage.getItem("relay_test_key")).toBeNull();
    pending.resolve({ data: models });
    expect(await loading).toBe(true);
    expect(sessionStorage.getItem("relay_test_key")).toBe("rk-secret-1");
    expect(sessionStorage.length).toBe(1);
    expect(localStorage.length).toBe(0);
  });

  it("is not stored when the load fails", async () => {
    vi.mocked(api.listModels).mockRejectedValue(unauthorized());
    const s = useTestSession();
    s.typedKey.value = "rk-wrong";
    expect(await s.loadModels()).toBe(false);
    expect(sessionStorage.getItem("relay_test_key")).toBeNull();
    expect(s.status.value).toBe("auth.invalid_key: bad key");
    expect(s.models.value).toEqual([]);
  });

  it("is not stored when the load fails with a server error", async () => {
    vi.mocked(api.listModels).mockRejectedValue(new api.TestApiError(500, "server.error", "boom"));
    const s = useTestSession();
    s.typedKey.value = "rk-secret-1";
    expect(await s.loadModels()).toBe(false);
    expect(sessionStorage.length).toBe(0);
    expect(s.status.value).toBe("server.error: boom");
  });

  it("is not stored when it was forgotten while the load was in flight", async () => {
    const pending = deferred<{ data: typeof models }>();
    vi.mocked(api.listModels).mockReturnValue(pending.promise);
    const s = useTestSession();
    s.typedKey.value = "rk-secret-1";
    const loading = s.loadModels();
    s.forget();
    pending.resolve({ data: models });
    expect(await loading).toBe(false);
    expect(sessionStorage.getItem("relay_test_key")).toBeNull();
    expect(s.models.value).toEqual([]);
  });

  it("is not stored when a sign-out reset the tab while the load was in flight", async () => {
    const pending = deferred<{ data: typeof models }>();
    vi.mocked(api.listModels).mockReturnValue(pending.promise);
    const s = useTestSession();
    s.typedKey.value = "rk-secret-1";
    const loading = s.loadModels();
    s.reset();
    pending.resolve({ data: models });
    await loading;
    expect(sessionStorage.length).toBe(0);
    expect(s.typedKey.value).toBe("");
  });

  it("is the one of the newest load: a slower older load is dropped", async () => {
    const first = deferred<{ data: typeof models }>();
    const second = deferred<{ data: typeof models }>();
    vi.mocked(api.listModels).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const s = useTestSession();
    s.typedKey.value = "rk-old";
    const a = s.loadModels();
    s.typedKey.value = "rk-new";
    const b = s.loadModels();
    second.resolve({ data: [{ id: "new", kind: "image", owned_by: "flow" }] });
    expect(await b).toBe(true);
    first.resolve({ data: models });
    expect(await a).toBe(false);
    expect(sessionStorage.getItem("relay_test_key")).toBe("rk-new");
    expect(s.models.value.map((m) => m.id)).toEqual(["new"]);
  });

  it("is trimmed", async () => {
    const s = useTestSession();
    s.typedKey.value = "  rk-spaced  ";
    await s.loadModels();
    expect(api.listModels).toHaveBeenCalledWith("rk-spaced");
    expect(sessionStorage.getItem("relay_test_key")).toBe("rk-spaced");
  });

  it("is asked for when empty, and nothing is called", async () => {
    const s = useTestSession();
    s.typedKey.value = "   ";
    expect(await s.loadModels()).toBe(false);
    expect(s.status.value).toBe(KEY_MISSING);
    expect(KEY_MISSING).toBe("Enter an API key");
    expect(api.listModels).not.toHaveBeenCalled();
  });

  it("is forgotten by Forget key: storage, memory and the box", async () => {
    const s = useTestSession();
    s.typedKey.value = "rk-secret-1";
    await s.loadModels();
    s.forget();
    expect(sessionStorage.getItem("relay_test_key")).toBeNull();
    expect(s.typedKey.value).toBe("");
    await expect(s.call(async () => "sent")).rejects.toThrow(KEY_MISSING);
  });

  it("is cleared by a reset (sign-out) together with the models and the points", async () => {
    const s = useTestSession();
    s.typedKey.value = "rk-secret-1";
    await s.loadModels();
    s.reset();
    expect(sessionStorage.length).toBe(0);
    expect(s.models.value).toEqual([]);
    expect(s.points.value).toBe("");
    expect(s.status.value).toBe("Idle");
  });

  it("is forgotten at once when a call with it answers 401", async () => {
    const s = useTestSession();
    s.typedKey.value = "rk-secret-1";
    await s.loadModels();
    await expect(
      s.call(async () => {
        throw unauthorized();
      }),
    ).rejects.toThrow("auth.invalid_key: bad key");
    expect(sessionStorage.getItem("relay_test_key")).toBeNull();
    await expect(s.call(async () => "sent")).rejects.toThrow(KEY_MISSING);
  });

  it("is kept when a call fails with anything other than 401", async () => {
    const s = useTestSession();
    s.typedKey.value = "rk-secret-1";
    await s.loadModels();
    await expect(
      s.call(async () => {
        throw new api.TestApiError(500, "server.error", "boom");
      }),
    ).rejects.toThrow("server.error: boom");
    expect(sessionStorage.getItem("relay_test_key")).toBe("rk-secret-1");
  });

  it("is dropped from storage when the load of the kept key answers 401", async () => {
    sessionStorage.setItem("relay_test_key", "rk-revoked");
    vi.mocked(api.listModels).mockRejectedValue(unauthorized());
    const s = useTestSession();
    expect(await s.restore()).toBe(false);
    expect(sessionStorage.getItem("relay_test_key")).toBeNull();
  });

  it("a wrong key typed over a good kept one does not remove the good one", async () => {
    sessionStorage.setItem("relay_test_key", "rk-good");
    vi.mocked(api.listModels).mockRejectedValue(unauthorized());
    const s = useTestSession();
    s.typedKey.value = "rk-typo";
    await s.loadModels();
    expect(sessionStorage.getItem("relay_test_key")).toBe("rk-good");
  });

  it("is loaded again when the tab starts and a key was kept for this browser tab", async () => {
    sessionStorage.setItem("relay_test_key", "rk-kept");
    const s = useTestSession();
    expect(await s.restore()).toBe(true);
    expect(s.typedKey.value).toBe("rk-kept");
    expect(api.listModels).toHaveBeenCalledWith("rk-kept");
    expect(await useTestSession().restore()).toBe(true);
    sessionStorage.clear();
    expect(await useTestSession().restore()).toBe(false);
  });

  it("is still used for the calls when the typed text changes after the load", async () => {
    const s = useTestSession();
    s.typedKey.value = "rk-loaded";
    await s.loadModels();
    s.typedKey.value = "rk-edited-after";
    const seen: string[] = [];
    await s.call(async (key) => void seen.push(key));
    expect(seen).toEqual(["rk-loaded"]);
  });

  it("is never kept when storage is blocked: the tab still works from memory", async () => {
    vi.stubGlobal("sessionStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    });
    const s = useTestSession();
    s.typedKey.value = "rk-memory";
    expect(await s.loadModels()).toBe(true);
    const seen: string[] = [];
    await s.call(async (key) => void seen.push(key));
    expect(seen).toEqual(["rk-memory"]);
  });
});

describe("the model load", () => {
  it("keeps the models and the points JSON, and says to pick a model", async () => {
    const s = useTestSession();
    s.typedKey.value = "rk-1";
    await s.loadModels();
    expect(s.models.value).toEqual(models);
    expect(s.points.value).toBe(JSON.stringify({ points: 5 }, null, 2));
    expect(s.status.value).toBe(PICK_A_MODEL);
  });

  it("shows the points error as text and still loads the models", async () => {
    vi.mocked(api.getPoints).mockRejectedValue(new api.TestApiError(403, "forbidden", "no points here"));
    const s = useTestSession();
    s.typedKey.value = "rk-1";
    expect(await s.loadModels()).toBe(true);
    expect(s.points.value).toBe("forbidden: no points here");
    expect(sessionStorage.getItem("relay_test_key")).toBe("rk-1");
  });
});
