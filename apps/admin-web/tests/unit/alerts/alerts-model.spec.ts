import { describe, expect, it } from "vitest";
import type { AlertDetail } from "@/api/alerts";
import {
  jobsLink,
  rangeFor,
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
  ackLabel,
  groupLine,
  openSummary,
  seenText,
  severityOf,
} from "@/components/alerts/alerts-model";

describe("the severity cards", () => {
  it("error is Critical, warning a Warning, any other word Info", () => {
    expect(["error", "warning", "info", "notice"].map(severityOf)).toEqual(["error", "warning", "info", "info"]);
  });

  it("the summary counts what is open by severity, only the severities that have some", () => {
    expect(openSummary({ error: 2, warning: 3, info: 1 })).toBe("6 open: 2 critical, 3 warnings, 1 info.");
    expect(openSummary({ error: 0, warning: 1, info: 0 })).toBe("1 open: 1 warning.");
    expect(openSummary({ error: 0, warning: 0, info: 0 })).toBe("Nothing open.");
  });

  it("a group's line says its kind, its open rows and its keys", () => {
    expect(groupLine({ kind: "clamp", count: 3, open_count: 2, keys: 1 })).toBe("clamp · 2 of 3 rows open · 1 key");
    expect(ackLabel(1)).toBe("Acknowledge 1 row");
    expect(ackLabel(4)).toBe("Acknowledge 4 rows");
  });

  it("first seen today is the time and how long ago; another day is the day and the time", () => {
    const now = new Date(2026, 9, 24, 14, 32).getTime();
    expect(seenText(new Date(2026, 9, 24, 14, 5).getTime(), now)).toBe("14:05 · 27 min ago");
    expect(seenText(new Date(2026, 9, 24, 11, 20).getTime(), now)).toBe("11:20 · 3 h ago");
    expect(seenText(now, now)).toBe("14:32 · just now");
    expect(seenText(new Date(2026, 9, 22, 9, 1).getTime(), now)).toBe("22 Oct 09:01");
    expect(seenText(null, now)).toBe("—");
  });
});

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
    expect(groupSentence({ ...g, kind: "clamp", title: "clamp: points wallet of key k1" })).toBe(
      "clamp: points wallet of key k1.",
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
    group: "6b1f0c2e-0000-4000-8000-000000000000.*",
    title: "clamp: points wallet of key 6b1f0c2e-0000-4000-8000-000000000000",
    provider: "",
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
    expect(alertTitle(clamp)).toBe("points wallet of key Shop A");
    expect(alertTitle({ ...clamp, kind: "drift", group: clamp.group.replace("*", "dola"), provider: "dola" })).toBe(
      "dola cap of key Shop A",
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

describe("jobsLink (Open Jobs filtered)", () => {
  const now = new Date(2026, 9, 24, 15, 0).getTime();
  const at = (days: number, hour = 9): number => new Date(2026, 9, 24 - days, hour, 0).getTime();
  const unclear = (over: Record<string, unknown> = {}) =>
    ({
      kind: "unclear_reply",
      group: "abc",
      provider: "dola",
      first_ms: at(0),
      outcomes: { done: 0, failed: 2, open: 0 },
      items: [
        { account_label: "dola-01", key_label: "Shop A" },
        { account_label: "dola-01", key_label: "Shop B" },
      ],
      ...over,
    }) as unknown as Parameters<typeof jobsLink>[0];

  it("the range holds the group's first row: today, 7 days, else 30", () => {
    expect(rangeFor(at(0, 0), now)).toBe("today");
    expect(rangeFor(at(1, 23), now)).toBe("7d");
    expect(rangeFor(at(6, 0), now)).toBe("7d");
    expect(rangeFor(at(7, 23), now)).toBe("30d");
    expect(rangeFor(null, now)).toBe("today");
  });

  it("an unclear group searches the one account its rows share, else the one key, else nothing", () => {
    expect(jobsLink(unclear(), now)).toEqual({
      provider: "dola",
      query: { range: "today", q: "dola-01", status: "failed" },
    });
    const twoAccounts = unclear({
      items: [
        { account_label: "dola-01", key_label: "Shop A" },
        { account_label: "dola-02", key_label: "Shop A" },
      ],
    });
    expect(jobsLink(twoAccounts, now).query.q).toBe("Shop A");
    const nothingShared = unclear({
      items: [
        { account_label: "dola-01", key_label: "Shop A" },
        { account_label: "dola-02", key_label: "Shop B" },
      ],
    });
    expect(jobsLink(nothingShared, now).query.q).toBeUndefined();
  });

  it("an unclear group asks for failed jobs only when every one of them was refunded", () => {
    expect(jobsLink(unclear({ outcomes: { done: 1, failed: 2, open: 0 } }), now).query.status).toBeUndefined();
    expect(jobsLink(unclear({ outcomes: { done: 0, failed: 2, open: 1 } }), now).query.status).toBeUndefined();
  });

  it("a drift of a cap names its key and its provider and no status", () => {
    const drift = { kind: "drift", group: "k9.flow", provider: "flow", first_ms: at(3), items: [] };
    expect(jobsLink(drift as unknown as Parameters<typeof jobsLink>[0], now)).toEqual({
      provider: "flow",
      query: { range: "7d", key_id: "k9" },
    });
  });
});
