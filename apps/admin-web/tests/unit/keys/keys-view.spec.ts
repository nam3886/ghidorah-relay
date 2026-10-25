import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import * as keys from "@/api/keys";
import type { ApiKey } from "@/api/keys";
import * as shell from "@/api/shell";
import * as confirm from "@/components/shared/confirm-dialog";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";
import KeysView from "@/views/KeysView.vue";

vi.mock("@/api/keys", () => ({
  listKeys: vi.fn(),
  createKey: vi.fn(),
  patchKey: vi.fn(),
  setKeyEnabled: vi.fn(),
  deleteKey: vi.fn(),
}));
vi.mock("@/api/shell", () => ({ getModels: vi.fn(), logout: vi.fn() }));
vi.mock("@/components/shared/confirm-dialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const RAW = "rk_live_THE_ONE_TIME_SECRET_0123456789";
const PLANTED = "<img src=x onerror=window.__pwned=1><b>planted</b>";

const key = (over: Partial<ApiKey> = {}): ApiKey => ({
  id: "k1",
  prefix: "rk_abcd",
  label: "shop",
  enabled: true,
  models: ["*"],
  max_unfinished: null,
  max_unfinished_effective: 3,
  created_ms: 1,
  last_used_ms: null,
  last_used_ip: null,
  jobs: 0,
  unfinished: 0,
  wallets: [],
  ...over,
});
const wallet = (over: Partial<keys.Wallet> = {}): keys.Wallet => ({
  scope: "dola",
  day_limit: 20,
  total_limit: 200,
  used_day: 3,
  used_total: 14,
  mode: "limit",
  ...over,
});

const list = vi.mocked(keys.listKeys);
let wrapper: VueWrapper | null = null;
let pinia = createPinia();

async function settle() {
  await flushPromises();
  await flushPromises();
}
// `leaves`: the drawer's own leave transition runs (it is stubbed to nothing by default, and `closed` follows it)
async function open(leaves = false) {
  wrapper = mount(KeysView, {
    global: { plugins: [pinia], stubs: leaves ? { transition: false } : {} },
    attachTo: document.body,
  });
  await settle();
  return wrapper;
}
const buttonIn = (root: ParentNode, text: string) =>
  [...root.querySelectorAll("button")].find((b) => b.textContent?.trim() === text)!;
const buttonOf = (text: string) => buttonIn(document.body, text);
const drawerEl = () => document.body.querySelector(".el-drawer")!;
// the form's own buttons (the page's primary is also called Create key on a desktop)
const formButton = (text: string) => buttonIn(drawerEl(), text);
const pagePrimary = () => document.body.querySelector<HTMLButtonElement>(".kv-primary")!;
const named = (label: string) => document.body.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;

// The row menu of a key: opens it and picks an item.
async function menuPick(name: string, item: string) {
  named(`More actions for ${name}`).click();
  await settle();
  [...document.body.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
    .find((b) => b.textContent?.trim() === item)!
    .click();
  await settle();
}

// Opens the New key form and types a label.
async function openNewKey(label = "Partner E") {
  pagePrimary().click();
  await settle();
  await typeLabel(label);
}
async function typeLabel(value: string) {
  const field = document.body.querySelector<HTMLInputElement>("#key-label")!;
  field.value = value;
  field.dispatchEvent(new Event("input"));
  await settle();
}

// Creates a key through the form and leaves the one-time modal on screen.
async function createOne(leaves = false) {
  const w = await open(leaves);
  await openNewKey();
  formButton("Create key").click();
  await settle();
  return w;
}
// The page as markup (attributes included), to prove the key is in no attribute or text node.
const markupOf = (el: Element | null): string => (el ? new XMLSerializer().serializeToString(el) : "");
const pageMarkup = (): string => markupOf(document.body);
const secretBox = () => document.body.querySelector('[data-testid="new-key"]');
const callout = () => document.body.querySelector<HTMLTextAreaElement>('[data-testid="new-key"] textarea');

// Everything a page can hold the key in besides the modal's own field.
function whereTheKeyIs(): string[] {
  const found: string[] = [];
  if (document.body.textContent?.includes(RAW)) found.push("dom text");
  for (const el of document.body.querySelectorAll("input, textarea")) {
    if ((el as HTMLInputElement).value.includes(RAW)) found.push("field value");
  }
  for (const store of [sessionStorage, localStorage]) {
    for (let i = 0; i < store.length; i++) {
      const k = store.key(i)!;
      if (k.includes(RAW) || store.getItem(k)!.includes(RAW)) found.push("storage");
    }
  }
  if (JSON.stringify(pinia.state.value).includes(RAW)) found.push("pinia");
  if (location.href.includes(RAW)) found.push("url");
  return found;
}

// happy-dom answers matchMedia for a 1024 px window (a tablet): each spec says which width it wants (desktop by default).
function showAt(width: number): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width: 767px")
      ? width <= 767
      : query.includes("min-width: 768px")
        ? width >= 768 && width <= 1279
        : false,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

beforeEach(() => {
  showAt(1440);
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
  sessionStorage.clear();
  localStorage.clear();
  for (const fn of [...Object.values(keys), ...Object.values(shell), ...Object.values(notify), confirm.confirmDialog]) {
    (fn as ReturnType<typeof vi.fn>).mockReset();
  }
  list.mockResolvedValue({ items: [key()] });
  vi.mocked(shell.getModels).mockResolvedValue({
    data: [
      { id: "m1", name: "Model One", owned_by: "vibes", kind: "video" },
      { id: "m2", owned_by: "dola", kind: "image" },
    ],
  });
  vi.mocked(keys.createKey).mockResolvedValue({ ...key({ id: "k2" }), key: RAW });
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
});

describe("the list", () => {
  it("draws the balance, the caps, the jobs and server strings as text", async () => {
    list.mockResolvedValue({
      items: [
        key({
          label: PLANTED,
          models: ["m1", "m2"],
          max_unfinished_effective: 0,
          unfinished: 2,
          jobs: 1912,
          wallets: [
            wallet({ scope: "*", day_limit: 1000, total_limit: 5000, used_day: 860, used_total: 1095 }),
            wallet({ scope: "dola", day_limit: 300, used_day: 288 }),
            wallet({ scope: "vibes", mode: "unlimited", day_limit: null, total_limit: null }),
          ],
        }),
      ],
    });
    const w = await open();
    const row = w.find("tbody tr");
    expect(row.text()).toContain(PLANTED);
    expect(row.find(".wbtn").text()).toContain("3,905 pts");
    expect(row.find(".wbtn").text()).toContain("−860 of 1,000 today");
    expect(row.findAll('[data-testid="cap-chip"]').map((c) => c.text().replace(/\s+/g, " ").trim())).toEqual([
      "dola 288 / 300",
      "vibes",
    ]);
    expect(row.find(".t-last").attributes("title")).toBe("1,912 jobs · 2 / unlimited unfinished");
    expect(row.find(".t-last").text()).toBe("never");
    expect(w.find("img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("Last used is the key's last call with its address under it, and never for a key never used", async () => {
    const now = Date.now();
    list.mockResolvedValue({
      items: [
        key({ id: "a", label: "used", last_used_ms: now - 2 * 60_000 - 5_000, last_used_ip: "203.0.113.8" }),
        key({ id: "b", label: "idle" }),
      ],
    });
    const w = await open();
    const [used, idle] = w.findAll("tbody tr");
    expect(used!.find(".t-last__n").text()).toBe("2 min ago");
    expect(used!.find(".t-last .meta").text()).toBe("203.0.113.8");
    expect(idle!.find(".t-last__n").text()).toBe("never");
    expect(idle!.find(".t-last .meta").exists()).toBe(false);
  });

  it("a paused key carries the one pill of the row, and an empty wallet is red", async () => {
    list.mockResolvedValue({
      items: [
        key({ id: "k1", wallets: [wallet({ scope: "*", total_limit: 100, used_total: 100 })] }),
        key({ id: "k2", label: "two", enabled: false }),
      ],
    });
    const w = await open();
    const [on, off] = w.findAll("tbody tr");
    expect(on!.find(".pill").exists()).toBe(false);
    expect(on!.find(".wbtn__bal").classes()).toContain("tone-bad");
    expect(off!.find(".pill").text()).toBe("Paused");
    expect(off!.classes()).toContain("is-off");
  });

  it("a switch that fails reads the list again, so it goes back", async () => {
    vi.mocked(keys.setKeyEnabled).mockRejectedValue(new ApiError(500, { error: { message: "boom" } }));
    await open();
    await menuPick("shop", "Disable key");
    expect(keys.setKeyEnabled).toHaveBeenCalledWith("k1", false);
    expect(list).toHaveBeenCalledTimes(2);
    expect(notify.error).toHaveBeenCalledWith("boom");
  });

  it("delete says whether the key went or was only disabled", async () => {
    vi.mocked(confirm.confirmDialog).mockResolvedValue(true);
    vi.mocked(keys.deleteKey).mockResolvedValueOnce({ id: "k1", result: "deleted" });
    list.mockResolvedValue({ items: [key({ jobs: 2104 })] });
    await open();
    await menuPick("shop", "Delete key");
    expect(confirm.confirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Delete key shop?",
        message: "A key that has jobs is only disabled. This one has 2,104 jobs, so it will be disabled, not removed.",
      }),
    );
    expect(notify.info).toHaveBeenLastCalledWith("Key deleted");
    vi.mocked(keys.deleteKey).mockResolvedValueOnce({ id: "k1", result: "disabled" });
    await menuPick("shop", "Delete key");
    expect(notify.info).toHaveBeenLastCalledWith("Key has jobs: disabled instead");
  });

  it("a cancelled delete sends nothing", async () => {
    vi.mocked(confirm.confirmDialog).mockResolvedValue(false);
    await open();
    await menuPick("shop", "Delete key");
    expect(keys.deleteKey).not.toHaveBeenCalled();
  });

  it("the row menu says what the switch does, as the server holds it", async () => {
    list.mockResolvedValue({ items: [key({ enabled: false }), key({ id: "k2", label: "two", enabled: true })] });
    await open();
    named("More actions for shop").click();
    await settle();
    const items = () => [...document.body.querySelectorAll('[role="menuitem"]')].map((b) => b.textContent?.trim());
    expect(items()).toEqual(["Edit key", "Enable key", "Delete key"]);
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await settle();
    named("More actions for two").click();
    await settle();
    expect(items()).toEqual(["Edit key", "Disable key", "Delete key"]);
  });
});

describe("the Show chips and the search", () => {
  beforeEach(() => {
    list.mockResolvedValue({
      items: [
        key({ id: "a", label: "Studio", wallets: [wallet({ scope: "*", total_limit: 1000, used_total: 10 })] }),
        key({ id: "b", label: "Hanoi", wallets: [wallet({ scope: "*", total_limit: 1000, used_total: 950 })] }),
        key({ id: "c", label: "Old", enabled: false, wallets: [wallet({ scope: "*", total_limit: 1000 })] }),
      ],
    });
  });
  const names = (w: VueWrapper) => w.findAll("tbody tr .k-label").map((c) => c.text());
  const chip = (text: string) =>
    [...document.body.querySelectorAll<HTMLButtonElement>(".kchip")].find((b) => b.textContent?.startsWith(text))!;

  it("each chip counts its keys and shows only them", async () => {
    const w = await open();
    expect(
      [...document.body.querySelectorAll(".kchip")].map((b) => b.textContent?.replace(/\s+/g, " ").trim()),
    ).toEqual(["All 3", "Active 2", "Low or empty 1", "Paused 1"]);
    chip("Low or empty").click();
    await settle();
    expect(names(w)).toEqual(["Hanoi"]);
    expect(chip("Low or empty").getAttribute("aria-pressed")).toBe("true");
    chip("Paused").click();
    await settle();
    expect(names(w)).toEqual(["Old"]);
  });

  it("the search finds a label or a prefix, and an empty result offers Clear filters", async () => {
    const w = await open();
    const q = document.body.querySelector<HTMLInputElement>("#keys-q")!;
    q.value = "hano";
    q.dispatchEvent(new Event("input"));
    await settle();
    expect(names(w)).toEqual(["Hanoi"]);
    q.value = "nothing at all";
    q.dispatchEvent(new Event("input"));
    await settle();
    expect(w.text()).toContain("No key matches these filters");
    buttonOf("Clear filters").click();
    await settle();
    expect(names(w)).toEqual(["Studio", "Hanoi", "Old"]);
  });

  it("the sentence under the title counts the keys, today's use and the empty wallets", async () => {
    const w = await open();
    expect(w.find('[data-testid="keys-summary"]').text()).toBe("3 keys, 3 used today. 9 points spent today.");
  });
});

describe("Adjust wallet", () => {
  beforeEach(() => {
    list.mockResolvedValue({
      items: [key({ wallets: [wallet({ scope: "*", day_limit: 500, total_limit: 5000, used_total: 1095 })] })],
    });
    vi.mocked(keys.patchKey).mockResolvedValue(key());
  });
  const amount = async (value: string) => {
    const input = document.body.querySelector<HTMLInputElement>('[data-testid="wallet-pop"] input')!;
    input.value = value;
    input.dispatchEvent(new Event("input"));
    await settle();
  };

  it("the wallet figure opens it from its row; a top-up sends the points wallet with a higher total", async () => {
    await open();
    named("Adjust wallet of shop, 3,905 points").click();
    await settle();
    const pop = document.body.querySelector('[data-testid="wallet-pop"]')!;
    expect(pop.getAttribute("role")).toBe("dialog");
    expect(named("Adjust wallet of shop, 3,905 points").getAttribute("aria-expanded")).toBe("true");
    await amount("5,000");
    expect(pop.textContent).toContain("8,905 pts");
    buttonIn(pop, "Top up 5,000").click();
    await settle();
    expect(keys.patchKey).toHaveBeenCalledExactlyOnceWith("k1", {
      wallets: [{ scope: "*", mode: "limit", day_limit: 500, total_limit: 10000 }],
    });
    expect(list).toHaveBeenCalledTimes(2);
    expect(document.body.querySelector('[data-testid="wallet-pop"]')).toBeNull();
  });

  it("a deduction above the balance is refused before it is sent", async () => {
    await open();
    named("Adjust wallet of shop, 3,905 points").click();
    await settle();
    const pop = document.body.querySelector('[data-testid="wallet-pop"]')!;
    buttonIn(pop, "Deduct").click();
    await amount("4000");
    expect(pop.textContent).toContain("At most 3,905 points can be deducted.");
    expect(buttonIn(pop, "Deduct 4,000").disabled).toBe(true);
    expect(keys.patchKey).not.toHaveBeenCalled();
  });

  it("Escape closes it", async () => {
    await open();
    named("Adjust wallet of shop, 3,905 points").click();
    await settle();
    document.body
      .querySelector('[data-testid="wallet-pop"]')!
      .dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await settle();
    expect(document.body.querySelector('[data-testid="wallet-pop"]')).toBeNull();
  });
});

describe("the number fields of the key form", () => {
  const max = () => document.body.querySelector<HTMLInputElement>("#key-max")!;
  const type = async (input: HTMLInputElement, value: string) => {
    input.value = value;
    input.dispatchEvent(new Event("input"));
    await settle();
  };

  it("max unfinished: an empty box sends null, a 0 sends 0 (unlimited), a number sends the number", async () => {
    await open();
    await openNewKey();
    expect(max().value).toBe("");
    await type(max(), "0");
    formButton("Create key").click();
    await settle();
    expect(keys.createKey).toHaveBeenLastCalledWith(expect.objectContaining({ max_unfinished: 0 }));
  });

  it("clearing the field goes back to the default (null)", async () => {
    await open();
    await openNewKey();
    await type(max(), "5");
    await type(max(), "");
    expect(max().value).toBe("");
    formButton("Create key").click();
    await settle();
    expect(keys.createKey).toHaveBeenLastCalledWith(expect.objectContaining({ max_unfinished: null }));
  });
});

describe("the one-time key", () => {
  it("is shown after create in a read-only field and sent nowhere else", async () => {
    await createOne();
    expect(keys.createKey).toHaveBeenCalledWith({
      label: "Partner E",
      models: ["*"],
      max_unfinished: null,
      wallets: expect.any(Array),
    });
    expect(callout()!.value).toBe(RAW);
    expect(callout()!.readOnly).toBe(true);
    // the field is the only place: not the page html, not storage, not Pinia, not the address
    expect(whereTheKeyIs()).toEqual(["field value"]);
  });

  it("Done is locked until the key is copied, then removes it from every place", async () => {
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn(async () => {}) }, configurable: true });
    await createOne();
    expect(buttonIn(secretBox()!, "Done").disabled).toBe(true);
    buttonIn(secretBox()!, "Copy").click();
    await settle();
    expect(buttonIn(secretBox()!, "Done").disabled).toBe(false);
    buttonIn(secretBox()!, "Done").click();
    await settle();
    expect(callout()).toBeNull();
    expect(whereTheKeyIs()).toEqual([]);
    expect(pageMarkup()).not.toContain(RAW);
  });

  it("a copy made from the field itself unlocks Done too", async () => {
    await createOne();
    callout()!.dispatchEvent(new Event("copy"));
    await settle();
    expect(buttonIn(secretBox()!, "Done").disabled).toBe(false);
  });

  it("is never in the form drawer, which is closed by the time the key shows", async () => {
    await createOne();
    expect(markupOf(document.body.querySelector(".el-drawer"))).not.toContain(RAW);
    expect(pageMarkup()).not.toContain(RAW);
    // the serialiser sees the page: the modal is in it, with the key only as a property of its field
    expect(pageMarkup()).toContain("Key created for");
  });

  it("is gone when the view is left", async () => {
    const w = await createOne();
    w.unmount();
    wrapper = null;
    expect(whereTheKeyIs()).toEqual([]);
  });

  it("is gone on sign-out", async () => {
    await createOne();
    useAuthStore(pinia).signOut();
    await settle();
    expect(callout()).toBeNull();
    expect(whereTheKeyIs()).toEqual([]);
  });

  it("a create that answers after sign-out does not bring it back", async () => {
    let answer!: (v: Awaited<ReturnType<typeof keys.createKey>>) => void;
    vi.mocked(keys.createKey).mockReturnValue(new Promise((r) => (answer = r)));
    await open();
    await openNewKey();
    formButton("Create key").click();
    await settle();
    useAuthStore(pinia).signOut();
    answer({ ...key({ id: "k2" }), key: RAW });
    await settle();
    expect(callout()).toBeNull();
    expect(whereTheKeyIs()).toEqual([]);
  });

  it("leaving the view clears the key it held in memory", async () => {
    const w = await createOne();
    const vm = w.vm as unknown as { rawKey: string | null };
    expect(vm.rawKey).toBe(RAW);
    w.unmount();
    wrapper = null;
    expect(vm.rawKey).toBeNull();
  });

  it("a failed create shows fixed text and the field, never the server's message or param", async () => {
    vi.mocked(keys.createKey).mockRejectedValue(
      new ApiError(422, { error: { code: "input.invalid", message: `bad ${RAW}`, param: `label.${RAW}` } }),
    );
    await open();
    await openNewKey();
    formButton("Create key").click();
    await settle();
    expect(drawerEl().textContent).toContain("Some of the values are not accepted. Check the highlighted field.");
    expect(drawerEl().querySelector("#key-label.is-error")).not.toBeNull();
    expect(whereTheKeyIs()).toEqual([]);
    for (const fn of Object.values(notify)) expect(fn).not.toHaveBeenCalledWith(expect.stringContaining(RAW));
    expect(notify.error).not.toHaveBeenCalled();
    expect(callout()).toBeNull();
  });

  it("a failed create with an unknown code or a network error shows the fixed fallback text", async () => {
    vi.mocked(keys.createKey).mockRejectedValueOnce(new ApiError(500, { error: { code: "x.y", message: RAW } }));
    await open();
    await openNewKey();
    formButton("Create key").click();
    await settle();
    expect(document.body.querySelector(".kf__error")!.textContent!.trim()).toBe(
      "The key could not be created. Try again.",
    );
    vi.mocked(keys.createKey).mockRejectedValueOnce(new TypeError(RAW));
    formButton("Create key").click();
    await settle();
    expect(document.body.querySelector(".kf__error")!.textContent!.trim()).toBe("Network error, please try again.");
    expect(whereTheKeyIs()).toEqual([]);
  });

  it("an edit shows no key", async () => {
    vi.mocked(keys.patchKey).mockResolvedValue(key());
    await open();
    await menuPick("shop", "Edit key");
    formButton("Save").click();
    await settle();
    expect(keys.patchKey).toHaveBeenCalledTimes(1);
    expect(callout()).toBeNull();
  });
});

