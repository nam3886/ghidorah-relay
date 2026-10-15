import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, KeepAlive } from "vue";
import * as api from "@/api/test";
import { DROPPED_NOTE, FILE_STORE, type FileStore, NOT_SAVED_NOTE, type StoredFile } from "@/lib/test-files";
import { useAuthStore } from "@/stores/auth";
import TestView from "@/views/TestView.vue";

vi.mock("@/api/test", async (original) => ({
  ...(await original<typeof import("@/api/test")>()),
  listModels: vi.fn(),
  getPoints: vi.fn(),
  getOptions: vi.fn(),
  createJob: vi.fn(),
  getJob: vi.fn(),
}));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

// Built in two parts: the lint rule that bans script URLs reads literals.
const SCRIPT_URL = "java" + "script:alert(1)";
const video = { id: "veo-3", kind: "video", owned_by: "flow", prices: { 8: 10 }, limits: { max: 8 } };
const image = { id: "imagen-4", kind: "image", owned_by: "flow", prices: 1, limits: {} };

function memoryStore(initial?: unknown) {
  let value: unknown = initial;
  const store: FileStore & { value: () => unknown } = {
    get: vi.fn(async () => value),
    put: vi.fn(async (files: StoredFile[]) => void (value = files)),
    remove: vi.fn(async () => void (value = undefined)),
    value: () => value,
  };
  return store;
}
const png = (name: string, size = 1): StoredFile => {
  const b = new Blob(["x"], { type: "image/png" });
  Object.defineProperty(b, "size", { value: size });
  return { name, type: "image/png", blob: b };
};

// KeepAlive as App.vue has it: the tab can be left and come back without being destroyed.
const Host = defineComponent({
  props: { show: { type: Boolean, default: true } },
  setup: (props) => () =>
    h(KeepAlive, { include: "TestView" }, { default: () => (props.show ? h(TestView) : h("div")) }),
});

let wrapper: VueWrapper | null = null;
async function open(store: FileStore = memoryStore()) {
  setActivePinia(createPinia());
  wrapper = mount(Host, { global: { provide: { [FILE_STORE as symbol]: store } }, attachTo: document.body });
  await flushPromises();
  return { wrapper, store };
}
const button = (name: string) => wrapper!.findAll("button").find((b) => b.text() === name)!;
async function loadModels(key = "rk-1") {
  await wrapper!.find("input[type=password]").setValue(key);
  await button("Load models").trigger("click");
  await flushPromises();
}
async function pickModel(id: string) {
  await wrapper!
    .findAll(".test-tab__model")
    .find((b) => b.text().startsWith(id))!
    .trigger("click");
  await flushPromises();
}
const text = () => wrapper!.text();

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  for (const fn of [api.listModels, api.getPoints, api.getOptions, api.createJob, api.getJob])
    vi.mocked(fn).mockReset();
  vi.mocked(api.listModels).mockResolvedValue({ data: [video, image] });
  vi.mocked(api.getPoints).mockResolvedValue({ points: 9 });
  vi.mocked(api.getOptions).mockResolvedValue({
    ratios: [{ name: "16:9" }, { label: "1:1" }],
    styles: [{ value: "noir" }],
    templates: [{ show_value: "Sunset", prompt: "a sunset over the sea" }],
  });
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
  vi.useRealTimers();
});

