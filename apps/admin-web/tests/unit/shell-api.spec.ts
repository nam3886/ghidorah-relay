import { beforeEach, describe, expect, it, vi } from "vitest";
import { getHourlyStats } from "@/api/shell";

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