describe("Copy", () => {
  const writeText = vi.fn(async (_text: string) => undefined);
  beforeEach(() => {
    writeText.mockClear();
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  });

  it.each([1440, 768, 390])("no row has a Copy-prefix button at %i px", async (width) => {
    showAt(width);
    list.mockResolvedValue({ items: [key(), key({ id: "k2", label: "", prefix: "rk_none" })] });
    const w = await open();
    const labels = [...document.body.querySelectorAll("button")].map((b) => b.getAttribute("aria-label") ?? "");
    expect(labels.some((n) => n.startsWith("Copy key"))).toBe(false);
    // the prefix itself is still text
    expect(document.body.textContent).toContain("rk_abcd");
    w.unmount();
    wrapper = null;
  });

  it("the one-time modal has a Copy that copies the raw key", async () => {
    await createOne();
    buttonIn(secretBox()!, "Copy").click();
    await settle();
    expect(writeText).toHaveBeenCalledExactlyOnceWith(RAW);
  });

  it("the Copy of the modal says Copied afterwards", async () => {
    await createOne();
    buttonIn(secretBox()!, "Copy").click();
    await settle();
    expect([...secretBox()!.querySelectorAll("button")].map((b) => b.textContent?.trim())).toEqual(["Copied", "Done"]);
  });
});

