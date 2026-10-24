import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as accounts from "@/api/accounts";
import type { Account } from "@/api/accounts";
import { ApiError } from "@/api/client";
import * as commands from "@/api/commands";
import * as shell from "@/api/shell";
import EditDialog from "@/components/accounts/EditDialog.vue";
import { hhmmss } from "@/components/accounts/account-model";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";
import AccountsView from "@/views/AccountsView.vue";

vi.mock("@/api/accounts", () => ({
  listAccounts: vi.fn(),
  patchAccount: vi.fn(),
  deleteAccount: vi.fn(),
  checkAccount: vi.fn(),
  wakeAccount: vi.fn(),
  recheckProxy: vi.fn(),
  accountScreenshot: vi.fn(),
  accountHistory: vi.fn(() => Promise.resolve({ hours: [], errors: [] })),
  importAccounts: vi.fn(),
  checkAll: vi.fn(),
  refreshCatalog: vi.fn(),
}));
vi.mock("@/api/commands", () => ({ getCommand: vi.fn() }));
vi.mock("@/api/shell", () => ({
  getModels: vi.fn(),
  getStats: vi.fn(),
  getOpenAlertGroups: vi.fn(),
  getProvidersSummary: vi.fn(),
}));
vi.mock("@/components/shared/confirm-dialog", () => ({ confirmDialog: vi.fn() }));
// the leave guard the page registers: its two predicates are read back by the tests below
const guards = vi.hoisted(() => ({ calls: [] as [() => boolean, () => boolean][] }));
vi.mock("@/components/shared/use-leave-guard", async (original) => {
  const actual = await original<typeof import("@/components/shared/use-leave-guard")>();
  return {
    ...actual,
    useRouteLeaveGuard: (dirty: () => boolean, blocked: () => boolean) => {
      guards.calls.push([dirty, blocked]);
      return actual.useRouteLeaveGuard(dirty, blocked);
    },
  };
});
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn(), clear: vi.fn() } }));

const PLANTED = "<img src=x onerror=window.__pwned=1><b>planted</b>";
const RECHECK = "Re-check account · R";

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
  cooldown_until_ms: null,
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
// a dead account: its row carries Re-check
const dead = (over: Partial<Account> = {}) => account({ state: "dead", state_reason: "signed out", ...over });

const page = (items: Account[], total = items.length) => ({ items, total });

// GET /admin/providers/summary: vibes takes jobs over HTTP with 3 of 4 accounts usable, dola is paused.
const providerRow = (provider: string, usable: number, total: number, claims = true): shell.ProviderSummary => ({
  provider,
  execution: "http",
  claims_enabled: claims,
  running: 0,
  queued: 0,
  done_today: 0,
  failed_today: 0,
  accounts: { usable, total },
  pools: [],
});
const summary = (): shell.ProvidersSummary => ({
  day_tz: "UTC",
  day_start_ms: 0,
  hours: [],
  providers: [providerRow("vibes", 3, 4), providerRow("dola", 1, 2, false)],
});
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

const button = (w: VueWrapper, name: string) =>
  w.findAll("button").find((b) => b.text() === name || b.attributes("aria-label") === name)!;
const rows = (w: VueWrapper) => w.findAll(".acct-row");
const menuItem = (name: string) =>
  [...document.body.querySelectorAll<HTMLElement>("[role=menuitem]")].find((b) => b.textContent?.trim() === name)!;
const chip = (w: VueWrapper, label: string) =>
  w.findAll(".av-chip").find((c) => c.text().replace(/\s+/g, " ").startsWith(label))!;

// Opens the More menu of the nth row and picks an item (the menu is drawn in the body).
async function pick(w: VueWrapper, name: string, nth = 0) {
  await w.findAll("[aria-label^='More actions for']")[nth]!.trigger("click");
  menuItem(name).click();
  await settle();
}

