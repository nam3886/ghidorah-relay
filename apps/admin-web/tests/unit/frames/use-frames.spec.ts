import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/api/frames";
import { ApiError } from "@/api/client";
import { forgetFramesMemory, useFrames } from "@/components/frames/use-frames";
import { askDiscard } from "@/components/shared/use-leave-guard";
import { notify } from "@/lib/notify";
import { deferred, frameInfo, framesList, version } from "./fixtures";

vi.mock("@/api/frames");
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("@/components/shared/use-leave-guard", () => ({ askDiscard: vi.fn() }));

const refused = (message = "frame must hold {{client_prompt}} once", param = "text") =>
  new ApiError(422, { error: { code: "input.invalid", message, param } });
const saved = (over: Partial<api.SavedFrame> = {}): api.SavedFrame => ({
  version: version(9, "new"),
  unchanged: false,
  warnings: [],
  ...over,
});

// The page opens on the first kind's first own frame (m1); any other target is picked after that.
async function opened(target = "m1", text: string | null = "own1") {
  const f = useFrames();
  vi.mocked(api.getFrame).mockResolvedValue(frameInfo(text));
  await f.init(["flow", "other"], "flow");
  if (target !== "m1") await f.edit("video", target);
  return f;
}

beforeEach(() => {
  forgetFramesMemory();
  vi.resetAllMocks();
  vi.mocked(askDiscard).mockResolvedValue(true);
  vi.mocked(api.listFrames).mockImplementation(async () => framesList());
  vi.mocked(api.getFrame).mockResolvedValue(frameInfo("own1"));
});