describe("no provider filter", () => {
  beforeEach(() => {
    vi.mocked(shell.getModels).mockResolvedValue({
      data: [
        { id: "m1", owned_by: "vibes", kind: "video" },
        { id: "m2", owned_by: "dola", kind: "image" },
        { id: "m3", owned_by: "dola", kind: "video", disabled: true },
      ],
    });
    list.mockResolvedValue({
      items: [
        key({ id: "k1", label: "any", models: ["*"], wallets: [wallet(), wallet({ scope: "vibes" })] }),
        key({
          id: "k2",
          label: "dola only",
          models: ["m2", "m3"],
          wallets: [wallet({ scope: "*" }), wallet(), wallet({ scope: "vibes" })],
        }),
        key({ id: "k3", label: "vibes only", models: ["m1"] }),
      ],
    });
  });

  it("a provider picked on another tab narrows nothing: every key and every cap chip stays (Keys has no strip)", async () => {
    const w = await open();
    useScopeStore().provider = "dola";
    await settle();
    const rows = w.findAll("tbody tr");
    expect(rows.map((r) => r.find(".k-label").text())).toEqual(["any", "dola only", "vibes only"]);
    expect(rows[0]!.findAll('[data-testid="cap-chip"]')).toHaveLength(2);
  });
});

describe("the page as the design draws it", () => {
  it("has the title, the sentence, Create key, the chip note, and no Refresh, no Updated line, no pager", async () => {
    const w = await open();
    expect(w.find("h1").text()).toBe("API keys");
    expect(w.find('[data-testid="keys-summary"]').text()).toBe(
      "1 key, 0 used today. 0 points spent today; 1 wallet is empty.",
    );
    expect(pagePrimary().textContent?.trim()).toBe("Create key");
    expect(w.text()).not.toContain("Updated");
    expect(w.text()).not.toContain("Refresh");
    expect(w.find(".el-pagination").exists()).toBe(false);
    expect(w.text()).toContain(
      "A chip = the key may use that provider · figure = today's points against its daily cap · amber = 80%+, red = full.",
    );
  });

  it("the modal names the new key, and its row is the one tinted", async () => {
    list.mockResolvedValueOnce({ items: [key()] });
    vi.mocked(keys.createKey).mockResolvedValue({ ...key({ id: "k2", label: "Partner D" }), key: RAW });
    list.mockResolvedValueOnce({ items: [key(), key({ id: "k2", label: "Partner D" })] });
    const w = await createOne();
    expect(secretBox()!.textContent).toContain("Key created for Partner D");
    expect(secretBox()!.textContent).toContain("Copy it now. It is shown once and not stored");
    const rows = w.findAll("tbody tr");
    expect(rows.map((r) => r.classes().includes("is-new"))).toEqual([false, true]);
    // the field is named by its visible label
    expect(document.body.querySelector('label[for="new-key-field"]')!.textContent).toBe("API key");
  });

  it("an empty list says so and offers New key, with no chip note", async () => {
    list.mockResolvedValue({ items: [] });
    const w = await open();
    expect(w.text()).toContain("No API keys yet");
    expect(buttonOf("New key")).toBeDefined();
    expect(w.text()).not.toContain("A chip = the key may use that provider");
  });

  it("a list that fails to load says why in the canvas's words", async () => {
    list.mockRejectedValue(new ApiError(502, { error: { code: "upstream.bad_gateway", message: "bad gateway" } }));
    const w = await open();
    expect(w.find('.kv-card [role="alert"]').text()).toContain("Could not load the API keys: the relay answered 502");
    expect(buttonOf("Retry")).toBeDefined();
  });
});

