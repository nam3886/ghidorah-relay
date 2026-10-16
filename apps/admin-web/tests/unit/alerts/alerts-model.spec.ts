import { describe, expect, it } from "vitest";
import type { AlertDetail } from "@/api/alerts";
import {
  ackToast,
  alertTime,
  EMPTY_WORDING_TITLE,
  groupFacts,
  groupTitle,
  isEmptyWording,
  ONE_CUSTOMER_NOTE,
  oneCustomer,
  outcomesText,
  outcomeStatus,
  REPLY_NOTE,
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
  it("counts delivered and refunded after the reply", () => {
    expect(outcomesText({ outcomes: { done: 3, failed: 1, open: 0 } })).toBe(
      "3 delivered after the reply, 1 refunded.",
    );
  });

  it("adds the jobs still open, only when there are some", () => {
    expect(outcomesText({ outcomes: { done: 0, failed: 0, open: 4 } })).toBe(
      "0 delivered after the reply, 0 refunded, 4 still open.",
    );
  });

  it("a group with no outcomes reads as all zero", () => {
    expect(outcomesText({})).toBe("0 delivered after the reply, 0 refunded.");
  });
});

describe("outcomeStatus", () => {
  it("a failed job was refunded; the other statuses are shown as they are", () => {
    expect(outcomeStatus("failed")).toBe("refunded");
    expect(outcomeStatus("done")).toBe("done");
    expect(outcomeStatus("queued")).toBe("queued");
  });
});

describe("alertTime", () => {
  it("is day, month name and the hour and minute, local time", () => {
    expect(alertTime(new Date(2026, 9, 14, 10, 2).getTime())).toBe("14 Oct 10:02");
    expect(alertTime(new Date(2026, 0, 5, 8, 5).getTime())).toBe("5 Jan 08:05");
  });
  it("a missing time is a dash", () => {
    expect(alertTime(null)).toBe("—");
    expect(alertTime(0)).toBe("—");
  });
});

describe("groupFacts", () => {
  const detail = {
    kind: "unclear_reply",
    provider: "dola",
    first_ms: new Date(2026, 9, 14, 10, 2).getTime(),
    last_ms: new Date(2026, 9, 15, 8, 55).getTime(),
    count: 6,
    open_count: 4,
    keys: 3,
  } as AlertDetail;

  it("lists the seven facts in the order of the design, Open in warn while rows are open", () => {
    expect(groupFacts(detail)).toEqual([
      { label: "Kind", value: "unclear_reply" },
      { label: "Provider", value: "dola" },
      { label: "First seen", value: "14 Oct 10:02" },
      { label: "Last seen", value: "15 Oct 08:55" },
      { label: "Rows", value: 6 },
      { label: "Open", value: 4, tone: "warn" },
      { label: "Keys", value: 3 },
    ]);
  });

  it("Open has no tone at 0", () => {
    expect(groupFacts({ ...detail, open_count: 0 }).find((f) => f.label === "Open")).toEqual({
      label: "Open",
      value: 0,
    });
  });
});

describe("the fixed sentences", () => {
  it("are the ones the detail shows", () => {
    expect(ONE_CUSTOMER_NOTE).toBe("One customer only: may be the customer’s own words.");
    expect(REPLY_NOTE).toBe("The reply is stored as the job’s answer; the job outcome decides what the customer got.");
  });
});

describe("ackToast", () => {
  it("says how many rows, or that nothing was new", () => {
    expect(ackToast(3)).toBe("Acknowledged 3 row(s)");
    expect(ackToast(1)).toBe("Acknowledged 1 row(s)");
    expect(ackToast(0)).toBe("Nothing new to acknowledge");
  });
});