describe("opening", () => {
  it("starts on the global filter when it names a provider, else the first one", async () => {
    const a = useFrames();
    await a.init(["flow", "other"], "other");
    expect(a.provider).toBe("other");
    expect(api.listFrames).toHaveBeenCalledWith("other");
    forgetFramesMemory();
    const b = useFrames();
    await b.init(["flow", "other"], "gone");
    expect(b.provider).toBe("flow");
    forgetFramesMemory();
    const c = useFrames();
    await c.init(["flow", "other"], "");
    expect(c.provider).toBe("flow");
  });

  it("keeps its own provider when it comes back, whatever the global filter says", async () => {
    const a = useFrames();
    await a.init(["flow", "other"], "flow");
    await a.openProvider("other");
    const again = useFrames();
    await again.init(["flow", "other"], "flow");
    expect(again.provider).toBe("other");
  });

  it("comes back to the frame it had", async () => {
    const f = await opened("m2", null);
    expect(f.shown).toEqual({ provider: "flow", kind: "video", target: "m2" });
    const again = useFrames();
    await again.init(["flow"], "flow");
    expect(again.shown).toEqual({ provider: "flow", kind: "video", target: "m2" });
    expect(again.data?.kinds).toHaveLength(2);
  });

  it("falls back to the first kind when the one it had is gone, and to the default when its model is gone", async () => {
    await opened("m2", null);
    vi.mocked(api.listFrames).mockImplementation(async () => {
      const list = framesList();
      list.kinds[0]!.models = list.kinds[0]!.models.filter((m) => m.model !== "m2");
      return list;
    });
    const gone = useFrames();
    await gone.init(["flow"], "flow");
    expect(gone.shown?.target).toBe("m1");
    vi.mocked(api.listFrames).mockImplementation(async () => {
      const list = framesList();
      return { ...list, kinds: list.kinds.filter((k) => k.kind === "image") };
    });
    const other = useFrames();
    await other.init(["flow"], "flow");
    expect(other.shown).toEqual({ provider: "flow", kind: "image", target: "default" });
  });

  it("forgets the remembered frame when the remembered provider is gone", async () => {
    await opened("m2", null);
    const other = useFrames();
    await other.init(["other"], "other");
    expect(other.provider).toBe("other");
    expect(other.shown?.target).toBe("m1");
  });

  it("loads the table and opens video first, on its first own frame", async () => {
    const f = useFrames();
    await f.init(["flow"], "flow");
    expect(f.data?.kinds.map((k) => k.kind)).toEqual(["video", "image"]);
    expect(f.kinds.map((k) => k.kind)).toEqual(["video", "image"]);
    expect(f.shown).toEqual({ provider: "flow", kind: "video", target: "m1" });
    expect(api.getFrame).toHaveBeenCalledTimes(1);
  });

  it("opens a kind with no own frame on its default, and video first whatever order the server lists", async () => {
    vi.mocked(api.listFrames).mockImplementation(async () => {
      const list = framesList();
      list.kinds[0]!.models[0]!.source = "default";
      return { ...list, kinds: [...list.kinds].reverse() };
    });
    const f = useFrames();
    await f.init(["flow"], "flow");
    expect(f.kinds.map((k) => k.kind)).toEqual(["video", "image"]);
    expect(f.shown).toEqual({ provider: "flow", kind: "video", target: "default" });
  });

  it("says the provider has no frames on a 404", async () => {
    vi.mocked(api.listFrames).mockRejectedValue(
      new ApiError(404, { error: { code: "resource.not_found", message: "x" } }),
    );
    const f = useFrames();
    await f.init(["flow"], "flow");
    expect(f.noFrames).toBe(true);
    expect(f.data).toBeNull();
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("toasts any other failure of the first load", async () => {
    vi.mocked(api.listFrames).mockRejectedValue(
      new ApiError(500, { error: { code: "server.error", message: "boom" } }),
    );
    const f = useFrames();
    await f.init(["flow"], "flow");
    expect(notify.error).toHaveBeenCalledWith("boom");
    expect(f.noFrames).toBe(false);
    expect(f.failed).toBe("boom");
  });

  it("Retry reads the table again and clears the failure", async () => {
    vi.mocked(api.listFrames).mockRejectedValueOnce(
      new ApiError(500, { error: { code: "server.error", message: "boom" } }),
    );
    const f = useFrames();
    await f.init(["flow"], "flow");
    expect(f.failed).toBe("boom");
    await f.reload();
    expect(f.failed).toBe("");
    expect(f.shown?.target).toBe("m1");
  });

  it("the draft starts as the current text, else the text in force, and Save waits for a change", async () => {
    const f = await opened("m1", "own1");
    expect(f.draft).toBe("own1");
    expect(f.canSave).toBe(false);
    f.setDraft("own1 edited");
    expect(f.canSave).toBe(true);
    f.setDraft("own1");
    expect(f.canSave).toBe(false);
    const g = await opened("m2", null);
    expect(g.draft).toBe("effective text");
    expect(g.canSave).toBe(false);
  });
});

describe("the editor", () => {
  it("keeps the frame it holds while the read of the next one runs, then shows the next", async () => {
    const f = await opened("m1", "own1");
    const slow = deferred<api.FrameInfo>();
    vi.mocked(api.getFrame).mockReturnValue(slow.promise);
    const reading = f.edit("video", "m2");
    expect(f.shown?.target).toBe("m1");
    expect(f.loading).toBe(1);
    slow.resolve(frameInfo("second"));
    await reading;
    expect(f.shown?.target).toBe("m2");
    expect(f.draft).toBe("second");
  });

  it("picking the frame that is already in the editor does nothing", async () => {
    const f = await opened("m1", "own1");
    vi.mocked(api.getFrame).mockClear();
    await f.edit("video", "m1");
    expect(api.getFrame).not.toHaveBeenCalled();
  });

  it("opens a kind on its first own frame, else its default", async () => {
    const f = await opened("m1", "own1");
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("D"));
    await f.openKind("image");
    expect(f.shown).toEqual({ provider: "flow", kind: "image", target: "default" });
    await f.openKind("video");
    expect(f.shown).toEqual({ provider: "flow", kind: "video", target: "m1" });
  });

  it("keeps its draft when a reload finds no such frame any more", async () => {
    const f = await opened("m1", "own1");
    f.setDraft("typed, not saved");
    vi.mocked(api.listFrames).mockImplementation(async () => ({ ...framesList(), kinds: [] }));
    vi.mocked(api.lockFrame).mockResolvedValue({});
    await f.setLock("video", "m1", true);
    expect(f.shown).toBeNull();
    expect(f.draft).toBe("typed, not saved");
    expect(f.loaded).toBe("own1");
  });

  it("asks before another frame replaces an unsaved draft; Keep editing changes nothing, Discard picks it", async () => {
    const f = await opened("m1", "own1");
    f.setDraft("typed, not saved");
    vi.mocked(api.getFrame).mockClear();
    vi.mocked(askDiscard).mockResolvedValueOnce(false);
    await f.edit("video", "m2");
    expect(askDiscard).toHaveBeenCalledTimes(1);
    expect(api.getFrame).not.toHaveBeenCalled();
    expect(f.shown?.target).toBe("m1");
    expect(f.draft).toBe("typed, not saved");
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("second"));
    await f.edit("video", "m2");
    expect(f.shown?.target).toBe("m2");
    expect(f.draft).toBe("second");
  });

  it("asks before another provider replaces an unsaved draft too, and a clean draft goes at once", async () => {
    const f = await opened("m1", "own1");
    f.setDraft("typed, not saved");
    vi.mocked(askDiscard).mockResolvedValueOnce(false);
    await f.openProvider("other");
    expect(f.provider).toBe("flow");
    expect(api.listFrames).toHaveBeenCalledTimes(1);
    vi.mocked(askDiscard).mockClear();
    f.setDraft("own1");
    await f.openProvider("other");
    expect(askDiscard).not.toHaveBeenCalled();
    expect(f.provider).toBe("other");
  });

  it("keeps the frame it had, after the toast, when the read of the next one fails", async () => {
    const f = await opened("m1", "own1");
    vi.mocked(api.getFrame).mockRejectedValue(new ApiError(500, { error: { code: "server.error", message: "no" } }));
    await f.edit("video", "m2");
    expect(notify.error).toHaveBeenCalledWith("no");
    expect(f.shown?.target).toBe("m1");
    const again = useFrames();
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("own1"));
    await again.init(["flow"], "flow");
    expect(again.shown?.target).toBe("m1");
  });

  it("stays on its frame after Save and after Roll back: the reload redraws it", async () => {
    const f = await opened();
    f.setDraft("x");
    vi.mocked(api.putFrame).mockResolvedValue(saved());
    await f.save();
    expect(f.shown?.target).toBe("m1");
    vi.mocked(api.rollbackFrame).mockResolvedValue(saved());
    await f.rollback(4);
    expect(f.shown?.target).toBe("m1");
  });
});

