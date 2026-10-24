import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import type { ApiKey, Wallet } from "@/api/keys";
import {
  compareWallets,
  createdText,
  createError,
  deleteToast,
  keyBody,
  keyForm,
  type KeyForm,
  keyName,
  keyReaches,
  loadFailedText,
  modelGroups,
  relativeLabel,
  relativePercent,
  sortedWallets,
  unfinishedText,
  usageLabel,
  usagePercent,
  walletMaxima,
  walletScopes,
  walletText,
} from "@/components/keys/key-model";

const wallet = (over: Partial<Wallet>): Wallet => ({
  scope: "fake",
  day_limit: null,
  total_limit: null,
  used_day: 0,
  used_total: 0,
  mode: "limit",
  ...over,
});

describe("modelGroups", () => {
  const catalog = [
    { id: "vibes-video", owned_by: "vibes", kind: "video" },
    { id: "seedance-2.0", owned_by: "dola", kind: "video", aliases: ["seedance-2"] },
    { id: "seedream-4.5", owned_by: "dola", kind: "image", disabled: true },
  ];

  it("groups by provider in the drawn order, finds aliases, marks paused, and puts unknown names last", () => {
    expect(modelGroups(["vibes-video", "gone", "seedream-4.5", "seedance-2"], catalog)).toEqual([
      {
        provider: "dola",
        models: [
          { id: "seedream-4.5", paused: true },
          { id: "seedance-2", paused: false },
        ],
      },
      { provider: "vibes", models: [{ id: "vibes-video", paused: false }] },
      { provider: "", models: [{ id: "gone", paused: false }] },
    ]);
  });

  it("keeps one provider's group when asked, and has none for any model", () => {
    expect(modelGroups(["vibes-video", "seedance-2.0"], catalog, "vibes").map((g) => g.provider)).toEqual(["vibes"]);
    expect(modelGroups(["*"], catalog)).toEqual([]);
  });

  it("a key reaches a provider by any model or by one of its models", () => {
    expect(keyReaches({ models: ["*"] }, "meta", catalog)).toBe(true);
    expect(keyReaches({ models: ["seedance-2"] }, "dola", catalog)).toBe(true);
    expect(keyReaches({ models: ["seedance-2"] }, "vibes", catalog)).toBe(false);
  });
});

describe("walletText", () => {
  it("closed", () => {
    expect(walletText(wallet({ mode: "closed", day_limit: 0, total_limit: 0 }))).toBe("closed");
  });
  it("unlimited shows what was used in all", () => {
    expect(walletText(wallet({ mode: "unlimited", used_total: 7, used_day: 2 }))).toBe("unlimited (7 used)");
  });
  it("limit with both, with the day only and with the total only", () => {
    expect(walletText(wallet({ day_limit: 10, total_limit: 100, used_day: 3, used_total: 40 }))).toBe(
      "day 3/10 · total 40/100",
    );
    expect(walletText(wallet({ day_limit: 10, used_day: 3 }))).toBe("day 3/10");
    expect(walletText(wallet({ total_limit: 100, used_total: 40 }))).toBe("total 40/100");
  });
});

describe("usagePercent", () => {
  it("is the rounded share of the limit used", () => {
    expect(usagePercent(3, 20)).toBe(15);
    expect(usagePercent(14, 200)).toBe(7);
    expect(usagePercent(88, 300)).toBe(29);
    expect(usagePercent(0, 20)).toBe(0);
  });
  it("is 0 without a limit and never more than 100", () => {
    expect(usagePercent(5, null)).toBe(0);
    expect(usagePercent(5, 0)).toBe(0);
    expect(usagePercent(30, 20)).toBe(100);
  });
});

