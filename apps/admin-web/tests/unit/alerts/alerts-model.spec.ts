import { describe, expect, it } from "vitest";
import type { AlertDetail } from "@/api/alerts";
import {
  ackToast,
  alertTime,
  alertTitle,
  EMPTY_WORDING_TITLE,
  groupFacts,
  groupSentence,
  groupTitle,
  isEmptyWording,
  liveWord,
  ONE_CUSTOMER_NOTE,
  oneCustomer,
  outcomesText,
  outcomeStatus,
  replyNote,
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

describe("groupSentence (the Jobs banner's muted sentence)", () => {
  const g = {
    kind: "unclear_reply",
    title: "Sorry, I can’t create that video",
    provider: "dola",
    open_count: 4,
    keys: 3,
  };

  it("says who replied what to how many open jobs from how many keys, as drawn", () => {
    expect(groupSentence(g)).toBe("Dola replied “Sorry, I can’t create that video” to 4 jobs from 3 keys.");
    expect(groupSentence({ ...g, open_count: 1, keys: 1 })).toBe(
      "Dola replied “Sorry, I can’t create that video” to 1 job from 1 key.",
    );
  });

  it("quotes the stored reply on one line when there is one, cut at 200 characters", () => {
    expect(
      groupSentence({ ...g, title: "sorry, i can't create that video" }, " Sorry, I can’t\n\ncreate that video "),
    ).toBe("Dola replied “Sorry, I can’t create that video” to 4 jobs from 3 keys.");
    expect(groupSentence({ ...g, title: "folded" }, "   ")).toBe("Dola replied “folded” to 4 jobs from 3 keys.");
    const long = groupSentence(g, "x".repeat(250));
    expect(long).toContain(`“${"x".repeat(200)}…”`);
  });

  it("a reply with no text, or a group of another kind, says its title", () => {
    expect(groupSentence({ ...g, title: EMPTY_WORDING_TITLE })).toBe("API read failure: no reply text was read.");
    expect(groupSentence({ ...g, kind: "clamp", title: "clamp: dola video wallet of key k1" })).toBe(
      "clamp: dola video wallet of key k1.",
    );
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
  });

  it("the reply is the video's or the image's answer when every job is one, else the job's", () => {
    const tail = "answer; the job outcome decides what the customer got.";
    expect(replyNote("video")).toBe(`The reply is stored as the video’s ${tail}`);
    expect(replyNote("image")).toBe(`The reply is stored as the image’s ${tail}`);
    expect(replyNote(null)).toBe(`The reply is stored as the job’s ${tail}`);
    expect(replyNote(undefined)).toBe(`The reply is stored as the job’s ${tail}`);
    expect(replyNote("audio")).toBe(`The reply is stored as the job’s ${tail}`);
  });
});

describe("ackToast", () => {
  it("says how many rows, or that nothing was new", () => {
    expect(ackToast(3)).toBe("Acknowledged 3 row(s)");
    expect(ackToast(1)).toBe("Acknowledged 1 row(s)");
    expect(ackToast(0)).toBe("Nothing new to acknowledge");
  });
});

describe("alertTitle (a group's name on the Alerts page)", () => {
  const reply = {
    kind: "unclear_reply",
    group: "0123456789abcdef",
    title: "sorry, i can't create that video",
    provider: "dola",
    reply: "Sorry, I can’t create that video",
    key_label: null,
  };
  const clamp = {
    kind: "clamp",
    group: "6b1f0c2e-0000-4000-8000-000000000000.dola.video",
    title: "clamp: dola video wallet of key 6b1f0c2e-0000-4000-8000-000000000000",
    provider: "dola",
    reply: null,
    key_label: "Shop A",
  };

  it("a reply group is who replied and the newest reply as stored, else its folded wording", () => {
    expect(alertTitle(reply)).toBe("Dola replied “Sorry, I can’t create that video”");
    expect(alertTitle({ ...reply, reply: " Sorry,\n\nI can’t " })).toBe("Dola replied “Sorry, I can’t”");
    expect(alertTitle({ ...reply, reply: null })).toBe("Dola replied “sorry, i can't create that video”");
    expect(alertTitle({ ...reply, reply: "y".repeat(250) })).toBe(`Dola replied “${"y".repeat(200)}…”`);
  });

  it("the read failure keeps its own words", () => {
    expect(alertTitle({ ...reply, title: EMPTY_WORDING_TITLE, reply: "" })).toBe(
      "API read failure: no reply text was read",
    );
  });

  it("a billing group is its wallet and the key's name, else the server's title", () => {
    expect(alertTitle(clamp)).toBe("dola video wallet of key Shop A");
    expect(alertTitle({ ...clamp, kind: "drift", group: clamp.group.replace("video", "image") })).toBe(
      "dola image wallet of key Shop A",
    );
    expect(alertTitle({ ...clamp, key_label: null })).toBe(clamp.title);
    expect(alertTitle({ ...clamp, key_label: undefined })).toBe(clamp.title);
    expect(alertTitle({ ...clamp, group: "no-wallet" })).toBe(clamp.title);
  });
});

describe("liveWord (the word before a live condition)", () => {
  it("names what the condition is about, as the canvas writes `pool:`", () => {
    expect(liveWord("accounts_low")).toBe("pool");
    expect(liveWord("account_dead")).toBe("account");
    expect(["worker_stale", "worker_errors", "no_worker"].map(liveWord)).toEqual(["worker", "worker", "worker"]);
    expect(["error_rate", "drift", "disk_low", "webhooks_failed"].map(liveWord)).toEqual([
      "jobs",
      "jobs",
      "disk",
      "webhooks",
    ]);
  });

  it("a kind it does not know shows as it is, whatever its name", () => {
    expect(liveWord("new_kind")).toBe("new_kind");
    expect(liveWord("constructor")).toBe("constructor");
    expect(liveWord("__proto__")).toBe("__proto__");
  });
});
