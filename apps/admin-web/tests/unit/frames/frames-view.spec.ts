import { DOMWrapper, flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import * as api from "@/api/frames";
import * as shell from "@/api/shell";
import * as confirm from "@/components/shared/confirm-dialog";
import { useRouteLeaveGuard } from "@/components/shared/use-leave-guard";
import { forgetFramesMemory } from "@/components/frames/use-frames";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import HintTip from "@/components/shared/HintTip.vue";
import { useScopeStore } from "@/stores/scope";
import FramesView from "@/views/FramesView.vue";
import { deferred, frameInfo, framesList, version } from "./fixtures";

vi.mock("@/api/frames");
vi.mock("@/api/shell", () => ({ getModels: vi.fn(), getProvidersSummary: vi.fn() }));
vi.mock("@/components/shared/confirm-dialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("@/components/shared/use-leave-guard", async (original) => ({
  ...(await original<typeof import("@/components/shared/use-leave-guard")>()),
  useRouteLeaveGuard: vi.fn(),
}));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

// The text of a hostile frame: if it ever became markup, the image would run its handler.
const PLANTED = "<img src=x onerror=window.__pwned=1><b>planted</b>";

let wrapper: VueWrapper | null = null;
async function open() {
  setActivePinia(createPinia());
  wrapper = mount(FramesView, { global: { plugins: [] }, attachTo: document.body });
  await flushPromises();
  return wrapper;
}
const page = () => new DOMWrapper(document.body);
const buttons = (name: string) =>
  page()
    .findAll("button")
    .filter((b) => b.text() === name || b.attributes("aria-label") === name);
const button = (name: string) => buttons(name)[0]!;
const textarea = () => page().find("textarea");
const rows = () => page().findAll('[data-testid="frame-models"] tbody tr');
const usePhone = (on: boolean) =>
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: on && /max-width:\s*767/.test(q),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
// the model's own button in the table; a click on it puts that model's frame in the editor
const pick = async (model: string) => {
  await page().find(`button[aria-label="Edit frame for ${model}"]`).trigger("click");
  await flushPromises();
};