describe("the tab", () => {
  it("loads models grouped by kind, shows the points, picks a model and fills ratio, style and templates", async () => {
    await open();
    await loadModels();
    expect(wrapper!.findAll(".test-tab__group-title").map((g) => g.text())).toEqual(["image", "video"]);
    expect(text()).toContain('"points": 9');
    expect(text()).toContain("Pick a model");
    await pickModel("veo-3");
    expect(api.getOptions).toHaveBeenCalledWith("rk-1", "flow");
    expect(text()).toContain("video · flow · price");
    expect(wrapper!.findAll("#test-ratio-list option").map((o) => o.attributes("value"))).toEqual(["16:9", "1:1"]);
    expect(wrapper!.findAll("#test-style-list option").map((o) => o.attributes("value"))).toEqual(["noir"]);
    // a template copies its prompt into the box
    await wrapper!.findAll(".test-tab__templates button")[0]!.trigger("click");
    expect((wrapper!.find("textarea").element as HTMLTextAreaElement).value).toBe("a sunset over the sea");
  });

  it("keeps the key in sessionStorage only, the form in localStorage on every input", async () => {
    await open();
    await loadModels("rk-secret-9");
    await pickModel("veo-3");
    await wrapper!.find("textarea").setValue("a cat on a sofa");
    expect(sessionStorage.getItem("relay_test_key")).toBe("rk-secret-9");
    const form = JSON.parse(localStorage.getItem("relay_test_form") ?? "");
    expect(form.prompt).toBe("a cat on a sofa");
    expect(form.model).toBe("veo-3");
    expect(localStorage.getItem("relay_test_form")).not.toContain("rk-secret-9");
    expect(localStorage.length).toBe(1);
  });

  it("restores the form, the kept key, the models and the picked model after a reload", async () => {
    localStorage.setItem(
      "relay_test_form",
      JSON.stringify({ model: "imagen-4", prompt: "restored prompt", style: "noir" }),
    );
    sessionStorage.setItem("relay_test_key", "rk-kept");
    await open();
    expect(api.listModels).toHaveBeenCalledWith("rk-kept");
    expect((wrapper!.find("textarea").element as HTMLTextAreaElement).value).toBe("restored prompt");
    expect(text()).toContain("imagen-4");
    expect(button("Generate").attributes("disabled")).toBeUndefined();
  });

  it("Forget key clears the key from storage and the box; Clear form clears form, images and model", async () => {
    const store = memoryStore([png("a.png")]);
    await open(store);
    await loadModels("rk-1");
    await pickModel("veo-3");
    await wrapper!.find("textarea").setValue("hello");
    await button("Forget key").trigger("click");
    expect(sessionStorage.getItem("relay_test_key")).toBeNull();
    expect((wrapper!.find("input[type=password]").element as HTMLInputElement).value).toBe("");
    await button("Clear form").trigger("click");
    await flushPromises();
    expect(localStorage.getItem("relay_test_form")).toBeNull();
    expect(store.value()).toBeUndefined();
    expect((wrapper!.find("textarea").element as HTMLTextAreaElement).value).toBe("");
    expect(button("Generate").attributes("disabled")).toBeDefined();
    expect(text()).toContain("Pick a model");
  });
});

describe("the empty state", () => {
  it("keeps the model column with a hint while no model is loaded, and drops the hint once models arrive", async () => {
    await open();
    expect(wrapper!.find(".test-tab__models").exists()).toBe(true);
    expect(wrapper!.find('[data-testid="test-models-empty"]').text()).toContain("Load models");
    expect(wrapper!.findAll(".test-tab__model")).toHaveLength(0);
    expect(wrapper!.find(".test-tab__points-box").exists()).toBe(false);
    expect(button("Generate").attributes("disabled")).toBeDefined();
    await loadModels();
    expect(wrapper!.find('[data-testid="test-models-empty"]').exists()).toBe(false);
    expect(wrapper!.findAll(".test-tab__model")).toHaveLength(2);
  });

  it("the wallet limits sit in a closed block of their own, and the optional fields say so", async () => {
    await open();
    await loadModels();
    const box = wrapper!.find<HTMLDetailsElement>(".test-tab__points-box");
    expect(box.find("summary").text()).toBe("Wallet limits");
    expect(box.element.open).toBe(false);
    expect(box.find("pre").text()).toContain('"points": 9');
    const placeholders = wrapper!.findAll(".test-tab__row input").map((i) => i.attributes("placeholder"));
    expect(placeholders).toEqual(["optional", "16:9", "optional", "optional"]);
  });

  it("Clear form is a plain button, not a danger one", async () => {
    await open();
    expect(button("Clear form").classes()).not.toContain("el-button--danger");
    expect(button("Clear form").classes()).toContain("test-tab__clear");
  });
});

