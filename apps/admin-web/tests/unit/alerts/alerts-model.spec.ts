import { describe, expect, it } from "vitest";
import {
  ackToast,
  EMPTY_WORDING_TITLE,
  groupTitle,
  isEmptyWording,
  oneCustomer,
  outcomesText,
} from "@/components/alerts/alerts-model";

describe("the empty-wording group", () => {
  const empty = { kind: "unclear_reply", title: EMPTY_WORDING_TITLE, keys: 1 };

  it("is rewritten as an API read failure, only for an unclear reply", () => {
    expect(isEmptyWording(empty)).toBe(true);
    expect(groupTitle(empty)).toBe("API read failure: no reply text was read");
    expect(isEmptyWording({ ...empty, kind: "clamp" })).toBe(false);
    expect(groupTitle({ ...empty, kind: "clamp" })).toBe(EMPTY_WORDING_TITLE);
  });

  it("any other title is shown as it is", () => {
    expect(groupTitle({ kind: "unclear_reply", title: "I cannot do that" })).toBe("I cannot do that");
  });
});

describe("oneCustomer", () => {
  it("is for an unclear wording seen under exactly one key", () => {
    expect(oneCustomer({ kind: "unclear_reply", title: "words", keys: 1 })).toBe(true);
    expect(oneCustomer({ kind: "unclear_reply", title: "words", keys: 2 })).toBe(false);
    expect(oneCustomer({ kind: "unclear_reply", title: "words", keys: 0 })).toBe(false);
  });

  it("is not for a billing group, nor for the read failure (it has no wording)", () => {
    expect(oneCustomer({ kind: "clamp", title: "x", keys: 1 })).toBe(false);
    expect(oneCustomer({ kind: "drift", title: "x", keys: 1 })).toBe(false);
    expect(oneCustomer({ kind: "unclear_reply", title: EMPTY_WORDING_TITLE, keys: 1 })).toBe(false);
  });
});

describe("outcomesText", () => {
  it("counts delivered, refunded and still open, with the refusal codes", () => {
    expect(
      outcomesText({
        outcomes: { done: 3, failed: 2, open: 1 },
        failed_codes: { "content.refused": 2, "upstream.error": 1 },
      }),
    ).toBe(
      "Jobs of this wording: delivered 3, refunded 2, still open 1 (refused with: content.refused x2, upstream.error x1). " +
        "Delivered means the wording was an acceptance; refunded, a refusal or nothing.",
    );
  });

  it("leaves the codes out when there are none", () => {
    expect(outcomesText({ outcomes: { done: 0, failed: 0, open: 4 }, failed_codes: {} })).toBe(
      "Jobs of this wording: delivered 0, refunded 0, still open 4. " +
        "Delivered means the wording was an acceptance; refunded, a refusal or nothing.",
    );
  });
});

describe("ackToast", () => {
  it("says how many rows, or that nothing was new", () => {
    expect(ackToast(3)).toBe("Acknowledged 3 row(s)");
    expect(ackToast(1)).toBe("Acknowledged 1 row(s)");
    expect(ackToast(0)).toBe("Nothing new to acknowledge");
  });
});
