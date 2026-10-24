import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AlertGroup } from "@/api/alerts";
import * as shell from "@/api/shell";
import type { Stats } from "@/api/shell";
import { notify } from "@/lib/notify";
import { TEST_KEY, TOKEN_KEY, USER_KEY } from "@/lib/session";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";
import { useStatsStore } from "@/stores/stats";
import { useUiStore } from "@/stores/ui";

vi.mock("@/api/shell", () => ({
  login: vi.fn(),
  logout: vi.fn(),
  getMe: vi.fn(),
  getModels: vi.fn(),
  getStats: vi.fn(),
  getOpenAlertGroups: vi.fn(),
  getNewestReply: vi.fn(),
}));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const group = (over: Partial<AlertGroup> = {}): AlertGroup => ({
  kind: "unclear_reply",
  group: "g1",
  title: "sorry, i can't create that video",
  severity: "warning",
  provider: "dola",
  first_ms: 1,
  last_ms: 2,
  count: 4,
  open_count: 4,
  keys: 3,
  newest_id: 9,
  ...over,
});

const statsOf = (done: number): Stats => ({
  jobs: { queued: 0, in_progress: 0, done, failed: 0, pre_job_failures_24h: 0 },
  made: { videos: 0, images: 0 },
  accounts: { usable: 0, available: 0, dead: 0, disabled: 0 },
  keys: 0,
  alerts: [],
});

beforeEach(() => {
  sessionStorage.clear();
  setActivePinia(createPinia());
  for (const fn of Object.values(shell)) (fn as ReturnType<typeof vi.fn>).mockReset();
  vi.mocked(notify.error).mockReset();
});

describe("auth store", () => {
  it("login stores the session under the old key names", async () => {
    vi.mocked(shell.login).mockResolvedValue({ token: "tok", expires_at: 1 });
    const auth = useAuthStore();
    await auth.login("owner", "pw");
    expect(shell.login).toHaveBeenCalledWith("owner", "pw");
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe("tok");
    expect(sessionStorage.getItem(USER_KEY)).toBe("owner");
    expect(auth.isSignedIn).toBe(true);
    expect(auth.user).toBe("owner");
  });

  it("a failed login stores nothing", async () => {
    vi.mocked(shell.login).mockRejectedValue(new Error("nope"));
    const auth = useAuthStore();
    await expect(auth.login("owner", "bad")).rejects.toThrow("nope");
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(auth.isSignedIn).toBe(false);
  });

  it("signOut clears token, user and Test key and runs every hook", () => {
    sessionStorage.setItem(TOKEN_KEY, "t");
    sessionStorage.setItem(USER_KEY, "owner");
    sessionStorage.setItem(TEST_KEY, "sk-typed");
    setActivePinia(createPinia());
    const auth = useAuthStore();
    const one = vi.fn();
    const two = vi.fn();
    auth.onSignOut(one);
    auth.onSignOut(two);
    auth.signOut();
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(sessionStorage.getItem(USER_KEY)).toBeNull();
    expect(sessionStorage.getItem(TEST_KEY)).toBeNull();
    expect(auth.isSignedIn).toBe(false);
    expect(auth.user).toBe("");
    expect(one).toHaveBeenCalledTimes(1);
    expect(two).toHaveBeenCalledTimes(1);
  });

  it("a hook that throws does not stop the others", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const auth = useAuthStore();
    const after = vi.fn();
    auth.onSignOut(() => {
      throw new Error("hook broke");
    });
    auth.onSignOut(after);
    auth.signOut();
    expect(after).toHaveBeenCalledTimes(1);
  });

  it("a removed hook is not called", () => {
    const auth = useAuthStore();
    const hook = vi.fn();
    const remove = auth.onSignOut(hook);
    remove();
    auth.signOut();
    expect(hook).not.toHaveBeenCalled();
  });

  it("logout signs out locally even when the call fails", async () => {
    sessionStorage.setItem(TOKEN_KEY, "t");
    setActivePinia(createPinia());
    vi.mocked(shell.logout).mockRejectedValue(new Error("session already gone"));
    const auth = useAuthStore();
    await auth.logout();
    expect(shell.logout).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(auth.isSignedIn).toBe(false);
  });

  it("loadUser takes the name from /me", async () => {
    vi.mocked(shell.getMe).mockResolvedValue({ username: "admin" });
    const auth = useAuthStore();
    await auth.loadUser();
    expect(auth.user).toBe("admin");
  });
});

