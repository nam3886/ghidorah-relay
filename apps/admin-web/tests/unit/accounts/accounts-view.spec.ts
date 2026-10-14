import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as accounts from "@/api/accounts";
import type { Account } from "@/api/accounts";
import { ApiError } from "@/api/client";
import * as commands from "@/api/commands";
import * as shell from "@/api/shell";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";
import { useUiStore } from "@/stores/ui";
import AccountsView from "@/views/AccountsView.vue";

vi.mock("@/api/accounts", () => ({
  listAccounts: vi.fn(),
  patchAccount: vi.fn(),
  deleteAccount: vi.fn(),
  checkAccount: vi.fn(),
  wakeAccount: vi.fn(),
  recheckProxy: vi.fn(),
  accountScreenshot: vi.fn(),
  importAccounts: vi.fn(),
  checkAll: vi.fn(),
  refreshCatalog: vi.fn(),
}));
vi.mock("@/api/commands", () => ({ getCommand: vi.fn() }));
vi.mock("@/api/shell", () => ({ getModels: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const PLANTED = "<img src=x onerror=window.__pwned=1><b>planted</b>";

const account = (over: Partial<Account> = {}): Account => ({
  id: "a1",
  provider: "vibes",
  label: "main",
  identity: "me@example.com",
  display_name: null,
  state: "available",
  state_reason: null,
  proxy_host: null,
  proxy_port: null,
  has_proxy_auth: false,
  proxy_ok: null,
  proxy_checked_ms: null,
  strikes: 0,
  cooldown_ms: null,
  cooldown_reason: null,
  last_error: null,
  last_used_ms: null,
  last_checked_ms: null,
  in_use: false,
  has_screenshot: false,
  composer: null,
  capacity: [],
  made: { video: 0, image: 0 },
  ...over,
});

const page = (items: Account[]) => ({ items, total: items.length });
const list = vi.mocked(accounts.listAccounts);
let wrapper: VueWrapper | null = null;
let pinia = createPinia();

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

async function settle() {
  await flushPromises();
  await flushPromises();
}

async function open(): Promise<VueWrapper> {
  wrapper = mount(AccountsView, { global: { plugins: [pinia] }, attachTo: document.body });
  await settle();
  return wrapper;
}

const button = (w: VueWrapper, text: string) => w.findAll("button").find((b) => b.text() === text)!;
const rows = (w: VueWrapper) => w.findAll("tbody tr");

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  pinia = createPinia();
  setActivePinia(pinia);
  for (const fn of [
    ...Object.values(accounts),
    ...Object.values(commands),
    ...Object.values(shell),
    ...Object.values(notify),
  ]) {
    (fn as ReturnType<typeof vi.fn>).mockReset();
  }
  list.mockResolvedValue(page([account()]));
  vi.mocked(shell.getModels).mockResolvedValue({
    data: [
      { id: "m1", owned_by: "vibes" },
      { id: "m2", owned_by: "dola" },
    ],
  });
  delete (window as unknown as { __pwned?: number }).__pwned;
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
  vi.useRealTimers();
});

describe("the list", () => {
  it("draws every server string as text, and hides the switch of an account in use", async () => {
    list.mockResolvedValue(
      page([
        account({ id: "a1", label: PLANTED, display_name: PLANTED, state_reason: PLANTED, last_error: PLANTED }),
        account({ id: "a2", label: "busy", in_use: true, made: { video: 3, image: 2 } }),
        account({ id: "a3", label: "gone", state: "dead", state_reason: "login failed" }),
      ]),
    );
    const w = await open();
    expect(w.text()).toContain(PLANTED);
    expect(w.find("img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    const table = rows(w);
    expect(table).toHaveLength(3);
    expect(table[0]!.find(".el-switch").exists()).toBe(true);
    expect(table[1]!.find(".el-switch").exists()).toBe(false);
    expect(table[1]!.text()).toContain("3 videos · 2 images");
    expect(table[1]!.text()).toContain("in use");
    expect(table[2]!.text()).toContain("login failed");
  });

  it("asks for the provider filter and 50 per page", async () => {
    useScopeStore(pinia).provider = "dola";
    await open();
    expect(list).toHaveBeenCalledWith("dola", 50, 0);
  });

  it("a slow first answer that arrives after the second does not bring its rows back", async () => {
    const w = await open();
    // a Refresh that is slow, then a change of the provider filter (Refresh itself is locked while it loads)
    const slow = deferred<ReturnType<typeof page>>();
    list.mockReturnValueOnce(slow.promise);
    await button(w, "Refresh").trigger("click");
    list.mockResolvedValueOnce(page([account({ label: "second" })]));
    useScopeStore(pinia).provider = "dola";
    await settle();
    expect(w.text()).toContain("second");
    slow.resolve(page([account({ label: "first-stale" })]));
    await settle();
    expect(w.text()).toContain("second");
    expect(w.text()).not.toContain("first-stale");
  });
});

describe("the enable switch", () => {
  it("sends enabled and reads the list again", async () => {
    vi.mocked(accounts.patchAccount).mockResolvedValue({ id: "a1", command_id: null });
    const w = await open();
    await rows(w)[0]!.find(".el-switch").trigger("click");
    await settle();
    expect(accounts.patchAccount).toHaveBeenCalledWith("a1", { enabled: false });
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("reads the list again when the change fails, so the switch goes back, and says why", async () => {
    vi.mocked(accounts.patchAccount).mockRejectedValue(
      new ApiError(409, { error: { code: "pool.account_busy", message: "account is busy" } }),
    );
    const w = await open();
    await rows(w)[0]!.find(".el-switch").trigger("click");
    await settle();
    expect(list).toHaveBeenCalledTimes(2);
    expect(notify.error).toHaveBeenCalledWith("account is busy");
  });
});

describe("Check all and Refresh catalog", () => {
  const done = (id: string) => ({ id, status: "done", result: null }) as unknown as commands.CommandStatus;

  it("with the filter on All, goes over the providers one after another", async () => {
    const calls: string[] = [];
    const firstDone = deferred<commands.CommandStatus>();
    vi.mocked(accounts.checkAll).mockImplementation(async (p) => {
      calls.push(p);
      return { command_id: `c-${p}` };
    });
    vi.mocked(commands.getCommand).mockImplementation((id) =>
      id === "c-dola" ? firstDone.promise : Promise.resolve(done(id)),
    );
    const w = await open();
    await button(w, "Check all").trigger("click");
    await settle();
    // dola (sorted first) is still running: vibes must not have been queued yet
    expect(calls).toEqual(["dola"]);
    firstDone.resolve(done("c-dola"));
    await vi.waitFor(() => expect(calls).toEqual(["dola", "vibes"]));
    expect(notify.info).toHaveBeenCalledWith("Check all dola: queued");
    expect(notify.info).toHaveBeenCalledWith("Check all vibes: queued");
  });

  it("with a provider chosen, asks that provider only", async () => {
    useScopeStore(pinia).provider = "vibes";
    vi.mocked(accounts.refreshCatalog).mockResolvedValue({ command_id: "c1" });
    vi.mocked(commands.getCommand).mockResolvedValue(done("c1"));
    const w = await open();
    await button(w, "Refresh catalog").trigger("click");
    await settle();
    expect(accounts.refreshCatalog).toHaveBeenCalledTimes(1);
    expect(accounts.refreshCatalog).toHaveBeenCalledWith("vibes");
    expect(notify.info).toHaveBeenCalledWith("Refresh catalog vibes: queued");
  });
});

describe("auto-refresh", () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] }));

  it("reads the list every 10 seconds while the switch is on, and stops when it is off", async () => {
    const ui = useUiStore(pinia);
    ui.accountsAutoRefresh = true;
    await open();
    expect(list).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(9999);
    expect(list).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(list).toHaveBeenCalledTimes(2);
    ui.accountsAutoRefresh = false;
    await settle();
    await vi.advanceTimersByTimeAsync(30000);
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("does not read the list while a dialog is open, and goes on when it closes", async () => {
    useUiStore(pinia).accountsAutoRefresh = true;
    const w = await open();
    await button(w, "Import").trigger("click");
    await settle();
    await vi.advanceTimersByTimeAsync(10000);
    expect(list).toHaveBeenCalledTimes(1);
    const close = [...document.body.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Close")!;
    close.click();
    await settle();
    await vi.advanceTimersByTimeAsync(10000);
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("stops with the view, and the switch comes back on the next visit", async () => {
    useUiStore(pinia).accountsAutoRefresh = true;
    const w = await open();
    w.unmount();
    wrapper = null;
    await vi.advanceTimersByTimeAsync(30000);
    expect(list).toHaveBeenCalledTimes(1);
    await open();
    expect(list).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(10000);
    expect(list).toHaveBeenCalledTimes(3);
  });
});
