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
  jobs: 0,
  unfinished: 0,
  wallets: [],
  ...over,
});

const list = vi.mocked(keys.listKeys);
let wrapper: VueWrapper | null = null;
let pinia = createPinia();

async function settle() {
  await flushPromises();
  await flushPromises();
}
async function open() {
  wrapper = mount(KeysView, { global: { plugins: [pinia] }, attachTo: document.body });
  await settle();
  return wrapper;
}
const buttonOf = (text: string) =>
  [...document.body.querySelectorAll("button")].find((b) => b.textContent?.trim() === text)!;

// Creates a key through the form and leaves the one-time callout on screen.
async function createOne() {
  const w = await open();
  buttonOf("New key").click();
  await settle();
  buttonOf("Create key").click();
  await settle();
  return w;
}
const callout = () => document.body.querySelector<HTMLInputElement>('[data-testid="new-key"] input');

// Everything a page can hold the key in besides the callout's own field.
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
      { id: "m1", owned_by: "vibes", kind: "video" },
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
  it("draws the wallets text, the unfinished count and server strings as text", async () => {
    list.mockResolvedValue({
      items: [
        key({
          label: PLANTED,
          models: ["m1", "m2"],
          max_unfinished_effective: 0,
          unfinished: 2,
          wallets: [
            {
              provider: "vibes",
              kind: "video",
              day_limit: 10,
              total_limit: 100,
              used_day: 3,
              used_total: 40,
              mode: "limit",
            },
            {
              provider: "dola",
              kind: "image",
              day_limit: 0,
              total_limit: 0,
              used_day: 0,
              used_total: 0,
              mode: "closed",
            },
          ],
        }),
      ],
    });
    const w = await open();
    const row = w.find("tbody tr").text();
    expect(row).toContain(PLANTED);
    expect(row).toContain("vibes video");
    expect(row).toContain("day 3/10 · total 40/100");
    expect(row).toContain("dola image");
    expect(row).toContain("closed");
    expect(row).toContain("2 / unlimited");
    expect(w.findAll("tbody tr")[0]!.find(".k-models").text()).toBe("m1m2");
    expect(w.find("img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("an enabled key is plain text, a disabled key is the only one with a chip, and 'any model' is neutral", async () => {
    list.mockResolvedValue({
      items: [key({ id: "k1", enabled: true, models: ["*"] }), key({ id: "k2", enabled: false, models: ["veo-3"] })],
    });
    const w = await open();
    const [on, off] = w.findAll("tbody tr");
    expect(on!.text()).toContain("enabled");
    expect(on!.find('[data-tone="ok"]').exists()).toBe(false);
    expect(on!.find('[data-tone="info"]').exists()).toBe(false);
    // "any model" is a quiet tag, not a status chip: the only chip of an enabled key would be colour for the normal case
    expect(on!.findAll("[data-tone]")).toEqual([]);
    expect(on!.find(".k-tag").text()).toBe("any model");
    expect(off!.findAll("[data-tone]").map((c) => [c.text(), c.attributes("data-tone")])).toEqual([
      ["disabled", "neutral"],
    ]);
  });

  it("a switch that fails reads the list again, so it goes back", async () => {
    vi.mocked(keys.setKeyEnabled).mockRejectedValue(new ApiError(500, { error: { message: "boom" } }));
    const w = await open();
    await w.find('tbody [role="switch"]').trigger("click");
    await settle();
    expect(keys.setKeyEnabled).toHaveBeenCalledWith("k1", false);
    expect(list).toHaveBeenCalledTimes(2);
    expect(notify.error).toHaveBeenCalledWith("boom");
  });

  it("delete says whether the key went or was only disabled", async () => {
    vi.mocked(confirm.confirmDialog).mockResolvedValue(true);
    vi.mocked(keys.deleteKey).mockResolvedValueOnce({ id: "k1", result: "deleted" });
    const w = await open();
    const del = () => w.findAll("tbody button").find((b) => b.attributes("aria-label") === "Delete key shop")!;
    await del().trigger("click");
    await settle();
    expect(confirm.confirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Delete key shop?", message: "A key that has jobs is only disabled." }),
    );
    expect(notify.info).toHaveBeenLastCalledWith("Key deleted");
    vi.mocked(keys.deleteKey).mockResolvedValueOnce({ id: "k1", result: "disabled" });
    await del().trigger("click");
    await settle();
    expect(notify.info).toHaveBeenLastCalledWith("Key has jobs: disabled instead");
  });

  it("a cancelled delete sends nothing", async () => {
    vi.mocked(confirm.confirmDialog).mockResolvedValue(false);
    const w = await open();
    await w
      .findAll("tbody button")
      .find((b) => b.attributes("aria-label") === "Delete key shop")!
      .trigger("click");
    await settle();
    expect(keys.deleteKey).not.toHaveBeenCalled();
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
    buttonOf("New key").click();
    await settle();
    expect(max().value).toBe("");
    await type(max(), "0");
    buttonOf("Create key").click();
    await settle();
    expect(keys.createKey).toHaveBeenLastCalledWith(expect.objectContaining({ max_unfinished: 0 }));
  });

  it("clearing the field goes back to the default (null)", async () => {
    await open();
    buttonOf("New key").click();
    await settle();
    await type(max(), "5");
    await type(max(), "");
    expect(max().value).toBe("");
    buttonOf("Create key").click();
    await settle();
    expect(keys.createKey).toHaveBeenLastCalledWith(expect.objectContaining({ max_unfinished: null }));
  });
});

