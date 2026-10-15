import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import * as api from "@/api/frames";
import * as shell from "@/api/shell";
import { forgetFramesMemory } from "@/components/frames/use-frames";
import FramesView from "@/views/FramesView.vue";
import { deferred, frameInfo, framesList, version } from "./fixtures";

vi.mock("@/api/frames");
vi.mock("@/api/shell", () => ({ getModels: vi.fn() }));
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
const buttons = (name: string) => wrapper!.findAll("button").filter((b) => b.text() === name);
const button = (name: string) => buttons(name)[0]!;
const textarea = () => wrapper!.find("textarea");

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

describe("the view", () => {
  it("draws a section per kind with the default line, the shipped-differs flag and the model table", async () => {
    await open();
    const sections = wrapper!.findAll("[data-frame-kind]");
    expect(sections.map((s) => s.attributes("data-frame-kind"))).toEqual(["video", "image"]);
    const video = sections[0]!;
    expect(video.text()).toContain("Default: version 1 by owner");
    expect(video.text()).toContain("differs from the shipped default");
    expect(video.text()).toContain("Reset to the shipped default");
    expect(video.text()).toContain("m1");
    expect(video.text()).toContain("keep own");
    expect(video.text()).toContain("never toggled");
    expect(sections[1]!.text()).toContain("Default: no version");
    expect(sections[1]!.text()).toContain("No models");
    expect(sections[1]!.text()).not.toContain("Reset to the shipped default");
    expect(wrapper!.text()).toContain("Pick a model or a default to edit its frame.");
  });

  it("says a provider without frames has none: the core frame applies", async () => {
    vi.mocked(api.listFrames).mockRejectedValue(
      new ApiError(404, { error: { code: "resource.not_found", message: "x" } }),
    );
    await open();
    expect(wrapper!.text()).toContain("This provider has no prompt frames: the core frame applies.");
    expect(wrapper!.findAll("[data-frame-kind]")).toHaveLength(0);
  });

  it("opens the editor of a model: help text, Follow only for an own frame, no Apply to all", async () => {
    await open();
    await wrapper!.find('button[aria-label="Edit m1"]').trigger("click");
    await flushPromises();
    const editor = wrapper!.find(".frame-editor");
    expect(editor.find("h3").text()).toBe("video / m1");
    expect(editor.text()).toContain("{{client_prompt}}");
    expect(editor.text()).toContain("{{tracking_code}}");
    expect(editor.text()).toContain("at most 1,000 characters");
    expect(editor.text()).not.toContain("This model follows the default.");
    expect(buttons("Follow the default")).toHaveLength(1);
    expect(buttons("Apply to all models of this kind")).toHaveLength(0);
    expect(textarea().element.value).toBe("own1");
  });

  it("the frame box shows the shape of a frame as its placeholder, and the lock history sits on the checkbox's line", async () => {
    await open();
    expect(wrapper!.find(".frame-kind__lock").text()).toContain("keep own");
    expect(wrapper!.find(".frame-kind__lock").text()).toContain("never toggled");
    await wrapper!.find('button[aria-label="Edit m1"]').trigger("click");
    await flushPromises();
    expect(textarea().attributes("placeholder")).toBe("{{client_prompt}} ... {{tracking_code}}");
  });

  it("a model that follows the default has the note, no Follow button, and the default's text in the box", async () => {
    await open();
    await wrapper!.find('button[aria-label="Edit m2"]').trigger("click");
    await flushPromises();
    expect(wrapper!.find(".frame-editor").text()).toContain("This model follows the default.");
    expect(buttons("Follow the default")).toHaveLength(0);
    expect(textarea().element.value).toBe("effective text");
  });

  it("the default editor has Apply to all and no Follow", async () => {
    await open();
    await button("Edit the default").trigger("click");
    await flushPromises();
    expect(wrapper!.find(".frame-editor h3").text()).toBe("video / default");
    expect(buttons("Apply to all models of this kind")).toHaveLength(1);
    expect(buttons("Follow the default")).toHaveLength(0);
  });

  it("Save is disabled until the draft differs from the loaded text", async () => {
    await open();
    await wrapper!.find('button[aria-label="Edit m1"]').trigger("click");
    await flushPromises();
    expect(button("Save").attributes("disabled")).toBeDefined();
    await textarea().setValue("own1 changed");
    expect(button("Save").attributes("disabled")).toBeUndefined();
    await textarea().setValue("own1");
    expect(button("Save").attributes("disabled")).toBeDefined();
  });

  it("shows a refused save beside the editor as an alert with the field", async () => {
    await open();
    await wrapper!.find('button[aria-label="Edit m1"]').trigger("click");
    await flushPromises();
    await textarea().setValue("no placeholders");
    vi.mocked(api.putFrame).mockRejectedValue(
      new ApiError(422, {
        error: { code: "input.invalid", message: "the frame needs the customer prompt", param: "text" },
      }),
    );
    await button("Save").trigger("click");
    await flushPromises();
    const alert = wrapper!.find('.frame-editor [role="alert"]');
    expect(alert.text()).toBe("the frame needs the customer prompt (text)");
    expect(textarea().element.value).toBe("no placeholders");
  });

  it("shows save warnings, the preview and the history as text", async () => {
    await open();
    await wrapper!.find('button[aria-label="Edit m1"]').trigger("click");
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
    expect(wrapper!.find(".frame-editor__warnings").text()).toBe("No tracking code.");
    vi.mocked(api.previewFrame).mockResolvedValue({
      text: "RENDERED",
      length: 8,
      frame_id: null,
      source: "draft",
      warnings: [],
    });
    await button("Preview").trigger("click");
    await flushPromises();
    expect(wrapper!.find(".frame-editor__preview").text()).toBe("RENDERED");
    expect(wrapper!.text()).toContain("8 characters, shown with the sample tracking code (draft frame)");
    expect(wrapper!.findAll(".frame-editor__history li")).toHaveLength(2);
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
    await wrapper!.find('button[aria-label="Edit m1"]').trigger("click");
    await flushPromises();
    await button("Preview").trigger("click");
    await flushPromises();
    expect(textarea().element.value).toBe(PLANTED);
    expect(wrapper!.find(".frame-editor__history pre").text()).toBe(PLANTED);
    expect(wrapper!.find(".frame-editor__preview").text()).toBe(PLANTED);
    expect(wrapper!.find(".frame-editor__warnings").text()).toBe(PLANTED);
    expect(wrapper!.find("img").exists()).toBe(false);
    expect(wrapper!.find("b").exists()).toBe(false);
  });

  it("during a load: Save and the other controls are off, the box is read-only, Edit and the provider stay on", async () => {
    await open();
    await wrapper!.find('button[aria-label="Edit m1"]').trigger("click");
    await flushPromises();
    await textarea().setValue("typed");
    const slow = deferred<api.FrameInfo>();
    vi.mocked(api.getFrame).mockReturnValue(slow.promise);
    await wrapper!.find('button[aria-label="Edit m2"]').trigger("click");
    expect(textarea().attributes("readonly")).toBeDefined();
    expect(button("Save").attributes("disabled")).toBeDefined();
    expect(button("Follow the default").attributes("disabled")).toBeDefined();
    expect(button("Preview").attributes("disabled")).toBeDefined();
    expect(button("Reset to the shipped default").attributes("disabled")).toBeDefined();
    expect(wrapper!.find(".el-checkbox input").attributes("disabled")).toBeDefined();
    expect(wrapper!.find('button[aria-label="Edit m2"]').attributes("disabled")).toBeUndefined();
    expect(button("Edit the default").attributes("disabled")).toBeUndefined();
    expect(wrapper!.find(".frames__provider .is-disabled").exists()).toBe(false);
    slow.resolve(frameInfo(null));
    await flushPromises();
    expect(textarea().attributes("readonly")).toBeUndefined();
  });

  it("during a write: everything is off, Edit buttons and the provider select too", async () => {
    await open();
    await wrapper!.find('button[aria-label="Edit m1"]').trigger("click");
    await flushPromises();
    await textarea().setValue("typed");
    const slow = deferred<api.SavedFrame>();
    vi.mocked(api.putFrame).mockReturnValue(slow.promise);
    await button("Save").trigger("click");
    expect(textarea().attributes("readonly")).toBeDefined();
    expect(wrapper!.find('button[aria-label="Edit m2"]').attributes("disabled")).toBeDefined();
    expect(button("Edit the default").attributes("disabled")).toBeDefined();
    expect(wrapper!.find(".frames__provider .is-disabled").exists()).toBe(true);
    slow.resolve({ version: version(9, "typed"), unchanged: false, warnings: [] });
    await flushPromises();
    expect(wrapper!.find('button[aria-label="Edit m2"]').attributes("disabled")).toBeUndefined();
  });
});
