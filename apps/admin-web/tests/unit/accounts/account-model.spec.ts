import { describe, expect, it } from "vitest";
import type { Account } from "@/api/accounts";
import {
  accountChanges,
  asleepLines,
  checkSummary,
  importBody,
  importSummary,
  pointsText,
  proxyCheck,
  proxyText,
} from "@/components/accounts/account-model";
import { fmtTime } from "@/lib/format";

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
  cooldown_ms: null,
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

describe("asleepLines", () => {
  const now = 1_000_000;
  it("lists the cooldown and the resting pools that are still in the future", () => {
    const a = account({
      cooldown_ms: now + 5000,
      cooldown_reason: "strike",
      capacity: [
        {
          pool: "video",
          left_n: 1,
          left_today: 1,
          budget: 5,
          drift: null,
          source: "counted",
          day_anchor: null,
          rest_until_ms: now + 9000,
          rest_reason: "quota",
        },
        {
          pool: "image",
          left_n: 1,
          left_today: 1,
          budget: 5,
          drift: null,
          source: "counted",
          day_anchor: null,
          rest_until_ms: now - 1,
          rest_reason: "old",
        },
      ],
    });
    expect(asleepLines(a, now)).toEqual([
      `cooldown until ${fmtTime(now + 5000)} (strike)`,
      `video rests until ${fmtTime(now + 9000)} (quota)`,
    ]);
  });

  it("is empty when everything is in the past", () => {
    expect(asleepLines(account({ cooldown_ms: now - 1 }), now)).toEqual([]);
  });
});

describe("pointsText, proxyCheck, checkSummary", () => {
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
    expect(pointsText(cap(3, 10))).toBe("video: 3 / 10");
    expect(pointsText(cap(null, 10))).toBe("video: ? / 10");
    expect(pointsText(cap(0, null))).toBe("video: 0");
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