beforeEach(() => {
  forgetFramesMemory();
  vi.resetAllMocks();
  usePhone(false); // a desktop: happy-dom's own window would answer as a tablet
  vi.mocked(shell.getModels).mockResolvedValue({
    data: [
      { id: "m1", owned_by: "flow" },
      { id: "m2", owned_by: "flow" },
    ],
  });
  vi.mocked(api.listFrames).mockImplementation(async () => framesList());
  vi.mocked(api.getFrame).mockImplementation(async (t) =>
    frameInfo(t.target === "m2" ? null : t.target === "m1" ? "own1" : "D"),
  );
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

describe("the page", () => {
  it("opens on the first kind's first own frame with no drawer and no dialog", async () => {
    await open();
    expect(page().find('[data-testid="frame-editor"]').exists()).toBe(true);
    expect(page().find('[role="dialog"]').exists()).toBe(false);
    expect(page().find("h1").text()).toBe("Prompt frames");
    expect(textarea().element.value).toBe("own1");
    expect(page().find('[data-testid="frame-editor"] h2').text()).toBe("flow Video");
    expect(page().find(".fed__help").text()).toBe("Default v1 is followed by 1 of 2 models");
  });

  it("lists the provider with its kinds on the left: Video and Image, where each text comes from, the open kind marked", async () => {
    await open();
    const nav = page().find('[data-testid="frames-nav"]');
    expect(nav.find('[aria-expanded="true"]').text()).toContain("flow");
    expect(nav.find('[aria-expanded="true"]').text()).toContain("2 kinds");
    const kinds = nav.findAll(".fsn__kind");
    expect(kinds.map((k) => k.text().replace(/\s+/g, " "))).toEqual(["Videoown 1", "Imagedefault"]);
    expect(kinds.map((k) => k.attributes("aria-current"))).toEqual(["page", undefined]);
    // a kind is picked from the list
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("D"));
    await kinds[1]!.trigger("click");
    await flushPromises();
    expect(page().find('[data-testid="frame-editor"] h2').text()).toBe("flow Image");
    expect(page().findAll(".fsn__kind")[1]!.attributes("aria-current")).toBe("page");
  });

  it("draws no provider strip and no native select", async () => {
    await open();
    expect(page().find(".pstrip").exists()).toBe(false);
    expect(page().find("select").exists()).toBe(false);
  });

  it("says a provider without frames has none: the core frame applies", async () => {
    vi.mocked(api.listFrames).mockRejectedValue(
      new ApiError(404, { error: { code: "resource.not_found", message: "x" } }),
    );
    await open();
    expect(page().text()).toContain("This provider has no prompt frames: the core frame applies.");
    expect(page().find('[data-testid="frame-editor"]').exists()).toBe(false);
  });

  it("draws a failed read as a card with Retry, and Retry reads again", async () => {
    vi.mocked(api.listFrames).mockRejectedValueOnce(
      new ApiError(500, { error: { code: "server.error", message: "relay down" } }),
    );
    await open();
    expect(page().find('[data-testid="frames-failed"]').text()).toContain("relay down");
    expect(page().find('[data-testid="frame-editor"]').exists()).toBe(false);
    await button("Retry").trigger("click");
    await flushPromises();
    expect(page().find('[data-testid="frames-failed"]').exists()).toBe(false);
    expect(textarea().element.value).toBe("own1");
  });

  it("says when the provider is paused, with a link to Settings", async () => {
    vi.mocked(shell.getProvidersSummary).mockResolvedValue({
      day_tz: "UTC",
      day_start_ms: 0,
      hours: [],
      providers: [
        {
          provider: "flow",
          execution: "http",
          claims_enabled: false,
          running: 0,
          queued: 0,
          done_today: 0,
          failed_today: 0,
          accounts: { usable: 0, total: 0 },
          pools: [],
        },
      ],
    });
    await open();
    const banner = page().find('[data-testid="frames-paused"]');
    expect(banner.text()).toContain("flow is paused");
    expect(banner.find("a").attributes("href")).toBe("#/settings");
    expect(page().find(".fsn__note.is-paused").text()).toBe("paused");
  });

  it("says in the editor when the provider sends the text as written, and says nothing for one that does not", async () => {
    await open();
    expect(page().find('[data-testid="frame-verbatim"]').exists()).toBe(false);
    wrapper?.unmount();
    document.body.replaceChildren();
    vi.mocked(api.listFrames).mockImplementation(async () => ({ ...framesList(), verbatim: true }));
    await open();
    expect(page().find('[data-testid="frame-verbatim"]').text()).toBe(
      "flow sends the text as written: {{tracking_code}} adds nothing and the prompt keeps its line breaks.",
    );
  });
});

describe("the editor", () => {
  it("names the frame in 'Frame for': the default, then each model with where its text comes from", async () => {
    await open();
    const select = wrapper!.findComponent(FilterSelect);
    expect(select.props("label")).toBe("Frame for");
    expect(select.props("options")).toEqual([
      { value: "default", label: "Default · all video models" },
      { value: "m1", label: "m1 · own frame v2" },
      { value: "m2", label: "m2 · follows default" },
    ]);
    expect(page().find(".filter-select__value").text()).toBe("m1 · own frame v2");
    // picking another puts its frame in the editor
    select.vm.$emit("update:modelValue", "m2");
    await flushPromises();
    expect(textarea().element.value).toBe("effective text");
  });

  it("picks the markers out of the frame, and says whether each is in once", async () => {
    await open();
    expect(page().findAll(".fed__ph")).toHaveLength(0);
    await textarea().setValue("Scene: {{client_prompt}} {{tracking_code}}");
    expect(
      page()
        .findAll(".fed__ph")
        .map((e) => e.text()),
    ).toEqual(["{{client_prompt}}", "{{tracking_code}}"]);
    expect(page().find('[data-testid="frame-check"]').text()).toContain("once each");
    await textarea().setValue("Scene: {{client_prompt}} {{client_prompt}}");
    expect(page().find('[data-testid="frame-check"]').text()).toBe(
      "{{client_prompt}} is in 2 times, {{tracking_code}} is in 0 times",
    );
    expect(page().text()).toContain("42 / 1,000 characters");
  });

  it("the Insert buttons put a marker at the caret", async () => {
    await open();
    await textarea().setValue("ab");
    const box = textarea().element as HTMLTextAreaElement;
    box.setSelectionRange(1, 1);
    await button("Insert {{tracking_code}}").trigger("click");
    await flushPromises();
    expect(box.value).toBe("a{{tracking_code}}b");
  });

  it("a frame is one line: Enter adds nothing, a pasted line break becomes a space", async () => {
    await open();
    const enter = new KeyboardEvent("keydown", { key: "Enter", cancelable: true, bubbles: true });
    textarea().element.dispatchEvent(enter);
    expect(enter.defaultPrevented).toBe(true);
    await textarea().setValue("one\ntwo\r\nthree");
    expect(textarea().element.value).toBe("one two three");
  });

  it("the box shows the shape of a frame as its placeholder", async () => {
    await open();
    expect(textarea().attributes("placeholder")).toBe("{{client_prompt}} ... {{tracking_code}}");
  });

  it("the footer: Follow only for an own frame, Apply to all only for the default, Reset only for a default that differs", async () => {
    await open();
    const foot = () =>
      page()
        .find(".fed__foot")
        .findAll("button")
        .map((b) => [b.text() || b.attributes("aria-label"), b.attributes("disabled") !== undefined]);
    expect(foot()).toEqual([
      ["Save", true],
      ["Follow the default", false],
      ["Apply to all models of this kind", true],
      ["Preview", false],
    ]);
    expect(page().find(".fed__foot-note").text()).toBe("Save applies to jobs created from now on.");
    await pick("m2");
    expect(foot()[1]).toEqual(["Follow the default", true]);
    expect(page().text()).toContain("This model follows the default.");
    wrapper!.findComponent(FilterSelect).vm.$emit("update:modelValue", "default");
    await flushPromises();
    expect(foot()).toEqual([
      ["Save", true],
      ["Follow the default", true],
      ["Apply to all models of this kind", false],
      ["Preview", false],
      ["Reset to the shipped default", false],
    ]);
    expect(page().text()).toContain("differs from the shipped default");
  });

  it("Save is disabled until the draft differs from the loaded text, and 'unsaved' says so", async () => {
    await open();
    expect(button("Save").attributes("disabled")).toBeDefined();
    expect(page().find('[data-testid="frame-unsaved"]').exists()).toBe(false);
    await textarea().setValue("own1 changed");
    expect(button("Save").attributes("disabled")).toBeUndefined();
    expect(page().find('[data-testid="frame-unsaved"]').text()).toBe("unsaved");
    await textarea().setValue("own1");
    expect(button("Save").attributes("disabled")).toBeDefined();
    expect(page().find('[data-testid="frame-unsaved"]').exists()).toBe(false);
  });

  it("shows a refused save beside the editor as an alert with the field", async () => {
    await open();
    await textarea().setValue("no placeholders");
    vi.mocked(api.putFrame).mockRejectedValue(
      new ApiError(422, {
        error: { code: "input.invalid", message: "the frame needs the customer prompt", param: "text" },
      }),
    );
    await button("Save").trigger("click");
    await flushPromises();
    const alert = page().find('.fed [role="alert"]');
    expect(alert.text()).toBe("the frame needs the customer prompt (text)");
    expect(textarea().element.value).toBe("no placeholders");
    expect(textarea().attributes("aria-invalid")).toBe("true");
    expect(textarea().attributes("aria-describedby")).toBe(alert.attributes("id"));
    expect(page().find(".fed__box").classes()).toContain("is-invalid");
  });

  it("marks no box invalid while nothing was refused", async () => {
    await open();
    expect(textarea().attributes("aria-invalid")).toBeUndefined();
    expect(textarea().attributes("aria-describedby")).toBeUndefined();
    expect(page().find(".fed__box").classes()).not.toContain("is-invalid");
  });

  it("shows save warnings, the preview and the history as text", async () => {
    await open();
    await textarea().setValue("changed");
    vi.mocked(api.putFrame).mockResolvedValue({
      version: version(9, "changed"),
      unchanged: false,
      warnings: ["No tracking code."],
    });
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("changed"));
    await button("Save").trigger("click");
    await flushPromises();
    expect(page().find('[data-testid="frame-warnings"]').text()).toBe("No tracking code.");
    vi.mocked(api.previewFrame).mockResolvedValue({
      text: "RENDERED a cat on a sofa [4060]",
      length: 8,
      frame_id: null,
      source: "draft",
      warnings: [],
    });
    await button("Preview").trigger("click");
    await flushPromises();
    expect(page().find('[data-testid="frame-preview"]').text()).toBe("RENDERED a cat on a sofa [4060]");
    // the sample prompt and the tracking code are picked out
    expect(
      page()
        .findAll(".fed__fill")
        .map((e) => e.text()),
    ).toEqual(["a cat on a sofa", "[4060]"]);
    expect(page().text()).toContain("8 characters");
    expect(page().text()).toContain("what flow receives");
    const hints = wrapper!.findAllComponents(HintTip);
    expect(hints.map((h) => h.props("text"))).toContain("Shown with the sample tracking code (draft frame).");
    expect(page().findAll('[data-testid="frame-history"] li')).toHaveLength(2);
    // the history runs newest first
    expect(
      page()
        .findAll('[data-testid="frame-history"] li')
        .map((li) => li.attributes("data-version")),
    ).toEqual(["5", "4"]);
  });

  it("shows the sample tracking code, and none for a provider that sends the text as written", async () => {
    await open();
    expect((page().find("input[readonly]").element as HTMLInputElement).value).toBe("[4060]");
    wrapper?.unmount();
    document.body.replaceChildren();
    vi.mocked(api.listFrames).mockImplementation(async () => ({ ...framesList(), verbatim: true }));
    await open();
    expect((page().find("input[readonly]").element as HTMLInputElement).value).toBe("");
  });

  it("frame text with markup is text: in the box, the history, the preview and the warnings", async () => {
    vi.mocked(api.getFrame).mockResolvedValue({
      current: version(5, PLANTED),
      effective: version(5, PLANTED),
      history: [version(5, PLANTED)],
    });
    vi.mocked(api.previewFrame).mockResolvedValue({
      text: PLANTED,
      length: 9,
      frame_id: null,
      source: "draft",
      warnings: [PLANTED],
    });
    await open();
    await button("Preview").trigger("click");
    await flushPromises();
    expect(textarea().element.value).toBe(PLANTED);
    expect(page().find('[data-testid="frame-preview"]').text()).toBe(PLANTED);
    expect(page().find('[data-testid="frame-warnings"]').text()).toBe(PLANTED);
    expect(page().find(".fed__mirror").text()).toContain(PLANTED);
    expect(page().find("img").exists()).toBe(false);
    expect(page().find("b").exists()).toBe(false);
  });

  it("during a load: Save and the other controls are off and the box is read-only, the pickers stay on", async () => {
    await open();
    await textarea().setValue("typed");
    const slow = deferred<api.FrameInfo>();
    vi.mocked(api.getFrame).mockReturnValue(slow.promise);
    vi.mocked(confirm.confirmDialog).mockResolvedValueOnce(true);
    await pick("m2");
    expect(textarea().attributes("readonly")).toBeDefined();
    expect(button("Save").attributes("disabled")).toBeDefined();
    expect(button("Follow the default").attributes("disabled")).toBeDefined();
    expect(button("Preview").attributes("disabled")).toBeDefined();
    expect(page().find('input[type="checkbox"]').attributes("disabled")).toBeDefined();
    expect(page().find('button[aria-label="Edit frame for m2"]').attributes("disabled")).toBeUndefined();
    slow.resolve(frameInfo(null));
    await flushPromises();
    expect(textarea().attributes("readonly")).toBeUndefined();
  });

  it("during a write: everything is off, the pickers too", async () => {
    await open();
    await textarea().setValue("typed");
    const slow = deferred<api.SavedFrame>();
    vi.mocked(api.putFrame).mockReturnValue(slow.promise);
    await button("Save").trigger("click");
    expect(textarea().attributes("readonly")).toBeDefined();
    expect(page().find('button[aria-label="Edit frame for m2"]').attributes("disabled")).toBeDefined();
    expect(page().find(".fsn__kind").attributes("disabled")).toBeDefined();
    slow.resolve({ version: version(9, "typed"), unchanged: false, warnings: [] });
    await flushPromises();
    expect(page().find('button[aria-label="Edit frame for m2"]').attributes("disabled")).toBeUndefined();
  });

  it("picking another frame over a typed draft asks first, and Keep editing leaves it as typed", async () => {
    await open();
    await textarea().setValue("typed");
    vi.mocked(confirm.confirmDialog).mockResolvedValueOnce(false);
    await pick("m2");
    expect(confirm.confirmDialog).toHaveBeenCalledTimes(1);
    expect(textarea().element.value).toBe("typed");
    vi.mocked(confirm.confirmDialog).mockResolvedValueOnce(true);
    await pick("m2");
    expect(textarea().element.value).toBe("effective text");
  });

  it("leaving the page asks only for a typed draft, and is refused during a write", async () => {
    await open();
    const [isDirty, isBlocked] = vi.mocked(useRouteLeaveGuard).mock.calls[0]!;
    expect(isDirty()).toBe(false);
    await textarea().setValue("typed");
    expect(isDirty()).toBe(true);
    const slow = deferred<api.SavedFrame>();
    vi.mocked(api.putFrame).mockReturnValue(slow.promise);
    expect(isBlocked?.()).toBe(false);
    await button("Save").trigger("click");
    expect(isBlocked?.()).toBe(true);
    slow.resolve({ version: version(9, "typed"), unchanged: false, warnings: [] });
    await flushPromises();
    expect(isBlocked?.()).toBe(false);
  });

  it("a reload that finds no such frame says so in the card and keeps the draft", async () => {
    await open();
    await textarea().setValue("typed");
    vi.mocked(api.listFrames).mockImplementation(async () => ({ ...framesList(), kinds: [] }));
    vi.mocked(api.lockFrame).mockResolvedValue({});
    await page().find('input[aria-label="Keep own frame of m1"]').setValue(true);
    await flushPromises();
    expect(page().find('[data-testid="frame-missing"]').text()).toContain("not listed any more");
  });
});