describe("stale answers", () => {
  it("drops the answer of an editor that was replaced before it came back", async () => {
    const f = useFrames();
    await f.init(["flow"], "flow");
    const slow = deferred<api.FrameInfo>();
    vi.mocked(api.getFrame).mockImplementation((t) =>
      t.target === "m2" ? slow.promise : Promise.resolve(frameInfo("second")),
    );
    const first = f.edit("video", "m2");
    const second = f.edit("video", "default");
    await second;
    slow.resolve(frameInfo("first"));
    await first;
    expect(f.shown?.target).toBe("default");
    expect(f.draft).toBe("second");
  });

  it("drops the failure of an editor that was replaced as quietly as its answer", async () => {
    const f = useFrames();
    await f.init(["flow"], "flow");
    const slow = deferred<api.FrameInfo>();
    vi.mocked(api.getFrame).mockImplementation((t) =>
      t.target === "m2" ? slow.promise : Promise.resolve(frameInfo("second")),
    );
    const first = f.edit("video", "m2");
    await f.edit("video", "default");
    slow.reject(new ApiError(500, { error: { code: "server.error", message: "late failure" } }));
    await first;
    expect(notify.error).not.toHaveBeenCalled();
    expect(f.shown?.target).toBe("default");
  });

  it("still shows the failure of the editor that is current", async () => {
    const f = useFrames();
    await f.init(["flow"], "flow");
    vi.mocked(api.getFrame).mockRejectedValue(new ApiError(500, { error: { code: "server.error", message: "now" } }));
    await f.edit("video", "m2");
    expect(notify.error).toHaveBeenCalledWith("now");
  });

  it("drops the table of a provider that was replaced by another provider", async () => {
    const f = useFrames();
    await f.init(["flow", "other"], "flow");
    const slow = deferred<api.FramesList>();
    vi.mocked(api.listFrames).mockImplementation((p) =>
      p === "other" ? slow.promise : Promise.resolve({ ...framesList(), provider: "flow" }),
    );
    const toOther = f.openProvider("other");
    const back = f.openProvider("flow");
    await back;
    slow.resolve({ ...framesList(), provider: "other" });
    await toOther;
    expect(f.listed).toBe("flow");
  });
});