describe("usageLabel", () => {
  it("speaks both bars", () => {
    expect(usageLabel(wallet({ day_limit: 20, total_limit: 200, used_day: 3, used_total: 14 }))).toBe(
      "Used 3 of 20 today, 14 of 200 in total",
    );
  });
  it("names a side that has no limit without a limit", () => {
    expect(usageLabel(wallet({ day_limit: null, total_limit: 200, used_day: 3, used_total: 14 }))).toBe(
      "Used 3 today, 14 of 200 in total",
    );
    expect(usageLabel(wallet({ day_limit: 20, total_limit: null, used_day: 3, used_total: 14 }))).toBe(
      "Used 3 of 20 today, 14 in total",
    );
  });
});

describe("createdText", () => {
  const now = new Date(2026, 9, 15, 20, 55, 2).getTime();
  it("a key of an earlier day shows day/month/year", () => {
    expect(createdText(new Date(2026, 8, 3, 9, 0).getTime(), now)).toBe("03/09/2026");
  });
  it("a key made today shows day/month and the hour and minute", () => {
    expect(createdText(new Date(2026, 9, 15, 20, 54).getTime(), now)).toBe("15/10 20:54");
  });
  it("a key made at midnight today still reads as today, one a minute before as yesterday's date", () => {
    expect(createdText(new Date(2026, 9, 15, 0, 0).getTime(), now)).toBe("15/10 00:00");
    expect(createdText(new Date(2026, 9, 14, 23, 59).getTime(), now)).toBe("14/10/2026");
  });
  it("no time is a dash", () => {
    expect(createdText(null, now)).toBe("-");
  });
});

describe("loadFailedText", () => {
  it("words a failed list as the canvas does, with the relay's status when it answered", () => {
    expect(loadFailedText(new ApiError(502, { error: { message: "bad gateway" } }), "bad gateway")).toBe(
      "Could not load the API keys: the relay answered 502 (bad gateway).",
    );
    expect(loadFailedText(new TypeError("x"), "Network error, please try again")).toBe(
      "Could not load the API keys: network error, please try again.",
    );
  });
});

describe("unfinishedText", () => {
  it("shows unlimited for 0 and the number otherwise", () => {
    expect(unfinishedText({ unfinished: 2, max_unfinished_effective: 0 })).toBe("2 / unlimited");
    expect(unfinishedText({ unfinished: 2, max_unfinished_effective: 5 })).toBe("2 / 5");
  });
});

const models = [
  { id: "m1", owned_by: "vibes", kind: "video" },
  { id: "m2", owned_by: "vibes", kind: "image" },
  { id: "m3", owned_by: "dola", kind: "video" },
  { id: "m4", owned_by: "dola", kind: "video" },
];

describe("walletScopes and keyForm", () => {
  it("the points wallet, then one cap per distinct provider, sorted", () => {
    expect(walletScopes(models)).toEqual(["*", "dola", "vibes"]);
  });

  it("a new key: any model, empty label, every wallet closed", () => {
    expect(keyForm(models, null)).toEqual({
      label: "",
      maxUnfinished: "",
      any: true,
      models: [],
      wallets: [
        { scope: "*", mode: "closed", day: "", total: "" },
        { scope: "dola", mode: "closed", day: "", total: "" },
        { scope: "vibes", mode: "closed", day: "", total: "" },
      ],
    });
  });

  it("an existing key starts from its own values; a wallet it lacks starts closed", () => {
    const key: ApiKey = {
      id: "k1",
      prefix: "rk_abc",
      label: "shop",
      enabled: true,
      models: ["m1", "m3"],
      max_unfinished: 0,
      max_unfinished_effective: 0,
      created_ms: 1,
      jobs: 0,
      unfinished: 0,
      wallets: [
        wallet({ scope: "vibes", day_limit: 5, total_limit: 50 }),
        wallet({ scope: "dola", mode: "unlimited" }),
      ],
    };
    const form = keyForm(models, key);
    expect(form.label).toBe("shop");
    expect(form.maxUnfinished).toBe("0");
    expect(form.any).toBe(false);
    expect(form.models).toEqual(["m1", "m3"]);
    expect(form.wallets).toEqual([
      { scope: "*", mode: "closed", day: "", total: "" },
      { scope: "dola", mode: "unlimited", day: "", total: "" },
      { scope: "vibes", mode: "limit", day: "5", total: "50" },
    ]);
  });
});

