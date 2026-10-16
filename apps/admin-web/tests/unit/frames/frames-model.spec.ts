import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import {
  appliedLine,
  loadedText,
  lockHistoryLine,
  lockLine,
  newestFirst,
  PROMPT_MARKER,
  refusalText,
  shortDay,
  shortStamp,
  TRACKING_MARKER,
  versionTag,
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

  it("version tag", () => {
    expect(versionTag(null)).toBe("no version");
    expect(versionTag(version(7, "x", "ann"))).toBe("v7");
  });

  it("day and time are day first, two digits each, in the browser time zone", () => {
    const at = new Date(2026, 9, 4, 8, 5).getTime();
    expect(shortDay(at)).toBe("04/10");
    expect(shortStamp(at)).toBe("04/10 08:05");
  });

  it("lock line and lock history", () => {
    expect(lockLine(null)).toBe("");
    expect(lockLine(lock(true))).toBe(`locked ${shortDay(1_700_000_000_000)} by owner`);
    expect(lockLine(lock(false))).toBe(`unlocked ${shortDay(1_700_000_000_000)} by owner`);
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

describe("the history", () => {
  it("lists the newest version first and leaves the input alone", () => {
    const history = [version(2, "b"), version(9, "i"), version(5, "e")];
    expect(newestFirst(history).map((v) => v.id)).toEqual([9, 5, 2]);
    expect(history.map((v) => v.id)).toEqual([2, 9, 5]);
  });
});
