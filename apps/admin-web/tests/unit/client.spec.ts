import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError, request, requestRaw, setUnauthorizedHandler } from "@/api/client";
import { errorText, RETRY_SAFE_TEXT } from "@/lib/errors";
import { OperationKeyStorageError } from "@/lib/operation-key";
import { TEST_KEY, TOKEN_KEY, USER_KEY } from "@/lib/session";

const fetchMock = vi.fn();

function reply(status: number, body?: unknown): Response {
  return new Response(body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body), { status });
}

function lastCall(): { url: string; init: RequestInit & { headers: Record<string, string> } } {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit & { headers: Record<string, string> }];
  return { url, init };
}

beforeEach(() => {
  sessionStorage.clear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  setUnauthorizedHandler(null);
});

describe("request", () => {
  it("sends the session header read from sessionStorage on every call", async () => {
    fetchMock.mockImplementation(async () => reply(200, { ok: true }));
    sessionStorage.setItem(TOKEN_KEY, "first");
    await request("/me");
    expect(lastCall().init.headers["X-Admin-Session"]).toBe("first");
    sessionStorage.setItem(TOKEN_KEY, "second");
    await request("/me");
    expect(lastCall().init.headers["X-Admin-Session"]).toBe("second");
    expect(lastCall().init.credentials).toBe("same-origin");
  });

  it("calls /admin<path>, GET by default, with no body and no content type", async () => {
    fetchMock.mockResolvedValue(reply(200, { username: "owner" }));
    const out = await request<{ username: string }>("/me");
    expect(out).toEqual({ username: "owner" });
    const { url, init } = lastCall();
    expect(url).toBe("/admin/me");
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
    expect(init.headers["Content-Type"]).toBeUndefined();
  });

  it("sends a JSON body with its content type", async () => {
    fetchMock.mockResolvedValue(reply(200, {}));
    await request("/jobs/bulk-recheck", { method: "POST", body: { job_ids: ["a", "b"] } });
    const { url, init } = lastCall();
    expect(url).toBe("/admin/jobs/bulk-recheck");
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"job_ids":["a","b"]}');
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("builds the query string and leaves out empty, null and undefined values", async () => {
    fetchMock.mockImplementation(async () => reply(200, {}));
    await request("/jobs", {
      query: { provider: "", status: "done", limit: 50, cursor: undefined, key_id: null, flag: false },
    });
    expect(lastCall().url).toBe("/admin/jobs?status=done&limit=50&flag=false");
    await request("/jobs", { query: { provider: "" } });
    expect(lastCall().url).toBe("/admin/jobs");
  });

  it("turns the error envelope into an ApiError", async () => {
    fetchMock.mockResolvedValue(
      reply(400, { error: { code: "input.invalid", message: "Password is too short", param: "new_password" } }),
    );
    const err = await request("/password", { method: "POST", body: {} }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({
      status: 400,
      code: "input.invalid",
      message: "Password is too short",
      param: "new_password",
      retrySafe: false,
    });
  });

  it("shows the server message of a 429", async () => {
    fetchMock.mockResolvedValue(
      reply(429, {
        error: { code: "auth.too_many_attempts", message: "Too many attempts, try again later", param: null },
      }),
    );
    const err = (await request("/login", { method: "POST", body: {}, noRedirect: true }).catch(
      (e: unknown) => e,
    )) as ApiError;
    expect(err.status).toBe(429);
    expect(err.code).toBe("auth.too_many_attempts");
    expect(errorText(err)).toBe("Too many attempts, try again later");
  });

  it("falls back to the status when the error body is not an envelope", async () => {
    fetchMock.mockResolvedValue(reply(502, "<html>bad gateway</html>"));
    const err = (await request("/stats").catch((e: unknown) => e)) as ApiError;
    expect(err).toMatchObject({ status: 502, code: "", param: "", message: "HTTP 502" });
  });

  it("treats an empty success body as an empty object", async () => {
    fetchMock.mockResolvedValue(reply(204));
    expect(await request("/logout", { method: "POST" })).toEqual({});
  });

  it("turns a failed fetch into a NetworkError that keeps the cause", async () => {
    const failure = new TypeError("Failed to fetch");
    fetchMock.mockRejectedValue(failure);
    const err = (await request("/stats").catch((e: unknown) => e)) as NetworkError;
    expect(err).toBeInstanceOf(NetworkError);
    expect(err).not.toBeInstanceOf(ApiError);
    expect(err.cause).toBe(failure);
    expect(err.retrySafe).toBe(false);
    expect(errorText(err)).toBe("Network error, please try again");
  });
});

describe("the /admin boundary", () => {
  beforeEach(() => {
    fetchMock.mockImplementation(async () => reply(200, {}));
    sessionStorage.setItem(TOKEN_KEY, "secret-token");
  });

  it.each(["/../x", "/%2e%2e/x", "/%2E%2E/x", "/..\\x", "/a/../../x", "/a/%2e%2e/%2e%2e/x"])(
    "refuses %s before anything is sent",
    async (path) => {
      await expect(request(path)).rejects.toThrow("Not an admin API path");
      await expect(requestRaw(path)).rejects.toThrow("Not an admin API path");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it.each(["https://evil.example/x", "http://evil.example", "me", "", "java" + "script:alert(1)"])(
    "refuses the non-path %j before anything is sent",
    async (path) => {
      await expect(request(path)).rejects.toThrow("Not an admin API path");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it.each(["//evil.example/x", "//", "///x"])("refuses the protocol-relative %s with no fetch", async (path) => {
    await expect(request(path)).rejects.toThrow("Not an admin API path");
    await expect(requestRaw(path)).rejects.toThrow("Not an admin API path");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends only a path, never a host", async () => {
    await request("/jobs", { query: { status: "done" } });
    expect(lastCall().url.startsWith("/admin/jobs?")).toBe(true);
  });

  it("allows a path that goes down and back up inside /admin", async () => {
    await request("/a/../me");
    expect(lastCall().url).toBe("/admin/me");
  });
});

describe("401", () => {
  it("clears the token, the user and the Test key, runs the handler once and throws", async () => {
    sessionStorage.setItem(TOKEN_KEY, "t");
    sessionStorage.setItem(USER_KEY, "owner");
    sessionStorage.setItem(TEST_KEY, "sk-typed");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    fetchMock.mockResolvedValue(
      reply(401, {
        error: { code: "auth.expired_elsewhere", message: "Signed out on another device", param: "session" },
      }),
    );
    const err = (await request("/stats").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({
      status: 401,
      code: "auth.expired_elsewhere",
      message: "Signed out on another device",
      param: "session",
    });
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(sessionStorage.getItem(USER_KEY)).toBeNull();
    expect(sessionStorage.getItem(TEST_KEY)).toBeNull();
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("gives a bare 401 the default session-ended text", async () => {
    sessionStorage.setItem(TOKEN_KEY, "t");
    fetchMock.mockResolvedValue(reply(401));
    const err = (await request("/stats").catch((e: unknown) => e)) as ApiError;
    expect(err).toMatchObject({
      status: 401,
      code: "auth.invalid_session",
      message: "Your session has ended, sign in again",
    });
  });

  it("does nothing but throw for a call marked no-redirect (the login call)", async () => {
    sessionStorage.setItem(TOKEN_KEY, "t");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    fetchMock.mockResolvedValue(
      reply(401, { error: { code: "auth.invalid", message: "Wrong username or password", param: null } }),
    );
    const err = (await request("/login", { method: "POST", body: {}, noRedirect: true }).catch(
      (e: unknown) => e,
    )) as ApiError;
    expect(err.status).toBe(401);
    expect(err.message).toBe("Wrong username or password");
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe("t");
    expect(handler).not.toHaveBeenCalled();
  });

  it("does not end a newer session when a slow answer for the old one arrives", async () => {
    sessionStorage.setItem(TOKEN_KEY, "old");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    fetchMock.mockImplementation(async () => {
      sessionStorage.setItem(TOKEN_KEY, "new");
      return reply(401);
    });
    await expect(request("/stats")).rejects.toBeInstanceOf(ApiError);
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe("new");
    expect(handler).not.toHaveBeenCalled();
  });

  it("also ends the session for the raw variant", async () => {
    sessionStorage.setItem(TOKEN_KEY, "t");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    fetchMock.mockResolvedValue(reply(401));
    await expect(requestRaw("/accounts/1/screenshot")).rejects.toMatchObject({ status: 401 });
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(handler).toHaveBeenCalledTimes(1);
  });
});

describe("requestRaw", () => {
  it("returns the response so the caller reads a blob or text, with the header sent", async () => {
    sessionStorage.setItem(TOKEN_KEY, "t");
    fetchMock.mockResolvedValue(new Response("png-bytes", { status: 200 }));
    const response = await requestRaw("/accounts/1/screenshot");
    expect(await response.text()).toBe("png-bytes");
    expect(lastCall().url).toBe("/admin/accounts/1/screenshot");
    expect(lastCall().init.headers["X-Admin-Session"]).toBe("t");
  });

  it("throws an ApiError for a non-ok answer", async () => {
    fetchMock.mockResolvedValue(
      reply(404, { error: { code: "resource.not_found", message: "Not found", param: null } }),
    );
    await expect(requestRaw("/jobs/x/content/0")).rejects.toMatchObject({ status: 404, code: "resource.not_found" });
  });
});

describe("errorText", () => {
  it("says retrying is safe for an error flagged retrySafe", () => {
    const err = new ApiError(503, { error: { code: "x", message: "Down", param: null } });
    err.retrySafe = true;
    expect(errorText(err)).toBe(RETRY_SAFE_TEXT);
    const network = Object.assign(new TypeError("Failed to fetch"), { retrySafe: true });
    expect(errorText(network)).toBe(RETRY_SAFE_TEXT);
    expect(RETRY_SAFE_TEXT).toBe(
      "Trying again is safe. Retry the same selection in this tab; it will not charge twice.",
    );
  });

  it("shows the storage error's own message", () => {
    expect(errorText(new OperationKeyStorageError(new Error("quota")))).toBe(
      "This browser could not save the retry key, so the regenerate was not sent.",
    );
  });

  it("gives the message, with the field when the server names one", () => {
    expect(errorText(new ApiError(400, { error: { code: "c", message: "Too short", param: "new_password" } }))).toBe(
      "Too short (new_password)",
    );
    expect(errorText(new ApiError(404, { error: { code: "c", message: "Not found", param: null } }))).toBe("Not found");
  });

  it("falls back to a network message for anything else", () => {
    expect(errorText(new TypeError("Failed to fetch"))).toBe("Network error, please try again");
    expect(errorText(null)).toBe("Network error, please try again");
  });
});