const form = (over: Partial<KeyForm> = {}): KeyForm => ({
  label: "k",
  maxUnfinished: "",
  any: true,
  models: [],
  wallets: [],
  ...over,
});

describe("keyBody (the create and edit payload)", () => {
  it("any model sends * and ignores the ticked list", () => {
    expect(keyBody(form({ any: true, models: ["m1"] })).models).toEqual(["*"]);
  });

  it("a list sends the ticked models", () => {
    expect(keyBody(form({ any: false, models: ["m1", "m3"] })).models).toEqual(["m1", "m3"]);
  });

  it("max unfinished: empty is the default (null), 0 is unlimited, a number is a number", () => {
    expect(keyBody(form({ maxUnfinished: "" })).max_unfinished).toBeNull();
    expect(keyBody(form({ maxUnfinished: "0" })).max_unfinished).toBe(0);
    expect(keyBody(form({ maxUnfinished: "12" })).max_unfinished).toBe(12);
  });

  it("closed and unlimited wallets carry no limits, even when the boxes still hold numbers", () => {
    const body = keyBody(
      form({
        wallets: [
          { scope: "*", mode: "closed", day: "5", total: "9" },
          { scope: "a", mode: "unlimited", day: "5", total: "9" },
        ],
      }),
    );
    expect(body.wallets).toEqual([
      { scope: "*", mode: "closed" },
      { scope: "a", mode: "unlimited" },
    ]);
  });

  it("a limit wallet sends only the boxes that are filled, as numbers", () => {
    const body = keyBody(
      form({
        wallets: [
          { scope: "*", mode: "limit", day: "5", total: "9" },
          { scope: "a", mode: "limit", day: "5", total: "" },
          { scope: "b", mode: "limit", day: "", total: "9" },
          { scope: "c", mode: "limit", day: "", total: "" },
        ],
      }),
    );
    expect(body.wallets).toEqual([
      { scope: "*", mode: "limit", day_limit: 5, total_limit: 9 },
      { scope: "a", mode: "limit", day_limit: 5 },
      { scope: "b", mode: "limit", total_limit: 9 },
      { scope: "c", mode: "limit" },
    ]);
  });

  it("the whole body", () => {
    expect(keyBody(form({ label: "shop", maxUnfinished: "3" }))).toEqual({
      label: "shop",
      models: ["*"],
      max_unfinished: 3,
      wallets: [],
    });
  });
});

describe("createError", () => {
  const api = (code: string, param = "") => new ApiError(422, { error: { code, message: "SERVER TEXT", param } });

  it("is fixed text by error code, never the server's message", () => {
    expect(createError(api("input.invalid", "label"))).toEqual({
      text: "Some of the values are not accepted. Check the highlighted field.",
      field: "label",
    });
    expect(createError(api("resource.not_found")).text).toBe(
      "A provider or model in the form no longer exists. Reload the page and try again.",
    );
    expect(createError(api("anything.else")).text).toBe("The key could not be created. Try again.");
    expect(JSON.stringify(createError(api("input.invalid", "label")))).not.toContain("SERVER TEXT");
  });

  it("highlights only a field from the fixed list; wallets.N is the wallets field", () => {
    expect(createError(api("input.invalid", "wallets.3")).field).toBe("wallets");
    expect(createError(api("input.invalid", "models")).field).toBe("models");
    expect(createError(api("input.invalid", "max_unfinished")).field).toBe("max_unfinished");
    expect(createError(api("input.invalid", "label rk-secret")).field).toBeNull();
    expect(createError(api("input.invalid", "")).field).toBeNull();
  });

  it("a network failure says so in fixed text", () => {
    expect(createError(new TypeError("rk-secret"))).toEqual({ text: "Network error, please try again.", field: null });
  });
});

