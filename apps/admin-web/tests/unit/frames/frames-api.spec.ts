import { beforeEach, describe, expect, it, vi } from "vitest";
import { lockFrame, previewFrame, putFrame, resetFrame } from "@/api/frames";

// The Prompt frames calls through the real client with fetch stubbed: the paths and the bodies that go out.
const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
const calls = () =>
  fetchMock.mock.calls.map(([url, init]) => ({
    url,
    method: init.method,
    body: init.body === undefined ? undefined : JSON.parse(String(init.body)),
  }));
const target = { provider: "flow", kind: "video", target: "veo 3/fast" };

beforeEach(() => {
  sessionStorage.clear();
  fetchMock.mockReset();
  fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({}), { status: 200 })));
  vi.stubGlobal("fetch", fetchMock);
});

describe("the write calls of the Prompt frames", () => {
  it("lock sends the checkbox value as {locked} to the lock path of the encoded target", async () => {
    await lockFrame(target, true);
    await lockFrame(target, false);
    expect(calls()).toEqual([
      { url: "/admin/frames/flow/video/veo%203%2Ffast/lock", method: "PUT", body: { locked: true } },
      { url: "/admin/frames/flow/video/veo%203%2Ffast/lock", method: "PUT", body: { locked: false } },
    ]);
  });

  it("Follow the default puts text: null and a save puts the text, on the target path", async () => {
    await putFrame(target, null);
    await putFrame(target, "frame text");
    expect(calls()).toEqual([
      { url: "/admin/frames/flow/video/veo%203%2Ffast", method: "PUT", body: { text: null } },
      { url: "/admin/frames/flow/video/veo%203%2Ffast", method: "PUT", body: { text: "frame text" } },
    ]);
  });

  it("Reset to the shipped default posts /reset on the kind path, with no body", async () => {
    await resetFrame("flow", "video");
    expect(calls()).toEqual([{ url: "/admin/frames/flow/video/reset", method: "POST", body: undefined }]);
  });

  it("the preview posts the model, the sample prompt and the draft text", async () => {
    await previewFrame("m1", "a cat", "draft {{client_prompt}}");
    expect(calls()).toEqual([
      {
        url: "/admin/frames/preview",
        method: "POST",
        body: { model: "m1", prompt: "a cat", text: "draft {{client_prompt}}" },
      },
    ]);
  });
});