describe("scope store", () => {
  const catalog = {
    data: [
      { id: "a", owned_by: "vibes" },
      { id: "b", owned_by: "dola" },
      { id: "c", owned_by: "vibes" },
    ],
  };

  it("lists the providers of the catalog once each, sorted", async () => {
    vi.mocked(shell.getModels).mockResolvedValue(catalog);
    const scope = useScopeStore();
    expect(scope.providers).toEqual([]);
    await scope.loadCatalog();
    expect(scope.providers).toEqual(["dola", "vibes"]);
  });

  it("reads the catalog once, even for parallel and later callers", async () => {
    vi.mocked(shell.getModels).mockResolvedValue(catalog);
    const scope = useScopeStore();
    await Promise.all([scope.loadCatalog(), scope.loadCatalog()]);
    await scope.loadCatalog();
    expect(shell.getModels).toHaveBeenCalledTimes(1);
  });

  it("a failed read can be tried again", async () => {
    vi.mocked(shell.getModels).mockRejectedValueOnce(new Error("down")).mockResolvedValueOnce(catalog);
    const scope = useScopeStore();
    await expect(scope.loadCatalog()).rejects.toThrow("down");
    await scope.loadCatalog();
    expect(scope.providers).toEqual(["dola", "vibes"]);
  });

  it("starts at All and forgets the filter and the catalog on sign-out", async () => {
    vi.mocked(shell.getModels).mockResolvedValue(catalog);
    const scope = useScopeStore();
    expect(scope.provider).toBe("");
    await scope.loadCatalog();
    scope.provider = "dola";
    useAuthStore().signOut();
    expect(scope.provider).toBe("");
    expect(scope.models).toBeNull();
  });
});

describe("stats store", () => {
  it("asks for the stats of the chosen provider and the open groups", async () => {
    vi.mocked(shell.getStats).mockResolvedValue(statsOf(7));
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [group(), group({ group: "g2" })] });
    useScopeStore().provider = "dola";
    const stats = useStatsStore();
    await stats.refresh();
    expect(shell.getStats).toHaveBeenCalledWith("dola");
    expect(stats.data?.jobs.done).toBe(7);
    expect(stats.openGroups).toBe(2);
    expect(stats.loading).toBe(false);
  });

  it("a group of a muted kind is never counted or worded", async () => {
    vi.mocked(shell.getStats).mockResolvedValue(statsOf(1));
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({
      items: [group({ kind: "clamp", muted_until_ms: Date.now() + 60_000 }), group({ kind: "drift", group: "g2" })],
    });
    const stats = useStatsStore();
    await stats.refresh();
    expect(stats.openGroups).toBe(1);
    expect(stats.firstGroup?.kind).toBe("drift");
  });

  it("keeps the first group and its newest reply, read again only when the group or its newest row changes", async () => {
    vi.mocked(shell.getStats).mockResolvedValue(statsOf(1));
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [group()] });
    vi.mocked(shell.getNewestReply).mockResolvedValue("Sorry, I can’t create that video");
    const stats = useStatsStore();
    await stats.refresh();
    expect(stats.firstGroup?.group).toBe("g1");
    expect(stats.firstReply).toBe("Sorry, I can’t create that video");
    await stats.refresh();
    expect(shell.getNewestReply).toHaveBeenCalledTimes(1);
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [group({ newest_id: 10 })] });
    vi.mocked(shell.getNewestReply).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await stats.refresh();
    expect(shell.getNewestReply).toHaveBeenCalledTimes(2);
    expect(stats.firstReply).toBeNull();
    expect(notify.error).not.toHaveBeenCalled();
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [group({ kind: "clamp" })] });
    await stats.refresh();
    expect(shell.getNewestReply).toHaveBeenCalledTimes(2);
    expect(stats.firstReply).toBeNull();
  });

  it("drops a slow first answer that arrives after the second", async () => {
    const slow = deferred<Stats>();
    vi.mocked(shell.getStats).mockReturnValueOnce(slow.promise).mockResolvedValueOnce(statsOf(2));
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [] });
    const stats = useStatsStore();
    const first = stats.refresh();
    await stats.refresh();
    expect(stats.data?.jobs.done).toBe(2);
    slow.resolve(statsOf(1));
    await first;
    expect(stats.data?.jobs.done).toBe(2);
  });

  it("drops a stale error too, and the newest error is toasted", async () => {
    const slow = deferred<Stats>();
    vi.mocked(shell.getStats).mockReturnValueOnce(slow.promise).mockResolvedValueOnce(statsOf(2));
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [] });
    const stats = useStatsStore();
    const first = stats.refresh();
    await stats.refresh();
    slow.reject(new TypeError("Failed to fetch"));
    await first;
    expect(notify.error).not.toHaveBeenCalled();

    vi.mocked(shell.getStats).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await stats.refresh();
    expect(notify.error).toHaveBeenCalledExactlyOnceWith("Network error, please try again");
    expect(stats.data?.jobs.done).toBe(2);
  });

  it("an answer that arrives after sign-out is dropped and the data is cleared", async () => {
    const slow = deferred<Stats>();
    vi.mocked(shell.getStats).mockReturnValue(slow.promise);
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [group()] });
    const stats = useStatsStore();
    const pending = stats.refresh();
    useAuthStore().signOut();
    slow.resolve(statsOf(9));
    await pending;
    expect(stats.data).toBeNull();
    expect(stats.openGroups).toBe(0);
  });
});

describe("ui store", () => {
  it("opens and closes the one job detail", () => {
    const ui = useUiStore();
    expect(ui.openJobId).toBeNull();
    ui.openJob("j1");
    expect(ui.openJobId).toBe("j1");
    ui.openJob("j2");
    expect(ui.openJobId).toBe("j2");
    ui.closeJob();
    expect(ui.openJobId).toBeNull();
  });

  it("sign-out closes the job detail", () => {
    const ui = useUiStore();
    ui.openJob("j1");
    useAuthStore().signOut();
    expect(ui.openJobId).toBeNull();
    expect("accountsAutoRefresh" in ui).toBe(false);
  });
});
