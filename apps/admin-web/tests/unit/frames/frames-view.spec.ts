import { DOMWrapper, flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import * as api from "@/api/frames";
import * as shell from "@/api/shell";
import * as confirm from "@/components/shared/confirm-dialog";
import { useRouteLeaveGuard } from "@/components/shared/use-leave-guard";
import { forgetFramesMemory } from "@/components/frames/use-frames";
import HintTip from "@/components/shared/HintTip.vue";
import FramesView from "@/views/FramesView.vue";
import { deferred, frameInfo, framesList, version } from "./fixtures";

vi.mock("@/api/frames");
vi.mock("@/api/shell", () => ({ getModels: vi.fn() }));
vi.mock("@/components/shared/confirm-dialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("@/components/shared/use-leave-guard", async (original) => ({
  ...(await original<typeof import("@/components/shared/use-leave-guard")>()),
  useRouteLeaveGuard: vi.fn(),
}));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

// The text of a hostile frame: if it ever became markup, the image would run its handler.
const PLANTED = "<img src=x onerror=window.__pwned=1><b>planted</b>";

let wrapper: VueWrapper | null = null;
// `leaves`: the drawer's own leave transition runs (stubbed to nothing by default), and `closed` follows it
async function open(leaves = false) {
  setActivePinia(createPinia());
  wrapper = mount(FramesView, {
    global: { plugins: [], stubs: leaves ? { transition: false } : {} },
    attachTo: document.body,
  });
  await flushPromises();
  return wrapper;
}
// the editor is a drawer teleported to <body>: the whole page is looked at, not only the view's own root
const page = () => new DOMWrapper(document.body);
const buttons = (name: string) =>
  page()
    .findAll("button")
    .filter((b) => b.text() === name || b.attributes("aria-label") === name);
const button = (name: string) => buttons(name)[0]!;
const textarea = () => page().find("textarea");
// the drawn select: a combobox button named by its label word
const providerSelect = () => page().find('button[role="combobox"]');

beforeEach(() => {
  forgetFramesMemory();
  vi.resetAllMocks();
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
});

describe("the lock history note", () => {
  const lock = (locked: boolean) => ({
    locked,
    created_ms: 1_700_000_000_000,
    created_by: "owner",
    created_by_id: null,
  });

  it("a model with a lock history has the History heading as a focusable note with the whole history; one without has none", async () => {
    vi.mocked(api.getFrame).mockImplementation(async () => frameInfo("own1", { lock_history: [lock(true)] }));
    await open();
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
    const heading = page()
      .findAll("h3")
      .find((h) => h.text() === "History")!;
    const tip = heading.findComponent(HintTip);
    expect(tip.exists()).toBe(true);
    expect(tip.props("text")).toMatch(/^Lock history: locked by owner /);
    expect(tip.find("span").attributes("tabindex")).toBe("0");
    expect(heading.attributes("title")).toBeUndefined();
    vi.mocked(api.getFrame).mockImplementation(async () => frameInfo("own1", { lock_history: [] }));
    await page().find('button[aria-label="Edit frame for m2"]').trigger("click");
    await flushPromises();
    const plain = page()
      .findAll("h3")
      .find((h) => h.text() === "History")!;
    expect(plain.findComponent(HintTip).exists()).toBe(false);
  });
});