// matchMedia answering by the width named in the query: the phone edge is 767.x, the tablet band ends at 1279.x
function stubWidth(kind: "phone" | "tablet" | "desktop") {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: kind === "phone" ? /max-width:\s*767/.test(q) : kind === "tablet" ? /1279/.test(q) : false,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

beforeEach(() => {
  // happy-dom answers matchMedia for a 1024 px window, which is a tablet: the default here is the desktop
  stubWidth("desktop");
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
  vi.mocked(shell.getStats).mockResolvedValue({
    jobs: { queued: 0, in_progress: 0, done: 0, failed: 0, pre_job_failures_24h: 0 },
    made: { videos: 0, images: 0 },
    accounts: { usable: 7, available: 8, dead: 1, disabled: 0 },
    keys: 0,
    alerts: [],
  });
  vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [] });
  vi.mocked(shell.getProvidersSummary).mockResolvedValue(summary());
  delete (window as unknown as { __pwned?: number }).__pwned;
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the list", () => {
  it("draws every server string as text; a busy account reads In use and a dead one its reason", async () => {
    list.mockResolvedValue(
      page([
        account({ id: "a1", label: PLANTED, display_name: PLANTED, state_reason: PLANTED, last_error: PLANTED }),
        account({ id: "a2", label: "busy", in_use: true }),
        dead({ id: "a3", label: "gone", state_reason: "login failed" }),
      ]),
    );
    const w = await open();
    await chip(w, "All").trigger("click");
    expect(w.text()).toContain(PLANTED);
    expect(w.find("img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    const table = rows(w);
    expect(table).toHaveLength(3);
    // what needs a look comes first
    expect(table[0]!.text()).toContain("gone");
    expect(table[0]!.find(".acct-pill").text()).toBe("Dead");
    expect(table[0]!.text()).toContain("login failed");
    expect(table[1]!.find(".acct-pill").text()).toBe("In use");
    // the list has no switch: Taking jobs is in Edit
    expect(w.find("[role=switch]").exists()).toBe(false);
  });

  it("a long account, reason or ident keeps its whole text as its title, where it ends in an ellipsis", async () => {
    const owner = "Nguyen Thi Thanh Huong Collective";
    const reason = "account.dead_session_after_password_change";
    list.mockResolvedValue(page([dead({ label: "acct-n11-00007919", display_name: owner, state_reason: reason })]));
    const w = await open();
    expect(rows(w)[0]!.find(".at__open").attributes("title")).toBe("acct-n11-00007919");
    expect(rows(w)[0]!.find(".d-acct .clip").attributes("title")).toBe(owner);
    expect(rows(w)[0]!.find(".why.clip").attributes("title")).toBe(reason);
  });

  it("each health has its pill: dead, proxy failing, asleep, low, in use, available, switched off", async () => {
    const pool = (left: number, budget: number) => ({
      pool: "video",
      left_n: left,
      left_today: left,
      budget,
      drift: false,
      source: "page",
      day_anchor: null,
      rest_until_ms: null,
      rest_reason: null,
    });
    list.mockResolvedValue(
      page([
        account({ id: "a1", label: "ok" }),
        dead({ id: "a2", label: "gone" }),
        account({ id: "a3", label: "off", state: "disabled", state_reason: "disabled by admin" }),
        account({ id: "a4", label: "px", proxy_host: "10.0.0.1", proxy_port: 80, proxy_ok: false }),
        account({ id: "a5", label: "nap", cooldown_until_ms: Date.now() + 3_600_000, cooldown_reason: "x.y" }),
        account({ id: "a6", label: "low", capacity: [pool(0, 4)] }),
        account({ id: "a7", label: "busy", in_use: true }),
      ]),
    );
    const w = await open();
    await chip(w, "All").trigger("click");
    const pills = rows(w).map((r) => r.find(".acct-pill").text());
    expect(pills.slice(0, 3)).toEqual(["Dead", "Proxy failing", expect.stringMatching(/^Asleep until \d\d:\d\d$/)]);
    expect(pills.slice(3)).toEqual(["Low credits", "In use", "Available", "Switched off"]);
    // a switched-off row shows no reason under its pill
    expect(rows(w)[6]!.find(".why").exists()).toBe(false);
    expect(rows(w)[3]!.find(".why").text()).toBe("video 0 of 4 left");
  });

  it("a row says its pools, its proxy and the last check as drawn", async () => {
    list.mockResolvedValue(
      page([
        account({
          label: "acct-n11-06",
          display_name: "Demo User F",
          provider: "dola",
          composer: "v2",
          capacity: [
            {
              pool: "video",
              left_n: 27,
              left_today: 27,
              budget: 30,
              drift: true,
              source: "page",
              day_anchor: null,
              rest_until_ms: null,
              rest_reason: null,
            },
          ],
          proxy_host: "10.0.0.13",
          proxy_port: 8000,
          has_proxy_auth: true,
          proxy_ok: true,
          proxy_checked_ms: Date.now() - 5 * 60_000,
        }),
      ]),
    );
    const w = await open();
    const row = rows(w)[0]!;
    expect(row.find(".d-acct .clip").text()).toBe("Demo User F · composer v2");
    expect(row.find(".acct-pchip").text()).toBe("dola");
    expect(row.find(".d-meter .pct").text()).toBe("90%");
    expect(row.find(".d-meter .fill").attributes("style")).toContain("width: 90%");
    expect(row.findAll(".d-meter .small").at(-1)!.text()).toBe("27 / 30");
    expect(row.find(".d-proxy .proxy").text()).toBe("10.0.0.13:8000");
    expect(row.find(".d-proxy .check-line").text()).toBe("ok · 5 min ago");
  });

  it("an account with no proxy says direct, no proxy", async () => {
    const w = await open();
    expect(rows(w)[0]!.find(".d-proxy .proxy").text()).toBe("direct");
    expect(rows(w)[0]!.find(".d-proxy .check-line").text()).toBe("no proxy");
  });

  it("asks for the provider filter and the server's largest page", async () => {
    useScopeStore(pinia).provider = "dola";
    await open();
    expect(list).toHaveBeenCalledWith("dola", 200, 0);
  });

  it("a slow first answer that arrives after the second does not bring its rows back", async () => {
    const slow = deferred<ReturnType<typeof page>>();
    list.mockReturnValueOnce(slow.promise);
    const w = await open();
    list.mockResolvedValueOnce(page([account({ label: "second" })]));
    useScopeStore(pinia).provider = "dola";
    await settle();
    expect(w.text()).toContain("second");
    slow.resolve(page([account({ label: "first-stale" })]));
    await settle();
    expect(w.text()).toContain("second");
    expect(w.text()).not.toContain("first-stale");
  });

  it("the first read draws five rows of flat bars in the real columns, and no footer count", async () => {
    list.mockReturnValue(new Promise(() => {}));
    const w = await open();
    const loading = w.find("[data-state=loading]");
    expect(loading.findAll(".acct-row")).toHaveLength(5);
    expect(loading.findAll(".acct-row")[0]!.findAll(".bar")).toHaveLength(5);
    expect(w.text()).not.toContain("Showing");
    expect(w.find("[data-state=empty]").exists()).toBe(false);
  });

  it("an empty list says so and offers Import; a failed read shows the error with Retry", async () => {
    list.mockResolvedValue(page([]));
    const w = await open();
    expect(w.find("[aria-label='Account list']").text()).toContain("No accounts yet");
    expect(button(w, "Import accounts")).toBeTruthy();
    list.mockRejectedValueOnce(new ApiError(502, { error: { code: "x", message: "bad gateway" } }));
    useScopeStore(pinia).provider = "dola";
    await settle();
    expect(w.find("[role=alert]").text()).toContain(
      "Could not load the accounts: the relay answered 502 (bad gateway).",
    );
    list.mockResolvedValue(page([account({ label: "back" })]));
    await w.find("[role=alert] button").trigger("click");
    await settle();
    expect(w.find("[role=alert]").exists()).toBe(false);
    expect(w.text()).toContain("back");
  });

  it("past the server's largest page the footer says Showing a–b of N with Previous and Next", async () => {
    const many = Array.from({ length: 3 }, (_, n) => account({ id: `a${n}`, label: `n${n}` }));
    list.mockResolvedValue(page(many));
    const w = await open();
    expect(w.text()).not.toContain("Showing");
    w.unmount();
    wrapper = null;
    list.mockResolvedValue(page(many, 450));
    const again = await open();
    expect(again.text()).toContain("Showing 1–3 of 450");
    await button(again, "Next page").trigger("click");
    await settle();
    expect(list).toHaveBeenLastCalledWith("", 200, 200);
  });
});

describe("the header", () => {
  it("names the usable accounts and what needs a look, with Re-check all, Refresh catalog and Import on a desktop", async () => {
    list.mockResolvedValue(page([dead({ id: "a1" }), dead({ id: "a2", label: "two" })]));
    const w = await open();
    expect(w.find("h1").text()).toBe("Accounts");
    expect(w.find(".head__sub").text()).toBe("7 of 9 usable. 2 dead.");
    expect(button(w, "Re-check all accounts")).toBeTruthy();
    expect(button(w, "Refresh catalog")).toBeTruthy();
    expect(button(w, "Import accounts")).toBeTruthy();
  });

  it("a tablet: Check all and Import as text, and a banner of what needs a look", async () => {
    stubWidth("tablet");
    list.mockResolvedValue(page([dead()]));
    const w = await open();
    expect(button(w, "Check all")).toBeTruthy();
    expect(button(w, "Import")).toBeTruthy();
    expect(w.find(".head__banner").text()).toBe("1 of 9 need a look. 1 dead.");
  });

  it("a phone: Filter by provider, Check all accounts, Refresh catalog and Import accounts as icons, then the card", async () => {
    stubWidth("phone");
    const w = await open();
    const names = w.findAll(".head__actions button").map((b) => b.attributes("aria-label"));
    expect(names).toEqual(["Filter by provider", "Check all accounts", "Refresh catalog", "Import accounts"]);
    expect(w.find(".head__card").text()).toContain("7 of 9 usable");
    await button(w, "Filter by provider").trigger("click");
    await settle();
    const dola = [...document.body.querySelectorAll<HTMLElement>(".av-sheet-chips button")].find(
      (b) => b.textContent?.trim() === "dola",
    )!;
    dola.click();
    await settle();
    expect(useScopeStore(pinia).provider).toBe("dola");
  });
});

describe("the filters", () => {
  it("Show starts on Needs a look when something needs one, counts each issue and filters the rows", async () => {
    list.mockResolvedValue(page([account({ id: "a1", label: "fine" }), dead({ id: "a2", label: "gone" })]));
    const w = await open();
    expect(chip(w, "Needs a look").attributes("aria-pressed")).toBe("true");
    expect(chip(w, "Needs a look").find(".av-chip__n").text()).toBe("1");
    expect(chip(w, "All").find(".av-chip__n").text()).toBe("2");
    expect(rows(w).map((r) => r.find(".at__open").text())).toEqual(["gone"]);
    await chip(w, "All").trigger("click");
    expect(rows(w)).toHaveLength(2);
    await chip(w, "Switched off").trigger("click");
    expect(w.find("[data-state=empty]").text()).toContain("No switched off accounts");
    await button(w, "Clear filters").trigger("click");
    expect(rows(w)).toHaveLength(2);
  });

  it("with nothing that needs a look Show starts on All", async () => {
    const w = await open();
    expect(chip(w, "All").attributes("aria-pressed")).toBe("true");
    expect(rows(w)).toHaveLength(1);
  });

  it("a provider chip sets the shared filter and a second click clears it", async () => {
    const w = await open();
    const scope = useScopeStore(pinia);
    expect(chip(w, "All providers").attributes("aria-pressed")).toBe("true");
    await chip(w, "dola").trigger("click");
    await settle();
    expect(scope.provider).toBe("dola");
    expect(list).toHaveBeenLastCalledWith("dola", 200, 0);
    await chip(w, "dola").trigger("click");
    expect(scope.provider).toBe("");
  });

  it("a paused provider says so over its accounts, which read Paused", async () => {
    useScopeStore(pinia).provider = "dola";
    list.mockResolvedValue(page([account({ provider: "dola" })]));
    const w = await open();
    expect(w.find(".av-banner").text()).toContain("dola is paused in Settings.");
    expect(rows(w)[0]!.find(".acct-pill").text()).toBe("Paused");
  });

  it("a phone: no Check button on a healthy row, Check first in the row menu", async () => {
    stubWidth("phone");
    const w = await open();
    expect(w.findAll(".acct-row")).toHaveLength(1);
    expect(w.find(`[aria-label='${RECHECK}']`).exists()).toBe(false);
    await w.find("[aria-label^='More actions for']").trigger("click");
    const names = [...document.body.querySelectorAll("[role=menuitem]")].map((i) =>
      i.textContent?.replace("none yet", "").trim(),
    );
    expect(names).toEqual(["Check", "Shot", "Wake", "Edit", "Delete"]);
  });
});

describe("the bulk Disable", () => {
  const tickFirst = async (w: VueWrapper) => {
    await rows(w)[0]!.find("input[type=checkbox]").trigger("change");
    await settle();
  };

  it("sends enabled false for each ticked row and reads the list again", async () => {
    vi.mocked(accounts.patchAccount).mockResolvedValue({ id: "a1", command_id: null });
    const w = await open();
    await tickFirst(w);
    expect(w.find("[aria-label='Bulk actions']").text()).toContain("1 selected");
    await button(w, "Disable").trigger("click");
    await settle();
    expect(accounts.patchAccount).toHaveBeenCalledWith("a1", { enabled: false });
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("reads the list again when the change fails, so the row goes back, and says why", async () => {
    vi.mocked(accounts.patchAccount).mockRejectedValue(
      new ApiError(409, { error: { code: "pool.account_busy", message: "account is busy" } }),
    );
    const w = await open();
    await tickFirst(w);
    await button(w, "Disable").trigger("click");
    await settle();
    expect(list).toHaveBeenCalledTimes(2);
    expect(notify.error).toHaveBeenCalledWith("account is busy");
  });
});

describe("the detail", () => {
  it("the name opens the account's drawer; its footer edits, re-checks and wakes", async () => {
    vi.mocked(accounts.wakeAccount).mockResolvedValue({ command_id: "w1" });
    vi.mocked(commands.getCommand).mockResolvedValue({ id: "w1", status: "done", result: null } as never);
    list.mockResolvedValue(
      page([account({ cooldown_until_ms: Date.now() + 3_600_000, cooldown_reason: "dola.refused", strikes: 3 })]),
    );
    const w = await open();
    await rows(w)[0]!.find(".at__open").trigger("click");
    await settle();
    const drawer = document.body.querySelector(".rl-drawer")!;
    expect(drawer.querySelector("h2")!.textContent!.trim()).toBe("main");
    expect(drawer.querySelector("[role=status]")!.textContent).toContain("Resting after 3 strikes.");
    const names = [...drawer.querySelectorAll(".rl-drawer__foot button")].map(
      (b) => b.getAttribute("aria-label") ?? b.textContent?.trim(),
    );
    expect(names).toEqual(["Delete account", "Edit account", RECHECK, "Wake now"]);
    (drawer.querySelector(".ad__primary") as HTMLElement).click();
    await settle();
    expect(accounts.wakeAccount).toHaveBeenCalledExactlyOnceWith("a1");
  });

  it("R re-checks the account whose drawer is open", async () => {
    vi.mocked(accounts.checkAccount).mockResolvedValue({ command_id: "c1" });
    vi.mocked(commands.getCommand).mockReturnValue(new Promise(() => {}));
    const w = await open();
    await rows(w)[0]!.find(".at__open").trigger("click");
    await settle();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "r", bubbles: true }));
    await settle();
    expect(accounts.checkAccount).toHaveBeenCalledExactlyOnceWith("a1");
  });
});