describe("deleteToast", () => {
  it("says deleted, or that the key was only disabled", () => {
    expect(deleteToast("deleted")).toBe("Key deleted");
    expect(deleteToast("disabled")).toBe("Key has jobs: disabled instead");
  });
});

describe("the order of the wallets", () => {
  const at = (scope: string) => ({ scope });

  it("the points wallet comes first, then the providers' caps by name", () => {
    const sorted = sortedWallets([at("vibes"), at("dola"), at("*"), at("flow")]);
    expect(sorted.map((w) => w.scope)).toEqual(["*", "dola", "flow", "vibes"]);
  });

  it("the points wallet is first whichever side of the comparison it is on", () => {
    expect(compareWallets(at("*"), at("a"))).toBeLessThan(0);
    expect(compareWallets(at("a"), at("*"))).toBeGreaterThan(0);
  });

  it("does not change the list it was given, and a wallet equals itself", () => {
    const list = [at("dola"), at("*")];
    expect(sortedWallets(list)).not.toBe(list);
    expect(list.map((w) => w.scope)).toEqual(["dola", "*"]);
    expect(compareWallets(at("dola"), at("dola"))).toBe(0);
  });
});

describe("relativePercent", () => {
  it("is the rounded share of the busiest key", () => {
    expect(relativePercent(10, 10)).toBe(100);
    expect(relativePercent(5, 10)).toBe(50);
    expect(relativePercent(1, 3)).toBe(33);
    expect(relativePercent(2, 3)).toBe(67);
  });
  it("is 0 when nobody used any, 0 for no use, and never more than 100", () => {
    expect(relativePercent(0, 0)).toBe(0);
    expect(relativePercent(5, 0)).toBe(0);
    expect(relativePercent(0, 40)).toBe(0);
    expect(relativePercent(50, 40)).toBe(100);
  });
});

describe("walletMaxima", () => {
  const keyOf = (...wallets: Wallet[]) => ({ wallets });
  it("is the highest use per scope among the keys that have the wallet open", () => {
    const out = walletMaxima([
      keyOf(
        wallet({ mode: "unlimited", used_day: 4, used_total: 9 }),
        wallet({ scope: "*", mode: "limit", used_day: 7 }),
      ),
      keyOf(wallet({ mode: "limit", used_day: 2, used_total: 30 })),
      keyOf(wallet({ mode: "closed", used_day: 500, used_total: 500 })),
    ]);
    expect(out.get("fake")).toEqual({ day: 4, total: 30 });
    expect(out.get("*")).toEqual({ day: 7, total: 0 });
    expect(out.has("other")).toBe(false);
  });
  it("has nothing for no keys or only closed wallets", () => {
    expect(walletMaxima([]).size).toBe(0);
    expect(walletMaxima([keyOf(wallet({ mode: "closed" }))]).size).toBe(0);
  });
});

describe("relativeLabel", () => {
  it("says what the bars are a share of", () => {
    expect(relativeLabel(wallet({ mode: "unlimited", used_day: 12, used_total: 40 }))).toBe(
      "Used 12 today, 40 in total; the bars show the share of the busiest key\x27s use",
    );
  });
});

describe("keyName", () => {
  it("is the label without its edges, else the prefix (a label of spaces is no label)", () => {
    expect(keyName({ label: " shop ", prefix: "rk_a" })).toBe("shop");
    expect(keyName({ label: "", prefix: "rk_a" })).toBe("rk_a");
    expect(keyName({ label: "   ", prefix: "rk_a" })).toBe("rk_a");
  });
});

describe("keyBody label", () => {
  it("sends the label without its edges, and an empty one for a label of spaces", () => {
    expect(keyBody(form({ label: "  shop " })).label).toBe("shop");
    expect(keyBody(form({ label: "   " })).label).toBe("");
    expect(keyBody(form({ label: "" })).label).toBe("");
  });
});