describe("the key form as the design draws it", () => {
  const editShop = async (over: Partial<ApiKey> = {}) => {
    list.mockResolvedValue({
      items: [
        key({
          models: ["m1"],
          jobs: 1912,
          wallets: [
            wallet({ scope: "*", total_limit: 5000, used_total: 1095, used_day: 860, day_limit: null }),
            wallet({ scope: "vibes" }),
            wallet({ scope: "dola", mode: "unlimited", day_limit: null, total_limit: null, used_day: 9 }),
          ],
          ...over,
        }),
      ],
    });
    const w = await open();
    await menuPick("shop", "Edit key");
    return w;
  };
  const field = (selector: string) => document.body.querySelector<HTMLInputElement>(selector)!;
  // a wallet's Mode: the field in the wallet's group, its list opened in the body
  const chooseMode = async (group: string, value: string) => {
    document.body
      .querySelector(`[role="group"][aria-label="${group}"]`)!
      .querySelector<HTMLButtonElement>('[role="combobox"]')!
      .click();
    await settle();
    [...document.body.querySelectorAll<HTMLElement>('[role="option"]')]
      .find((o) => o.textContent?.trim() === value)!
      .click();
    await settle();
  };

  it("an edit names the key in its title and tells the balance and what each cap has used", async () => {
    await editShop();
    expect(drawerEl().querySelector("h2")!.textContent).toBe("Edit key · shop");
    // the drawer is named by its visible heading
    const dialog = document.body.querySelector('[role="dialog"]')!;
    expect(dialog.hasAttribute("aria-label")).toBe(false);
    expect(document.getElementById(dialog.getAttribute("aria-labelledby")!)).toBe(drawerEl().querySelector("h2"));
    expect(drawerEl().textContent).toContain("rk_abcd•••••••• · created");
    expect(drawerEl().textContent).toContain("1,912 jobs");
    expect(drawerEl().textContent).toContain("balance 3,905 pts · −860 today");
    expect(drawerEl().textContent).toContain("total 14 / 200");
    expect(drawerEl().textContent).toContain("unlimited · 9 used today");
    expect(drawerEl().textContent).toContain("Caps apply in limit mode. Per day resets at 07:00 GMT+7.");
  });

  it("a new key has a plain title and says nothing of use", async () => {
    await open();
    pagePrimary().click();
    await settle();
    expect(drawerEl().querySelector("h2")!.textContent).toBe("New API key");
    expect(drawerEl().querySelector(".kf__whead")!.textContent).not.toContain("balance");
    expect(drawerEl().textContent).toContain("Starting points");
  });

  it("a wallet that leaves limit mode shows empty, disabled number boxes, and a limit wallet shows its numbers", async () => {
    await editShop();
    expect(field('input[aria-label="vibes cap per day"]').value).toBe("20");
    expect(field('input[aria-label="vibes cap total"]').value).toBe("200");
    expect(field('input[aria-label="vibes cap per day"]').disabled).toBe(false);
    await chooseMode("vibes cap", "closed");
    expect(field('input[aria-label="vibes cap per day"]').value).toBe("");
    expect(field('input[aria-label="vibes cap per day"]').disabled).toBe(true);
    expect(field('input[aria-label="vibes cap total"]').disabled).toBe(true);
    await chooseMode("vibes cap", "limit");
    expect(field('input[aria-label="vibes cap per day"]').value).toBe("20");
    expect(field('input[aria-label="points total"]').value).toBe("5000");
  });

  it("each wallet's Mode is named after its wallet and keeps the visible word", async () => {
    await editShop();
    const modes = [...document.body.querySelectorAll<HTMLElement>('.kf [role="combobox"]')];
    expect(modes.map((m) => m.getAttribute("aria-label"))).toEqual(["points Mode", "dola cap Mode", "vibes cap Mode"]);
    expect(modes.some((m) => m.hasAttribute("aria-labelledby"))).toBe(false);
    modes[2]!.click();
    await settle();
    expect(document.body.querySelector('[role="listbox"]')!.getAttribute("aria-label")).toBe("vibes cap Mode");
  });

  it("the model boxes are off while Any model is ticked and on once it is cleared", async () => {
    await editShop({ models: ["*"] });
    const boxes = () => [
      ...document.body.querySelectorAll<HTMLInputElement>('[role="group"][aria-label="Pick models"] input'),
    ];
    expect(boxes()).toHaveLength(2);
    expect(boxes().every((b) => b.disabled)).toBe(true);
    const group = () => document.body.querySelector('[role="group"][aria-label="Pick models"]')!;
    expect(group().getAttribute("aria-disabled")).toBe("true");
    document.body.querySelector<HTMLInputElement>(".kf__any input")!.click();
    await settle();
    expect(boxes().every((b) => !b.disabled)).toBe(true);
    expect(group().getAttribute("aria-disabled")).toBe("false");
  });

  it("the models are grouped by provider in the drawn order; a paused one can come off a key but not go on", async () => {
    vi.mocked(shell.getModels).mockResolvedValue({
      data: [
        { id: "m1", owned_by: "vibes", kind: "video" },
        { id: "m2", owned_by: "dola", kind: "image" },
        { id: "m3", owned_by: "dola", kind: "video", disabled: true },
        { id: "m4", owned_by: "vibes", kind: "image", disabled: true },
      ],
    });
    await editShop({ models: ["m1", "m3"] });
    const groups = [...document.body.querySelectorAll(".kf__group")].map((g) => [
      g.querySelector(".kf__ghead")!.textContent!.trim(),
      [...g.querySelectorAll(".kf__model")].map((m) =>
        [...m.querySelectorAll("span")].map((x) => x.textContent!.trim()).join(" "),
      ),
    ]);
    expect(groups).toEqual([
      ["dola", ["m2", "m3 paused"]],
      ["vibes", ["m1", "m4 paused"]],
    ]);
    const box = (id: string) => document.body.querySelector<HTMLInputElement>(`.kf__model input[value="${id}"]`)!;
    expect(box("m3").disabled).toBe(false);
    expect(box("m4").disabled).toBe(true);
  });

  it("the footer counts what changed", async () => {
    await editShop();
    expect(drawerEl().textContent).not.toContain("unsaved");
    await typeLabel("shop 2");
    expect(drawerEl().textContent).toContain("1 unsaved change");
    // clearing Any model changes the allowed models: a second change
    document.body.querySelector<HTMLInputElement>(".kf__any input")!.click();
    await settle();
    expect(drawerEl().textContent).toContain("2 unsaved changes");
  });
});