describe("Load models", () => {
  it("shows a busy state and sends one request for a double click and an Enter while the load is on its way", async () => {
    await open();
    let loaded!: (out: { data: (typeof video)[] }) => void;
    vi.mocked(api.listModels).mockReturnValue(new Promise((resolve) => (loaded = resolve)));
    const input = wrapper!.find("input[type=password]");
    await input.setValue("rk-1");
    expect(button("Load models").classes()).not.toContain("is-loading");
    void button("Load models").trigger("click");
    void button("Load models").trigger("click");
    await input.trigger("keyup.enter");
    await flushPromises();
    expect(api.listModels).toHaveBeenCalledTimes(1);
    expect(button("Load models").classes()).toContain("is-loading");
    loaded({ data: [video] });
    await flushPromises();
    expect(button("Load models").classes()).not.toContain("is-loading");
    expect(wrapper!.findAll(".test-tab__model")).toHaveLength(1);
  });

  it("is free again after a load that failed, so the next click sends a request", async () => {
    await open();
    vi.mocked(api.listModels).mockRejectedValueOnce(new api.TestApiError(500, "boom", "server down"));
    await loadModels();
    expect(button("Load models").classes()).not.toContain("is-loading");
    await loadModels();
    expect(api.listModels).toHaveBeenCalledTimes(2);
    expect(wrapper!.findAll(".test-tab__model")).toHaveLength(2);
  });
});

describe("images", () => {
  it("restores stored images, and a restore that drops anything rewrites the store and shows the notice", async () => {
    const store = memoryStore([...Array.from({ length: 10 }, (_, i) => png(`f${i}.png`)), png("eleven.png")]);
    await open(store);
    expect(text()).toContain(DROPPED_NOTE);
    expect(wrapper!.findAll(".test-images__list li")).toHaveLength(10);
    expect(store.value()).toHaveLength(10);
  });

  it("a non-array stored value is dropped whole with the notice", async () => {
    const store = memoryStore({ bad: true });
    await open(store);
    expect(text()).toContain(DROPPED_NOTE);
    expect(wrapper!.findAll(".test-images__list li")).toHaveLength(0);
    expect(store.value()).toBeUndefined();
  });

  it("writes only a checked list on Remove", async () => {
    const store = memoryStore([png("a.png"), png("b.png")]);
    await open(store);
    await wrapper!.find('button[aria-label="Remove a.png"]').trigger("click");
    await flushPromises();
    expect((store.value() as StoredFile[]).map((f) => f.name)).toEqual(["b.png"]);
  });
});