describe("busy states", () => {
  it("a load turns every control off except the pickers", async () => {
    const f = useFrames();
    await f.init(["flow"], "flow");
    const slow = deferred<api.FrameInfo>();
    vi.mocked(api.getFrame).mockReturnValue(slow.promise);
    const loading = f.edit("video", "m2");
    expect(f.loading).toBe(1);
    expect(f.controlsOff).toBe(true);
    expect(f.opensOff).toBe(false);
    expect(f.canSave).toBe(false);
    slow.resolve(frameInfo("own1"));
    await loading;
    expect(f.controlsOff).toBe(false);
  });

  it("a write turns every control off, the pickers included, and the draft read-only", async () => {
    const f = await opened();
    f.setDraft("changed");
    const slow = deferred<api.SavedFrame>();
    vi.mocked(api.putFrame).mockReturnValue(slow.promise);
    const writing = f.save();
    expect(f.busy).toBe(true);
    expect(f.controlsOff).toBe(true);
    expect(f.opensOff).toBe(true);
    expect(f.canSave).toBe(false);
    slow.resolve(saved());
    await writing;
    expect(f.busy).toBe(false);
    expect(f.controlsOff).toBe(false);
  });

  it("clears the busy flags when a write fails", async () => {
    const f = await opened();
    f.setDraft("changed");
    vi.mocked(api.putFrame).mockRejectedValue(new ApiError(500, { error: { code: "server.error", message: "boom" } }));
    await f.save();
    expect(f.busy).toBe(false);
    expect(f.loading).toBe(0);
  });
});

describe("a lock toggle", () => {
  it("keeps the typed draft of the editor that is open", async () => {
    const f = await opened("m1", "own1");
    f.setDraft("typed but not saved");
    vi.mocked(api.lockFrame).mockResolvedValue({});
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("own1"));
    await f.setLock("video", "m1", true);
    expect(api.lockFrame).toHaveBeenCalledWith({ provider: "flow", kind: "video", target: "m1" }, true);
    expect(f.draft).toBe("typed but not saved");
    expect(f.loaded).toBe("own1");
    expect(f.canSave).toBe(true);
    expect(notify.success).toHaveBeenCalledWith("Locked: apply to all keeps this model");
  });

  it("toggling another model's lock keeps this editor's draft too, and Unlocked is its own toast", async () => {
    const f = await opened("m1", "own1");
    f.setDraft("typed");
    vi.mocked(api.lockFrame).mockResolvedValue({});
    await f.setLock("video", "m2", false);
    expect(api.lockFrame).toHaveBeenCalledWith({ provider: "flow", kind: "video", target: "m2" }, false);
    expect(f.draft).toBe("typed");
    expect(notify.success).toHaveBeenCalledWith("Unlocked");
  });

  it("is not kept by a save: the draft becomes the stored text", async () => {
    const f = await opened("m1", "own1");
    f.setDraft("new text");
    vi.mocked(api.putFrame).mockResolvedValue(saved());
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("new text"));
    await f.save();
    expect(f.draft).toBe("new text");
    expect(f.loaded).toBe("new text");
    expect(f.canSave).toBe(false);
  });

  it("reloads the table when the call fails, so the checkbox shows what is stored", async () => {
    const f = await opened();
    vi.mocked(api.lockFrame).mockRejectedValue(new ApiError(500, { error: { code: "server.error", message: "boom" } }));
    vi.mocked(api.listFrames).mockClear();
    await f.setLock("video", "m1", true);
    expect(api.listFrames).toHaveBeenCalledTimes(1);
    expect(notify.error).toHaveBeenCalledWith("boom");
    expect(notify.success).not.toHaveBeenCalled();
  });
});