describe("the row commands and the edit dialog", () => {
  const done = (id: string) => ({ id, status: "done", result: null }) as unknown as commands.CommandStatus;
  const inBody = (text: string) =>
    [...document.body.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === text || b.getAttribute("aria-label") === text,
    )!;
  const editOpen = (w: VueWrapper) => w.findComponent(EditDialog).props("modelValue");
  const type = (label: string, value: string) => {
    const el = document.body.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
    el.value = value;
    el.dispatchEvent(new Event("input"));
  };
  const recheck = (w: VueWrapper, nth = 0) => rows(w)[nth]!.find(`[aria-label='${RECHECK}']`);

  it("Wake queues the wake command under the label that names the cooldown and the strikes", async () => {
    vi.mocked(accounts.wakeAccount).mockResolvedValue({ command_id: "w1" });
    vi.mocked(commands.getCommand).mockResolvedValue(done("w1"));
    const w = await open();
    await pick(w, "Wake");
    expect(accounts.wakeAccount).toHaveBeenCalledExactlyOnceWith("a1");
    expect(notify.info).toHaveBeenCalledWith("Wake: clear cooldown and strikes: queued");
    expect(notify.success).toHaveBeenCalledWith("Wake: clear cooldown and strikes done");
  });

  it("a healthy row has its More menu only, a dead one Re-check too; the menu holds Shot (only with a screenshot), Wake, Edit, Delete", async () => {
    list.mockResolvedValue(page([account(), dead({ id: "a2", label: "gone" })]));
    const w = await open();
    await chip(w, "All").trigger("click");
    const labels = (r: number) =>
      rows(w)
        [r]!.findAll(".d-act button")
        .map((b) => b.attributes("aria-label"));
    expect(labels(0)).toEqual([RECHECK, "More actions for gone"]);
    expect(labels(1)).toEqual(["More actions for main"]);
    await w.find("[aria-label='More actions for main']").trigger("click");
    const items = [...document.body.querySelectorAll("[role=menuitem]")];
    expect(items.map((i) => i.textContent?.replace("none yet", "").trim())).toEqual(["Shot", "Wake", "Edit", "Delete"]);
    expect(items[0]!.getAttribute("aria-disabled")).toBe("true");
    expect(items[1]!.getAttribute("aria-disabled")).toBeNull();
    document.body.click();
    list.mockResolvedValue(page([account({ has_screenshot: true })]));
    wrapper!.unmount();
    wrapper = null;
    const again = await open();
    await again.find("[aria-label='More actions for main']").trigger("click");
    expect(menuItem("Shot").getAttribute("aria-disabled")).toBeNull();
  });

  it("Shot of an account without a screenshot does nothing", async () => {
    const w = await open();
    await w.find("[aria-label^='More actions for']").trigger("click");
    [...document.body.querySelectorAll<HTMLElement>("[role=menuitem]")][0]!.click();
    await settle();
    expect(accounts.accountScreenshot).not.toHaveBeenCalled();
  });

  it("Check queues a check, locks that button while it runs, and says what it found", async () => {
    list.mockResolvedValue(page([dead()]));
    vi.mocked(accounts.checkAccount).mockResolvedValue({ command_id: "c1" });
    const gate = deferred<commands.CommandStatus>();
    vi.mocked(commands.getCommand).mockReturnValue(gate.promise);
    const w = await open();
    await recheck(w).trigger("click");
    await settle();
    expect(accounts.checkAccount).toHaveBeenCalledExactlyOnceWith("a1");
    expect(recheck(w).attributes("disabled")).toBeDefined();
    // R on the row while it runs queues nothing more
    rows(w)[0]!.element.dispatchEvent(new KeyboardEvent("keydown", { key: "r", bubbles: true }));
    await settle();
    expect(accounts.checkAccount).toHaveBeenCalledTimes(1);
    gate.resolve(done("c1"));
    await settle();
    expect(recheck(w).attributes("disabled")).toBeUndefined();
    expect(notify.info).not.toHaveBeenCalledWith("Check: queued");
    expect(notify.error).toHaveBeenCalledWith("Check done: account dead (signed out)");
  });

  it("Delete asks first and queues the delete only after a yes", async () => {
    const ask = vi.mocked(confirmDialog).mockReset().mockResolvedValue(false);
    vi.mocked(accounts.deleteAccount).mockResolvedValue({ command_id: "d1" });
    vi.mocked(commands.getCommand).mockResolvedValue(done("d1"));
    const w = await open();
    await pick(w, "Delete");
    expect(ask).toHaveBeenCalledTimes(1);
    // as Dialogs draws a delete confirm: the title asks and names the account, one sentence says the consequence
    expect(ask).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Delete account main?",
        message: "A used account cannot be deleted: disable it instead.",
        confirmText: "Delete",
        danger: true,
      }),
    );
    expect(accounts.deleteAccount).not.toHaveBeenCalled();
    ask.mockResolvedValue(true);
    await pick(w, "Delete");
    expect(accounts.deleteAccount).toHaveBeenCalledExactlyOnceWith("a1");
  });

  it("Check proxy keeps the dialog open with its chip, queues the re-check, polls Proxy check and reads the list again", async () => {
    vi.mocked(accounts.recheckProxy).mockResolvedValue({ command_id: "r1" });
    const gate = deferred<commands.CommandStatus>();
    vi.mocked(commands.getCommand).mockReturnValue(gate.promise);
    const w = await open();
    await pick(w, "Edit");
    expect(editOpen(w)).toBe(true);
    inBody("Check proxy").click();
    await settle();
    expect(editOpen(w)).toBe(true);
    expect(document.body.querySelector(".rl-drawer [data-state=running]")).not.toBeNull();
    // the check polls for a long time: Save stays usable meanwhile
    expect(inBody("Save").getAttribute("aria-disabled")).toBeNull();
    expect(accounts.recheckProxy).toHaveBeenCalledExactlyOnceWith("a1");
    expect(notify.info).not.toHaveBeenCalledWith("Proxy check: queued");
    gate.resolve(done("r1"));
    await settle();
    expect(commands.getCommand).toHaveBeenCalledWith("r1");
    expect(notify.success).toHaveBeenCalledWith("Proxy check done");
    expect(document.body.querySelector(".rl-drawer [data-state=ok]")).not.toBeNull();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("a Check's chip runs under its button, says how it ended, stays through the next automatic read and goes with the one after", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      list.mockResolvedValue(page([dead()]));
      vi.mocked(accounts.checkAccount).mockResolvedValue({ command_id: "c1" });
      const gate = deferred<commands.CommandStatus>();
      vi.mocked(commands.getCommand).mockReturnValue(gate.promise);
      const w = await open();
      const cmd = () => w.find(".acct-row .cmd-line [data-state]");
      await recheck(w).trigger("click");
      await settle();
      expect(cmd().attributes("data-state")).toBe("running");
      const finished = Date.UTC(2026, 9, 15, 13, 55, 7);
      gate.resolve({ ...done("c1"), finished_ms: finished });
      await settle();
      expect(cmd().text()).toBe(`ok · ${hhmmss(finished)}`);
      // the next automatic read keeps it (it is seen at least one period); the one after replaces the row
      await vi.advanceTimersByTimeAsync(10000);
      await settle();
      expect(cmd().text()).toBe(`ok · ${hhmmss(finished)}`);
      await vi.advanceTimersByTimeAsync(10000);
      await settle();
      expect(cmd().exists()).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("two quick Checks keep both results: the reads the commands make do not clear a chip", async () => {
    const labels = ["main", "two", "three"];
    list.mockResolvedValue(page(labels.map((label, n) => dead({ id: `a${n}`, label }))));
    vi.mocked(accounts.checkAccount).mockImplementation(async (id) => ({ command_id: `c-${id}` }));
    vi.mocked(commands.getCommand).mockImplementation(async (id) => done(id));
    const w = await open();
    for (const n of [0, 1, 2]) {
      await recheck(w, n).trigger("click");
      await settle();
    }
    expect(w.findAll(".acct-row .cmd-line [data-state=ok]")).toHaveLength(3);
  });

  it("a failed Check's chip and toast carry the worker's reason", async () => {
    list.mockResolvedValue(page([dead()]));
    vi.mocked(accounts.checkAccount).mockResolvedValue({ command_id: "c1" });
    vi.mocked(commands.getCommand).mockResolvedValue({
      ...done("c1"),
      status: "failed",
      result: { error: "connect timeout after 8 s" },
    });
    const w = await open();
    await recheck(w).trigger("click");
    await settle();
    expect(w.find(".acct-row .cmd-line [data-state=error]").text()).toBe("error · connect timeout after 8 s");
    expect(notify.error).toHaveBeenCalledWith("Check failed: connect timeout after 8 s");
  });

  it("Save of a changed proxy polls the Proxy check the server queued, then reads the list", async () => {
    vi.mocked(accounts.patchAccount).mockResolvedValue({ id: "a1", command_id: "p1" });
    vi.mocked(commands.getCommand).mockResolvedValue(done("p1"));
    const w = await open();
    await pick(w, "Edit");
    type("Proxy", "10.9.9.9:3128");
    inBody("Save").click();
    await settle();
    expect(accounts.patchAccount).toHaveBeenCalledExactlyOnceWith("a1", { proxy: "10.9.9.9:3128" });
    expect(notify.success).toHaveBeenCalledWith("Saved");
    expect(commands.getCommand).toHaveBeenCalledWith("p1");
    expect(notify.success).toHaveBeenCalledWith("Proxy check done");
    expect(list).toHaveBeenCalledTimes(2);
    expect(editOpen(w)).toBe(false);
  });

  it("Save of a flipped Taking jobs switch sends enabled", async () => {
    vi.mocked(accounts.patchAccount).mockResolvedValue({ id: "a1", command_id: null });
    const w = await open();
    await pick(w, "Edit");
    document.body.querySelector<HTMLElement>("[role=switch]")!.click();
    await settle();
    inBody("Save").click();
    await settle();
    expect(accounts.patchAccount).toHaveBeenCalledExactlyOnceWith("a1", { enabled: false });
  });

  it("a Check proxy that ends while the dialog is open never wipes what was typed", async () => {
    vi.mocked(accounts.recheckProxy).mockResolvedValue({ command_id: "r1" });
    const gate = deferred<commands.CommandStatus>();
    vi.mocked(commands.getCommand).mockReturnValue(gate.promise);
    const w = await open();
    await pick(w, "Edit");
    inBody("Check proxy").click();
    await settle();
    type("Label", "renamed");
    gate.resolve(done("r1"));
    await settle();
    // the check's read of the list brought a new copy of the account: the field keeps the typed value
    expect(list).toHaveBeenCalledTimes(2);
    expect(document.body.querySelector<HTMLInputElement>('[aria-label="Label"]')!.value).toBe("renamed");
  });

  it("Save during a running Check proxy waits for it, then sends what was typed", async () => {
    vi.mocked(accounts.recheckProxy).mockResolvedValue({ command_id: "r1" });
    vi.mocked(accounts.patchAccount).mockResolvedValue({ id: "a1", command_id: null });
    const gate = deferred<commands.CommandStatus>();
    vi.mocked(commands.getCommand).mockReturnValue(gate.promise);
    const w = await open();
    await pick(w, "Edit");
    inBody("Check proxy").click();
    await settle();
    type("Label", "renamed");
    // Save stays clickable while the check runs; the save waits for the check and its read of the list
    expect(inBody("Save").getAttribute("aria-disabled")).toBeNull();
    inBody("Save").click();
    await settle();
    // a second click while the save waits sends nothing more
    inBody("Save").click();
    await settle();
    expect(accounts.patchAccount).not.toHaveBeenCalled();
    gate.resolve(done("r1"));
    await settle();
    expect(accounts.patchAccount).toHaveBeenCalledExactlyOnceWith("a1", { label: "renamed" });
    // the order: the check's read, then the save, then the save's own read
    expect(vi.mocked(accounts.patchAccount).mock.invocationCallOrder[0]!).toBeGreaterThan(
      list.mock.invocationCallOrder[1]!,
    );
    expect(list).toHaveBeenCalledTimes(3);
    expect(editOpen(w)).toBe(false);
  });

  it("a second click while an action runs sends nothing: Wake, Delete, the bulk Disable, the phone's Check", async () => {
    const wakeGate = deferred<commands.CommandStatus>();
    vi.mocked(accounts.wakeAccount).mockResolvedValue({ command_id: "w1" });
    vi.mocked(commands.getCommand).mockReturnValue(wakeGate.promise);
    const w = await open();
    await pick(w, "Wake");
    await pick(w, "Wake");
    expect(accounts.wakeAccount).toHaveBeenCalledTimes(1);
    wakeGate.resolve(done("w1"));
    await settle();

    const ask = deferred<boolean>();
    vi.mocked(confirmDialog).mockReset().mockReturnValue(ask.promise);
    await pick(w, "Delete");
    await pick(w, "Delete");
    expect(confirmDialog).toHaveBeenCalledTimes(1);
    ask.resolve(false);
    await settle();

    const patched = deferred<{ id: string; command_id: null }>();
    vi.mocked(accounts.patchAccount).mockReturnValue(patched.promise);
    for (let twice = 0; twice < 2; twice++) {
      await rows(w)[0]!.find("input[type=checkbox]").trigger("change");
      await button(w, "Disable").trigger("click");
    }
    expect(accounts.patchAccount).toHaveBeenCalledTimes(1);
    patched.resolve({ id: "a1", command_id: null });
    await settle();
    wrapper!.unmount();
    wrapper = null;

    stubWidth("phone");
    const checkGate = deferred<commands.CommandStatus>();
    vi.mocked(accounts.checkAccount).mockResolvedValue({ command_id: "c1" });
    vi.mocked(commands.getCommand).mockReturnValue(checkGate.promise);
    const phone = await open();
    await pick(phone, "Check");
    await pick(phone, "Check");
    expect(accounts.checkAccount).toHaveBeenCalledTimes(1);
    checkGate.resolve(done("c1"));
    await settle();
  });

  it("Save of a label only polls nothing", async () => {
    vi.mocked(accounts.patchAccount).mockResolvedValue({ id: "a1", command_id: null });
    const w = await open();
    await pick(w, "Edit");
    type("Label", "renamed");
    inBody("Save").click();
    await settle();
    expect(accounts.patchAccount).toHaveBeenCalledExactlyOnceWith("a1", { label: "renamed" });
    expect(commands.getCommand).not.toHaveBeenCalled();
    expect(list).toHaveBeenCalledTimes(2);
  });
});

describe("Check all and Refresh catalog", () => {
  const done = (id: string) => ({ id, status: "done", result: null }) as unknown as commands.CommandStatus;
  const CHECK_ALL = "Re-check all accounts";

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
    await button(w, CHECK_ALL).trigger("click");
    await settle();
    // dola (sorted first) is still running: vibes must not have been queued yet
    expect(calls).toEqual(["dola"]);
    // and Check all is drawn disabled meanwhile, so a second click queues nothing
    expect(button(w, CHECK_ALL).attributes("disabled")).toBeDefined();
    await button(w, CHECK_ALL).trigger("click");
    expect(calls).toEqual(["dola"]);
    firstDone.resolve(done("c-dola"));
    await vi.waitFor(() => expect(calls).toEqual(["dola", "vibes"]));
    expect(notify.info).not.toHaveBeenCalledWith("Check all dola: queued");
    expect(notify.info).not.toHaveBeenCalledWith("Check all vibes: queued");
  });

  it("each page command has its chip under its button: running, then how the round ended (the first failure)", async () => {
    vi.mocked(accounts.checkAll).mockImplementation(async (p) => ({ command_id: `c-${p}` }));
    const gate = deferred<commands.CommandStatus>();
    vi.mocked(commands.getCommand).mockImplementation((id) =>
      id === "c-dola" ? gate.promise : Promise.resolve(done(id)),
    );
    const w = await open();
    await button(w, CHECK_ALL).trigger("click");
    await settle();
    const cmd = () => w.find(".head__cmds [data-state]");
    expect(cmd().attributes("data-state")).toBe("running");
    gate.resolve({ ...done("c-dola"), status: "failed", result: { error: "worker gone" } });
    await vi.waitFor(() => expect(cmd().attributes("data-state")).toBe("error"));
    expect(cmd().text()).toBe("error · worker gone");
    expect(accounts.checkAll).toHaveBeenCalledWith("vibes");
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
    expect(notify.info).not.toHaveBeenCalledWith("Refresh catalog vibes: queued");
  });
});