describe("the one-time key", () => {
  it("is shown after create in a read-only field and sent nowhere else", async () => {
    await createOne();
    expect(keys.createKey).toHaveBeenCalledWith({
      label: "",
      models: ["*"],
      max_unfinished: null,
      wallets: expect.any(Array),
    });
    expect(callout()!.value).toBe(RAW);
    expect(callout()!.readOnly).toBe(true);
    // the field is the only place: not the page html, not storage, not Pinia, not the address
    expect(whereTheKeyIs()).toEqual(["field value"]);
  });

  it("is gone from every place after Dismiss", async () => {
    await createOne();
    buttonOf("Dismiss").click();
    await settle();
    expect(callout()).toBeNull();
    expect(whereTheKeyIs()).toEqual([]);
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
    buttonOf("New key").click();
    await settle();
    buttonOf("Create key").click();
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
    buttonOf("New key").click();
    await settle();
    buttonOf("Create key").click();
    await settle();
    const dialog = document.body.querySelector(".el-dialog")!;
    expect(dialog.textContent).toContain("Some of the values are not accepted. Check the highlighted field.");
    expect(dialog.querySelector("#key-label.is-error")).not.toBeNull();
    expect(whereTheKeyIs()).toEqual([]);
    for (const fn of Object.values(notify)) expect(fn).not.toHaveBeenCalledWith(expect.stringContaining(RAW));
    expect(notify.error).not.toHaveBeenCalled();
    expect(callout()).toBeNull();
  });

  it("a failed create with an unknown code or a network error shows the fixed fallback text", async () => {
    vi.mocked(keys.createKey).mockRejectedValueOnce(new ApiError(500, { error: { code: "x.y", message: RAW } }));
    await open();
    buttonOf("New key").click();
    await settle();
    buttonOf("Create key").click();
    await settle();
    expect(document.body.querySelector(".kd__error")!.textContent!.trim()).toBe(
      "The key could not be created. Try again.",
    );
    vi.mocked(keys.createKey).mockRejectedValueOnce(new TypeError(RAW));
    buttonOf("Create key").click();
    await settle();
    expect(document.body.querySelector(".kd__error")!.textContent!.trim()).toBe("Network error, please try again.");
    expect(whereTheKeyIs()).toEqual([]);
  });

  it("an edit shows no key", async () => {
    vi.mocked(keys.patchKey).mockResolvedValue(key());
    const w = await open();
    await w
      .findAll("tbody button")
      .find((b) => b.attributes("aria-label") === "Edit key shop")!
      .trigger("click");
    await settle();
    buttonOf("Save").click();
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
  const copyIn = (root: ParentNode) =>
    [...root.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Copy");

  const prefixCopy = (root: ParentNode) => root.querySelector<HTMLButtonElement>('button[title="Copy key prefix"]');

  it("the prefix in a row has a Copy that copies the prefix and nothing else", async () => {
    const w = await open();
    const button = prefixCopy(w.find("tbody").element)!;
    expect(button.getAttribute("aria-label")).toBe("Copy key prefix of shop");
    button.click();
    await settle();
    expect(writeText).toHaveBeenCalledExactlyOnceWith("rk_abcd");
  });

  it("a key without a prefix has no Copy in its row", async () => {
    list.mockResolvedValue({ items: [key({ prefix: "" })] });
    const w = await open();
    expect(prefixCopy(w.find("tbody").element)).toBeNull();
  });

  it("the one-time callout has a Copy that copies the raw key", async () => {
    await createOne();
    copyIn(document.body.querySelector('[data-testid="new-key"]')!)!.click();
    await settle();
    expect(writeText).toHaveBeenCalledExactlyOnceWith(RAW);
  });

  it("the Copy of the callout says Copied afterwards", async () => {
    await createOne();
    const box = document.body.querySelector('[data-testid="new-key"]')!;
    copyIn(box)!.click();
    await settle();
    expect([...box.querySelectorAll("button")].map((b) => b.textContent?.trim())).toEqual(["Copied", "Dismiss"]);
  });
});

const limitWallet = (over: Partial<keys.Wallet> = {}): keys.Wallet => ({
  provider: "dola",
  kind: "video",
  day_limit: 20,
  total_limit: 200,
  used_day: 3,
  used_total: 14,
  mode: "limit",
  ...over,
});

describe("the page as the design draws it", () => {
  it("has the title, the sentence, New key, and no Refresh, no Updated line, no pager", async () => {
    const w = await open();
    expect(w.find("h1").text()).toBe("API keys");
    expect(w.text()).toContain("Who may call the relay, which models they may use and how much each wallet may spend.");
    expect(buttonOf("New key")).toBeDefined();
    expect(w.text()).not.toContain("Updated");
    expect(w.text()).not.toContain("Refresh");
    expect(w.find(".el-pagination").exists()).toBe(false);
    expect(w.text()).toContain(
      "A key that has jobs is only disabled when deleted. Wallet bars: left = today, right = total.",
    );
  });

  it("a limit wallet draws two bars of the rounded share used, with a spoken label; other modes draw none", async () => {
    list.mockResolvedValue({
      items: [
        key({
          wallets: [
            limitWallet(),
            limitWallet({ kind: "image", mode: "unlimited", used_total: 9 }),
            limitWallet({ provider: "x", kind: "audio", mode: "closed" }),
          ],
        }),
      ],
    });
    const w = await open();
    const bars = w.findAll('tbody [role="img"]');
    expect(bars).toHaveLength(1);
    expect(bars[0]!.attributes("aria-label")).toBe("Used 3 of 20 today, 14 of 200 in total");
    expect(bars[0]!.findAll(".wallet__fill").map((f) => (f.element as HTMLElement).style.width)).toEqual(["15%", "7%"]);
    const lines = w.findAll('tbody [data-testid="wallet-line"]').map((l) => l.text());
    expect(lines[0]).toContain("dola video");
    expect(lines[0]).toContain("day 3/20 · total 14/200");
    expect(lines[1]).toContain("unlimited (9 used)");
    expect(lines[2]).toContain("closed");
  });

  it("lists a provider's video wallet before its image wallet, whatever order the server sent", async () => {
    list.mockResolvedValue({
      items: [
        key({
          wallets: [limitWallet({ kind: "image", mode: "closed" }), limitWallet({ kind: "video", mode: "closed" })],
        }),
      ],
    });
    const w = await open();
    expect(w.findAll('tbody [data-testid="wallet-line"] .wallet__kind').map((k) => k.text())).toEqual([
      "dola video",
      "dola image",
    ]);
  });

  it("the switch is a labelled switch that shows what the server holds", async () => {
    list.mockResolvedValue({ items: [key({ enabled: false }), key({ id: "k2", label: "two", enabled: true })] });
    const w = await open();
    const [off, on] = w.findAll('tbody [role="switch"]');
    expect(off!.attributes("aria-checked")).toBe("false");
    expect(off!.attributes("aria-label")).toBe("Enable key shop");
    expect(off!.attributes("title")).toBe("Enable key");
    expect(on!.attributes("aria-checked")).toBe("true");
    expect(on!.attributes("title")).toBe("Disable key");
  });

  it("the callout names the new key, and its row is the one tinted", async () => {
    list.mockResolvedValueOnce({ items: [key()] });
    vi.mocked(keys.createKey).mockResolvedValue({ ...key({ id: "k2", label: "Partner D" }), key: RAW });
    list.mockResolvedValueOnce({ items: [key(), key({ id: "k2", label: "Partner D" })] });
    const w = await createOne();
    expect(document.body.querySelector('[data-testid="new-key"]')!.textContent).toContain("Key created for Partner D.");
    expect(document.body.querySelector('[data-testid="new-key"]')!.textContent).toContain(
      "Copy it now: it is shown once and not stored.",
    );
    const rows = w.findAll("tbody tr");
    expect(rows.map((r) => r.classes().includes("key-new"))).toEqual([false, true]);
    // the field is named after the key it holds, as drawn
    expect(document.body.querySelector('[data-testid="new-key"] input')!.getAttribute("aria-label")).toBe(
      "New API key for Partner D",
    );
  });

  it("an empty list says so and offers New key", async () => {
    list.mockResolvedValue({ items: [] });
    const w = await open();
    expect(w.text()).toContain("No API keys yet.");
    expect(w.text()).not.toContain("A key that has jobs is only disabled when deleted");
  });

  it("a list that fails to load says why in the canvas's words, under the card's heading", async () => {
    list.mockRejectedValue(new ApiError(502, { error: { code: "upstream.bad_gateway", message: "bad gateway" } }));
    const w = await open();
    expect(w.find(".keys-card h2").text()).toBe("API keys");
    expect(w.find('.keys-card [role="alert"]').text()).toContain("Could not load the API keys: the relay answered 502");
  });

  it("an empty list draws its card with the heading API keys; a list with keys has no heading", async () => {
    list.mockResolvedValue({ items: [] });
    const empty = await open();
    expect(empty.find(".keys-card h2").text()).toBe("API keys");
    empty.unmount();
    list.mockResolvedValue({ items: [key()] });
    const listed = await open();
    expect(listed.find(".keys-card h2").exists()).toBe(false);
  });
});

describe("the key form as the design draws it", () => {
  const editShop = async (over: Partial<ApiKey> = {}) => {
    list.mockResolvedValue({
      items: [
        key({
          models: ["m1"],
          wallets: [
            limitWallet({ provider: "vibes", kind: "video" }),
            limitWallet({ provider: "dola", kind: "image", mode: "unlimited", used_total: 9 }),
          ],
          ...over,
        }),
      ],
    });
    const w = await open();
    await w.find('button[aria-label="Edit key shop"]').trigger("click");
    await settle();
    return w;
  };
  const field = (selector: string) => document.body.querySelector<HTMLInputElement>(selector)!;
  const choose = async (selector: string, value: string) => {
    const select = document.body.querySelector<HTMLSelectElement>(selector)!;
    select.value = value;
    select.dispatchEvent(new Event("change"));
    await settle();
  };

  it("an edit names the key in its title and tells what each wallet has used", async () => {
    await editShop();
    const dialog = document.body.querySelector(".el-dialog")!;
    expect(dialog.querySelector("h2")!.textContent).toBe("Edit API key — shop");
    // the dialog is named by its heading, as drawn (aria-labelledby, no aria-label of its own)
    const named = document.body.querySelector('[role="dialog"]')!;
    expect(dialog.querySelector("h2")!.id).not.toBe("");
    expect(named.getAttribute("aria-labelledby")).toBe(dialog.querySelector("h2")!.id);
    expect(named.hasAttribute("aria-label")).toBe(false);
    expect(dialog.textContent).toContain("3 used today · 14 total");
    expect(dialog.textContent).toContain("9 used");
    expect(dialog.textContent).toContain("Limits apply in limit mode only. Per day resets at midnight.");
  });

  it("a new key has a plain title and says nothing of use", async () => {
    await open();
    buttonOf("New key").click();
    await settle();
    const dialog = document.body.querySelector(".el-dialog")!;
    expect(dialog.querySelector("h2")!.textContent).toBe("New API key");
    expect(dialog.textContent).not.toContain("used");
  });

  it("a wallet that leaves limit mode shows empty, disabled number boxes, and a limit wallet shows its numbers", async () => {
    await editShop();
    expect(field('input[aria-label="vibes video per day"]').value).toBe("20");
    expect(field('input[aria-label="vibes video total"]').value).toBe("200");
    expect(field('input[aria-label="vibes video per day"]').disabled).toBe(false);
    await choose('select[aria-label="vibes video mode"]', "closed");
    expect(field('input[aria-label="vibes video per day"]').value).toBe("");
    expect(field('input[aria-label="vibes video per day"]').disabled).toBe(true);
    expect(field('input[aria-label="vibes video total"]').disabled).toBe(true);
    await choose('select[aria-label="vibes video mode"]', "limit");
    expect(field('input[aria-label="vibes video per day"]').value).toBe("20");
  });

  it("the model boxes are off while Any model is ticked and on once it is cleared", async () => {
    await editShop({ models: ["*"] });
    const boxes = () => [
      ...document.body.querySelectorAll<HTMLInputElement>('[role="group"][aria-label="Pick models"] input'),
    ];
    expect(boxes()).toHaveLength(2);
    expect(boxes().every((b) => b.disabled)).toBe(true);
    expect(document.body.querySelector('[role="group"][aria-label="Pick models"]')!.getAttribute("aria-disabled")).toBe(
      "true",
    );
    const any = [...document.body.querySelectorAll<HTMLInputElement>("fieldset input[type=checkbox]")][0]!;
    any.click();
    await settle();
    expect(boxes().every((b) => !b.disabled)).toBe(true);
    expect(document.body.querySelector('[role="group"][aria-label="Pick models"]')!.getAttribute("aria-disabled")).toBe(
      "false",
    );
  });

  it("each model row says its kind and provider", async () => {
    await editShop();
    const rows = [...document.body.querySelectorAll(".kd__model")].map((r) =>
      r.textContent!.replace(/\s+/g, " ").trim(),
    );
    expect(rows).toEqual(["m1video · vibes", "m2image · dola"]);
    expect(document.body.querySelector(".kd__model-hint")!.textContent).toBe("video · vibes");
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
          wallets: [limitWallet()],
        }),
        key({ id: "k2", label: "any", models: ["*"], enabled: false }),
      ],
    });

  it("a tablet keeps five columns and folds the models and the date into the label and unfinished cells", async () => {
    showAt(768);
    detailed();
    const w = await open();
    expect(w.findAll("thead th").map((h) => h.text())).toEqual([
      "Label",
      "Status",
      "Wallets",
      "Unfinished · jobs",
      "Actions",
    ]);
    const [shop, any] = w.findAll("tbody tr");
    expect(shop!.find(".k-models-line").text()).toBe("m1, m2");
    expect(any!.find(".k-models-line").text()).toBe("any model");
    expect(shop!.text()).toContain("1 / 5 · 61 jobs");
    expect(shop!.find(".k-created").exists()).toBe(true);
    // the two lines of that cell start at the left, as drawn; the wallet lines take the tablet form
    expect(w.findAll("thead th")[3]!.classes()).not.toContain("is-right");
    expect(shop!.find(".wallets").classes()).toContain("is-compact");
  });

  it("a phone lists each key as a block with its models, wallets, counts and the two row buttons", async () => {
    showAt(390);
    detailed();
    const w = await open();
    expect(w.find("table").exists()).toBe(false);
    const [shop, any] = w.findAll(".p-key");
    expect(shop!.text()).toContain("Models");
    expect(shop!.find(".p-key__models-list").text()).toBe("m1 · m2");
    expect(shop!.find('[role="img"]').attributes("aria-label")).toBe("Used 3 of 20 today, 14 of 200 in total");
    expect(shop!.find(".p-key__facts").text()).toContain("Unfinished 1 / 5 · 61 jobs · created");
    // the phone draws the wallet lines at the desktop size (12 px values, one line each)
    expect(shop!.find(".wallets").classes()).not.toContain("is-compact");
    expect(any!.find(".k-tag").text()).toBe("any model");
    expect(any!.text()).toContain("disabled");
    expect(shop!.find('button[aria-label="Edit key shop"]').exists()).toBe(true);
    expect(shop!.find('button[aria-label="Delete key shop"]').exists()).toBe(true);
  });

  it("the switch of a phone block asks the server like the table's does", async () => {
    showAt(390);
    const w = await open();
    await w.find('.p-key [role="switch"]').trigger("click");
    await settle();
    expect(keys.setKeyEnabled).toHaveBeenCalledWith("k1", false);
  });
});