describe("write errors", () => {
  it("shows a refused write (4xx) beside the editor, with its param, and no toast", async () => {
    const f = await opened();
    f.setDraft("bad");
    vi.mocked(api.putFrame).mockRejectedValue(refused());
    await f.save();
    expect(f.refusal).toBe("frame must hold {{client_prompt}} once (text)");
    expect(notify.error).not.toHaveBeenCalled();
    expect(notify.success).not.toHaveBeenCalled();
    expect(f.draft).toBe("bad");
  });

  it("shows a refusal without a param as the message alone", async () => {
    const f = await opened();
    f.setDraft("bad");
    vi.mocked(api.putFrame).mockRejectedValue(new ApiError(409, { error: { code: "x", message: "conflict" } }));
    await f.save();
    expect(f.refusal).toBe("conflict");
  });

  it("sends any other error (5xx, network, 401) to a toast and shows nothing beside the editor", async () => {
    const f = await opened();
    f.setDraft("bad");
    for (const err of [
      new ApiError(500, { error: { code: "server.error", message: "boom" } }),
      new ApiError(401, { error: { code: "auth.invalid_session", message: "ended" } }),
    ]) {
      vi.mocked(api.putFrame).mockRejectedValue(err);
      await f.save();
    }
    expect(f.refusal).toBe("");
    expect(vi.mocked(notify.error).mock.calls.map((c) => c[0])).toEqual(["boom", "ended"]);
  });

  it("drops the refusal when another editor was opened while the write ran", async () => {
    const f = await opened("m1");
    f.setDraft("bad");
    const slow = deferred<api.SavedFrame>();
    vi.mocked(api.putFrame).mockReturnValue(slow.promise);
    const writing = f.save();
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("m2 text"));
    await f.edit("video", "m2");
    slow.reject(refused());
    await writing;
    expect(f.refusal).toBe("");
    expect(f.shown?.target).toBe("m2");
  });

  it("clears an old refusal when the next write starts", async () => {
    const f = await opened();
    f.setDraft("bad");
    vi.mocked(api.putFrame).mockRejectedValueOnce(refused()).mockResolvedValueOnce(saved());
    await f.save();
    expect(f.refusal).not.toBe("");
    await f.save();
    expect(f.refusal).toBe("");
  });
});

describe("an action that reloads the editor over an unsaved draft", () => {
  const actions: [string, (f: Awaited<ReturnType<typeof opened>>) => Promise<void>, () => unknown][] = [
    ["Follow the default", (f) => f.follow(), () => api.putFrame],
    ["Roll back", (f) => f.rollback(4), () => api.rollbackFrame],
  ];
  for (const [name, run, call] of actions) {
    it(`${name} asks first, and Keep editing leaves the draft and sends nothing`, async () => {
      const f = await opened();
      vi.mocked(api.putFrame).mockResolvedValue(saved());
      vi.mocked(api.rollbackFrame).mockResolvedValue(saved());
      f.setDraft("typed, not saved");
      vi.mocked(askDiscard).mockResolvedValueOnce(false);
      await run(f);
      expect(askDiscard).toHaveBeenCalledTimes(1);
      expect(call()).not.toHaveBeenCalled();
      expect(f.draft).toBe("typed, not saved");
      expect(f.busy).toBe(false);
    });

    it(`${name} performs the action on Discard, and the draft is the stored text again`, async () => {
      const f = await opened();
      vi.mocked(api.putFrame).mockResolvedValue(saved());
      vi.mocked(api.rollbackFrame).mockResolvedValue(saved());
      f.setDraft("typed, not saved");
      await run(f);
      expect(askDiscard).toHaveBeenCalledTimes(1);
      expect(call()).toHaveBeenCalledTimes(1);
      expect(f.draft).toBe(f.loaded);
    });

    it(`${name} acts at once on a clean draft`, async () => {
      const f = await opened();
      vi.mocked(api.putFrame).mockResolvedValue(saved());
      vi.mocked(api.rollbackFrame).mockResolvedValue(saved());
      await run(f);
      expect(askDiscard).not.toHaveBeenCalled();
      expect(call()).toHaveBeenCalledTimes(1);
    });
  }

  it("Apply to all submits the draft on purpose and does not ask", async () => {
    const f = await opened();
    vi.mocked(api.applyAllFrames).mockResolvedValue({ applied: ["m2"], kept: [], unchanged: [], warnings: [] });
    f.setDraft("typed, not saved");
    await f.applyAll();
    expect(askDiscard).not.toHaveBeenCalled();
    expect(api.applyAllFrames).toHaveBeenCalledWith("flow", "video", "typed, not saved");
  });
});