describe("auto-refresh", () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] }));

  it("reads the list every 10 seconds while the page is open", async () => {
    await open();
    expect(list).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(9999);
    expect(list).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(list).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(10000);
    expect(list).toHaveBeenCalledTimes(3);
  });

  it("does not read the list while a dialog is open, and goes on when it closes", async () => {
    const w = await open();
    await button(w, "Import accounts").trigger("click");
    await settle();
    await vi.advanceTimersByTimeAsync(10000);
    expect(list).toHaveBeenCalledTimes(1);
    document.body.querySelector<HTMLElement>("[data-testid=import-drawer] [aria-label='Close · Esc']")!.click();
    await settle();
    await vi.advanceTimersByTimeAsync(10000);
    expect(list).toHaveBeenCalledTimes(2);
  });

  const hidden = (value: boolean) => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => value });
    document.dispatchEvent(new Event("visibilitychange"));
  };
  afterEach(() => hidden(false));

  it("a hidden tab does not read the list, and the tab that comes back reads at once", async () => {
    await open();
    hidden(true);
    await vi.advanceTimersByTimeAsync(30000);
    expect(list).toHaveBeenCalledTimes(1);
    hidden(false);
    await settle();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("a read still in flight is never joined by a second one", async () => {
    await open();
    const slow = deferred<ReturnType<typeof page>>();
    list.mockReturnValueOnce(slow.promise);
    await vi.advanceTimersByTimeAsync(10000);
    expect(list).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(30000);
    expect(list).toHaveBeenCalledTimes(2);
    slow.resolve(page([account()]));
    await settle();
    await vi.advanceTimersByTimeAsync(10000);
    expect(list).toHaveBeenCalledTimes(3);
  });

  it("stops with the view", async () => {
    const w = await open();
    w.unmount();
    wrapper = null;
    await vi.advanceTimersByTimeAsync(30000);
    expect(list).toHaveBeenCalledTimes(1);
  });
});