describe("the view", () => {
  it("says in the editor when the provider sends the text as written, and says nothing for one that does not", async () => {
    await open();
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
    expect(page().find('[data-testid="frame-verbatim"]').exists()).toBe(false);
    wrapper?.unmount();
    document.body.replaceChildren();
    vi.mocked(api.listFrames).mockImplementation(async () => ({ ...framesList(), verbatim: true }));
    await open();
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
    expect(page().find('[data-testid="frame-verbatim"]').text()).toBe(
      "flow sends the text as written: {{tracking_code}} adds nothing and the prompt keeps its line breaks.",
    );
  });

  it("draws a section per kind with the default line, the shipped-differs flag and the model table", async () => {
    await open();
    const sections = page().findAll("[data-frame-kind]");
    expect(sections.map((s) => s.attributes("data-frame-kind"))).toEqual(["video", "image"]);
    const video = sections[0]!;
    expect(video.text()).toContain("Default: v1");
    expect(video.text()).toContain("differs from the shipped default");
    expect(video.text()).toContain("Reset to the shipped default");
    expect(video.text()).toContain("m1");
    expect(video.text()).toContain("v2");
    expect(video.text()).toContain("Keep own");
    // a frame whose lock was never toggled says nothing about it
    expect(video.text()).not.toContain("never toggled");
    expect(sections[1]!.text()).toContain("Default: no version");
    expect(sections[1]!.text()).toContain("No models");
    expect(sections[1]!.text()).not.toContain("Reset to the shipped default");
    expect(page().text()).not.toContain("Pick a model or a default");
    expect(page().find(".frame-editor").exists()).toBe(false);
  });

  it("draws video before image whatever order the server lists them in", async () => {
    vi.mocked(api.listFrames).mockImplementation(async () => {
      const list = framesList();
      return { ...list, kinds: [...list.kinds].reverse() };
    });
    await open();
    expect(
      page()
        .findAll("[data-frame-kind]")
        .map((s) => s.attributes("data-frame-kind")),
    ).toEqual(["video", "image"]);
  });

  it("says a provider without frames has none: the core frame applies", async () => {
    vi.mocked(api.listFrames).mockRejectedValue(
      new ApiError(404, { error: { code: "resource.not_found", message: "x" } }),
    );
    await open();
    expect(page().text()).toContain("This provider has no prompt frames: the core frame applies.");
    expect(page().findAll("[data-frame-kind]")).toHaveLength(0);
  });

  it("opens the editor of a model: help text, Follow only for an own frame, no Apply to all", async () => {
    await open();
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
    const editor = page().find(".frame-editor");
    expect(page().find(".detail-drawer__title").text()).toBe("Editing video / m1");
    expect(editor.text()).toContain("{{client_prompt}}");
    expect(editor.text()).toContain("{{tracking_code}}");
    expect(editor.text()).toContain("at most 1,000 characters");
    expect(editor.text()).not.toContain("This model follows the default.");
    expect(buttons("Follow the default")).toHaveLength(1);
    expect(buttons("Apply to all models of this kind")).toHaveLength(0);
    expect(textarea().element.value).toBe("own1");
  });

  it("the frame box shows the shape of a frame as its placeholder, and the lock line sits on the checkbox cell", async () => {
    vi.mocked(api.listFrames).mockImplementation(async () => {
      const list = framesList();
      list.kinds[0]!.models[0] = {
        ...list.kinds[0]!.models[0]!,
        locked: true,
        lock: {
          locked: true,
          created_ms: new Date(2026, 9, 14, 18, 40).getTime(),
          created_by: "admin",
          created_by_id: null,
        },
      };
      return list;
    });
    await open();
    const cell = page().find('input[aria-label="Keep own frame of m1"]').element.closest("td")!;
    expect(cell.textContent).toContain("Keep own");
    expect(cell.textContent).toContain("locked 14/10 by admin");
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
    expect(textarea().attributes("placeholder")).toBe("{{client_prompt}} ... {{tracking_code}}");
  });

  it("a model that follows the default has the note, no Follow button, and the default's text in the box", async () => {
    await open();
    await page().find('button[aria-label="Edit frame for m2"]').trigger("click");
    await flushPromises();
    expect(page().find(".frame-editor").text()).toContain("This model follows the default.");
    expect(buttons("Follow the default")).toHaveLength(0);
    expect(textarea().element.value).toBe("effective text");
  });

  it("the default editor has Apply to all and no Follow", async () => {
    await open();
    await button("Edit the default").trigger("click");
    await flushPromises();
    expect(page().find(".detail-drawer__title").text()).toBe("Editing video / default");
    expect(buttons("Apply to all models of this kind")).toHaveLength(1);
    expect(buttons("Follow the default")).toHaveLength(0);
  });

  it("Save is disabled until the draft differs from the loaded text", async () => {
    await open();
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
    expect(button("Save").attributes("disabled")).toBeDefined();
    await textarea().setValue("own1 changed");
    expect(button("Save").attributes("disabled")).toBeUndefined();
    await textarea().setValue("own1");
    expect(button("Save").attributes("disabled")).toBeDefined();
  });

  it("shows a refused save beside the editor as an alert with the field", async () => {
    await open();
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
    await textarea().setValue("no placeholders");
    vi.mocked(api.putFrame).mockRejectedValue(
      new ApiError(422, {
        error: { code: "input.invalid", message: "the frame needs the customer prompt", param: "text" },
      }),
    );
    await button("Save").trigger("click");
    await flushPromises();
    const alert = page().find('.frame-editor [role="alert"]');
    expect(alert.text()).toBe("the frame needs the customer prompt (text)");
    expect(textarea().element.value).toBe("no placeholders");
    // drawn as a field error: the box it is about is marked invalid and described by it
    expect(textarea().attributes("aria-invalid")).toBe("true");
    expect(textarea().classes()).toContain("is-invalid");
    expect(textarea().attributes("aria-describedby")).toBe(alert.attributes("id"));
  });

  it("marks no box invalid while nothing was refused", async () => {
    await open();
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
    expect(textarea().attributes("aria-invalid")).toBeUndefined();
    expect(textarea().attributes("aria-describedby")).toBeUndefined();
    expect(textarea().classes()).not.toContain("is-invalid");
  });

  it("titles each kind's section as drawn: Video, Image", async () => {
    await open();
    expect(
      page()
        .findAll("section[data-frame-kind] h2")
        .map((h) => h.text()),
    ).toEqual(["Video", "Image"]);
  });

  it("shows save warnings, the preview and the history as text", async () => {
    await open();
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
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
      text: "RENDERED",
      length: 8,
      frame_id: null,
      source: "draft",
      warnings: [],
    });
    await button("Preview").trigger("click");
    await flushPromises();
    expect(page().find('[data-testid="frame-preview"]').text()).toBe("RENDERED");
    expect(page().text()).toContain("8 characters");
    // the note is a HintTip (focusable, on the menu surface), not a native title
    const hints = wrapper!.findAllComponents(HintTip);
    expect(hints.map((h) => h.props("text"))).toContain("Shown with the sample tracking code (draft frame).");
    expect(
      page().find('[data-testid="frame-preview"]').element.previousElementSibling!.querySelector("[title]"),
    ).toBeNull();
    expect(page().findAll('[data-testid="frame-history"] li')).toHaveLength(2);
    // the history runs newest first
    expect(
      page()
        .findAll('[data-testid="frame-history"] li')
        .map((li) => li.attributes("data-version")),
    ).toEqual(["5", "4"]);
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
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
    await button("Preview").trigger("click");
    await flushPromises();
    expect(textarea().element.value).toBe(PLANTED);
    expect(page().find('[data-testid="frame-preview"]').text()).toBe(PLANTED);
    expect(page().find('[data-testid="frame-warnings"]').text()).toBe(PLANTED);
    expect(page().find("img").exists()).toBe(false);
    expect(page().find("b").exists()).toBe(false);
  });

  it("during a load: Save and the other controls are off, the box is read-only, Edit and the provider stay on", async () => {
    await open();
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
    await textarea().setValue("typed");
    const slow = deferred<api.FrameInfo>();
    vi.mocked(api.getFrame).mockReturnValue(slow.promise);
    await page().find('button[aria-label="Edit frame for m2"]').trigger("click");
    expect(textarea().attributes("readonly")).toBeDefined();
    expect(button("Save").attributes("disabled")).toBeDefined();
    expect(button("Follow the default").attributes("disabled")).toBeDefined();
    expect(button("Preview").attributes("disabled")).toBeDefined();
    expect(button("Reset to the shipped default").attributes("disabled")).toBeDefined();
    expect(page().find('input[type="checkbox"]').attributes("disabled")).toBeDefined();
    expect(page().find('button[aria-label="Edit frame for m2"]').attributes("disabled")).toBeUndefined();
    expect(button("Edit the default").attributes("disabled")).toBeUndefined();
    expect(providerSelect().attributes("disabled")).toBeUndefined();
    slow.resolve(frameInfo(null));
    await flushPromises();
    expect(textarea().attributes("readonly")).toBeUndefined();
  });

  it("during a write: everything is off, Edit buttons and the provider select too", async () => {
    await open();
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    await flushPromises();
    await textarea().setValue("typed");
    const slow = deferred<api.SavedFrame>();
    vi.mocked(api.putFrame).mockReturnValue(slow.promise);
    await button("Save").trigger("click");
    expect(textarea().attributes("readonly")).toBeDefined();
    expect(page().find('button[aria-label="Edit frame for m2"]').attributes("disabled")).toBeDefined();
    expect(button("Edit the default").attributes("disabled")).toBeDefined();
    expect(providerSelect().attributes("disabled")).toBeDefined();
    slow.resolve({ version: version(9, "typed"), unchanged: false, warnings: [] });
    await flushPromises();
    expect(page().find('button[aria-label="Edit frame for m2"]').attributes("disabled")).toBeUndefined();
  });
});