describe("the models of the kind", () => {
  it("lists each model with its source, its version and Keep own; the open one is on the head tint", async () => {
    await open();
    const card = page().find('[data-testid="frame-models"]');
    expect(card.find("h3").text()).toBe("Models of this kind");
    expect(card.findAll("thead th").map((th) => th.text())).toEqual(["Model", "Source", "Version", "Keep own"]);
    expect(rows().map((r) => r.findAll("td").map((td) => td.text()))).toEqual([
      ["m1", "own", "v2", ""],
      ["m2", "default", "v1", ""],
    ]);
    expect(rows().map((r) => r.classes().includes("is-selected"))).toEqual([true, false]);
    await pick("m2");
    expect(rows().map((r) => r.classes().includes("is-selected"))).toEqual([false, true]);
  });

  it("writes a model's name under its id only when it differs, and marks a model the admin paused", async () => {
    vi.mocked(api.listFrames).mockImplementation(async () => {
      const list = framesList();
      list.kinds[0]!.models[0]!.name = "Model One";
      return list;
    });
    await open();
    useScopeStore().models = [
      { id: "m1", owned_by: "flow" },
      { id: "m2", owned_by: "flow", disabled: true },
    ];
    await flushPromises();
    expect(rows()[0]!.find(".fm__name").text()).toBe("Model One");
    expect(rows().map((r) => r.find(".fm__paused").exists())).toEqual([false, true]);
  });

  it("toggles the lock on Keep own and opens nothing", async () => {
    vi.mocked(api.lockFrame).mockResolvedValue({});
    await open();
    const box = rows()[1]!.find('input[type="checkbox"]');
    await box.setValue(true);
    await flushPromises();
    expect(api.lockFrame).toHaveBeenCalledWith({ provider: "flow", kind: "video", target: "m2" }, true);
    expect(textarea().element.value).toBe("own1");
  });

  it("a locked model says when and by whom on its checkbox's tooltip", async () => {
    vi.mocked(api.listFrames).mockImplementation(async () => {
      const list = framesList();
      list.kinds[0]!.models[0] = {
        ...list.kinds[0]!.models[0]!,
        locked: true,
        lock: {
          locked: true,
          created_ms: new Date(2026, 9, 22, 18, 40).getTime(),
          created_by: "admin",
          created_by_id: null,
        },
      };
      return list;
    });
    await open();
    expect(rows()[0]!.find("label").attributes("title")).toBe("Locked 22 Oct by admin");
  });

  it("says a kind with no model has none, and shows flat bars while the first read runs", async () => {
    const slow = deferred<api.FramesList>();
    vi.mocked(api.listFrames).mockReturnValue(slow.promise);
    await open();
    expect(page().findAll(".fm__skel li")).toHaveLength(5);
    slow.resolve(framesList());
    await flushPromises();
    expect(page().find(".fm__skel").exists()).toBe(false);
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("D"));
    await page().findAll(".fsn__kind")[1]!.trigger("click");
    await flushPromises();
    expect(page().find('[data-testid="frame-models"]').text()).toContain("No models");
  });
});

