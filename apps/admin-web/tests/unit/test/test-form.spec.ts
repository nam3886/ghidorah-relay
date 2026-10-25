import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PublicModel } from "@/api/test";
import {
  buildBody,
  clearSavedForm,
  emptyForm,
  FIELDS,
  FORM_KEY,
  optionLabel,
  optionValue,
  readForm,
  saveForm,
  type TestForm,
} from "@/lib/test-form";

const video: PublicModel = { id: "veo-3", kind: "video", owned_by: "flow" };
const image: PublicModel = { id: "imagen-4", kind: "image", owned_by: "flow" };
// a video that names the lengths it takes
const timed: PublicModel = { ...video, limits: { durations: [5, 8] } };
const form = (over: Partial<TestForm> = {}): TestForm => ({ ...emptyForm(), prompt: "a cat", ...over });

beforeEach(() => {
  localStorage.clear();
});

describe("the saved form", () => {
  it("is kept under relay_test_form with the model and the ten fields, and nothing else", () => {
    expect(FORM_KEY).toBe("relay_test_form");
    expect(FIELDS).toEqual([
      "prompt",
      "duration",
      "ratio",
      "count",
      "style",
      "images",
      "title",
      "options",
      "resolution",
      "source",
    ]);
    const typed = form({
      model: "veo-3",
      duration: "8",
      ratio: "16:9",
      count: "2",
      style: "noir",
      images: "https://x",
      title: "t",
      options: "{}",
      resolution: "720p",
      source: "job-1",
    });
    saveForm(typed);
    expect(JSON.parse(localStorage.getItem("relay_test_form") ?? "")).toEqual({
      model: "veo-3",
      prompt: "a cat",
      duration: "8",
      ratio: "16:9",
      count: "2",
      style: "noir",
      images: "https://x",
      title: "t",
      options: "{}",
      resolution: "720p",
      source: "job-1",
    });
    expect(localStorage.length).toBe(1);
  });

  it("reads back what was saved", () => {
    const typed = form({ model: "veo-3", duration: "8", title: "t" });
    saveForm(typed);
    expect(readForm()).toEqual({ form: typed, ok: true });
  });

  it("reads an empty form when nothing is saved, or the value is damaged, and that is not a storage failure", () => {
    expect(readForm()).toEqual({ form: emptyForm(), ok: true });
    for (const bad of ["not json", "null", "[1,2]", '"text"', "7"]) {
      localStorage.setItem("relay_test_form", bad);
      expect(readForm()).toEqual({ form: emptyForm(), ok: true });
    }
  });

  it("ignores a field that is not a string, one field at a time", () => {
    localStorage.setItem("relay_test_form", JSON.stringify({ model: "m", prompt: 5, duration: "8", ratio: null }));
    expect(readForm().form).toEqual({ ...emptyForm(), model: "m", duration: "8" });
  });

  it("reports a refused write and a refused clear as failures, and a good one as success", () => {
    expect(saveForm(form())).toBe(true);
    expect(clearSavedForm()).toBe(true);
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => {
        throw new Error("quota");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    });
    expect(saveForm(form())).toBe(false);
    expect(clearSavedForm()).toBe(false);
  });

  it("reports a store that cannot be read as a failure", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
    });
    expect(readForm()).toEqual({ form: emptyForm(), ok: false });
  });

  it("clearSavedForm removes the key", () => {
    saveForm(form());
    clearSavedForm();
    expect(localStorage.getItem("relay_test_form")).toBeNull();
  });

  it("survives a blocked or full localStorage: nothing throws and the form reads empty", () => {
    const blocked = {
      getItem: vi.fn(() => {
        throw new Error("blocked");
      }),
      setItem: vi.fn(() => {
        throw new Error("blocked");
      }),
      removeItem: vi.fn(() => {
        throw new Error("blocked");
      }),
    };
    vi.stubGlobal("localStorage", blocked);
    expect(() => saveForm(form())).not.toThrow();
    expect(() => clearSavedForm()).not.toThrow();
    expect(readForm().form).toEqual(emptyForm());
  });
});

describe("optionValue", () => {
  it("takes the value a provider takes, and the label only when there is no value", () => {
    expect(optionValue({ label: "Widescreen, for most screens (16:9)", value: "16:9" })).toBe("16:9");
    expect(optionValue({ label: "Oil painting", value: "oil_painting" })).toBe("oil_painting");
    expect(optionValue({ name: "16:9" })).toBe("16:9");
    expect(optionValue({ label: "1:1", value: "" })).toBe("1:1");
    expect(optionValue({})).toBe("");
  });
});

