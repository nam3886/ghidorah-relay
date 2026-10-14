import { createMemoryHistory, createWebHashHistory } from "vue-router";
import { beforeEach, describe, expect, it } from "vitest";
import { createAppRouter, TABS } from "@/router";
import { TOKEN_KEY } from "@/lib/session";

beforeEach(() => {
  sessionStorage.clear();
  sessionStorage.setItem(TOKEN_KEY, "t");
});

describe("routes", () => {
  it("registers the login route and all eight tabs, Jobs first", () => {
    const router = createAppRouter(createMemoryHistory());
    const names = router
      .getRoutes()
      .map((r) => r.name)
      .filter(Boolean);
    expect(names).toEqual(
      expect.arrayContaining(["login", "jobs", "accounts", "keys", "logs", "alerts", "frames", "settings", "test"]),
    );
    expect(TABS.map((t) => t.name)).toEqual([
      "jobs",
      "accounts",
      "keys",
      "logs",
      "alerts",
      "frames",
      "settings",
      "test",
    ]);
    expect(TABS.map((t) => router.resolve({ name: t.name }).path)).toEqual([
      "/jobs",
      "/accounts",
      "/keys",
      "/logs",
      "/alerts",
      "/frames",
      "/settings",
      "/test",
    ]);
  });

  it("opens Jobs for the root and for an unknown path", async () => {
    const router = createAppRouter(createMemoryHistory());
    await router.push("/");
    expect(router.currentRoute.value.name).toBe("jobs");
    await router.push("/nothing/here");
    expect(router.currentRoute.value.name).toBe("jobs");
  });

  it("sends a signed-out visitor to login from every tab, and lets login through", async () => {
    sessionStorage.clear();
    for (const tab of TABS) {
      const router = createAppRouter(createMemoryHistory());
      await router.push({ name: tab.name });
      expect(router.currentRoute.value.name).toBe("login");
    }
    const router = createAppRouter(createMemoryHistory());
    await router.push("/login");
    expect(router.currentRoute.value.name).toBe("login");
  });

  it("keeps the query a stats cell links with", async () => {
    const router = createAppRouter(createMemoryHistory());
    await router.push({ name: "jobs", query: { status: "failed" } });
    expect(router.currentRoute.value.fullPath).toBe("/jobs?status=failed");
  });
});

describe("old bookmarks with a bare tab hash", () => {
  async function openAtHash(hash: string) {
    history.replaceState(null, "", `/manage${hash}`);
    const hashHistory = createWebHashHistory();
    const router = createAppRouter(hashHistory);
    await router.push(hashHistory.location);
    return router;
  }

  it.each(["jobs", "accounts", "keys", "logs", "alerts", "frames", "settings", "test"])(
    "#%s lands on its route",
    async (id) => {
      const router = await openAtHash(`#${id}`);
      expect(router.currentRoute.value.name).toBe(id);
      expect(router.currentRoute.value.path).toBe(`/${id}`);
      expect(location.hash).toBe(`#/${id}`);
    },
  );

  it("an empty hash opens Jobs", async () => {
    const router = await openAtHash("");
    expect(router.currentRoute.value.name).toBe("jobs");
  });

  it("a stale or mistyped hash opens Jobs", async () => {
    const router = await openAtHash("#nope");
    expect(router.currentRoute.value.name).toBe("jobs");
  });
});