describe("the history", () => {
  it("is titled by the open frame, marks the current version and rolls back an older one by an icon button", async () => {
    await open();
    const card = page().find('[data-testid="frame-history-card"]');
    expect(card.find("h3").text()).toBe("History · m1");
    const items = card.findAll("li");
    expect(items[0]!.text()).toContain("v5");
    expect(items[0]!.find(".fh__cur").classes()).not.toContain("is-hidden");
    expect(items[1]!.find(".fh__cur").classes()).toContain("is-hidden");
    expect(items[0]!.find("button").exists()).toBe(false);
    expect(items[0]!.text()).toContain("· owner");
    vi.mocked(api.rollbackFrame).mockResolvedValue({ version: version(9, "x"), unchanged: false, warnings: [] });
    await items[1]!.find('button[aria-label="Roll back to version 4"]').trigger("click");
    await flushPromises();
    expect(api.rollbackFrame).toHaveBeenCalledWith({ provider: "flow", kind: "video", target: "m1" }, 4);
  });

  it("a model with a lock history has the History heading as a focusable note with the whole history", async () => {
    const lock = { locked: true, created_ms: 1_700_000_000_000, created_by: "owner", created_by_id: null };
    vi.mocked(api.getFrame).mockImplementation(async () => frameInfo("own1", { lock_history: [lock] }));
    await open();
    const tip = page().find('[data-testid="frame-history-card"] h3').findComponent(HintTip);
    expect(tip.props("text")).toMatch(/^Lock history: locked by owner /);
    expect(tip.find("span").attributes("tabindex")).toBe("0");
    vi.mocked(api.getFrame).mockImplementation(async () => frameInfo("own1", { lock_history: [] }));
    await pick("m2");
    expect(page().find('[data-testid="frame-history-card"] h3').findComponent(HintTip).exists()).toBe(false);
  });
});

