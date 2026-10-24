import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import type { ApiKey, Wallet } from "@/api/keys";
import {
  balanceOf,
  capChips,
  capLine,
  changedCount,
  compareWallets,
  createdText,
  createError,
  deleteText,
  deleteToast,
  inFilter,
  inSearch,
  keyBody,
  type KeyForm,
  keyForm,
  keyName,
  keyReaches,
  keyState,
  loadFailedText,
  modelGroups,
  planChange,
  sortedWallets,
  summaryText,
  todayText,
  unfinishedText,
  walletScopes,
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

const keyOf = (over: Partial<ApiKey> = {}): ApiKey => ({
  id: "k1",
  prefix: "rk_abcd",
  label: "shop",
  enabled: true,
  models: ["*"],
  max_unfinished: null,
  max_unfinished_effective: 5,
  created_ms: 1,
  jobs: 0,
  unfinished: 0,
  wallets: [],
  ...over,
});
const points = (over: Partial<Wallet>): Wallet => wallet({ scope: "*", ...over });

describe("balanceOf", () => {
  it("is the total less what was used in total, red at 0 and amber under a fifth of the total", () => {
    expect(balanceOf(points({ total_limit: 1000, used_total: 100 }))).toEqual({ left: 900, word: "", tone: "ok" });
    expect(balanceOf(points({ total_limit: 1000, used_total: 850 })).tone).toBe("warn");
    expect(balanceOf(points({ total_limit: 1000, used_total: 800 })).tone).toBe("ok");
    expect(balanceOf(points({ total_limit: 1000, used_total: 1200 }))).toEqual({ left: 0, word: "", tone: "bad" });
  });
  it("a day limit alone is what is left of today's", () => {
    expect(balanceOf(points({ day_limit: 50, used_day: 20, used_total: 900 })).left).toBe(30);
  });
  it("a closed wallet (or none) and an unlimited one have no figure", () => {
    expect(balanceOf(undefined)).toEqual({ left: null, word: "closed", tone: "bad" });
    expect(balanceOf(points({ mode: "closed", day_limit: 0 }))).toEqual({ left: null, word: "closed", tone: "bad" });
    expect(balanceOf(points({ mode: "unlimited" }))).toEqual({ left: null, word: "unlimited", tone: "ok" });
  });
});

describe("todayText", () => {
  it("says what was spent today, against the day limit when there is one", () => {
    expect(todayText(points({ total_limit: 9000, used_day: 1240 }))).toBe("−1,240 today");
    expect(todayText(points({ day_limit: 2000, used_day: 860 }))).toBe("−860 of 2,000 today");
    expect(todayText(points({ mode: "unlimited", used_day: 3 }))).toBe("−3 today");
    expect(todayText(points({ total_limit: 10 }))).toBe("nothing today");
    expect(todayText(points({ mode: "closed", day_limit: 0 }))).toBe("");
  });
});

describe("capChips", () => {
  const catalog = [
    { id: "seedance-2.0", owned_by: "dola", kind: "video" },
    { id: "vibes-video", owned_by: "vibes", kind: "video" },
    { id: "meta-video", owned_by: "meta", kind: "video" },
  ];
  const wallets = [
    points({ total_limit: 100 }),
    wallet({ scope: "vibes", mode: "limit", day_limit: 50, used_day: 40 }),
    wallet({ scope: "dola", mode: "unlimited", used_day: 7 }),
    wallet({ scope: "meta", mode: "closed", day_limit: 0 }),
  ];
  it("one chip per open cap of a provider the key may use, in the drawn order, the day limit as the cap", () => {
    expect(capChips(keyOf({ wallets }), catalog)).toEqual([
      { provider: "dola", used: 7, cap: null },
      { provider: "vibes", used: 40, cap: 50 },
    ]);
  });
  it("a listed model reaches only its own provider, and a provider filter keeps its chip alone", () => {
    expect(capChips(keyOf({ wallets, models: ["vibes-video"] }), catalog).map((c) => c.provider)).toEqual(["vibes"]);
    expect(capChips(keyOf({ wallets }), catalog, "dola").map((c) => c.provider)).toEqual(["dola"]);
  });
});

describe("keyState, the filters and the search", () => {
  const low = keyOf({ wallets: [points({ total_limit: 100, used_total: 90 })] });
  const empty = keyOf({ wallets: [points({ total_limit: 100, used_total: 100 })] });
  const paused = keyOf({ enabled: false, wallets: [points({ total_limit: 100 })] });
  const fine = keyOf({ wallets: [points({ mode: "unlimited" })] });
  it("paused first, then the wallet's tone", () => {
    expect([low, empty, paused, fine, keyOf()].map(keyState)).toEqual(["low", "empty", "paused", "ok", "closed"]);
  });
  it("Active, Low or empty and Paused pick their keys", () => {
    const all = [low, empty, paused, fine];
    expect(all.filter((k) => inFilter(k, "all"))).toHaveLength(4);
    expect(all.filter((k) => inFilter(k, "active"))).toEqual([low, empty, fine]);
    expect(all.filter((k) => inFilter(k, "low"))).toEqual([low, empty]);
    expect(all.filter((k) => inFilter(k, "paused"))).toEqual([paused]);
  });
  it("the search finds a piece of the label or the prefix in any case", () => {
    expect(inSearch(keyOf({ label: "Lotus Media" }), "lotus")).toBe(true);
    expect(inSearch(keyOf({ prefix: "rk-AbCd" }), "abc")).toBe(true);
    expect(inSearch(keyOf(), "nothing")).toBe(false);
    expect(inSearch(keyOf(), "  ")).toBe(true);
  });
});

describe("summaryText", () => {
  it("counts the keys, those used today, the points spent today and the empty wallets", () => {
    const keys = [
      keyOf({ wallets: [points({ total_limit: 5000, used_day: 2000 })] }),
      keyOf({ wallets: [points({ total_limit: 100, used_total: 100, used_day: 600 })] }),
      keyOf({ wallets: [points({ mode: "unlimited" })] }),
    ];
    expect(summaryText(keys)).toBe("3 keys, 2 used today. 2,600 points spent today; 1 wallet is empty.");
    expect(summaryText(keys, true)).toBe("3 keys, 2 used today · 2,600 points spent today");
    expect(summaryText([keys[2]!])).toBe("1 key, 0 used today. 0 points spent today.");
  });
});

describe("capLine and deleteText", () => {
  it("the line under a cap of the form", () => {
    expect(capLine(wallet({ total_limit: 600, used_total: 412, day_limit: 300 }))).toBe("total 412 / 600");
    expect(capLine(wallet({ day_limit: 200 }))).toBe("no total cap");
    expect(capLine(wallet({ mode: "unlimited", used_day: 318 }))).toBe("unlimited · 318 used today");
    expect(capLine(wallet({ mode: "closed", day_limit: 0 }))).toBe("closed");
  });
  it("the delete question says a key with jobs is only disabled, with its count", () => {
    expect(deleteText({ jobs: 0 })).toBe("A key that has jobs is only disabled.");
    expect(deleteText({ jobs: 2104 })).toBe(
      "A key that has jobs is only disabled. This one has 2,104 jobs, so it will be disabled, not removed.",
    );
  });
});

describe("changedCount", () => {
  it("counts the label, the max, the models and each wallet that differ", () => {
    const a = form({
      wallets: [
        { scope: "*", mode: "closed", day: "", total: "" },
        { scope: "dola", mode: "closed", day: "", total: "" },
      ],
    });
    expect(changedCount(a, a)).toBe(0);
    const b: KeyForm = { ...a, label: "x", any: false, wallets: [{ ...a.wallets[0]!, mode: "limit" }, a.wallets[1]!] };
    expect(changedCount(b, a)).toBe(3);
  });
});

describe("planChange (Adjust wallet)", () => {
  it("a top-up raises the total, a deduction lowers it, the day limit kept", () => {
    const w = points({ day_limit: 500, total_limit: 5000, used_total: 1095 });
    expect(planChange(w, "top", 5000)).toEqual({
      now: 3905,
      after: 8905,
      body: { scope: "*", mode: "limit", day_limit: 500, total_limit: 10000 },
      problem: "",
    });
    expect(planChange(w, "deduct", 905).body).toEqual({ scope: "*", mode: "limit", day_limit: 500, total_limit: 4095 });
  });
  it("never deducts more than the balance, and wants a whole amount of 1 or more", () => {
    const w = points({ total_limit: 100, used_total: 60 });
    expect(planChange(w, "deduct", 41)).toMatchObject({ body: null, problem: "At most 40 points can be deducted." });
    expect(planChange(w, "top", 0).body).toBeNull();
    expect(planChange(w, "top", 2.5).body).toBeNull();
  });
  it("a closed wallet or one with a day limit only gets a total: what it used plus the top-up", () => {
    expect(planChange(points({ mode: "closed", day_limit: 0, used_total: 30 }), "top", 70).body).toEqual({
      scope: "*",
      mode: "limit",
      total_limit: 100,
    });
    expect(planChange(points({ day_limit: 20, used_total: 30 }), "top", 70)).toMatchObject({
      now: 20,
      after: 70,
      body: { scope: "*", mode: "limit", day_limit: 20, total_limit: 100 },
    });
  });
  it("an unlimited wallet has no balance to change", () => {
    expect(planChange(points({ mode: "unlimited" }), "top", 10)).toMatchObject({ body: null });
  });
});