describe("writes", () => {
  it("Save sends the draft, then shows the warnings of the answer after the redraw and a toast", async () => {
    const f = await opened();
    f.setDraft("x");
    vi.mocked(api.putFrame).mockResolvedValue(saved({ warnings: ["no tracking code"] }));
    await f.save();
    expect(api.putFrame).toHaveBeenCalledWith({ provider: "flow", kind: "video", target: "m1" }, "x");
    expect(f.warnings).toEqual(["no tracking code"]);
    expect(notify.success).toHaveBeenCalledWith("Saved: applies to jobs created from now on");
  });

  it("says Nothing changed when the server added no version", async () => {
    const f = await opened();
    f.setDraft("x");
    vi.mocked(api.putFrame).mockResolvedValue(saved({ unchanged: true }));
    await f.save();
    expect(notify.success).toHaveBeenCalledWith("Nothing changed");
  });

  it("Follow the default sends a null text", async () => {
    const f = await opened();
    vi.mocked(api.putFrame).mockResolvedValue(saved());
    await f.follow();
    expect(api.putFrame).toHaveBeenCalledWith({ provider: "flow", kind: "video", target: "m1" }, null);
    expect(notify.success).toHaveBeenCalledWith("This model follows the default");
  });

  it("Roll back sends the version id", async () => {
    const f = await opened();
    vi.mocked(api.rollbackFrame).mockResolvedValue(saved({ warnings: ["w"] }));
    await f.rollback(4);
    expect(api.rollbackFrame).toHaveBeenCalledWith({ provider: "flow", kind: "video", target: "m1" }, 4);
    expect(f.warnings).toEqual(["w"]);
    expect(notify.success).toHaveBeenCalledWith("Rolled back: a new version was saved");
  });

  it("Reset to the shipped default posts the kind of the section and keeps the open editor", async () => {
    const f = await opened();
    vi.mocked(api.resetFrame).mockResolvedValue(saved());
    await f.resetShipped("video");
    expect(api.resetFrame).toHaveBeenCalledWith("flow", "video");
    expect(f.shown?.target).toBe("m1");
    expect(notify.success).toHaveBeenCalledWith("The default is the shipped text again");
  });

  it("Apply to all sends the draft of the default editor and lists applied, kept and unchanged", async () => {
    const f = await opened("default", "D");
    f.setDraft("new default");
    vi.mocked(api.applyAllFrames).mockResolvedValue({ applied: ["m2"], kept: ["m1"], unchanged: [], warnings: ["w"] });
    await f.applyAll();
    expect(api.applyAllFrames).toHaveBeenCalledWith("flow", "video", "new default");
    expect(f.applied).toEqual({ applied: ["m2"], kept: ["m1"], unchanged: [], warnings: ["w"] });
    expect(f.warnings).toEqual(["w"]);
    expect(notify.success).toHaveBeenCalledWith("Applied to 1 model(s)");
  });

  it("Apply to all draws no summary when the operator moved to another editor meanwhile", async () => {
    const f = await opened("default", "D");
    const slow = deferred<api.ApplyAllResult>();
    vi.mocked(api.applyAllFrames).mockReturnValue(slow.promise);
    const writing = f.applyAll();
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("m2 text"));
    await f.edit("video", "m2");
    slow.resolve({ applied: ["m2"], kept: [], unchanged: [], warnings: ["w"] });
    await writing;
    expect(f.applied).toBeNull();
    expect(f.warnings).toEqual([]);
    expect(notify.success).toHaveBeenCalledWith("Applied to 1 model(s)");
  });
});

