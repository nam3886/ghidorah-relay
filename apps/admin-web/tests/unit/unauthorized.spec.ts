import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request, setUnauthorizedHandler } from "@/api/client";
import { installUnauthorizedHandler } from "@/api/unauthorized";
import { TEST_KEY, TOKEN_KEY, USER_KEY } from "@/lib/session";
import { createAppRouter } from "@/router";
import { useAuthStore } from "@/stores/auth";

beforeEach(() => {
  sessionStorage.clear();
  setUnauthorizedHandler(null);
  setActivePinia(createPinia());
});

describe("a 401 on any call", () => {
  it("clears the session, runs the sign-out hooks once and routes to login", async () => {
    sessionStorage.setItem(TOKEN_KEY, "t");
    sessionStorage.setItem(USER_KEY, "owner");
    sessionStorage.setItem(TEST_KEY, "sk-typed");
    setActivePinia(createPinia());
    const router = createAppRouter(createMemoryHistory());
    await router.push({ name: "keys" });
    expect(router.currentRoute.value.name).toBe("keys");
    installUnauthorizedHandler(router);
    const hook = vi.fn();
    useAuthStore().onSignOut(hook);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 401 })),
    );

    await expect(request("/keys")).rejects.toMatchObject({ status: 401 });
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe("login"), { timeout: 15000 });
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(sessionStorage.getItem(TEST_KEY)).toBeNull();
    expect(useAuthStore().isSignedIn).toBe(false);
    expect(hook).toHaveBeenCalledTimes(1);
  });
});