describe("storage that refuses, each says so at once", () => {
  const refusing = (over: object) => ({
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
    ...over,
  });
  const boom = () => {
    throw new Error("blocked");
  };

  it("IndexedDB blocked on restore: the notice is there when the tab opens", async () => {
    const store = memoryStore();
    vi.mocked(store.get).mockRejectedValue(new Error("blocked"));
    await open(store);
    expect(text()).toContain(NOT_SAVED_NOTE);
  });

  it("localStorage blocked on read: the notice is there when the tab opens", async () => {
    vi.stubGlobal("localStorage", refusing({ getItem: boom }));
    await open();
    expect(text()).toContain(NOT_SAVED_NOTE);
  });

  it("localStorage blocked on write: the notice shows on the first input, and goes when a write works again", async () => {
    let writes = false;
    vi.stubGlobal(
      "localStorage",
      refusing({
        setItem: () => {
          if (!writes) throw new Error("quota");
        },
      }),
    );
    await open();
    expect(text()).not.toContain(NOT_SAVED_NOTE);
    await wrapper!.find("textarea").setValue("a cat");
    expect(text()).toContain(NOT_SAVED_NOTE);
    writes = true;
    await wrapper!.find("textarea").setValue("a cat again");
    expect(text()).not.toContain(NOT_SAVED_NOTE);
  });

  it("IndexedDB blocked on write: the notice shows when an image is attached, with localStorage working", async () => {
    const store = memoryStore();
    vi.mocked(store.put).mockRejectedValue(new Error("quota"));
    await open(store);
    expect(text()).not.toContain(NOT_SAVED_NOTE);
    const input = wrapper!.find("#test-image-files");
    Object.defineProperty(input.element, "files", { value: [new File(["x"], "a.png", { type: "image/png" })] });
    await input.trigger("change");
    await flushPromises();
    expect(text()).toContain(NOT_SAVED_NOTE);
  });

  it("with every store working there is no notice", async () => {
    await open();
    await wrapper!.find("textarea").setValue("a cat");
    expect(text()).not.toContain(NOT_SAVED_NOTE);
  });
});

describe("blocked storage", () => {
  it("still loads models and generates, and says the images are lost on reload", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    const blocked = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    };
    vi.stubGlobal("localStorage", blocked);
    vi.stubGlobal("sessionStorage", blocked);
    const store = memoryStore();
    vi.mocked(store.put).mockRejectedValue(new Error("blocked"));
    vi.mocked(store.get).mockRejectedValue(new Error("blocked"));
    vi.mocked(store.remove).mockRejectedValue(new Error("blocked"));
    await open(store);
    await loadModels("rk-1");
    await pickModel("imagen-4");
    vi.mocked(api.createJob).mockResolvedValue({ id: "j1", status: "done", result_urls: ["/m/a.png"] });
    vi.mocked(api.getJob).mockResolvedValue({ id: "j1", status: "done", result_urls: ["/m/a.png"] });
    await wrapper!.find("textarea").setValue("a cat");
    await button("Generate").trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    expect(text()).toContain("Job j1 done");
    expect(api.createJob).toHaveBeenCalledWith("rk-1", "image", { model: "imagen-4", prompt: "a cat" });
    // attaching a file: the store refuses, the tab says so
    const input = wrapper!.find("#test-image-files");
    const file = new File(["x"], "a.png", { type: "image/png" });
    Object.defineProperty(input.element, "files", { value: [file] });
    await input.trigger("change");
    await flushPromises();
    expect(text()).toContain(NOT_SAVED_NOTE);
  });
});

