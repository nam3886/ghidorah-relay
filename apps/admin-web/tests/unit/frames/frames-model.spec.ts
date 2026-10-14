import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import {
  appliedLine,
  loadedText,
  lockHistoryLine,
  lockLine,
  PROMPT_MARKER,
  refusalText,
  TRACKING_MARKER,
  versionLine,
} from "@/components/frames/frames-model";
import { fmtTime } from "@/lib/format";
import { frameInfo, version } from "./fixtures";

const T = fmtTime(1_700_000_000_000);
const lock = (locked: boolean) => ({ locked, created_ms: 1_700_000_000_000, created_by: "owner", created_by_id: null });

describe("frame lines", () => {
  it("names the markers a frame must hold", () => {
    expect(PROMPT_MARKER).toBe("{{client_prompt}}");
    expect(TRACKING_MARKER).toBe("{{tracking_code}}");
  });

  it("version line", () => {
    expect(versionLine(null)).toBe("no version");
    expect(versionLine(version(7, "x", "ann"))).toBe(`version 7 by ann, ${T}`);
    expect(versionLine({ ...version(8, "x"), restored_from: 3 })).toBe(`version 8 by owner, ${T} (restored from 3)`);
  });

  it("lock line and lock history", () => {
    expect(lockLine(null)).toBe("never toggled");
    expect(lockLine(lock(true))).toBe(`locked by owner, ${T}`);
    expect(lockLine(lock(false))).toBe(`unlocked by owner, ${T}`);
    expect(lockHistoryLine([lock(true), lock(false)])).toBe(
      `Lock history: locked by owner ${T}; unlocked by owner ${T}`,
    );
  });

  it("the text the editor starts with is the current own text, else the text in force, else empty", () => {
    expect(loadedText(frameInfo("own"))).toBe("own");
    expect(loadedText(frameInfo(null))).toBe("effective text");
    expect(loadedText({ current: version(1, null), effective: null, history: [] })).toBe("");
  });

  it("a refusal carries its field", () => {
    expect(refusalText(new ApiError(422, { error: { code: "c", message: "bad", param: "text" } }))).toBe("bad (text)");
    expect(refusalText(new ApiError(422, { error: { code: "c", message: "bad" } }))).toBe("bad");
  });

  it("the apply-to-all summary lists applied, kept and unchanged, with - for none", () => {
    expect(appliedLine({ applied: ["a", "b"], kept: ["c"], unchanged: [], warnings: [] })).toBe(
      "Applied: a, b. Kept (locked): c. Unchanged: -.",
    );
    expect(appliedLine({ applied: [], kept: [], unchanged: ["d"], warnings: [] })).toBe(
      "Applied: -. Kept (locked): -. Unchanged: d.",
    );
  });
});
