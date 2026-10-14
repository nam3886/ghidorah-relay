import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiError, NetworkError } from "@/api/client";

// Every test starts from a fresh page: the module's memory is new, sessionStorage is empty. `reload()` inside a test
// is a page reload: memory gone, sessionStorage kept. Calls go through the real client with fetch stubbed, so the
// errors are the client's own ApiError and NetworkError.
type KeyModule = typeof import("@/lib/operation-key");
type ClientModule = typeof import("@/api/client");
type SessionModule = typeof import("@/lib/session");

const NAME = "regenerateOperations";
const READ_ERROR = new Error("storage read blocked");
const WRITE_ERROR = new Error("storage quota exceeded");
const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
let m: KeyModule;
let client: ClientModule;
let session: SessionModule;
let draws = 0;

async function reload(): Promise<void> {
  vi.resetModules();
  m = await import("@/lib/operation-key");
  client = await import("@/api/client");
  session = await import("@/lib/session");
}

// The stubbed random source fills the n-th key's 16 bytes with n, so the n-th key made is K(n).
const K = (n: number): string => n.toString(16).padStart(2, "0").repeat(16);

beforeEach(async () => {
  sessionStorage.clear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  draws = 0;
  vi.spyOn(crypto, "getRandomValues").mockImplementation(((bytes: Uint8Array) =>
    bytes.fill(++draws)) as typeof crypto.getRandomValues);
  await reload();
});

// A regenerate of one job, as the job detail will send it (the action defaults to that job).
function regenerate(action = "job:j1"): Promise<unknown> {
  return m.regenerateCall(action, (key) =>
    client.request("/jobs/j1/regenerate", { method: "POST", body: { operation_key: key } }),
  );
}

const answer = (status: number, body: unknown = {}) =>
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(body), { status }));
const lose = () => fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
// The operation_key of every request that went out, in order.
const sent = (): unknown[] =>
  fetchMock.mock.calls.map(([, init]) => (JSON.parse(String(init.body)) as { operation_key: unknown }).operation_key);
const stored = (): unknown => JSON.parse(sessionStorage.getItem(NAME) ?? "null");
const failure = (p: Promise<unknown>): Promise<unknown> =>
  p.then(
    (value) => {
      throw new Error(`expected a failure, got ${JSON.stringify(value)}`);
    },
    (err: unknown) => err,
  );

// The real sessionStorage behind switches: reads or writes can fail, or writes can vanish without an error.
function storage() {
  const real = sessionStorage;
  const state = { read: true, write: true, keep: true, writes: 0 };
  vi.stubGlobal("sessionStorage", {
    getItem: (name: string) => {
      if (!state.read) throw READ_ERROR;
      return real.getItem(name);
    },
    setItem: (name: string, value: string) => {
      state.writes += 1;
      if (!state.write) throw WRITE_ERROR;
      if (state.keep) real.setItem(name, value);
    },
    removeItem: (name: string) => real.removeItem(name),
    clear: () => real.clear(),
  });
  return state;
}

// The next request stays open until the returned function answers it with a status.
function hold(): (status: number) => void {
  let release: ((status: number) => void) | undefined;
  fetchMock.mockImplementationOnce(
    () => new Promise<Response>((resolve) => (release = (status) => resolve(new Response("{}", { status })))),
  );
  return (status) => release?.(status);
}

describe("the key", () => {
  it("is 32 hex characters built from 16 random bytes", async () => {
    vi.mocked(crypto.getRandomValues).mockImplementationOnce(((bytes: Uint8Array) => {
      bytes.forEach((_, i) => (bytes[i] = i));
      return bytes;
    }) as typeof crypto.getRandomValues);
    answer(200);
    await regenerate();
    expect(sent()).toEqual(["000102030405060708090a0b0c0d0e0f"]);
    expect(crypto.getRandomValues).toHaveBeenCalledTimes(1);
    expect((vi.mocked(crypto.getRandomValues).mock.calls[0]?.[0] as Uint8Array).length).toBe(16);
  });

  it("pads small bytes to two characters", async () => {
    answer(200);
    await regenerate();
    expect(sent()).toEqual(["01".repeat(16)]);
  });

  it("comes from the browser's random source: two actions get two different keys", async () => {
    vi.mocked(crypto.getRandomValues).mockRestore();
    answer(200);
    answer(200);
    await regenerate("job:a");
    await regenerate("job:b");
    const [a, b] = sent();
    expect(a).toMatch(/^[0-9a-f]{32}$/);
    expect(b).toMatch(/^[0-9a-f]{32}$/);
    expect(a).not.toBe(b);
  });
});