describe("a running job", () => {
  async function startRunning() {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    await open();
    await loadModels();
    await pickModel("imagen-4");
    vi.mocked(api.createJob).mockResolvedValue({ id: "j1", status: "queued" });
    vi.mocked(api.getJob).mockResolvedValue({ id: "j1", status: "running" });
    await button("Generate").trigger("click");
    await vi.advanceTimersByTimeAsync(0);
  }

  it("keeps polling while the tab is not shown and shows the result when it comes back", async () => {
    await startRunning();
    expect(text()).toContain("Job j1 queued");
    await wrapper!.setProps({ show: false });
    expect(wrapper!.find(".test-tab").exists()).toBe(false);
    vi.mocked(api.getJob).mockResolvedValue({ id: "j1", status: "done", result_urls: ["/m/a.png"] });
    await vi.advanceTimersByTimeAsync(3000);
    expect(api.getJob).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(3000);
    expect(api.getJob).toHaveBeenCalledTimes(1);
    await wrapper!.setProps({ show: true });
    await flushPromises();
    expect(text()).toContain("Job j1 done");
    expect(wrapper!.find(".test-result__media img").attributes("src")).toBe("/m/a.png");
  });

  it("a sign-out ends the loop and drops the key and the models", async () => {
    await startRunning();
    useAuthStore().signOut();
    await vi.advanceTimersByTimeAsync(30000);
    expect(api.getJob).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("relay_test_key")).toBeNull();
    expect(wrapper!.findAll(".test-tab__model")).toHaveLength(0);
    // the loop is over, not failing on a missing key: the status line was reset and not overwritten
    expect(wrapper!.find(".test-result__status").text()).toBe("Idle");
  });

  it("a second Generate after the first ended draws only its own job", async () => {
    await startRunning();
    vi.mocked(api.getJob).mockResolvedValue({ id: "j1", status: "done" });
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    expect(text()).toContain("Job j1 done");
    vi.mocked(api.createJob).mockResolvedValue({ id: "j2", status: "queued" });
    await button("Generate").trigger("click");
    await vi.advanceTimersByTimeAsync(0);
    expect(text()).toContain("Job j2 queued");
  });

  it("disables Generate only while its own request is in flight, then enables it again", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    await open();
    await loadModels();
    await pickModel("imagen-4");
    let made!: (j: { id: string; status: string }) => void;
    vi.mocked(api.createJob).mockReturnValue(new Promise((resolve) => (made = resolve)));
    vi.mocked(api.getJob).mockResolvedValue({ id: "j1", status: "running" });
    await button("Generate").trigger("click");
    await vi.advanceTimersByTimeAsync(0);
    expect(button("Generate").attributes("disabled")).toBeDefined();
    made({ id: "j1", status: "queued" });
    await vi.advanceTimersByTimeAsync(0);
    expect(button("Generate").attributes("disabled")).toBeUndefined();
  });

  it("a second Generate while the first job is polling ends the first loop and starts a new one", async () => {
    await startRunning();
    vi.mocked(api.createJob).mockResolvedValue({ id: "j2", status: "queued" });
    vi.mocked(api.getJob).mockImplementation(async (_k, _kind, id) => ({ id, status: "running" }));
    await button("Generate").trigger("click");
    await vi.advanceTimersByTimeAsync(0);
    expect(api.createJob).toHaveBeenCalledTimes(2);
    expect(text()).toContain("Job j2 queued");
    await vi.advanceTimersByTimeAsync(3000);
    expect(vi.mocked(api.getJob).mock.calls.map((c) => c[2])).toEqual(["j2"]);
  });
});

describe("media", () => {
  it("draws a same-origin share link and shows any other link as text", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    await open();
    await loadModels();
    await pickModel("imagen-4");
    const done = {
      id: "j1",
      status: "done",
      result_urls: ["/m/ok.png", SCRIPT_URL, "https://evil.example/m/x.png"],
      images: ["<img src=x onerror=window.__pwned=1>"],
    };
    vi.mocked(api.createJob).mockResolvedValue(done);
    vi.mocked(api.getJob).mockResolvedValue(done);
    await button("Generate").trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    expect(wrapper!.findAll(".test-result__media img").map((i) => i.attributes("src"))).toEqual(["/m/ok.png"]);
    expect(wrapper!.findAll(".test-result__unsafe").map((p) => p.text())).toEqual([
      `link not shown: ${SCRIPT_URL}`,
      "link not shown: https://evil.example/m/x.png",
      "link not shown: <img src=x onerror=window.__pwned=1>",
    ]);
    expect(wrapper!.find(".test-result__media video").exists()).toBe(false);
  });

  it("draws a video link in a video element", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    await open();
    await loadModels();
    await pickModel("veo-3");
    const done = { id: "j1", status: "done", result_urls: ["/m/v.mp4"] };
    vi.mocked(api.createJob).mockResolvedValue(done);
    vi.mocked(api.getJob).mockResolvedValue(done);
    await button("Generate").trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    expect(wrapper!.find(".test-result__media video").attributes("src")).toBe("/m/v.mp4");
    expect(wrapper!.find(".test-result__media img").exists()).toBe(false);
  });
});
