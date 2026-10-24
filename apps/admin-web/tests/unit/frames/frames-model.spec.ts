import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import {
  appliedLine,
  dayMonth,
  defaultTargetOf,
  followerCount,
  kindsInOrder,
  loadedText,
  lockDay,
  lockHistoryLine,
  lockSentence,
  longStamp,
  markerChecks,
  newestFirst,
  ownCount,
  pickOut,
  PROMPT_MARKER,
  refusalText,
  TRACKING_MARKER,
  versionTag,
} from "@/components/frames/frames-model";
import { fmtTime } from "@/lib/format";
import { frameInfo, framesList, version } from "./fixtures";

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

  it("day and time are day, month name and a two digit clock, in the browser time zone", () => {
    const at = new Date(2026, 9, 4, 8, 5).getTime();
    expect(dayMonth(at)).toBe("4 Oct");
    expect(longStamp(at)).toBe("4 Oct 08:05");
    expect(dayMonth(null)).toBe("-");
    expect(longStamp(0)).toBe("-");
  });

  it("lock cell, lock sentence and lock history", () => {
    const at = dayMonth(1_700_000_000_000);
    expect(lockDay(null)).toBe("");
    expect(lockDay(lock(true))).toBe(`locked ${at}`);
    expect(lockDay(lock(false))).toBe(`unlocked ${at}`);
    expect(lockSentence(null)).toBe("");
    expect(lockSentence(lock(true))).toBe(`Locked ${at} by owner`);
    expect(lockSentence(lock(false))).toBe(`Unlocked ${at} by owner`);
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

describe("the kinds", () => {
  it("a kind opens on its first own frame, else its default; the counts say who follows", () => {
    const [video, image] = framesList().kinds;
    expect(defaultTargetOf(video!)).toBe("m1");
    expect(ownCount(video!)).toBe(1);
    expect(followerCount(video!)).toBe(1);
    expect(defaultTargetOf(image!)).toBe("default");
    expect(ownCount(image!)).toBe(0);
  });

  it("video comes first and the input is left alone", () => {
    const kinds = [...framesList().kinds].reverse();
    expect(kindsInOrder(kinds).map((k) => k.kind)).toEqual(["video", "image"]);
    expect(kinds.map((k) => k.kind)).toEqual(["image", "video"]);
  });
});

describe("the frame text", () => {
  it("each marker must be in once; the checks count them", () => {
    expect(markerChecks(`a ${PROMPT_MARKER} b ${TRACKING_MARKER}`)).toEqual({ prompt: 1, tracking: 1, ok: true });
    expect(markerChecks(`${PROMPT_MARKER} ${PROMPT_MARKER}`)).toEqual({ prompt: 2, tracking: 0, ok: false });
    expect(markerChecks("")).toEqual({ prompt: 0, tracking: 0, ok: false });
    const twice = `${PROMPT_MARKER} ${PROMPT_MARKER} ${TRACKING_MARKER} ${TRACKING_MARKER}`;
    expect(markerChecks(twice)).toEqual({ prompt: 2, tracking: 2, ok: false });
  });

  it("picks the sample prompt and the tracking code out of a preview, in the order they occur", () => {
    expect(pickOut("Scene: a cat [4060]", ["a cat", "[4060]"])).toEqual([
      { text: "Scene: ", hit: false },
      { text: "a cat", hit: true },
      { text: " ", hit: false },
      { text: "[4060]", hit: true },
    ]);
    // a needle that is not there, or is empty, picks nothing; the text is whole
    expect(pickOut("plain", ["", "zzz"])).toEqual([{ text: "plain", hit: false }]);
    expect(pickOut("", ["x"])).toEqual([]);
    // a needle inside one already picked out is not picked again
    expect(pickOut("aa", ["aa", "a"])).toEqual([{ text: "aa", hit: true }]);
  });
});