describe("the label of a key is optional", () => {
  const label = () => document.body.querySelector<HTMLInputElement>("#key-label")!;
  const create = () => formButton("Create key");

  it("Create key is on with an empty label, says nothing of a missing label and creates a key", async () => {
    await open();
    pagePrimary().click();
    await settle();
    expect(create().hasAttribute("aria-disabled")).toBe(false);
    expect(create().hasAttribute("aria-describedby")).toBe(false);
    label().dispatchEvent(new Event("blur"));
    await settle();
    expect(document.body.textContent).not.toContain("A label is required");
    expect(document.body.textContent).not.toContain("Fill in the label first");
    expect(label().getAttribute("aria-invalid")).toBeNull();
    create().click();
    await settle();
    expect(keys.createKey).toHaveBeenCalledWith(expect.objectContaining({ label: "" }));
  });

  it("a label of spaces is sent as no label, and a padded one without its edges", async () => {
    await open();
    await openNewKey("   ");
    create().click();
    await settle();
    expect(keys.createKey).toHaveBeenLastCalledWith(expect.objectContaining({ label: "" }));
    callout()!.dispatchEvent(new Event("copy"));
    await settle();
    buttonIn(secretBox()!, "Done").click();
    await settle();
    await openNewKey("  Partner E ");
    create().click();
    await settle();
    expect(keys.createKey).toHaveBeenLastCalledWith(expect.objectContaining({ label: "Partner E" }));
  });

  it("the modal of a key made with no label names it by its prefix, never 'Key created for '", async () => {
    vi.mocked(keys.createKey).mockResolvedValue({ ...key({ id: "k2", label: "", prefix: "rk_new1" }), key: RAW });
    await open();
    await openNewKey("");
    create().click();
    await settle();
    expect(secretBox()!.textContent).toContain("Key created for rk_new1");
    expect(secretBox()!.querySelector("h2")!.textContent).toBe("Key created for rk_new1");
    // a label of spaces in the answer reads the same
    callout()!.dispatchEvent(new Event("copy"));
    await settle();
    buttonIn(secretBox()!, "Done").click();
    await settle();
    vi.mocked(keys.createKey).mockResolvedValue({ ...key({ id: "k3", label: "   ", prefix: "rk_new2" }), key: RAW });
    await openNewKey("");
    create().click();
    await settle();
    expect(secretBox()!.querySelector("h2")!.textContent).toBe("Key created for rk_new2");
  });

  it("an edit can clear a label: the patch sends an empty one", async () => {
    vi.mocked(keys.patchKey).mockResolvedValue(key());
    await open();
    await menuPick("shop", "Edit key");
    expect(label().value).toBe("shop");
    await typeLabel("");
    formButton("Save").click();
    await settle();
    expect(keys.patchKey).toHaveBeenCalledWith("k1", expect.objectContaining({ label: "" }));
  });

  it("an edit of a key that has no label is not held back, and is titled by its prefix", async () => {
    list.mockResolvedValue({ items: [key({ label: "  ", prefix: "rk_none" })] });
    await open();
    await menuPick("rk_none", "Edit key");
    expect(drawerEl().querySelector("h2")!.textContent).toBe("Edit key · rk_none");
    formButton("Save").click();
    await settle();
    expect(keys.patchKey).toHaveBeenCalledWith("k1", expect.objectContaining({ label: "" }));
  });
});

