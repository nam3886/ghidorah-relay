import { beforeEach, describe, expect, it, vi } from "vitest";
import { getStats } from "@/api/shell";

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
