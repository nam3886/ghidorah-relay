import { beforeEach, describe, expect, it, vi } from "vitest";
import { getPoolStats, getStats } from "@/api/shell";

const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
const urlOf = (n: number) => new URL(fetchMock.mock.calls[n]![0], "http://x");

beforeEach(() => {
  sessionStorage.clear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("stats cards", () => {
  it("asks /admin/stats for the provider and hands the account counts through, in use and asleep included", async () => {
    const accounts = { usable: 4, available: 6, dead: 1, disabled: 2, in_use: 3, asleep: 1 };
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ accounts }), { status: 200 }));
    const stats = await getStats("dola");
    expect(urlOf(0).pathname).toBe("/admin/stats");
    expect(urlOf(0).search).toBe("?provider=dola");
    expect(stats.accounts).toEqual(accounts);
  });
});

describe("pool stats", () => {
  it("asks /admin/stats/pool for the provider with GET and returns the figures as sent", async () => {
    const body = {
      pools: [{ pool: "video", left: 143, budget: 210, unknown: 2, drift: 1 }],
      proxy_failed: 1,
      last_full_check_ms: 1_700_000_000_000,
    };
    fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }));
    expect(await getPoolStats("dola")).toEqual(body);
    expect(urlOf(0).pathname).toBe("/admin/stats/pool");
    expect(urlOf(0).search).toBe("?provider=dola");
    expect(fetchMock.mock.calls[0]![1].method).toBe("GET");
    expect(fetchMock.mock.calls[0]![1].body).toBeUndefined();
  });

  it("sends no provider for all providers, keeps a null last check, and passes a refusal on", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ pools: [], proxy_failed: 0, last_full_check_ms: null }), { status: 200 }),
    );
    expect((await getPoolStats("")).last_full_check_ms).toBeNull();
    expect(urlOf(0).search).toBe("");
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: { code: "auth.invalid_session", message: "no" } }), { status: 401 }),
    );
    await expect(getPoolStats("x")).rejects.toMatchObject({ status: 401 });
  });
});
