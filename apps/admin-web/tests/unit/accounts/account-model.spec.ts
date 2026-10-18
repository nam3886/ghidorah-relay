import { describe, expect, it } from "vitest";
import type { Account } from "@/api/accounts";
import { ApiError } from "@/api/client";
import {
  accountChanges,
  accountSub,
  ago,
  asleepInfo,
  asleepLine,
  checkSummary,
  chipsAfterRead,
  commandChip,
  extraError,
  healthItems,
  hhmm,
  hhmmss,
  importBody,
  importSummary,
  loadFailedText,
  madeLine,
  pointsFigure,
  poolItems,
  poolsInOrder,
  rejectedText,
  proxyCheck,
  proxyChipText,
  proxyStatus,
  proxyText,
  rowMenu,
  statusReason,
} from "@/components/accounts/account-model";

const account = (over: Partial<Account> = {}): Account => ({
  id: "a1",
  provider: "fake",
  label: "main",
  identity: "me@example.com",
  display_name: null,
  state: "available",
  state_reason: null,
  proxy_host: "10.0.0.1",
  proxy_port: 8080,
  has_proxy_auth: true,
  proxy_ok: null,
  proxy_checked_ms: null,
  strikes: 0,
  cooldown_until_ms: null,
  cooldown_reason: null,
  last_error: null,
  last_used_ms: null,
  last_checked_ms: null,
  in_use: false,
  has_screenshot: false,
  composer: null,
  capacity: [],
  made: { video: 0, image: 0 },
  ...over,
});

describe("proxyText", () => {
  it("is host:port, or empty without a proxy", () => {
    expect(proxyText(account())).toBe("10.0.0.1:8080");
    expect(proxyText(account({ proxy_host: null, proxy_port: null }))).toBe("");
  });
});

describe("accountChanges (the edit payload)", () => {
  it("sends nothing when nothing changed", () => {
    expect(accountChanges(account(), "main", "10.0.0.1:8080")).toBeNull();
  });

  it("sends only the label when only the label changed", () => {
    expect(accountChanges(account(), "renamed", "10.0.0.1:8080")).toEqual({ label: "renamed" });
  });

  it("sends only the proxy when only the proxy changed, trimmed", () => {
    expect(accountChanges(account(), "main", "  10.0.0.2:9000:u:p ")).toEqual({ proxy: "10.0.0.2:9000:u:p" });
  });

  it("sends proxy null when the proxy was emptied", () => {
    expect(accountChanges(account(), "main", "")).toEqual({ proxy: null });
    expect(accountChanges(account(), "main", "   ")).toEqual({ proxy: null });
  });

  it("sends both when both changed", () => {
    expect(accountChanges(account(), "x", "")).toEqual({ label: "x", proxy: null });
  });

  it("does not send a proxy that was empty and still is", () => {
    expect(accountChanges(account({ proxy_host: null, proxy_port: null }), "main", "")).toBeNull();
  });

  it("an untouched proxy shown as host:port is not sent back (its password is never shown)", () => {
    expect(accountChanges(account(), "main", proxyText(account()))).toBeNull();
  });
});

describe("importBody", () => {
  it("is null when there is no credential", () => {
    expect(importBody("fake", "", "")).toBeNull();
    expect(importBody("fake", " \n  \n\t", "p:1")).toBeNull();
  });

  it("one item per non-empty line, trimmed", () => {
    expect(importBody("fake", " one \n\ntwo\n", "")).toEqual({
      provider: "fake",
      items: [{ credential: "one" }, { credential: "two" }],
    });
  });

  it("the proxy applies to every item, trimmed", () => {
    expect(importBody("fake", "a\nb", " h:1:u:p ")).toEqual({
      provider: "fake",
      items: [
        { credential: "a", proxy: "h:1:u:p" },
        { credential: "b", proxy: "h:1:u:p" },
      ],
    });
  });
});