describe("optionLabel", () => {
  it("takes the first of name, label, title, value, id that holds text", () => {
    expect(optionLabel({ id: "i", value: "v", title: "t", label: "l", name: "n" })).toBe("n");
    expect(optionLabel({ id: "i", value: "v", title: "t", label: "l" })).toBe("l");
    expect(optionLabel({ id: "i", value: "v", title: "t" })).toBe("t");
    expect(optionLabel({ id: "i", value: "v" })).toBe("v");
    expect(optionLabel({ id: "i" })).toBe("i");
    expect(optionLabel({ name: "", label: 3, id: "i" })).toBe("i");
    expect(optionLabel({})).toBe("");
  });
});

describe("the request body", () => {
  it("sends only the model and the prompt for an otherwise empty form", () => {
    expect(buildBody(video, form(), [])).toEqual({ model: "veo-3", prompt: "a cat" });
  });

  it("sends duration for a video that names its lengths and for nothing else, style for an image and not for a video", () => {
    const typed = form({ duration: "8", style: "noir" });
    expect(buildBody(timed, typed, [])).toEqual({ model: "veo-3", prompt: "a cat", duration: 8 });
    // no list: the form shows no Duration, so a value saved earlier is not sent
    expect(buildBody(video, typed, [])).toEqual({ model: "veo-3", prompt: "a cat" });
    expect(buildBody(image, typed, [])).toEqual({ model: "imagen-4", prompt: "a cat", style: "noir" });
  });

  it("sends ratio, count as n, and title trimmed, and leaves empty ones out", () => {
    const typed = form({ ratio: " 16:9 ", count: " 2 ", title: " Cat ", duration: "  " });
    expect(buildBody(timed, typed, [])).toEqual({ model: "veo-3", prompt: "a cat", ratio: "16:9", n: 2, title: "Cat" });
  });

  it("sends the URL lines, then the attached files as data URIs, in that order", () => {
    const typed = form({ images: "https://a/x.png\n\n  https://b/y.png  \n" });
    expect(buildBody(image, typed, ["data:image/png;base64,AA"]).images).toEqual([
      "https://a/x.png",
      "https://b/y.png",
      "data:image/png;base64,AA",
    ]);
    expect(buildBody(image, form(), ["data:image/png;base64,AA"]).images).toEqual(["data:image/png;base64,AA"]);
  });

  it("parses the options as JSON and fails on text that is not JSON", () => {
    expect(buildBody(image, form({ options: '{"seed": 3}' }), []).options).toEqual({ seed: 3 });
    expect(() => buildBody(image, form({ options: "{nope" }), [])).toThrow(SyntaxError);
    expect(buildBody(image, form({ options: "   " }), [])).not.toHaveProperty("options");
  });

  it("sends the resolution in options for a model that names resolutions, under one the JSON sets, and not for others", () => {
    const vibes: PublicModel = { ...video, limits: { resolutions: ["480p", "720p"] } };
    expect(buildBody(vibes, form({ resolution: "720p" }), []).options).toEqual({ resolution: "720p" });
    expect(buildBody(vibes, form({ resolution: "720p", options: '{"seed": 3}' }), []).options).toEqual({
      resolution: "720p",
      seed: 3,
    });
    // the JSON's own resolution wins over the select
    expect(buildBody(vibes, form({ resolution: "720p", options: '{"resolution": "480p"}' }), []).options).toEqual({
      resolution: "480p",
    });
    expect(buildBody(vibes, form(), [])).not.toHaveProperty("options");
    // no list: the form shows no Resolution, so a value saved earlier is not sent
    expect(buildBody(video, form({ resolution: "720p" }), [])).not.toHaveProperty("options");
  });

  it("sends the source job trimmed for a model that works on one, and never for another model", () => {
    const extend: PublicModel = { ...video, limits: { source: "video" } };
    expect(buildBody(extend, form({ source: "  job-1 " }), []).source_job_id).toBe("job-1");
    expect(buildBody(extend, form(), [])).not.toHaveProperty("source_job_id");
    expect(buildBody(video, form({ source: "job-1" }), [])).not.toHaveProperty("source_job_id");
  });
});
