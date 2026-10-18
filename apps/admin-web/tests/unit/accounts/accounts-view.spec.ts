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
  importAccounts: vi.fn(),
  checkAll: vi.fn(),
  refreshCatalog: vi.fn(),
}));
vi.mock("@/api/commands", () => ({ getCommand: vi.fn() }));
vi.mock("@/api/shell", () => ({
  getModels: vi.fn(),
  getStats: vi.fn(),
  getOpenAlertGroups: vi.fn(),
  getPoolStats: vi.fn(),
}));
vi.mock("@/components/shared/confirm-dialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn(), clear: vi.fn() } }));

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

const button = (w: VueWrapper, name: string) =>
  w.findAll("button").find((b) => b.text() === name || b.attributes("aria-label") === name)!;
const rows = (w: VueWrapper) => w.findAll("tbody tr");
const menuItem = (name: string) =>
  [...document.body.querySelectorAll<HTMLElement>("[role=menuitem]")].find((b) => b.textContent?.trim() === name)!;

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
  vi.mocked(shell.getPoolStats).mockResolvedValue({
    pools: [{ pool: "video", left: 143, budget: 210, unknown: 2, drift: 1 }],
    proxy_failed: 1,
    last_full_check_ms: new Date(2026, 9, 15, 20, 48, 10).getTime(),
  });
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
    expect(table[0]!.find("[role=switch]").exists()).toBe(true);
    expect(table[1]!.find("[role=switch]").exists()).toBe(false);
    // the two counts are two right-aligned number columns, not one text cell
    const headers = w.findAll("thead th").map((th) => th.text());
    const cells = table[1]!.findAll("td").map((td) => td.text());
    expect(cells[headers.indexOf("Videos made")]).toBe("3");
    expect(cells[headers.indexOf("Images made")]).toBe("2");
    expect(table[1]!.text()).not.toContain("videos ·");
    expect(table[1]!.text()).toContain("in use");
    expect(table[2]!.text()).toContain("login failed");
  });

  it("a dead account's row is tinted, and a disabled account reads disabled", async () => {
    list.mockResolvedValue(
      page([
        account({ id: "a1", label: "ok" }),
        account({ id: "a2", label: "gone", state: "dead" }),
        account({ id: "a3", label: "off", state: "disabled" }),
      ]),
    );
    const w = await open();
    const table = rows(w);
    expect(table[0]!.classes()).not.toContain("is-dead");
    expect(table[1]!.classes()).toContain("is-dead");
    expect(table[2]!.classes()).not.toContain("is-dead");
    expect(table[2]!.text()).toContain("disabled");
    expect(table[2]!.find("[role=switch]").attributes("aria-checked")).toBe("false");
    expect(table[0]!.find("[role=switch]").attributes("aria-checked")).toBe("true");
  });

  it("a row says its points, drift, sleep, proxy and last error as drawn", async () => {
    const at = Date.now() + 3_600_000;
    list.mockResolvedValue(
      page([
        account({
          label: "acct-n11-06",
          display_name: "Demo User F",
          provider: "dola",
          composer: "v2",
          last_checked_ms: Date.now() - 4 * 60_000,
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
          cooldown_until_ms: at,
          cooldown_reason: "dola.likeness_refused",
          strikes: 1,
          proxy_host: "10.0.0.13",
          proxy_port: 8000,
          has_proxy_auth: true,
          proxy_ok: false,
          proxy_checked_ms: Date.now() - 5 * 60_000,
          last_error: "drift: page shows 26, ledger says 27",
        }),
      ]),
    );
    const w = await open();
    const text = rows(w)[0]!.text();
    expect(text).toContain("acct-n11-06");
    expect(text).toContain("Demo User F · dola · composer v2");
    expect(text).toContain("Checked 4 min ago");
    expect(text).toContain("video 27 / 30");
    expect(text).toContain("drift");
    expect(text).toContain("asleep until");
    expect(text).toContain("· strike");
    expect(text).toContain("dola.likeness_refused");
    expect(text).toContain("10.0.0.13:8000");
    expect(text).toContain("auth");
    expect(text).toContain("failed · 5 min ago");
    expect(text).toContain("drift: page shows 26, ledger says 27");
  });

  it("an account with no proxy says direct; an awake one shows a dash in Asleep", async () => {
    const w = await open();
    expect(rows(w)[0]!.text()).toContain("direct");
    expect(rows(w)[0]!.text()).toContain("—");
  });

  it("asks for the provider filter and 50 per page", async () => {
    useScopeStore(pinia).provider = "dola";
    await open();
    expect(list).toHaveBeenCalledWith("dola", 50, 0);
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

  it("the first read draws the list's own header over five rows of bars, and no footer count", async () => {
    list.mockReturnValue(new Promise(() => {}));
    const w = await open();
    const loading = w.find("[data-state=loading]");
    expect(loading.find("thead").text()).toContain("Points left / budget");
    expect(loading.findAll("tbody tr")).toHaveLength(5);
    expect(loading.findAll("tbody tr")[0]!.findAll(".acct-skel")).toHaveLength(10);
    expect(w.text()).not.toContain("Showing");
    expect(w.find("[data-state=empty]").exists()).toBe(false);
  });

  it("an empty list says so and offers Import; a failed read shows the error with Retry", async () => {
    list.mockResolvedValue(page([]));
    const w = await open();
    expect(w.find("[aria-label='Account list']").text()).toContain("No accounts yet.");
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

  it("the footer says Showing 1–N of N", async () => {
    list.mockResolvedValue(page([account({ id: "a1" }), account({ id: "a2", label: "two" })]));
    const w = await open();
    expect(w.text()).toContain("Showing 1–2 of 2");
  });
});

describe("the pool card", () => {
  it("the left half is the stats' (all accounts), even when the page shows fewer", async () => {
    vi.mocked(shell.getStats).mockResolvedValue({
      jobs: { queued: 0, in_progress: 0, done: 0, failed: 0, pre_job_failures_24h: 0 },
      made: { videos: 0, images: 0 },
      accounts: { usable: 7, available: 8, dead: 1, disabled: 0, in_use: 5, asleep: 3 },
      keys: 0,
      alerts: [],
    });
    list.mockResolvedValue(page([account({ id: "a1" })]));
    const w = await open();
    const card = w.find("[data-testid=pool-card]");
    const ledger = (label: string) =>
      [...card.element.querySelectorAll(".ledger__row")]
        .find((r) => r.querySelector("dt")!.textContent === label)!
        .querySelector("dd")!
        .textContent!.trim();
    expect(card.text()).toContain("Pool health · 1 account");
    expect(ledger("Accounts ready")).toBe("7 / 8");
    expect(ledger("Accounts dead")).toBe("1");
    expect(ledger("Accounts disabled")).toBe("0");
    expect(ledger("In use")).toBe("5");
    expect(ledger("Asleep")).toBe("3");
  });

  it("the right half comes from the pool endpoint of the provider filter and is titled with the provider", async () => {
    useScopeStore(pinia).provider = "dola";
    const w = await open();
    expect(shell.getPoolStats).toHaveBeenCalledWith("dola");
    const card = w.find("[data-testid=pool-card]");
    expect(card.text()).toContain("dola pool");
    expect(card.text()).toContain("143 / 210");
    expect(card.text()).toContain("2 accounts");
    expect(card.text()).toContain("20:48:10");
  });

  it("a failed pool read does not hide the list; it shows an alert and keeps the old figures", async () => {
    const w = await open();
    expect(w.find("[data-testid=pool-card]").text()).toContain("143 / 210");
    expect(w.find("[data-testid=pool-error]").exists()).toBe(false);
    vi.mocked(shell.getPoolStats).mockRejectedValue(
      new ApiError(502, { error: { code: "x", message: "pool is down" } }),
    );
    useScopeStore(pinia).provider = "dola";
    await settle();
    expect(rows(w)).toHaveLength(1);
    expect(w.find("[data-testid=pool-error]").text()).toContain(
      "Could not load the pool figures: the relay answered 502 (pool is down).",
    );
    expect(w.find("[data-testid=pool-card]").text()).toContain("143 / 210");
    vi.mocked(shell.getPoolStats).mockResolvedValue({ pools: [], proxy_failed: 0, last_full_check_ms: null });
    useScopeStore(pinia).provider = "vibes";
    await settle();
    expect(w.find("[data-testid=pool-error]").exists()).toBe(false);
  });
});

describe("the header", () => {
  it("shows the Updated line with the auto period, and Check all, Refresh catalog and Import on the desktop", async () => {
    const w = await open();
    expect(w.find("h1").text()).toBe("Accounts");
    expect(w.text()).toMatch(/Updated \d\d:\d\d:\d\d · auto every 10 s/);
    expect(button(w, "Check all")).toBeTruthy();
    expect(button(w, "Refresh catalog")).toBeTruthy();
    expect(button(w, "Import")).toBeTruthy();
  });

  it("a tablet folds Check all and Refresh catalog into a More menu and moves Updated under the subtitle", async () => {
    stubWidth("tablet");
    const w = await open();
    expect(w.findAll("button").some((b) => b.text() === "Check all")).toBe(false);
    await button(w, "More actions: Check all, Refresh catalog").trigger("click");
    expect(menuItem("Check all")).toBeTruthy();
    expect(menuItem("Refresh catalog")).toBeTruthy();
    expect(w.find(".text .updated").exists()).toBe(true);
  });
});

describe("the layouts", () => {
  it("a tablet draws five columns and no Videos made column", async () => {
    stubWidth("tablet");
    list.mockResolvedValue(page([account({ made: { video: 31, image: 12 } })]));
    const w = await open();
    expect(w.findAll("thead th").map((th) => th.text())).toEqual(["Account", "Status", "Points", "Asleep", "Actions"]);
    expect(rows(w)[0]!.text()).toContain("31 videos · 12 images");
    expect(rows(w)[0]!.text()).toContain("direct, no proxy");
  });

  it("a phone draws a list: no table, no Check button, Check first in the row menu, one footer line", async () => {
    stubWidth("phone");
    const w = await open();
    expect(w.find("table").exists()).toBe(false);
    expect(w.findAll("li")).toHaveLength(1);
    expect(w.findAll("button").some((b) => b.text() === "Check")).toBe(false);
    await w.find("[aria-label^='More actions for']").trigger("click");
    const names = [...document.body.querySelectorAll("[role=menuitem]")].map((i) => i.textContent?.trim());
    expect(names).toEqual(["Check", "Shot", "Wake", "Edit", "Delete"]);
    expect(w.find("[aria-label='Account list']").text()).toContain("Showing 1–1 of 1");
  });
});

describe("the enable switch", () => {
  it("sends enabled and reads the list again", async () => {
    vi.mocked(accounts.patchAccount).mockResolvedValue({ id: "a1", command_id: null });
    const w = await open();
    await rows(w)[0]!.find("[role=switch]").trigger("click");
    await settle();
    expect(accounts.patchAccount).toHaveBeenCalledWith("a1", { enabled: false });
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("reads the list again when the change fails, so the switch goes back, and says why", async () => {
    vi.mocked(accounts.patchAccount).mockRejectedValue(
      new ApiError(409, { error: { code: "pool.account_busy", message: "account is busy" } }),
    );
    const w = await open();
    await rows(w)[0]!.find("[role=switch]").trigger("click");
    await settle();
    expect(list).toHaveBeenCalledTimes(2);
    expect(notify.error).toHaveBeenCalledWith("account is busy");
  });
});

describe("the row commands and the edit dialog", () => {
  const done = (id: string) => ({ id, status: "done", result: null }) as unknown as commands.CommandStatus;
  const inBody = (text: string) =>
    [...document.body.querySelectorAll("button")].find((b) => b.textContent?.trim() === text)!;
  const editOpen = (w: VueWrapper) => w.findComponent(EditDialog).props("modelValue");
  const type = (label: string, value: string) => {
    const el = document.body.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
    el.value = value;
    el.dispatchEvent(new Event("input"));
  };

  it("Wake queues the wake command under the label that names the cooldown and the strikes", async () => {
    vi.mocked(accounts.wakeAccount).mockResolvedValue({ command_id: "w1" });
    vi.mocked(commands.getCommand).mockResolvedValue(done("w1"));
    const w = await open();
    await pick(w, "Wake");
    expect(accounts.wakeAccount).toHaveBeenCalledExactlyOnceWith("a1");
    expect(notify.info).toHaveBeenCalledWith("Wake: clear cooldown and strikes: queued");
    expect(notify.success).toHaveBeenCalledWith("Wake: clear cooldown and strikes done");
  });

  it("the row has a switch, a Check button and one More menu; the menu holds Shot (only with a screenshot), Wake, Edit, Delete", async () => {
    const w = await open();
    const names = w.findAll("tbody button").map((b) => b.attributes("aria-label"));
    expect(names).toEqual(["Enable main", "Check main", "More actions for main"]);
    await w.find("[aria-label='More actions for main']").trigger("click");
    const items = [...document.body.querySelectorAll("[role=menuitem]")];
    expect(items.map((i) => i.textContent?.trim())).toEqual(["Shot", "Wake", "Edit", "Delete"]);
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
    await pick(w, "Shot");
    expect(accounts.accountScreenshot).not.toHaveBeenCalled();
  });

  it("Check queues a check, locks that button while it runs, and says what it found", async () => {
    vi.mocked(accounts.checkAccount).mockResolvedValue({ command_id: "c1" });
    const gate = deferred<commands.CommandStatus>();
    vi.mocked(commands.getCommand).mockReturnValue(gate.promise);
    const w = await open();
    await w.find("[aria-label='Check main']").trigger("click");
    await settle();
    expect(accounts.checkAccount).toHaveBeenCalledExactlyOnceWith("a1");
    expect(w.find("[aria-label='Check main']").attributes("aria-disabled")).toBe("true");
    await w.find("[aria-label='Check main']").trigger("click");
    expect(accounts.checkAccount).toHaveBeenCalledTimes(1);
    gate.resolve(done("c1"));
    await settle();
    expect(w.find("[aria-label='Check main']").attributes("aria-disabled")).toBeUndefined();
    expect(notify.info).not.toHaveBeenCalledWith("Check: queued");
  });

  it("Delete asks first and queues the delete only after a yes", async () => {
    const ask = vi.mocked(confirmDialog).mockReset().mockResolvedValue(false);
    vi.mocked(accounts.deleteAccount).mockResolvedValue({ command_id: "d1" });
    vi.mocked(commands.getCommand).mockResolvedValue(done("d1"));
    const w = await open();
    await pick(w, "Delete");
    expect(ask).toHaveBeenCalledTimes(1);
    // as B-States draws a delete confirm: the title asks and names the account, one sentence says the consequence
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
    expect(document.body.querySelector(".acct-dialog [data-state=running]")).not.toBeNull();
    // the check polls for a long time: Save stays usable meanwhile
    expect(inBody("Save").getAttribute("aria-disabled")).toBeNull();
    expect(accounts.recheckProxy).toHaveBeenCalledExactlyOnceWith("a1");
    expect(notify.info).not.toHaveBeenCalledWith("Proxy check: queued");
    gate.resolve(done("r1"));
    await settle();
    expect(commands.getCommand).toHaveBeenCalledWith("r1");
    expect(notify.success).toHaveBeenCalledWith("Proxy check done");
    expect(document.body.querySelector(".acct-dialog [data-state=ok]")).not.toBeNull();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("a Check's chip runs under its button, says how it ended, stays through the next automatic read and goes with the one after", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      vi.mocked(accounts.checkAccount).mockResolvedValue({ command_id: "c1" });
      const gate = deferred<commands.CommandStatus>();
      vi.mocked(commands.getCommand).mockReturnValue(gate.promise);
      const w = await open();
      const chip = () => w.find("tbody td.last [data-state]");
      await w.find("[aria-label='Check main']").trigger("click");
      await settle();
      expect(chip().attributes("data-state")).toBe("running");
      const finished = Date.UTC(2026, 9, 15, 13, 55, 7);
      gate.resolve({ ...done("c1"), finished_ms: finished });
      await settle();
      expect(chip().text()).toBe(`ok · ${hhmmss(finished)}`);
      // the next automatic read keeps it (it is seen at least one period); the one after replaces the row
      await vi.advanceTimersByTimeAsync(10000);
      await settle();
      expect(chip().text()).toBe(`ok · ${hhmmss(finished)}`);
      await vi.advanceTimersByTimeAsync(10000);
      await settle();
      expect(chip().exists()).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("two quick Checks keep both results: the reads the commands make do not clear a chip", async () => {
    const labels = ["main", "two", "three"];
    list.mockResolvedValue(page(labels.map((label, n) => account({ id: `a${n}`, label }))));
    vi.mocked(accounts.checkAccount).mockImplementation(async (id) => ({ command_id: `c-${id}` }));
    vi.mocked(commands.getCommand).mockImplementation(async (id) => done(id));
    const w = await open();
    for (const label of labels) {
      await w.find(`[aria-label='Check ${label}']`).trigger("click");
      await settle();
    }
    expect(w.findAll("tbody td.last [data-state=ok]")).toHaveLength(3);
  });

  it("a failed Check's chip and toast carry the worker's reason", async () => {
    vi.mocked(accounts.checkAccount).mockResolvedValue({ command_id: "c1" });
    vi.mocked(commands.getCommand).mockResolvedValue({
      ...done("c1"),
      status: "failed",
      result: { error: "connect timeout after 8 s" },
    });
    const w = await open();
    await w.find("[aria-label='Check main']").trigger("click");
    await settle();
    expect(w.find("tbody td.last [data-state=error]").text()).toBe("error · connect timeout after 8 s");
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

  it("a second click while an action runs sends nothing: Wake, Delete, the switch, the phone's Check", async () => {
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
    await rows(w)[0]!.find("[role=switch]").trigger("click");
    await rows(w)[0]!.find("[role=switch]").trigger("click");
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
    // and Check all is drawn disabled meanwhile, so a second click queues nothing
    expect(button(w, "Check all").attributes("aria-disabled")).toBe("true");
    await button(w, "Check all").trigger("click");
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
    await button(w, "Check all").trigger("click");
    await settle();
    const chip = () => w.find(".cmd [data-state]");
    expect(chip().attributes("data-state")).toBe("running");
    gate.resolve({ ...done("c-dola"), status: "failed", result: { error: "worker gone" } });
    await vi.waitFor(() => expect(chip().attributes("data-state")).toBe("error"));
    expect(chip().text()).toBe("error · worker gone");
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
    await button(w, "Import").trigger("click");
    await settle();
    await vi.advanceTimersByTimeAsync(10000);
    expect(list).toHaveBeenCalledTimes(1);
    document.body.querySelector<HTMLElement>("[aria-label='Close']")!.click();
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