describe("the Edit and Import drawers on the page", () => {
  const done = (id: string) => ({ id, status: "done", result: null }) as unknown as commands.CommandStatus;
  const editOpen = (w: VueWrapper) => w.findComponent(EditDialog).props("modelValue");
  const escape = () =>
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }));
  const type = (selector: string, value: string) => {
    const el = document.body.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector)!;
    el.value = value;
    el.dispatchEvent(new Event("input"));
  };
  const inDrawer = (text: string) =>
    [...document.body.querySelectorAll<HTMLElement>(".rl-drawer__foot button")].find(
      (b) => b.textContent?.trim() === text,
    )!;
  const leave = () => guards.calls.at(-1)!;

  it("a row command that is running does not stop the Edit drawer from closing", async () => {
    list.mockResolvedValue(page([dead()]));
    vi.mocked(accounts.checkAccount).mockResolvedValue({ command_id: "c1" });
    const gate = deferred<commands.CommandStatus>();
    vi.mocked(commands.getCommand).mockReturnValue(gate.promise);
    const w = await open();
    await w.find(`[aria-label='${RECHECK}']`).trigger("click");
    await settle();
    await pick(w, "Edit");
    expect(editOpen(w)).toBe(true);
    escape();
    await settle();
    expect(editOpen(w)).toBe(false);
    gate.resolve(done("c1"));
    await settle();
  });

  it("a paste in Import and a change in Edit are what a leave of the page would lose", async () => {
    const w = await open();
    const [dirty] = leave();
    expect(dirty()).toBe(false);
    await button(w, "Import accounts").trigger("click");
    await settle();
    type("textarea", "cookie-one");
    await settle();
    expect(dirty()).toBe(true);
    type("textarea", "");
    await settle();
    expect(dirty()).toBe(false);
    escape();
    await settle();
    await pick(w, "Edit");
    type("[aria-label=Label]", "renamed");
    await settle();
    expect(dirty()).toBe(true);
  });

  it("after the save the proxy check it queued does not lock the drawer: Edit opens again and closes", async () => {
    vi.mocked(accounts.patchAccount).mockResolvedValue({ id: "a1", command_id: "p1" });
    const poll = deferred<commands.CommandStatus>();
    vi.mocked(commands.getCommand).mockReturnValue(poll.promise);
    const w = await open();
    const [, blocked] = leave();
    await pick(w, "Edit");
    type("[aria-label=Label]", "renamed");
    await settle();
    inDrawer("Save").click();
    await settle();
    expect(notify.success).toHaveBeenCalledWith("Saved");
    expect(editOpen(w)).toBe(false);
    // the poll is still waiting on its command
    await pick(w, "Edit");
    expect(editOpen(w)).toBe(true);
    escape();
    await settle();
    expect(editOpen(w)).toBe(false);
    expect(blocked()).toBe(false);
    poll.resolve(done("p1"));
    await settle();
  });

  it("an import under way blocks a leave of the page, and so does a save in flight", async () => {
    const importing = deferred<accounts.ImportResult>();
    vi.mocked(accounts.importAccounts).mockReturnValue(importing.promise);
    const w = await open();
    const [, blocked] = leave();
    expect(blocked()).toBe(false);
    await button(w, "Import accounts").trigger("click");
    await settle();
    type("textarea", "cookie-one");
    await settle();
    inDrawer("Import").click();
    await settle();
    expect(blocked()).toBe(true);
    importing.resolve({ imported: [], rejected: [] });
    await settle();
    expect(blocked()).toBe(false);
    document.body.querySelector<HTMLElement>("[data-testid=import-drawer] [aria-label='Close · Esc']")!.click();
    await settle();

    const saving = deferred<{ command_id: string | null }>();
    vi.mocked(accounts.patchAccount).mockReturnValue(saving.promise as ReturnType<typeof accounts.patchAccount>);
    await pick(w, "Edit");
    type("[aria-label=Label]", "renamed");
    await settle();
    inDrawer("Save").click();
    await settle();
    expect(blocked()).toBe(true);
    saving.resolve({ command_id: null });
    await settle();
    expect(blocked()).toBe(false);
  });

  describe("focus after the drawer is gone", () => {
    // the drawer's leave transition does not run here: its `closed` is sent by hand, with the focus off every control
    const gone = async (w: VueWrapper) => {
      (document.activeElement as HTMLElement | null)?.blur();
      w.findComponent(EditDialog).vm.$emit("closed");
      await settle();
    };
    const seeable = () =>
      vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{}] as unknown as DOMRectList);
    afterEach(() => vi.restoreAllMocks());

    it("is left to Element Plus, which gives it back to the row button, while that button can be seen", async () => {
      seeable();
      const w = await open();
      await pick(w, "Edit");
      await gone(w);
      expect(document.activeElement).toBe(document.body);
    });

    it("goes to the page's Import button when the opener can no longer be seen", async () => {
      vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([] as unknown as DOMRectList);
      const w = await open();
      await pick(w, "Edit");
      await gone(w);
      expect(document.activeElement).toBe(button(w, "Import accounts").element);
    });

    it("goes to the page's Import button when the opener was removed from the page", async () => {
      seeable();
      const w = await open();
      await pick(w, "Edit");
      w.find("[aria-label='More actions for main']").element.remove();
      await gone(w);
      expect(document.activeElement).toBe(button(w, "Import accounts").element);
    });
  });
});