describe("a key with no label in the list", () => {
  it.each([
    ["empty", ""],
    ["spaces", "   "],
  ])(
    "a label that is %s shows the prefix, muted and in mono, with no (no label) and no second prefix line",
    async (_n, label) => {
      list.mockResolvedValue({ items: [key({ label, prefix: "rk_none" })] });
      const w = await open();
      const cell = w.find("tbody tr .k-label");
      expect(cell.text()).toBe("rk_none");
      expect(cell.classes()).toEqual(expect.arrayContaining(["k-label--prefix", "mono"]));
      expect(w.find("tbody tr .t-key .meta").exists()).toBe(false);
      expect(document.body.textContent).not.toContain("(no label)");
      expect(named("More actions for rk_none")).not.toBeNull();
    },
  );

  it("a labelled key shows its label with the prefix under it", async () => {
    const w = await open();
    expect(w.find("tbody tr .k-label").text()).toBe("shop");
    expect(w.find("tbody tr .t-key .meta").text()).toBe("rk_abcd••••••••");
  });

  it("the phone block shows the prefix once, as the name", async () => {
    showAt(390);
    list.mockResolvedValue({ items: [key({ label: " ", prefix: "rk_none", jobs: 3 })] });
    const w = await open();
    const block = w.find(".p-key");
    expect(block.find(".p-key__title").text()).toBe("rk_none");
    expect(block.find(".p-key__title").classes()).toContain("mono");
    expect(block.find(".p-key__sub").text()).toBe("never used");
  });

  it("the delete question names it by its prefix", async () => {
    list.mockResolvedValue({ items: [key({ label: "  ", prefix: "rk_none" })] });
    vi.mocked(confirm.confirmDialog).mockResolvedValue(false);
    await open();
    await menuPick("rk_none", "Delete key");
    expect(confirm.confirmDialog).toHaveBeenCalledWith(expect.objectContaining({ title: "Delete key rk_none?" }));
  });
});