describe("command chips", () => {
  const command = (status: string, over: object = {}) => ({ id: "c", status, result: null, ...over }) as never;

  it("the end of a poll: ok with the time it finished, error with the worker's reason, running when it gave up", () => {
    const finished = Date.UTC(2026, 9, 15, 13, 55, 7);
    expect(commandChip(command("done", { finished_ms: finished }), 4)).toEqual({
      state: "ok",
      time: hhmmss(finished),
      settled: true,
      read: 4,
    });
    expect(commandChip(command("failed", { result: { error: "connect timeout after 8 s" } }), 4)).toEqual({
      state: "error",
      message: "connect timeout after 8 s",
      settled: true,
      read: 4,
    });
    expect(commandChip(null, 4)).toEqual({ state: "running", settled: true, read: 4 });
  });

  it("a finished chip stays through the next automatic read and goes with the one after; a running one stays", () => {
    const chips = {
      running: { state: "running", settled: false, read: 0 },
      done: { state: "ok", time: "20:55:07", settled: true, read: 2 },
    } as const;
    expect(Object.keys(chipsAfterRead(chips, 3))).toEqual(["running", "done"]);
    expect(Object.keys(chipsAfterRead(chips, 4))).toEqual(["running"]);
  });
});

describe("rejectedText", () => {
  it("says why a line was rejected in a sentence; an unknown code stays as it is", () => {
    expect(rejectedText("credential_unparseable")).toBe("The line is not a cookie jar.");
    expect(rejectedText("proxy_invalid")).toBe("The proxy is not host:port or host:port:user:pass.");
    expect(rejectedText("something_new")).toBe("something_new");
  });
});

describe("importSummary", () => {
  it("counts, with the rejected lines numbered from 1", () => {
    expect(importSummary({ imported: [{ index: 0, id: "x", label: "l" }], rejected: [] })).toBe(
      "Imported 1, rejected 0",
    );
    expect(
      importSummary({
        imported: [],
        rejected: [
          { index: 0, reason: "credential_unparseable" },
          { index: 3, reason: "proxy_invalid" },
        ],
      }),
    ).toBe("Imported 0, rejected 2 (line 1: credential_unparseable; line 4: proxy_invalid)");
  });
});

describe("asleepInfo", () => {
  const now = 1_000_000;
  const rest = (until: number | null, reason: string | null) => ({
    pool: "video",
    left_n: 1,
    left_today: 1,
    budget: 5,
    drift: null,
    source: "counted",
    day_anchor: null,
    rest_until_ms: until,
    rest_reason: reason,
  });

  it("a cooldown still ahead: the time it ends, a strike when the account has strikes, and the reason", () => {
    const a = account({ cooldown_until_ms: now + 5000, cooldown_reason: "dola.likeness_refused", strikes: 1 });
    expect(asleepInfo(a, now)).toEqual({ until: hhmm(now + 5000), kind: "strike", why: "dola.likeness_refused" });
    expect(asleepInfo(account({ cooldown_until_ms: now + 5000, cooldown_reason: "x" }), now)?.kind).toBeNull();
  });

  it("with no cooldown the first pool that still rests counts, with its own reason", () => {
    const a = account({ capacity: [rest(now - 1, "old"), rest(now + 9000, "quota")] });
    expect(asleepInfo(a, now)).toEqual({ until: hhmm(now + 9000), kind: null, why: "quota" });
  });

  it("a time in the past, or none, is awake", () => {
    expect(asleepInfo(account({ cooldown_until_ms: now - 1, capacity: [rest(now - 1, "old")] }), now)).toBeNull();
    expect(asleepInfo(account(), now)).toBeNull();
  });

  it("the one-line form is `asleep until HH:MM`, plus ` · strike` for a strike nap", () => {
    expect(asleepLine({ until: "21:30", kind: "strike", why: "r" })).toBe("asleep until 21:30 · strike");
    expect(asleepLine({ until: "21:30", kind: null, why: null })).toBe("asleep until 21:30");
  });
});