describe("the editor drawer", () => {
  const drawer = () => wrapper!.findComponent({ name: "ElDrawer" });
  const edit = async (model = "m1") => {
    await page().find(`button[aria-label="Edit frame for ${model}"]`).trigger("click");
    await flushPromises();
  };

  it("opens with no drawer, even when the last visit left an editor open", async () => {
    await open();
    await edit();
    expect(drawer().props("modelValue")).toBe(true);
    wrapper!.unmount();
    document.body.replaceChildren();
    await open();
    expect(drawer().props("modelValue")).toBe(false);
    expect(page().find(".frame-editor").exists()).toBe(false);
    // the provider that was chosen stays
    expect(providerSelect().text()).toContain("flow");
  });

  it("a row's Edit and Edit the default each open it, named by its heading", async () => {
    await open();
    await edit("m2");
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(document.getElementById(dialog.getAttribute("aria-labelledby")!)?.textContent).toBe("Editing video / m2");
    await button("Edit the default").trigger("click");
    await flushPromises();
    expect(page().find(".detail-drawer__title").text()).toBe("Editing video / default");
  });

  it("opens at once on Edit and says it is loading while the read runs", async () => {
    await open();
    const slow = deferred<api.FrameInfo>();
    vi.mocked(api.getFrame).mockReturnValue(slow.promise);
    await page().find('button[aria-label="Edit frame for m1"]').trigger("click");
    expect(drawer().props("modelValue")).toBe(true);
    expect(page().find('[data-testid="frame-missing"]').text()).toContain("Loading");
    expect(page().find(".frame-editor").exists()).toBe(false);
    slow.resolve(frameInfo("own1"));
    await flushPromises();
    expect(page().find(".frame-editor").exists()).toBe(true);
  });

  it("holds Save, Follow, Apply to all and Preview in the footer, not in the body", async () => {
    await open();
    await edit();
    const foot = page().find(".detail-drawer__foot");
    expect(foot.findAll("button").map((b) => b.text())).toEqual(["Save", "Follow the default", "Preview"]);
    expect(page().find(".detail-drawer__body").find("button.btn--primary").exists()).toBe(false);
    await button("Edit the default").trigger("click");
    await flushPromises();
    expect(
      page()
        .find(".detail-drawer__foot")
        .findAll("button")
        .map((b) => b.text()),
    ).toEqual(["Save", "Apply to all models of this kind", "Preview"]);
  });

  // a click on the scrim acts as Escape does
  const clickScrim = async () => {
    const scrim = document.body.querySelector(".el-overlay.is-drawer")!;
    for (const type of ["mousedown", "mouseup", "click"]) scrim.dispatchEvent(new MouseEvent(type, { bubbles: true }));
    await flushPromises();
  };

  it("a click on the scrim closes a clean editor, asks over a typed draft, and does nothing during a write", async () => {
    await open();
    await edit();
    await clickScrim();
    expect(confirm.confirmDialog).not.toHaveBeenCalled();
    expect(drawer().props("modelValue")).toBe(false);
    await edit();
    await textarea().setValue("typed");
    vi.mocked(confirm.confirmDialog).mockResolvedValueOnce(false);
    await clickScrim();
    expect(confirm.confirmDialog).toHaveBeenCalledTimes(1);
    expect(drawer().props("modelValue")).toBe(true);
    expect(textarea().element.value).toBe("typed");
    const slow = deferred<api.SavedFrame>();
    vi.mocked(api.putFrame).mockReturnValue(slow.promise);
    await button("Save").trigger("click");
    await clickScrim();
    expect(confirm.confirmDialog).toHaveBeenCalledTimes(1);
    expect(drawer().props("modelValue")).toBe(true);
    slow.resolve({ version: version(9, "typed"), unchanged: false, warnings: [] });
    await flushPromises();
  });

  describe("focus after the drawer is gone", () => {
    const scrollArea = () => {
      const wrap = document.createElement("div");
      wrap.className = "app-scroll__wrap";
      wrap.tabIndex = -1;
      document.body.append(wrap);
      return wrap;
    };
    const openFrom = async () => {
      const wrap = scrollArea();
      await open(true);
      const opener = document.body.querySelector<HTMLElement>('button[aria-label="Edit frame for m1"]')!;
      opener.focus();
      opener.click();
      await flushPromises();
      return { wrap, opener };
    };
    const closeIt = async () => {
      document.body.querySelector<HTMLElement>('button[aria-label="Close"]')!.click();
      await flushPromises();
      await new Promise((ok) => setTimeout(ok, 0));
      await flushPromises();
    };

    it("goes to the page's scroll area when the button that opened the editor is gone", async () => {
      const { wrap, opener } = await openFrom();
      opener.remove();
      await closeIt();
      expect(document.activeElement).toBe(wrap);
    });

    it("is left alone while the button that opened the editor is still on the page", async () => {
      const { wrap, opener } = await openFrom();
      await closeIt();
      expect(opener.isConnected).toBe(true);
      expect(document.activeElement).not.toBe(wrap);
    });
  });

  it("closes at once when nothing was typed and drops the editor", async () => {
    await open();
    await edit();
    await page().find('button[aria-label="Close"]').trigger("click");
    await flushPromises();
    expect(confirm.confirmDialog).not.toHaveBeenCalled();
    expect(drawer().props("modelValue")).toBe(false);
    expect(page().find(".frame-editor").exists()).toBe(false);
  });

  it("asks before it closes over a typed draft, and keeps it on Keep editing", async () => {
    await open();
    await edit();
    await textarea().setValue("typed");
    vi.mocked(confirm.confirmDialog).mockResolvedValueOnce(false);
    await page().find('button[aria-label="Close"]').trigger("click");
    await flushPromises();
    expect(confirm.confirmDialog).toHaveBeenCalledTimes(1);
    expect(drawer().props("modelValue")).toBe(true);
    expect(textarea().element.value).toBe("typed");
    vi.mocked(confirm.confirmDialog).mockResolvedValueOnce(true);
    await page().find('button[aria-label="Close"]').trigger("click");
    await flushPromises();
    expect(drawer().props("modelValue")).toBe(false);
  });

  it("a reload that finds no such frame keeps a drawer holding a draft, and says so inside", async () => {
    await open();
    await edit();
    await textarea().setValue("typed");
    vi.mocked(api.listFrames).mockImplementation(async () => ({ ...framesList(), kinds: [] }));
    vi.mocked(api.lockFrame).mockResolvedValue({});
    await page().find('input[aria-label="Keep own frame of m1"]').setValue(true);
    await flushPromises();
    expect(drawer().props("modelValue")).toBe(true);
    expect(page().find('[data-testid="frame-missing"]').text()).toContain("not listed any more");
  });

  it("Save keeps the drawer open and redraws its history", async () => {
    await open();
    await edit();
    await textarea().setValue("changed");
    vi.mocked(api.putFrame).mockResolvedValue({ version: version(9, "changed"), unchanged: false, warnings: [] });
    vi.mocked(api.getFrame).mockResolvedValue({
      current: version(9, "changed"),
      effective: version(9, "changed"),
      history: [version(9, "changed"), version(5, "own1"), version(4, "older")],
    });
    await button("Save").trigger("click");
    await flushPromises();
    expect(drawer().props("modelValue")).toBe(true);
    expect(page().findAll('[data-testid="frame-history"] li')).toHaveLength(3);
  });

  it("no way out works while a write runs", async () => {
    await open();
    await edit();
    await textarea().setValue("changed");
    const slow = deferred<api.SavedFrame>();
    vi.mocked(api.putFrame).mockReturnValue(slow.promise);
    await button("Save").trigger("click");
    await page().find('button[aria-label="Close"]').trigger("click");
    await flushPromises();
    expect(confirm.confirmDialog).not.toHaveBeenCalled();
    expect(drawer().props("modelValue")).toBe(true);
    slow.resolve({ version: version(9, "changed"), unchanged: false, warnings: [] });
    await flushPromises();
  });

  it("leaving the page asks only for a typed draft in an open drawer, and is refused during a write", async () => {
    await open();
    const [isDirty, isBlocked] = vi.mocked(useRouteLeaveGuard).mock.calls[0]!;
    expect(isDirty()).toBe(false);
    await edit();
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
});

describe("a model row", () => {
  const heading = () => {
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]');
    return dialog ? document.getElementById(dialog.getAttribute("aria-labelledby")!)?.textContent : undefined;
  };
  const row = (model: string) =>
    page()
      .findAll("tbody tr")
      .find((tr) => tr.find(".kind__model").text().startsWith(model))!;
  const usePhone = (on: boolean) =>
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: on && /max-width:\s*767/.test(q),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
  afterEach(() => vi.unstubAllGlobals());

  it("has no Edit column: the table heads end at Keep own", async () => {
    await open();
    const video = page().find('[data-frame-kind="video"]');
    expect(video.findAll("thead th").map((th) => th.text())).toEqual(["Model", "Effective version", "Keep own"]);
    expect(buttons("Edit")).toHaveLength(0);
  });

  it("opens the model's editor on a click anywhere in its row", async () => {
    await open();
    await row("m1").find(".kind__effective-cell").trigger("click");
    await flushPromises();
    expect(heading()).toBe("Editing video / m1");
  });

  it("names the model as the row's keyboard opener: a button reading the model, labelled Edit frame for it", async () => {
    await open();
    const opener = row("m1").find('button[aria-label="Edit frame for m1"]');
    expect(opener.text()).toBe("m1");
    await opener.trigger("click");
    await flushPromises();
    expect(heading()).toBe("Editing video / m1");
  });

  it("toggles the lock and opens nothing on a click on Keep own", async () => {
    vi.mocked(api.lockFrame).mockResolvedValue({});
    await open();
    const box = row("m1").find('input[type="checkbox"]');
    await box.trigger("click");
    await box.trigger("change");
    await flushPromises();
    expect(api.lockFrame).toHaveBeenCalled();
    expect(wrapper!.findComponent({ name: "ElDrawer" }).props("modelValue")).toBe(false);
  });

  it("opens nothing on a row click while a write runs", async () => {
    await open();
    await row("m1").trigger("click");
    await flushPromises();
    await textarea().setValue("typed");
    const slow = deferred<api.SavedFrame>();
    vi.mocked(api.putFrame).mockReturnValue(slow.promise);
    await button("Save").trigger("click");
    await row("m2").trigger("click");
    await flushPromises();
    expect(heading()).toBe("Editing video / m1");
    slow.resolve({ version: version(9, "typed"), unchanged: false, warnings: [] });
    await flushPromises();
  });

  it("on a phone opens the editor on a tap on the model's block, which has no Edit button", async () => {
    usePhone(true);
    await open();
    const block = page()
      .findAll(".kind__list li")
      .find((li) => li.find(".kind__model").text().startsWith("m1"))!;
    expect(block.findAll("button").filter((b) => b.text() === "Edit")).toHaveLength(0);
    await block.find(".kind__effective").trigger("click");
    await flushPromises();
    expect(heading()).toBe("Editing video / m1");
  });
});