describe("the key form as a drawer that guards what was typed", () => {
  const drawer = () => document.body.querySelector(".el-drawer");
  const xButton = () => document.body.querySelector<HTMLButtonElement>(".el-drawer button[aria-label='Close · Esc']")!;
  const label = () => document.body.querySelector<HTMLInputElement>("#key-label")!;
  const askedWith = (message: string) => expect.objectContaining({ message });
  const confirmed = vi.mocked(confirm.confirmDialog);
  // a press and release on the scrim itself, as a mouse does
  const scrim = () => {
    const el = document.body.querySelector<HTMLElement>(".el-overlay")!;
    for (const type of ["mousedown", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(type, { bubbles: true }));
  };
  const drawerOpen = () => wrapper!.findComponent({ name: "ElDrawer" }).props("modelValue");

  it("is a right drawer named by its heading, and a click beside it is a way out like Escape", async () => {
    await open();
    pagePrimary().click();
    await settle();
    expect(drawer()).not.toBeNull();
    expect(document.body.querySelector(".el-dialog")).toBeNull();
    const dialog = document.body.querySelector('[role="dialog"]')!;
    expect(dialog.hasAttribute("aria-label")).toBe(false);
    expect(document.getElementById(dialog.getAttribute("aria-labelledby")!)).toBe(
      document.body.querySelector(".el-drawer h2"),
    );
    const el = wrapper!.findComponent({ name: "ElDrawer" });
    expect(el.props("closeOnClickModal")).toBe(true);
    expect(el.props("direction")).toBe("rtl");
  });

  it("a click beside a typed form asks like Escape, and beside a clean form closes it", async () => {
    await open();
    await openNewKey("half typed");
    confirmed.mockResolvedValueOnce(false);
    scrim();
    await settle();
    expect(confirmed).toHaveBeenCalledExactlyOnceWith(askedWith("Discard what you typed?"));
    expect(label().value).toBe("half typed");
    expect(drawerOpen()).toBe(true);
    confirmed.mockResolvedValueOnce(true);
    scrim();
    await settle();
    expect(drawerOpen()).toBe(false);
    pagePrimary().click();
    await settle();
    scrim();
    await settle();
    expect(confirmed).toHaveBeenCalledTimes(2);
    expect(drawerOpen()).toBe(false);
  });

  it("a form nobody typed in closes at once", async () => {
    await open();
    pagePrimary().click();
    await settle();
    xButton().click();
    await settle();
    expect(confirmed).not.toHaveBeenCalled();
    expect(drawerOpen()).toBe(false);
  });

  it("Back is a way out too, through the same guard", async () => {
    await open();
    await openNewKey("half typed");
    confirmed.mockResolvedValueOnce(false);
    named("Back to API keys").click();
    await settle();
    expect(confirmed).toHaveBeenCalledTimes(1);
    expect(drawerOpen()).toBe(true);
  });

  it("a typed form asks; Keep editing keeps what was typed; Discard closes and the next form is empty", async () => {
    await open();
    await openNewKey("half typed");
    confirmed.mockResolvedValueOnce(false);
    xButton().click();
    await settle();
    expect(confirmed).toHaveBeenCalledExactlyOnceWith(askedWith("Discard what you typed?"));
    expect(label().value).toBe("half typed");
    expect(drawerOpen()).toBe(true);
    confirmed.mockResolvedValueOnce(true);
    xButton().click();
    await settle();
    expect(confirmed).toHaveBeenCalledTimes(2);
    expect(drawerOpen()).toBe(false);
    expect(keys.createKey).not.toHaveBeenCalled();
    pagePrimary().click();
    await settle();
    expect(label().value).toBe("");
    // and a form that was only opened again is clean
    xButton().click();
    await settle();
    expect(confirmed).toHaveBeenCalledTimes(2);
  });

  it("a form edited and put back as it was is clean", async () => {
    await open();
    await openNewKey("abc");
    await typeLabel("");
    xButton().click();
    await settle();
    expect(confirmed).not.toHaveBeenCalled();
  });

  it("an edit is dirty once a field changes, not before", async () => {
    vi.mocked(keys.patchKey).mockResolvedValue(key());
    await open();
    await menuPick("shop", "Edit key");
    xButton().click();
    await settle();
    expect(confirmed).not.toHaveBeenCalled();
    await menuPick("shop", "Edit key");
    await typeLabel("shop 2");
    confirmed.mockResolvedValueOnce(false);
    xButton().click();
    await settle();
    expect(confirmed).toHaveBeenCalledTimes(1);
  });

  it("while the create is in flight no way out works, and a good answer closes the drawer without a question", async () => {
    let answer!: (v: Awaited<ReturnType<typeof keys.createKey>>) => void;
    vi.mocked(keys.createKey).mockReturnValue(new Promise((r) => (answer = r)));
    await open();
    await openNewKey("Slow");
    formButton("Create key").click();
    await settle();
    expect(xButton().disabled).toBe(true);
    expect(formButton("Cancel").disabled).toBe(true);
    xButton().click();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }));
    formButton("Cancel").click();
    scrim();
    await settle();
    expect(confirmed).not.toHaveBeenCalled();
    expect(drawerOpen()).toBe(true);
    answer({ ...key({ id: "k2" }), key: RAW });
    await settle();
    expect(confirmed).not.toHaveBeenCalled();
    expect(drawerOpen()).toBe(false);
    expect(callout()!.value).toBe(RAW);
  });

  it("a create that fails after the form is gone is a toast, not a lost error", async () => {
    let fail!: (e: unknown) => void;
    vi.mocked(keys.createKey).mockReturnValue(new Promise((_r, reject) => (fail = reject)));
    const w = await open();
    await openNewKey("Gone");
    formButton("Create key").click();
    await settle();
    (w.vm as unknown as { formOpen: boolean }).formOpen = false;
    fail(new ApiError(422, { error: { code: "input.invalid", message: "x", param: "label" } }));
    await settle();
    expect(notify.error).toHaveBeenCalledExactlyOnceWith(
      "Some of the values are not accepted. Check the highlighted field.",
    );
  });

  it("a failed create with the form open stays inside the form and sends no toast", async () => {
    vi.mocked(keys.createKey).mockRejectedValue(new TypeError("down"));
    await open();
    await openNewKey();
    formButton("Create key").click();
    await settle();
    expect(document.body.querySelector(".kf__error")!.textContent!.trim()).toBe("Network error, please try again.");
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("while a create is in flight a reload or a closed tab is warned about, though nothing was typed", async () => {
    vi.mocked(keys.createKey).mockReturnValue(new Promise(() => undefined));
    await open();
    pagePrimary().click();
    await settle();
    formButton("Create key").click();
    await settle();
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(true);
  });

  it("a good save puts focus in the modal's field once the drawer is gone", async () => {
    await createOne(true);
    await new Promise((r) => setTimeout(r, 600));
    expect(document.activeElement).toBe(callout());
  });

  it("focus goes to the page's primary button when the button that opened the form is gone", async () => {
    vi.mocked(keys.patchKey).mockResolvedValue(key());
    await open(true);
    await menuPick("shop", "Edit key");
    // the save reads the list again, and the key (with its row menu) is no longer in it
    list.mockResolvedValue({ items: [] });
    formButton("Save").click();
    await settle();
    await new Promise((r) => setTimeout(r, 600));
    expect(document.activeElement).toBe(pagePrimary());
  });

  it("a reload or a closed tab is warned about while the form holds typing, and not after", async () => {
    const warned = () => {
      const e = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    };
    await open();
    expect(warned()).toBe(false);
    await openNewKey("typing");
    expect(warned()).toBe(true);
    confirmed.mockResolvedValueOnce(true);
    xButton().click();
    await settle();
    expect(warned()).toBe(false);
  });
});