describe("preview", () => {
  it("sends the draft with the sample prompt for the open model and shows the answer", async () => {
    const f = await opened("m1", "own1");
    f.setDraft("draft text");
    vi.mocked(api.previewFrame).mockResolvedValue({
      text: "rendered",
      length: 8,
      frame_id: null,
      source: "draft",
      warnings: ["pw"],
    });
    await f.previewNow();
    expect(api.previewFrame).toHaveBeenCalledWith("m1", "a cat on a sofa", "draft text");
    expect(f.preview?.text).toBe("rendered");
    expect(f.warnings).toEqual(["pw"]);
  });

  it("for the default editor previews with the first model of the kind", async () => {
    const f = await opened("default", "D");
    vi.mocked(api.previewFrame).mockResolvedValue({
      text: "r",
      length: 1,
      frame_id: null,
      source: "draft",
      warnings: [],
    });
    await f.previewNow();
    expect(vi.mocked(api.previewFrame).mock.calls[0]?.[0]).toBe("m1");
  });

  it("says a kind with no model has nothing to preview with, and sends nothing", async () => {
    const f = useFrames();
    await f.init(["flow"], "flow");
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("D"));
    await f.edit("image", "default");
    await f.previewNow();
    expect(notify.error).toHaveBeenCalledWith("This kind has no model to preview with");
    expect(api.previewFrame).not.toHaveBeenCalled();
  });

  it("drops the result when the draft changed while it was on its way", async () => {
    const f = await opened();
    const slow = deferred<api.PreviewResult>();
    vi.mocked(api.previewFrame).mockReturnValue(slow.promise);
    f.setDraft("first draft");
    const asking = f.previewNow();
    f.setDraft("second draft");
    slow.resolve({ text: "for the first", length: 13, frame_id: null, source: "draft", warnings: ["pw"] });
    await asking;
    expect(f.preview).toBeNull();
    expect(f.warnings).toEqual([]);
  });

  it("drops the result when another editor was opened while it was on its way", async () => {
    const f = await opened("m1");
    const slow = deferred<api.PreviewResult>();
    vi.mocked(api.previewFrame).mockReturnValue(slow.promise);
    f.setDraft("x");
    const asking = f.previewNow();
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("x"));
    await f.edit("video", "m2");
    slow.resolve({ text: "for m1", length: 6, frame_id: null, source: "draft", warnings: [] });
    await asking;
    expect(f.preview).toBeNull();
  });

  it("shows a refused preview beside the editor if it is still the one that asked, and drops it if not", async () => {
    const f = await opened("m1");
    f.setDraft("x");
    vi.mocked(api.previewFrame).mockRejectedValue(refused("bad draft", "text"));
    await f.previewNow();
    expect(f.refusal).toBe("bad draft (text)");

    const slow = deferred<api.PreviewResult>();
    vi.mocked(api.previewFrame).mockReturnValue(slow.promise);
    const asking = f.previewNow();
    f.setDraft("changed meanwhile");
    slow.reject(refused("late refusal", "text"));
    await asking;
    expect(f.refusal).toBe("");
  });

  it("an editor that is redrawn starts with the sample prompt and no preview", async () => {
    const f = await opened();
    f.setPreviewPrompt("a dog");
    vi.mocked(api.previewFrame).mockResolvedValue({
      text: "rendered",
      length: 8,
      frame_id: null,
      source: "draft",
      warnings: [],
    });
    await f.previewNow();
    expect(f.preview?.text).toBe("rendered");
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("own1"));
    await f.reload();
    expect(f.previewPrompt).toBe("a cat on a sofa");
    expect(f.preview).toBeNull();
  });
});

