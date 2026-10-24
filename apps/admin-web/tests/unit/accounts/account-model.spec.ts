import { describe, expect, it } from "vitest";
import type { Account } from "@/api/accounts";
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
  healthOf,
  hhmm,
  inIssue,
  issueLine,
  hhmmss,
  importBody,
  madeLine,
  pointsFigure,
  poolsInOrder,
  rejectedText,
  proxyCheck,
  proxyChipText,
  proxyStatus,
  proxyText,
  rowAction,
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
  it("says why a line was rejected in a sentence; any other code gets one fixed sentence", () => {
    expect(rejectedText("credential_unparseable")).toBe("The line is not a cookie jar.");
    expect(rejectedText("proxy_invalid")).toBe("The proxy is not host:port or host:port:user:pass.");
    // any other code gets the one fixed sentence: nothing the server sends back is shown, whatever it carries
    expect(rejectedText("something_new")).toBe("The line could not be imported.");
    expect(rejectedText("sessionid=secret-cookie-value; csrftoken=abc")).toBe("The line could not be imported.");
    expect(rejectedText("__proto__")).toBe("The line could not be imported.");
    expect(rejectedText("toString")).toBe("The line could not be imported.");
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
  it("the second line is name and composer, with no empty piece; the lane names the provider", () => {
    expect(accountSub(account({ display_name: "Demo User H", provider: "dola", composer: "v2" }))).toBe(
      "Demo User H · composer v2",
    );
    expect(accountSub(account({ display_name: null, identity: null, provider: "dola", composer: "v2" }))).toBe(
      "composer v2",
    );
    expect(accountSub(account({ display_name: null, identity: "me@x", provider: "dola" }))).toBe("me@x");
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
    // the toasts' form, "<label> done: <detail>", no " - "
    expect(checkSummary("checked", { state: "available", state_reason: null })).toBe(
      "Check done: checked, account available",
    );
    expect(checkSummary(undefined, { state: "dead", state_reason: "login failed" })).toBe(
      "Check done: account dead (login failed)",
    );
  });
});

describe("healthOf, the Show chips and the summary", () => {
  const t = 1_000_000_000_000;
  const pool = (left: number, budget: number) =>
    ({ pool: "video", left_today: left, budget }) as Account["capacity"][number];

  it("names one health per account, the first that holds", () => {
    expect(healthOf(account({ state: "disabled", state_reason: "dead too" }), false, t).kind).toBe("off");
    expect(healthOf(account({ state: "dead", state_reason: "account.dead_session" }), false, t)).toMatchObject({
      kind: "dead",
      label: "Dead",
      why: "account.dead_session",
      tone: "bad",
    });
    const failing = account({ proxy_ok: false, last_error: "proxy check: connect timeout after 8 s" });
    expect(healthOf(failing, false, t)).toMatchObject({ kind: "proxy", why: "connect timeout after 8 s" });
    const asleep = healthOf(account({ cooldown_until_ms: t + 60_000, cooldown_reason: "x.y" }), false, t);
    expect(asleep).toMatchObject({ kind: "asleep", why: "x.y", tone: "warn" });
    expect(asleep.label).toMatch(/^Asleep until \d\d:\d\d$/);
    expect(asleep.short).toMatch(/^Asleep · \d\d:\d\d$/);
    expect(healthOf(account({ capacity: [pool(0, 4)] }), false, t)).toMatchObject({
      kind: "low",
      why: "video 0 of 4 left",
    });
    expect(healthOf(account({ capacity: [pool(1, 4)] }), false, t).kind).toBe("ok");
    expect(healthOf(account({ in_use: true }), true, t).kind).toBe("inuse");
    expect(healthOf(account(), true, t).label).toBe("Paused");
    expect(healthOf(account(), false, t).label).toBe("Available");
  });

  it("Needs a look holds dead, proxy failing, asleep and low; the row action is Re-check or Wake", () => {
    const h = (over: Partial<Account>) => healthOf(account(over), false, t);
    expect(inIssue("look", h({ state: "dead" }))).toBe(true);
    expect(inIssue("look", h({ in_use: true }))).toBe(false);
    expect(inIssue("off", h({ state: "disabled" }))).toBe(true);
    expect(inIssue("all", h({}))).toBe(true);
    expect(rowAction(h({ state: "dead" }))).toBe("check");
    expect(rowAction(h({ cooldown_until_ms: t + 60_000 }))).toBe("wake");
    expect(rowAction(h({}))).toBeNull();
  });

  it("the summary names the issues that have accounts, in order", () => {
    const hs = [
      healthOf(account({ state: "dead" }), false, t),
      healthOf(account({ state: "dead" }), false, t),
      healthOf(account({ cooldown_until_ms: t + 60_000 }), false, t),
      healthOf(account(), false, t),
    ];
    expect(issueLine(hs)).toBe("2 dead, 1 asleep");
    expect(issueLine([])).toBe("");
  });

  it("accountChanges sends the Taking jobs switch only when it was flipped", () => {
    expect(accountChanges(account(), "main", "10.0.0.1:8080", true)).toBeNull();
    expect(accountChanges(account(), "main", "10.0.0.1:8080", false)).toEqual({ enabled: false });
    expect(accountChanges(account({ state: "disabled" }), "main", "10.0.0.1:8080", true)).toEqual({ enabled: true });
    expect(accountChanges(account(), "main", "10.0.0.1:8080")).toBeNull();
  });
});