describe("the tablet and the phone", () => {
  const detailed = () =>
    list.mockResolvedValue({
      items: [
        key({
          models: ["m1", "m2"],
          jobs: 61,
          unfinished: 1,
          max_unfinished_effective: 5,
          wallets: [wallet({ scope: "*", total_limit: 1000, used_total: 950 }), wallet()],
        }),
        key({ id: "k2", label: "any", models: ["*"], enabled: false, wallets: [wallet({ scope: "*" })] }),
        key({ id: "k3", label: "empty", wallets: [wallet({ scope: "*", total_limit: 10, used_total: 10 })] }),
      ],
    });

  it("a tablet keeps the desktop's columns in a table that scrolls sideways, and New key at the head", async () => {
    showAt(768);
    detailed();
    const w = await open();
    expect(w.findAll("thead th").map((h) => h.text())).toEqual([
      "Key",
      "Wallet",
      "Providers and caps today",
      "Last used",
      "Actions",
    ]);
    expect(pagePrimary().textContent?.trim()).toBe("New key");
    expect(w.find(".kl--tablet").exists()).toBe(true);
  });

  it("a phone lists each key as a block with its pill, balance, caps and row buttons", async () => {
    showAt(390);
    detailed();
    const w = await open();
    expect(w.find("table").exists()).toBe(false);
    const [shop, any, empty] = w.findAll(".p-key");
    expect(shop!.find(".pill").text()).toBe("Low");
    expect(shop!.find(".p-key__right").text()).toBe("50 pts");
    expect(shop!.find(".p-key__sub").text()).toBe("rk_abcd•••• · never used");
    expect(shop!.findAll('[data-testid="cap-chip"]')).toHaveLength(1);
    expect(shop!.find('button[aria-label="Edit key shop"]').exists()).toBe(true);
    expect(buttonIn(shop!.element, "Adjust wallet")).toBeDefined();
    expect(any!.find(".pill").text()).toBe("Paused");
    expect(buttonIn(empty!.element, "Top up")).toBeDefined();
  });

  it("Enable on a paused phone block asks the server", async () => {
    showAt(390);
    list.mockResolvedValue({ items: [key({ enabled: false })] });
    await open();
    named("Enable key shop").click();
    await settle();
    expect(keys.setKeyEnabled).toHaveBeenCalledWith("k1", true);
  });
});
