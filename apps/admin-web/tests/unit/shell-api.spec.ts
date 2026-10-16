import { beforeEach, describe, expect, it, vi } from "vitest";
import { getHourlyStats, getPoolStats } from "@/api/shell";

const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
const urlOf = (n: number) => new URL(fetchMock.mock.calls[n]![0], "http://x");

beforeEach(() => {
  sessionStorage.clear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("hourly stats", () => {
  it("asks /admin/stats/hourly for the provider, with GET and no body, and returns the buckets as sent", async () => {
    const hours = [{ hour_ms: 1_700_000_000_000, delivered: 3, failed: 1, in_progress: 2 }];
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ hours }), { status: 200 }));
    expect(await getHourlyStats("dola")).toEqual({ hours });
    expect(urlOf(0).pathname).toBe("/admin/stats/hourly");
    expect(urlOf(0).search).toBe("?provider=dola");
    expect(fetchMock.mock.calls[0]![1].method).toBe("GET");
    expect(fetchMock.mock.calls[0]![1].body).toBeUndefined();
  });

  it("sends no provider for all providers, and passes a server refusal on", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ hours: [] }), { status: 200 }));
    await getHourlyStats("");
    expect(urlOf(0).search).toBe("");
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: { code: "input.invalid", message: "bad" } }), { status: 400 }),
    );
    await expect(getHourlyStats("x")).rejects.toMatchObject({ status: 400 });
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
