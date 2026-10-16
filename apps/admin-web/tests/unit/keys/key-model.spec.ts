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
  sortedWallets,
  unfinishedText,
  usageLabel,
  usagePercent,
  walletPairs,
  walletText,
} from "@/components/keys/key-model";

const wallet = (over: Partial<Wallet>): Wallet => ({
  provider: "fake",
  kind: "video",
  day_limit: null,
  total_limit: null,
  used_day: 0,
  used_total: 0,
  mode: "limit",
  ...over,
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

describe("walletPairs and keyForm", () => {
  it("one pair per distinct (provider, kind), sorted", () => {
    expect(walletPairs(models)).toEqual([
      ["dola", "video"],
      ["vibes", "video"],
      ["vibes", "image"],
    ]);
  });

  it("a new key: any model, empty label, every wallet closed", () => {
    expect(keyForm(models, null)).toEqual({
      label: "",
      maxUnfinished: "",
      any: true,
      models: [],
      wallets: [
        { provider: "dola", kind: "video", mode: "closed", day: "", total: "" },
        { provider: "vibes", kind: "video", mode: "closed", day: "", total: "" },
        { provider: "vibes", kind: "image", mode: "closed", day: "", total: "" },
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
        wallet({ provider: "vibes", kind: "video", day_limit: 5, total_limit: 50 }),
        wallet({ provider: "dola", kind: "video", mode: "unlimited" }),
      ],
    };
    const form = keyForm(models, key);
    expect(form.label).toBe("shop");
    expect(form.maxUnfinished).toBe("0");
    expect(form.any).toBe(false);
    expect(form.models).toEqual(["m1", "m3"]);
    expect(form.wallets).toEqual([
      { provider: "dola", kind: "video", mode: "unlimited", day: "", total: "" },
      { provider: "vibes", kind: "video", mode: "limit", day: "5", total: "50" },
      { provider: "vibes", kind: "image", mode: "closed", day: "", total: "" },
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
          { provider: "a", kind: "video", mode: "closed", day: "5", total: "9" },
          { provider: "a", kind: "image", mode: "unlimited", day: "5", total: "9" },
        ],
      }),
    );
    expect(body.wallets).toEqual([
      { provider: "a", kind: "video", mode: "closed" },
      { provider: "a", kind: "image", mode: "unlimited" },
    ]);
  });

  it("a limit wallet sends only the boxes that are filled, as numbers", () => {
    const body = keyBody(
      form({
        wallets: [
          { provider: "a", kind: "video", mode: "limit", day: "5", total: "9" },
          { provider: "a", kind: "image", mode: "limit", day: "5", total: "" },
          { provider: "b", kind: "image", mode: "limit", day: "", total: "9" },
          { provider: "b", kind: "video", mode: "limit", day: "", total: "" },
        ],
      }),
    );
    expect(body.wallets).toEqual([
      { provider: "a", kind: "video", mode: "limit", day_limit: 5, total_limit: 9 },
      { provider: "a", kind: "image", mode: "limit", day_limit: 5 },
      { provider: "b", kind: "image", mode: "limit", total_limit: 9 },
      { provider: "b", kind: "video", mode: "limit" },
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
  const at = (provider: string, kind: string) => ({ provider, kind });

  it("a provider's video wallet comes before its image wallet, then any other kind by name", () => {
    const sorted = sortedWallets([at("dola", "voice"), at("dola", "image"), at("dola", "audio"), at("dola", "video")]);
    expect(sorted.map((w) => w.kind)).toEqual(["video", "image", "audio", "voice"]);
  });

  it("providers go in name order whatever their kinds", () => {
    const sorted = sortedWallets([at("vibes", "video"), at("dola", "image"), at("dola", "video")]);
    expect(sorted.map((w) => `${w.provider}/${w.kind}`)).toEqual(["dola/video", "dola/image", "vibes/video"]);
  });

  it("does not change the list it was given, and a wallet equals itself", () => {
    const list = [at("dola", "image"), at("dola", "video")];
    expect(sortedWallets(list)).not.toBe(list);
    expect(list.map((w) => w.kind)).toEqual(["image", "video"]);
    expect(compareWallets(at("dola", "video"), at("dola", "video"))).toBe(0);
  });
});
