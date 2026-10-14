import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createJob, getJob, getOptions, getPoints, listModels, TestApiError } from "@/api/test";

const fetchMock = vi.fn();
const reply = (status: number, body: unknown) =>
  new Response(typeof body === "string" ? body : JSON.stringify(body), { status });

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  sessionStorage.setItem("relay_admin_token", "admin-token");
});
afterEach(() => {
  sessionStorage.clear();
});

describe("the public calls of the Test tab", () => {
  it("send the typed key as a Bearer header and no admin session header, without cookies", async () => {
    fetchMock.mockResolvedValue(reply(200, { data: [] }));
    await listModels("rk-typed");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/models");
    expect(init.headers).toEqual({ Authorization: "Bearer rk-typed" });
    expect(init.credentials).toBe("omit");
    expect(init.method).toBe("GET");
    expect(JSON.stringify(init)).not.toContain("admin-token");
  });

  it("use the public routes for the options, the points and both kinds of job", async () => {
    fetchMock.mockImplementation(async () => reply(200, {}));
    await getPoints("k");
    await getOptions("k", "flow/x");
    await createJob("k", "image", { model: "m" });
    await createJob("k", "video", { model: "m" });
    await getJob("k", "image", "j 1");
    await getJob("k", "video", "j2");
    expect(fetchMock.mock.calls.map((c) => `${(c[1] as RequestInit).method} ${c[0]}`)).toEqual([
      "GET /v1/points",
      "GET /v1/providers/flow%2Fx/options",
      "POST /v1/images/generations",
      "POST /v1/video/generations",
      "GET /v1/images/generations/j%201",
      "GET /v1/video/generations/j2",
    ]);
  });

  it("post a JSON body", async () => {
    fetchMock.mockResolvedValue(reply(200, { id: "j1", status: "queued" }));
    await createJob("k", "video", { model: "veo-3", prompt: "x" });
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(init.headers).toEqual({ Authorization: "Bearer k", "Content-Type": "application/json" });
    expect(init.body).toBe('{"model":"veo-3","prompt":"x"}');
  });

  it("fail with `code: message` from the error envelope", async () => {
    fetchMock.mockResolvedValue(reply(402, { error: { code: "billing.insufficient", message: "not enough points" } }));
    const err = await listModels("k").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(TestApiError);
    expect((err as TestApiError).message).toBe("billing.insufficient: not enough points");
    expect((err as TestApiError).status).toBe(402);
  });

  it("fail with the bare message when the envelope has no code, and HTTP <status> when there is no envelope", async () => {
    fetchMock.mockResolvedValueOnce(reply(400, { error: { message: "just text" } }));
    expect(((await listModels("k").catch((e: unknown) => e)) as Error).message).toBe("just text");
    fetchMock.mockResolvedValueOnce(reply(502, "<html>bad gateway</html>"));
    expect(((await listModels("k").catch((e: unknown) => e)) as Error).message).toBe("HTTP 502");
  });

  it("never show the request's own key in an error text, even when the server echoes it", async () => {
    fetchMock.mockResolvedValue(
      reply(401, { error: { code: "auth.invalid_key", message: "invalid key rk-secret-123 (rk-secret-123)" } }),
    );
    const err = (await listModels("rk-secret-123").catch((e: unknown) => e)) as TestApiError;
    expect(err.message).toBe("auth.invalid_key: invalid key [redacted] ([redacted])");
    fetchMock.mockResolvedValue(reply(400, { error: { code: "bad.rk-secret-123", message: "x" } }));
    expect(((await getPoints("rk-secret-123").catch((e: unknown) => e)) as Error).message).toBe("bad.[redacted]: x");
    expect(JSON.stringify(err)).not.toContain("rk-secret-123");
  });

  it("do not touch the admin client: a 401 is an error of the key, not a sign-out", async () => {
    fetchMock.mockResolvedValue(reply(401, { error: { code: "auth.invalid_key", message: "bad key" } }));
    await expect(listModels("k")).rejects.toThrow("auth.invalid_key: bad key");
    expect(sessionStorage.getItem("relay_admin_token")).toBe("admin-token");
  });
});