describe("the text of a row", () => {
  it("the second line is name, provider and composer, with no empty piece", () => {
    expect(accountSub(account({ display_name: "Demo User H", provider: "dola", composer: "v2" }))).toBe(
      "Demo User H · dola · composer v2",
    );
    expect(accountSub(account({ display_name: null, identity: null, provider: "dola" }))).toBe("dola");
    expect(accountSub(account({ display_name: null, identity: "me@x", provider: "dola" }))).toBe("me@x · dola");
  });

  it("the video pool comes first, the others keep their order; the tablet counts are singular for one", () => {
    const pool = (name: string) => ({ pool: name }) as Account["capacity"][number];
    expect(poolsInOrder([pool("image"), pool("video"), pool("audio")]).map((c) => c.pool)).toEqual([
      "video",
      "image",
      "audio",
    ]);
    expect(madeLine({ video: 31, image: 12 })).toBe("31 videos · 12 images");
    expect(madeLine({ video: 1, image: 1 })).toBe("1 video · 1 image");
    expect(madeLine({ video: 0, image: 0 })).toBe("0 videos · 0 images");
  });

  it("a switched-off account shows no reason under its pill; a dead one shows its reason", () => {
    expect(statusReason({ state: "disabled", state_reason: "disabled by admin" })).toBeNull();
    expect(statusReason({ state: "dead", state_reason: "account.dead_session" })).toBe("account.dead_session");
    expect(statusReason({ state: "available", state_reason: null })).toBeNull();
  });

  it("the failed read of the list says what the relay answered, as drawn", () => {
    const answered = new ApiError(502, { error: { code: "x", message: "bad gateway" } });
    expect(loadFailedText(answered, "bad gateway")).toBe(
      "Could not load the accounts: the relay answered 502 (bad gateway).",
    );
    expect(loadFailedText(new TypeError("x"), "Network error, please try again")).toBe(
      "Could not load the accounts: network error, please try again.",
    );
  });

  it("a narrow row shows the last error only when it adds to the reason", () => {
    expect(extraError({ last_error: "e", state_reason: "e" })).toBeNull();
    expect(extraError({ last_error: null, state_reason: "e" })).toBeNull();
    expect(extraError({ last_error: "e", state_reason: null })).toBe("e");
  });

  it("the proxy chip says ok or failed with how long ago, and not checked alone", () => {
    const t = 1_000_000_000_000;
    const base = { proxy_checked_ms: t - 4 * 60_000 };
    expect(proxyChipText(account({ ...base, proxy_ok: true }), t)).toBe("ok · 4 min ago");
    expect(proxyChipText(account({ ...base, proxy_ok: false }), t)).toBe("failed · 4 min ago");
    expect(proxyChipText(account({ ...base, proxy_ok: null }), t)).toBe("not checked");
  });

  it("the proxy status of the Edit dialog carries the stored reason of a failed check", () => {
    const failed = account({ proxy_ok: false, last_error: "proxy check: connect timeout after 8 s" });
    expect(proxyStatus(failed)).toEqual({ tone: "bad", text: "failed · connect timeout after 8 s" });
    const t = 1_000_000_000_000;
    expect(proxyStatus(account({ proxy_ok: false, last_error: "boom", proxy_checked_ms: t - 120_000 }), t).text).toBe(
      "failed · 2 min ago",
    );
  });

  it("the row menu: Shot cannot be picked without a screenshot, Delete is last and apart, a phone adds Check", () => {
    const keys = (items: { key: string }[]) => items.map((i) => i.key);
    expect(keys(rowMenu({ has_screenshot: true }, false))).toEqual(["shot", "wake", "edit", "delete"]);
    expect(keys(rowMenu({ has_screenshot: true }, true))).toEqual(["check", "shot", "wake", "edit", "delete"]);
    expect(rowMenu({ has_screenshot: false }, false)[0]).toMatchObject({ key: "shot", disabled: true });
    expect(rowMenu({ has_screenshot: true }, false)[0]!.disabled).toBe(false);
    const del = rowMenu({ has_screenshot: true }, false).at(-1)!;
    expect(del).toMatchObject({ key: "delete", danger: true, separated: true });
  });
});