describe("actions", () => {
  it("names a bulk action by its sorted distinct ids and a job action by its id", () => {
    expect(m.bulkAction(["b", "c", "a"])).toBe("bulk:a,b,c");
    expect(m.bulkAction(["c", "a", "b", "a"])).toBe("bulk:a,b,c");
    expect(m.jobAction("j1")).toBe("job:j1");
  });

  it("takes over a key the old admin stored under the same names", async () => {
    sessionStorage.setItem(NAME, JSON.stringify({ "job:j1": "ab".repeat(16), "bulk:x,y": "cd".repeat(16) }));
    answer(200);
    answer(200);
    await regenerate(m.jobAction("j1"));
    await regenerate(m.bulkAction(["y", "x"]));
    expect(sent()).toEqual(["ab".repeat(16), "cd".repeat(16)]);
  });

  it("retries the same selection in another order under the same key", async () => {
    answer(503);
    answer(200);
    await failure(regenerate(m.bulkAction(["z", "x", "y"])));
    await regenerate(m.bulkAction(["y", "z", "x"]));
    expect(sent()).toEqual([K(1), K(1)]);
  });
});

describe("saved before it is sent", () => {
  it("saves a new key under regenerateOperations before the request goes out", async () => {
    sessionStorage.setItem(NAME, JSON.stringify({ "job:other": "ef".repeat(16) }));
    const atSend: (string | null)[] = [];
    fetchMock.mockImplementationOnce(async () => {
      atSend.push(sessionStorage.getItem(NAME));
      return new Response("{}", { status: 503 });
    });
    await failure(regenerate());
    expect(atSend.map((text) => JSON.parse(text ?? "null"))).toEqual([
      { "job:other": "ef".repeat(16), "job:j1": K(1) },
    ]);
  });

  it("sends nothing and says so when the key cannot be saved", async () => {
    const state = storage();
    state.write = false;
    const err = await failure(regenerate());
    expect(err).toBeInstanceOf(m.OperationKeyStorageError);
    expect((err as Error).name).toBe("OperationKeyStorageError");
    expect((err as Error).message).toBe("This browser could not save the retry key, so the regenerate was not sent.");
    expect((err as Error).cause).toBe(WRITE_ERROR);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("after a failed save and a reload nothing was sent, and the retry sends one new key", async () => {
    const state = storage();
    state.write = false;
    await failure(regenerate());
    await reload();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(NAME)).toBeNull();
    state.write = true;
    answer(503);
    await failure(regenerate());
    expect(sent()).toEqual([K(2)]);
  });

  it("sends nothing and rewrites nothing when storage cannot be read", async () => {
    const state = storage();
    state.read = false;
    const err = await failure(regenerate());
    expect(err).toBeInstanceOf(m.OperationKeyStorageError);
    expect((err as Error).cause).toBe(READ_ERROR);
    expect(state.writes).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends nothing when the browser drops the write without an error", async () => {
    const state = storage();
    state.keep = false;
    const err = await failure(regenerate());
    expect(err).toBeInstanceOf(m.OperationKeyStorageError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // Stores each raw record in turn, regenerates, and returns the record as it was when each request went out.
  async function recordsAtSend(raws: string[]): Promise<(string | null)[]> {
    const atSend: (string | null)[] = [];
    for (const raw of raws) {
      sessionStorage.setItem(NAME, raw);
      fetchMock.mockImplementationOnce(async () => {
        atSend.push(sessionStorage.getItem(NAME));
        return new Response("{}", { status: 200 });
      });
      await regenerate();
    }
    return atSend;
  }

  it("treats a stored record that is not JSON as empty, and saves the new key before sending it", async () => {
    expect(await recordsAtSend(["not json", '{"job:j1": "ab'])).toEqual(
      [1, 2].map((n) => JSON.stringify({ "job:j1": K(n) })),
    );
  });

  it("replaces a stored record that is JSON but not an object, and saves the new key before sending it", async () => {
    expect(await recordsAtSend(["null", "[]", "7", '"text"'])).toEqual(
      [1, 2, 3, 4].map((n) => JSON.stringify({ "job:j1": K(n) })),
    );
  });

  it("replaces a stored key the server would refuse and reuses one it accepts", async () => {
    sessionStorage.setItem(
      NAME,
      JSON.stringify({
        "job:short": "abc",
        "job:number": 1234567890123456,
        "job:sixteen": "Ab_-".repeat(4),
        "job:long": "x".repeat(65),
      }),
    );
    for (let i = 0; i < 4; i += 1) answer(200);
    for (const action of ["job:short", "job:number", "job:sixteen", "job:long"]) await regenerate(action);
    expect(sent()).toEqual([K(1), K(2), "Ab_-".repeat(4), K(3)]);
  });
});

describe("keep or drop after the answer", () => {
  it("keeps the key on a 5xx and flags the error retry-safe", async () => {
    for (const status of [500, 502, 503, 599]) {
      answer(status);
      answer(status);
      const err = await failure(regenerate(`job:s${status}`));
      await failure(regenerate(`job:s${status}`));
      expect(err).toBeInstanceOf(client.ApiError);
      expect((err as ApiError).status).toBe(status);
      expect((err as ApiError).retrySafe).toBe(true);
    }
    expect(sent()).toEqual([K(1), K(1), K(2), K(2), K(3), K(3), K(4), K(4)]);
  });

  it("keeps the key on a network error and flags it retry-safe", async () => {
    lose();
    lose();
    const err = await failure(regenerate());
    await failure(regenerate());
    expect(err).toBeInstanceOf(client.NetworkError);
    expect((err as NetworkError).retrySafe).toBe(true);
    expect(sent()).toEqual([K(1), K(1)]);
    expect(stored()).toEqual({ "job:j1": K(1) });
  });

  const otherFailures = (): unknown[] => [new TypeError("a bug in our code"), new Error("boom"), "a string", undefined];

  it("keeps the key for any other failure", async () => {
    const keys: string[] = [];
    for (const [i, thrown] of otherFailures().entries()) {
      const call = async (key: string): Promise<never> => {
        keys.push(key);
        throw thrown;
      };
      await failure(m.regenerateCall(`job:o${i}`, call));
      await failure(m.regenerateCall(`job:o${i}`, call));
    }
    expect(keys).toEqual([K(1), K(1), K(2), K(2), K(3), K(3), K(4), K(4)]);
    expect(stored()).toEqual({ "job:o0": K(1), "job:o1": K(2), "job:o2": K(3), "job:o3": K(4) });
  });

  it("does not call any other failure retry-safe, and rethrows it as is", async () => {
    for (const [i, thrown] of otherFailures().entries()) {
      const err = await failure(
        m.regenerateCall(`job:o${i}`, async () => {
          throw thrown;
        }),
      );
      expect(err).toBe(thrown);
      expect((err as { retrySafe?: boolean } | undefined)?.retrySafe).toBeUndefined();
    }
  });

  it("drops a new key on any answer below 500", async () => {
    const statuses = [400, 401, 404, 409, 422, 429, 499];
    for (const status of statuses) {
      answer(status);
      answer(200);
      const err = await failure(regenerate(`job:f${status}`));
      expect((err as ApiError).status).toBe(status);
      expect((err as ApiError).retrySafe).toBe(false);
      await regenerate(`job:f${status}`);
    }
    expect(sent()).toEqual(statuses.flatMap((_, i) => [K(2 * i + 1), K(2 * i + 2)]));
    expect(stored()).toEqual({});
  });

  it("drops the key after a success, so the next click is a new action", async () => {
    answer(200, { id: "n1" });
    answer(200);
    expect(await regenerate()).toEqual({ id: "n1" });
    expect(stored()).toEqual({});
    await regenerate();
    expect(sent()).toEqual([K(1), K(2)]);
  });

  it("a 503 then a success: the retry sends the same key and the success drops it", async () => {
    answer(503);
    answer(200);
    answer(200);
    await failure(regenerate());
    await regenerate();
    await regenerate();
    expect(sent()).toEqual([K(1), K(1), K(2)]);
  });

  it("a 4xx on a retry after a 5xx keeps the key, so jobs made under it are not made again", async () => {
    for (const status of [503, 401, 429, 409]) answer(status);
    answer(200);
    answer(200);
    await failure(regenerate());
    const later = [await failure(regenerate()), await failure(regenerate()), await failure(regenerate())];
    await regenerate();
    await regenerate();
    expect(later.map((err) => [(err as ApiError).status, (err as ApiError).retrySafe])).toEqual([
      [401, false],
      [429, false],
      [409, false],
    ]);
    expect(sent()).toEqual([K(1), K(1), K(1), K(1), K(1), K(2)]);
  });

  it("removes only the dropped action from storage", async () => {
    answer(503);
    answer(400);
    await failure(regenerate("job:kept"));
    await failure(regenerate("job:gone"));
    expect(stored()).toEqual({ "job:kept": K(1) });
  });

  it("reports a success even when the key cannot be removed afterwards", async () => {
    const state = storage();
    fetchMock.mockImplementationOnce(async () => {
      state.read = false;
      state.write = false;
      return new Response(JSON.stringify({ id: "n1" }), { status: 200 });
    });
    expect(await regenerate()).toEqual({ id: "n1" });
  });
});

describe("single flight per action", () => {
  it("a second call during flight sends nothing and gets the same success", async () => {
    const release = hold();
    const first = regenerate();
    const second = vi.fn(async () => "never");
    const joined = m.regenerateCall("job:j1", second);
    release(200);
    expect(await first).toEqual({});
    expect(await joined).toEqual({});
    expect(second).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("a second call during flight gets the same failure, flagged retry-safe for a 503", async () => {
    const release = hold();
    const first = failure(regenerate());
    const second = vi.fn(async () => "never");
    const joined = failure(m.regenerateCall("job:j1", second));
    release(503);
    const err = await first;
    expect(await joined).toBe(err);
    expect((err as ApiError).retrySafe).toBe(true);
    expect(second).not.toHaveBeenCalled();
  });

  it("a click after the flight ended sends a new request", async () => {
    answer(500);
    answer(200);
    await failure(regenerate());
    await regenerate();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("a failure before anything was sent does not hold up the next click", async () => {
    const state = storage();
    state.write = false;
    await failure(regenerate());
    state.write = true;
    const broken = vi.fn((): Promise<unknown> => {
      throw new TypeError("a bug in the call site");
    });
    await failure(m.regenerateCall("job:j2", broken));
    await failure(m.regenerateCall("job:j2", broken));
    answer(200);
    await regenerate();
    expect(broken).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("reload and sign-out", () => {
  it("a reload mid-flight: the retry after the reload sends the same key", async () => {
    hold();
    void regenerate();
    await reload();
    answer(200);
    await regenerate();
    expect(sent()).toEqual([K(1), K(1)]);
  });

  it("a kept key survives a 401 sign-out and re-login: keys are not cleared with the session", async () => {
    sessionStorage.setItem(session.TOKEN_KEY, "old-session");
    answer(503);
    answer(401);
    answer(200);
    await failure(regenerate());
    await failure(regenerate());
    expect(sessionStorage.getItem(session.TOKEN_KEY)).toBeNull();
    session.setSession("new-session", "owner");
    await regenerate();
    expect(sent()).toEqual([K(1), K(1), K(1)]);
  });
});

describe("storage that can be read but not written", () => {
  it("a key dropped after a success is not sent again from the stale stored copy", async () => {
    sessionStorage.setItem(NAME, JSON.stringify({ "job:j1": "cd".repeat(16) }));
    storage().write = false;
    answer(200);
    await regenerate();
    const err = await failure(regenerate());
    expect(err).toBeInstanceOf(m.OperationKeyStorageError);
    expect(sent()).toEqual(["cd".repeat(16)]);
  });

  it("a stored key is still sent when writes fail, and kept after a 5xx", async () => {
    sessionStorage.setItem(NAME, JSON.stringify({ "job:j1": "ef".repeat(16) }));
    storage().write = false;
    answer(503);
    answer(503);
    await failure(regenerate());
    await failure(regenerate());
    expect(sent()).toEqual(["ef".repeat(16), "ef".repeat(16)]);
  });

  it("once writes work again, a new key is saved and kept as usual", async () => {
    sessionStorage.setItem(NAME, JSON.stringify({ "job:j1": "cd".repeat(16) }));
    const state = storage();
    state.write = false;
    answer(200);
    await regenerate();
    state.write = true;
    answer(503);
    answer(200);
    await failure(regenerate());
    await regenerate();
    expect(sent()).toEqual(["cd".repeat(16), K(1), K(1)]);
    expect(stored()).toEqual({});
  });
});

describe("abandoning a kept key", () => {
  it("drops the kept key only when asked, so the next click sends a new key", async () => {
    answer(503);
    answer(401);
    answer(200);
    await failure(regenerate());
    await failure(regenerate());
    expect(stored()).toEqual({ "job:j1": K(1) });
    expect(m.abandonOperation("job:j1")).toBe(true);
    expect(stored()).toEqual({});
    await regenerate();
    expect(sent()).toEqual([K(1), K(1), K(2)]);
  });

  it("is refused while the action is in flight, and the key stays", async () => {
    const release = hold();
    const first = failure(regenerate());
    expect(m.abandonOperation("job:j1")).toBe(false);
    release(503);
    await first;
    answer(200);
    await regenerate();
    expect(sent()).toEqual([K(1), K(1)]);
  });
});

describe("module surface", () => {
  it("exports only the regenerate call, the action names, abandon and the storage error", () => {
    expect(Object.keys(m).sort()).toEqual([
      "OperationKeyStorageError",
      "abandonOperation",
      "bulkAction",
      "jobAction",
      "regenerateCall",
    ]);
  });

  it("the storage error has its exact message, name and cause", () => {
    const err = new m.OperationKeyStorageError(WRITE_ERROR);
    expect(err).toBeInstanceOf(Error);
    expect(err.name).toBe("OperationKeyStorageError");
    expect(err.message).toBe("This browser could not save the retry key, so the regenerate was not sent.");
    expect(err.cause).toBe(WRITE_ERROR);
  });
});
