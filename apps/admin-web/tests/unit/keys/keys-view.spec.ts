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
  buttonOf("Create").click();
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
    expect(row).toContain("vibes video: day 3/10, total 40/100");
    expect(row).toContain("dola image: closed");
    expect(row).toContain("2 / unlimited");
    expect(row).toContain("m1, m2");
    expect(w.find("img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("a switch that fails reads the list again, so it goes back", async () => {
    vi.mocked(keys.setKeyEnabled).mockRejectedValue(new ApiError(500, { error: { message: "boom" } }));
    const w = await open();
    await w.find("tbody .el-switch").trigger("click");
    await settle();
    expect(keys.setKeyEnabled).toHaveBeenCalledWith("k1", false);
    expect(list).toHaveBeenCalledTimes(2);
    expect(notify.error).toHaveBeenCalledWith("boom");
  });

  it("delete says whether the key went or was only disabled", async () => {
    vi.mocked(confirm.confirmDialog).mockResolvedValue(true);
    vi.mocked(keys.deleteKey).mockResolvedValueOnce({ id: "k1", result: "deleted" });
    const w = await open();
    const del = () => w.findAll("tbody button").find((b) => b.text() === "Delete")!;
    await del().trigger("click");
    await settle();
    expect(confirm.confirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ message: 'Delete key "shop"? A key that has jobs is only disabled.' }),
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
      .find((b) => b.text() === "Delete")!
      .trigger("click");
    await settle();
    expect(keys.deleteKey).not.toHaveBeenCalled();
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
    buttonOf("Create").click();
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
    buttonOf("Create").click();
    await settle();
    const dialog = document.body.querySelector(".el-dialog")!;
    expect(dialog.textContent).toContain("Some of the values are not accepted. Check the highlighted field.");
    expect(dialog.querySelector(".el-form-item.is-error")).not.toBeNull();
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
    buttonOf("Create").click();
    await settle();
    expect(document.body.querySelector(".form-error")!.textContent).toBe("The key could not be created. Try again.");
    vi.mocked(keys.createKey).mockRejectedValueOnce(new TypeError(RAW));
    buttonOf("Create").click();
    await settle();
    expect(document.body.querySelector(".form-error")!.textContent).toBe("Network error, please try again.");
    expect(whereTheKeyIs()).toEqual([]);
  });

  it("an edit shows no key", async () => {
    vi.mocked(keys.patchKey).mockResolvedValue(key());
    const w = await open();
    await w
      .findAll("tbody button")
      .find((b) => b.text() === "Edit")!
      .trigger("click");
    await settle();
    buttonOf("Save").click();
    await settle();
    expect(keys.patchKey).toHaveBeenCalledTimes(1);
    expect(callout()).toBeNull();
  });
});