describe("the pool card figures", () => {
  const stats = { usable: 7, available: 8, dead: 1, disabled: 0 };

  it("health: ready is usable / available, dead is bad only when there is one, in use and asleep are the stats'", () => {
    expect(healthItems({ ...stats, in_use: 2, asleep: 1 })).toEqual([
      { label: "Accounts ready", value: "7 / 8", mono: true },
      { label: "Accounts dead", value: 1, tone: "bad", mono: true },
      { label: "Accounts disabled", value: 0, tone: undefined, mono: true },
      { label: "In use", value: 2, tone: undefined, mono: true },
      { label: "Asleep", value: 1, tone: undefined, mono: true },
    ]);
    expect(healthItems({ ...stats, dead: 0 })[1]).toMatchObject({ value: 0, tone: undefined });
  });

  it("an old server without in use and asleep shows dashes for those two, not zeros", () => {
    const items = healthItems(stats);
    expect(items[3]!.value).toBe("-");
    expect(items[4]!.value).toBe("-");
  });

  it("health without stats yet shows dashes, never zeros", () => {
    const items = healthItems(null);
    expect(items[0]!.value).toBe("-");
    expect(items[1]!.value).toBe("-");
    expect(items[2]!.value).toBe("-");
  });

  it("pool: video points left / budget, unknown, drift as a warning, failed proxies as bad, the last full check", () => {
    const at = new Date(2026, 9, 15, 20, 48, 10).getTime();
    const items = poolItems({
      pools: [
        { pool: "image", left: 0, budget: 0, unknown: 9, drift: 5 },
        { pool: "video", left: 143, budget: 210, unknown: 2, drift: 1 },
      ],
      proxy_failed: 1,
      last_full_check_ms: at,
    });
    expect(items.map((i) => [i.label, i.value, i.tone])).toEqual([
      ["Video points left", "143 / 210", undefined],
      ["Points unknown", "2 accounts", undefined],
      ["Drift flagged", 1, "warn"],
      ["Proxy checks failed", 1, "bad"],
      ["Last full check", "20:48:10", undefined],
    ]);
  });

  it("pool: one account is singular, zeros carry no colour, no full check reads never, no answer shows dashes", () => {
    const items = poolItems({
      pools: [{ pool: "video", left: 3, budget: 30, unknown: 1, drift: 0 }],
      proxy_failed: 0,
      last_full_check_ms: null,
    });
    expect(items[1]!.value).toBe("1 account");
    expect(items[2]).toMatchObject({ value: 0, tone: undefined });
    expect(items[3]).toMatchObject({ value: 0, tone: undefined });
    expect(items[4]!.value).toBe("never");
    expect(poolItems(null).map((i) => i.value)).toEqual(["-", "-", "-", "-", "-"]);
    expect(poolItems({ pools: [], proxy_failed: 0, last_full_check_ms: null })[0]!.value).toBe("0 / 0");
  });
});

describe("pointsFigure, ago, proxyCheck, checkSummary", () => {
  const cap = (left: number | null, budget: number | null) => ({
    pool: "video",
    left_n: left,
    left_today: left,
    budget,
    drift: null,
    source: "counted",
    day_anchor: null,
    rest_until_ms: null,
    rest_reason: null,
  });
  it("shows ? when the count is unknown and the budget when there is one", () => {
    expect(pointsFigure(cap(3, 10))).toBe("3 / 10");
    expect(pointsFigure(cap(null, 10))).toBe("? / 10");
    expect(pointsFigure(cap(0, null))).toBe("0");
    expect(pointsFigure(cap(null, null))).toBe("?");
  });

  it("ago: never, just now, minutes, hours, days; a future time is just now", () => {
    const t = 1_000_000_000_000;
    expect(ago(null, t)).toBe("never");
    expect(ago(t - 59_000, t)).toBe("just now");
    expect(ago(t + 5 * 60_000, t)).toBe("just now");
    expect(ago(t - 2 * 60_000, t)).toBe("2 min ago");
    expect(ago(t - 59 * 60_000, t)).toBe("59 min ago");
    expect(ago(t - 60 * 60_000, t)).toBe("1 h ago");
    expect(ago(t - 47 * 3_600_000, t)).toBe("47 h ago");
    expect(ago(t - 48 * 3_600_000, t)).toBe("2 d ago");
  });

  it("proxy check: ok, failed, not checked", () => {
    expect(proxyCheck(account({ proxy_ok: true }))).toEqual({ label: "ok", tone: "ok" });
    expect(proxyCheck(account({ proxy_ok: false }))).toEqual({ label: "failed", tone: "bad" });
    expect(proxyCheck(account({ proxy_ok: null }))).toEqual({ label: "not checked", tone: "neutral" });
  });

  it("check summary names the outcome, the state and the reason", () => {
    expect(checkSummary("ok", { state: "available", state_reason: null })).toBe("Check: ok - account available");
    expect(checkSummary(undefined, { state: "dead", state_reason: "login failed" })).toBe(
      "Check: done - account dead (login failed)",
    );
  });
});