describe("the quiet preview", () => {
  const answer = (text: string): api.PreviewResult => ({
    text,
    length: text.length,
    frame_id: null,
    source: "draft",
    warnings: ["w"],
  });

  it("sends the draft and the sample prompt for the open model, and shows the answer and its warnings", async () => {
    const f = await opened("m1", "own1");
    f.setDraft("draft {{client_prompt}} {{tracking_code}}");
    vi.mocked(api.previewFrame).mockResolvedValue(answer("rendered"));
    await f.previewQuiet();
    expect(api.previewFrame).toHaveBeenCalledWith("m1", "a cat on a sofa", "draft {{client_prompt}} {{tracking_code}}");
    expect(f.preview?.text).toBe("rendered");
    expect(f.warnings).toEqual(["w"]);
  });

  it("shows no refusal and no toast: a draft the server refuses just has no preview", async () => {
    const f = await opened("m1", "own {{client_prompt}} {{tracking_code}}");
    vi.mocked(api.previewFrame).mockResolvedValueOnce(answer("rendered")).mockRejectedValueOnce(refused());
    await f.previewQuiet();
    expect(f.preview?.text).toBe("rendered");
    f.setDraft("bad {{client_prompt}} {{tracking_code}}");
    await f.previewQuiet();
    expect(f.preview).toBeNull();
    expect(f.refusal).toBe("");
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("sends nothing while a load or a write is on, or for a kind with no model", async () => {
    const f = await opened("m1", "own1");
    const slow = deferred<api.SavedFrame>();
    vi.mocked(api.putFrame).mockReturnValue(slow.promise);
    f.setDraft("x {{client_prompt}} {{tracking_code}}");
    const writing = f.save();
    await f.previewQuiet();
    slow.resolve(saved());
    await writing;
    vi.mocked(api.getFrame).mockResolvedValue(frameInfo("D"));
    await f.edit("image", "default");
    await f.previewQuiet();
    expect(api.previewFrame).not.toHaveBeenCalled();
  });

  it("sends nothing for a draft that visibly breaks a rule, and clears the old preview", async () => {
    const f = await opened("m1", "own {{client_prompt}} {{tracking_code}}");
    vi.mocked(api.previewFrame).mockResolvedValue(answer("rendered"));
    await f.previewQuiet();
    expect(f.preview?.text).toBe("rendered");
    vi.mocked(api.previewFrame).mockClear();
    for (const text of ["no markers", `{{client_prompt}} {{tracking_code}} ${"x".repeat(1000)}`]) {
      f.setDraft(text);
      await f.previewQuiet();
      expect(f.preview).toBeNull();
    }
    f.setDraft("fine {{client_prompt}} {{tracking_code}}");
    f.setPreviewPrompt("   ");
    await f.previewQuiet();
    expect(f.preview).toBeNull();
    expect(api.previewFrame).not.toHaveBeenCalled();
  });

  it("drops an answer for a frame that was replaced while it was on its way, though the text is the same", async () => {
    const f = await opened("m1", "same {{client_prompt}} {{tracking_code}}");
    const slow = deferred<api.PreviewResult>();
    vi.mocked(api.previewFrame).mockReturnValue(slow.promise);
    const asking = f.previewQuiet();
    await f.edit("video", "m2");
    expect(f.draft).toBe("same {{client_prompt}} {{tracking_code}}");
    slow.resolve(answer("for m1"));
    await asking;
    expect(f.preview).toBeNull();
  });

  it("drops an answer for a draft that was replaced while it was on its way", async () => {
    const f = await opened();
    const slow = deferred<api.PreviewResult>();
    vi.mocked(api.previewFrame).mockReturnValue(slow.promise);
    f.setDraft("first {{client_prompt}} {{tracking_code}}");
    const asking = f.previewQuiet();
    f.setDraft("second {{client_prompt}} {{tracking_code}}");
    slow.resolve(answer("for the first"));
    await asking;
    expect(f.preview).toBeNull();
    expect(f.warnings).toEqual([]);
  });
});