describe("a phone", () => {
  it("has the provider chips and the kinds as a segmented control instead of the list, and the Save bar above the tab bar", async () => {
    usePhone(true);
    await open();
    expect(page().find('[data-testid="frames-nav"]').exists()).toBe(false);
    // the title is in the top bar: the page keeps a heading for a screen reader only
    expect(page().find("h1").classes()).toContain("frames__sr");
    expect(page().find(".page-header").exists()).toBe(false);
    const picker = page().find('[data-testid="frames-picker"]');
    expect(picker.findAll(".fpk__chip").map((c) => c.text())).toEqual(["flow"]);
    expect(picker.findAll(".fpk__opt").map((c) => c.text())).toEqual(["Video", "Image"]);
    expect(picker.findAll(".fpk__opt").map((c) => c.attributes("aria-pressed"))).toEqual(["true", "false"]);
    const bar = page().find(".fed__bar");
    expect(bar.findAll("button").map((b) => b.text())).toEqual(["Follow the default", "Save"]);
    expect(page().find(".fed__foot").exists()).toBe(false);
    expect(buttons("Save")).toHaveLength(1);
    expect(
      page()
        .findAll(".fed__phone-actions button")
        .map((b) => b.text() || b.attributes("aria-label")),
    ).toEqual(["Apply to all models of this kind", "Preview"]);
  });

  it("draws the models as a list, a block per model with a Keep own box", async () => {
    usePhone(true);
    await open();
    const blocks = page().findAll(".fm__list li");
    expect(blocks).toHaveLength(2);
    expect(blocks[0]!.text()).toContain("m1");
    expect(blocks[0]!.text()).toContain("Keep own");
    expect(page().find('[data-testid="frame-models"] table').exists()).toBe(false);
  });
});

describe("a tablet", () => {
  it("has the chips and the segmented kinds with the own count, and the text 'Frame for' as a field", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: /min-width:\s*768px.*max-width:\s*1279/.test(q),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    await open();
    expect(page().find(".page-header").exists()).toBe(false);
    expect(
      page()
        .findAll(".fpk__opt")
        .map((c) => c.text()),
    ).toEqual(["Video · own 1", "Image"]);
    expect(page().find(".fed__stack-field").exists()).toBe(true);
    expect(page().find(".fed__help").text()).toBe("default v1 is followed by 1 of 2 models");
  });
});
