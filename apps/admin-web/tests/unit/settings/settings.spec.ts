import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { ApiError } from "@/api/client";
import * as api from "@/api/settings";
import {
  bootValue,
  fullDefault,
  isLongDefault,
  newPasswordProblem,
  parseValue,
  valueText,
} from "@/components/settings/settings-value";
import * as shell from "@/api/shell";
import {
  cardsOf,
  fieldLabel,
  otherPrices,
  priceInputs,
  withModelOn,
  withPrice,
} from "@/components/settings/settings-cards";
import { notify } from "@/lib/notify";
import { TEST_KEY, TOKEN_KEY, USER_KEY } from "@/lib/session";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";
import SettingsView from "@/views/SettingsView.vue";

vi.mock("@/api/settings");
vi.mock("@/api/shell", () => ({ getModels: vi.fn(), getProvidersSummary: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

describe("setting values", () => {
  it("are parsed as JSON first and fall back to the raw string", () => {
    expect(parseValue("5")).toBe(5);
    expect(parseValue("true")).toBe(true);
    expect(parseValue("null")).toBeNull();
    expect(parseValue("[1,2]")).toEqual([1, 2]);
    expect(parseValue('{"a":1}')).toEqual({ a: 1 });
    expect(parseValue('"quoted"')).toBe("quoted");
    expect(parseValue("plain words")).toBe("plain words");
    expect(parseValue("{bad json")).toBe("{bad json");
    expect(parseValue("")).toBe("");
    expect(parseValue("UTC")).toBe("UTC");
  });

  it("start in the input as the string itself, or JSON for anything else", () => {
    expect(valueText({ value: "UTC" })).toBe("UTC");
    expect(valueText({ value: 5 })).toBe("5");
    expect(valueText({ value: false })).toBe("false");
    expect(valueText({ value: [1, 2] })).toBe("[1,2]");
    expect(valueText({ value: null })).toBe("null");
  });

  it("hold a new password to the server's rule, 10 to 256 characters", () => {
    expect(newPasswordProblem("x".repeat(9))).toBe("At least 10 characters");
    expect(newPasswordProblem("x".repeat(10))).toBe("");
    expect(newPasswordProblem("x".repeat(256))).toBe("");
    expect(newPasswordProblem("x".repeat(257))).toBe("At most 256 characters");
  });

  it("show a long default on one line, the whole value laid out in its note", () => {
    expect(isLongDefault("x".repeat(30))).toBe(false);
    expect(isLongDefault("x".repeat(31))).toBe(true);
    expect(fullDefault({ "a@5s": 1 })).toBe('{\n  "a@5s": 1\n}');
    expect(fullDefault("{prompt}_{duration}")).toBe("{prompt}_{duration}");
  });

  it("show a boot-only value that is unset or empty as a dash", () => {
    expect(bootValue(null)).toBe("-");
    expect(bootValue(undefined)).toBe("-");
    expect(bootValue("")).toBe("-");
    expect(bootValue("UTC")).toBe("UTC");
    expect(bootValue(0)).toBe("0");
  });
});

const setting = (over: Partial<api.SettingItem> & Pick<api.SettingItem, "key" | "value">): api.SettingItem => ({
  group: over.key.includes(".") ? over.key.split(".")[0]! : "core",
  basic: false,
  type: typeof over.value === "number" ? "int" : typeof over.value === "boolean" ? "bool" : "str",
  source: "default",
  default: over.value,
  description: "",
  allowed: null,
  ...over,
});
const items = (): api.SettingItem[] => [
  setting({
    key: "session_ttl_s",
    value: 3600,
    source: "db",
    default: 1800,
    description: "How long a session lasts",
    allowed: "at least 60",
  }),
  setting({ key: "billing_label", value: "relay", description: "A label" }),
  setting({ key: "dola.claims_enabled", value: true, basic: true, description: "Drain mode off." }),
  setting({
    key: "dola.disabled_models",
    value: ["seedance-1.0"],
    default: [],
    type: "list",
    basic: true,
    source: "db",
    description: "Models paused for every key.",
  }),
  setting({
    key: "dola.prices",
    value: { "seedance_v2.5@5s": 2, "seedance_v2.5@10s": 4, "seedream-4.5": 1, "old-model": 3 },
    type: "dict",
    basic: true,
    description: "Points per model.",
  }),
  setting({ key: "dola.video_budget", value: 4, basic: true, description: "Video points per account a day." }),
  setting({ key: "dola.render_deadline_s", value: 9000, description: "Render deadline." }),
];
const models = (): shell.CatalogModel[] => [
  { id: "seedance-2.5", name: "Dreamina Seedance 2.5", owned_by: "dola", kind: "video", aliases: ["seedance_v2.5"] },
  { id: "seedance-1.0", name: "Dreamina Seedance 1.0", owned_by: "dola", kind: "video", aliases: ["seedance_v1.0"] },
  { id: "seedream-4.5", name: "Seedream 4.5", owned_by: "dola", kind: "image", aliases: [] },
];
const priceRows = (): api.PriceRow[] => [
  {
    provider: "dola",
    model: "seedance",
    duration: 8,
    charged: 10,
    jobs: 3,
    disagreed: 2,
    lowest_upstream: 7,
    substituted: 1,
  },
  {
    provider: "dola",
    model: "seedance",
    duration: null,
    charged: 4,
    jobs: 1,
    disagreed: 0,
    lowest_upstream: null,
    substituted: 0,
  },
];

let wrapper: VueWrapper | null = null;
async function open() {
  setActivePinia(createPinia());
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/settings", name: "settings", component: { render: () => null } },
      { path: "/login", name: "login", component: { render: () => null } },
    ],
  });
  await router.push("/settings");
  wrapper = mount(SettingsView, { global: { plugins: [router] }, attachTo: document.body });
  await flushPromises();
  return router;
}
const button = (label: string) => wrapper!.find(`button[aria-label="${label}"]`);
const valueInput = (key: string) => wrapper!.find(`input[aria-label="Value of ${key}"]`);

beforeEach(() => {
  sessionStorage.clear();
  vi.resetAllMocks();
  vi.mocked(api.getSettings).mockImplementation(async () => ({
    items: items(),
    env_only: [
      { name: "BILLING_TZ", value: "Asia/Saigon" },
      { name: "PUBLIC_BASE_URL", value: null },
    ],
    fixed: [],
    containers: [],
  }));
  vi.mocked(api.getPrices).mockImplementation(async () => ({ items: priceRows() }));
  vi.mocked(shell.getModels).mockResolvedValue({ data: models() });
  vi.mocked(shell.getProvidersSummary).mockResolvedValue({
    day_tz: "UTC",
    day_start_ms: 0,
    hours: [],
    providers: [
      {
        provider: "dola",
        execution: "browser",
        claims_enabled: true,
        running: 0,
        queued: 0,
        done_today: 0,
        failed_today: 0,
        accounts: { usable: 1, total: 1 },
        pools: [],
      },
    ],
  });
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
});

describe("the settings page", () => {
  it("lets the page scroll: no table holds a fixed height that would clip a row", async () => {
    await open();
    // Boot-only values and Measured prices have rows; Fixed limits and Containers read None
    expect(wrapper!.findAll("table")).toHaveLength(2);
    expect(wrapper!.html()).not.toMatch(/max-height:\s*(\d|calc)/);
  });

  it("draws the first read as the drawn loading rows: each table's header over five rows of bars", async () => {
    vi.mocked(api.getSettings).mockReturnValue(new Promise(() => undefined));
    vi.mocked(api.getPrices).mockReturnValue(new Promise(() => undefined));
    await open();
    const tables = wrapper!.findAll("table");
    expect(tables).toHaveLength(4);
    // the settings cards wait as one card of bars
    expect(wrapper!.findAll("#settings [data-state=loading] .set-skel")).toHaveLength(5);
    for (const table of tables) {
      expect(table.find("thead").exists()).toBe(true);
      const body = table.find("tbody");
      expect(body.attributes("aria-hidden")).toBe("true");
      expect(body.findAll("tr")).toHaveLength(5);
      expect(body.findAll(".set-skel").length).toBeGreaterThan(5);
    }
    // the boot-only list shares the settings read: bars too, and no empty text while it runs
    expect(wrapper!.find("#boot .set-skel").exists()).toBe(true);
    expect(wrapper!.text()).not.toContain("No settings");
    expect(wrapper!.text()).not.toContain("None");
    expect(wrapper!.text()).not.toContain("No job has an upstream figure yet");
  });
});

const card = (group: string) => wrapper!.find(`[data-testid="settings-card-${group}"]`);
const field = (key: string) => wrapper!.find(`.field[data-key="${key}"]`);

describe("the cards", () => {
  it("draws General first, then a card per provider; a strip pick shows one card, General its own", async () => {
    await open();
    expect(wrapper!.findAll(".scard").map((c) => c.attributes("data-testid"))).toEqual([
      "settings-card-core",
      "settings-card-dola",
    ]);
    const scope = useScopeStore();
    scope.provider = "dola";
    await flushPromises();
    expect(wrapper!.findAll(".scard").map((c) => c.attributes("data-testid"))).toEqual(["settings-card-dola"]);
    // a provider keeps its measured prices; the password and the boot-only values go with General
    expect(wrapper!.find("#prices").exists()).toBe(true);
    expect(wrapper!.find("#password").exists()).toBe(false);
    scope.provider = "";
    scope.general = true;
    await flushPromises();
    expect(wrapper!.findAll(".scard").map((c) => c.attributes("data-testid"))).toEqual(["settings-card-core"]);
    expect(wrapper!.find("#password").exists()).toBe(true);
  });

  it("a provider card's header: its tile, how it sends, the models on, and the Taking jobs switch", async () => {
    await open();
    const head = card("dola").find(".scard__head");
    expect(head.find(".ptile").text()).toBe("D");
    expect(head.find(".scard__sub").text()).toBe("browser · 2/3 models on");
    expect(head.find("[role=switch]").attributes("aria-checked")).toBe("true");
    expect(head.text()).toContain("Taking jobs");
    expect(head.text()).toContain("dola.claims_enabled");
    await head.find("[role=switch]").trigger("click");
    expect(head.text()).toContain("Paused");
    expect(head.text()).toContain("edited");
  });

  it("Models & prices: a row per model with its switch and price inputs, a paused one dimmed, other keys apart", async () => {
    await open();
    const rows = card("dola").findAll(".mrow");
    expect(rows.map((r) => r.attributes("data-model"))).toEqual(["seedance-2.5", "seedance-1.0", "seedream-4.5", ""]);
    expect(rows[0]!.find(".mrow__name").text()).toBe("Dreamina Seedance 2.5video · seedance-2.5");
    expect(rows[0]!.findAll("label").map((l) => l.text())).toEqual(["5 s", "10 s"]);
    expect(rows[1]!.classes()).toContain("is-off");
    expect(rows[1]!.find("small").text()).toBe("video · seedance-1.0 · Paused");
    expect(rows[1]!.find("[role=switch]").attributes("aria-checked")).toBe("false");
    expect(rows[2]!.findAll("label").map((l) => l.text())).toEqual(["points"]);
    expect(rows[3]!.text()).toContain("old-model");
  });

  it("a model switch and a price input edit the drafts; Save sends each changed setting, then reads again", async () => {
    await open();
    await card("dola").find('[aria-label="Offer seedance-1.0"]').trigger("click");
    await card("dola").find('[aria-label="seedance-2.5 5 s price"]').setValue("3");
    expect(card("dola").find(".scard__changed").text()).toBe("2 changes not saved");
    vi.mocked(api.putSetting).mockResolvedValue(items()[0]!);
    vi.mocked(api.getSettings).mockClear();
    await card("dola").find('[aria-label="Save dola"]').trigger("click");
    await flushPromises();
    expect(api.putSetting).toHaveBeenCalledWith("dola.disabled_models", []);
    expect(api.putSetting).toHaveBeenCalledWith("dola.prices", {
      "seedance_v2.5@5s": 3,
      "seedance_v2.5@10s": 4,
      "seedream-4.5": 1,
      "old-model": 3,
    });
    expect(api.getSettings).toHaveBeenCalledTimes(1);
    expect(notify.success).toHaveBeenCalledWith("Saved");
    expect(card("dola").find('[aria-label="Save dola"]').attributes("disabled")).toBeDefined();
  });

  it("basic settings show on the card, the rest folded under Advanced", async () => {
    await open();
    const dola = card("dola");
    expect(dola.find(".blk .fields").find('[data-key="dola.video_budget"]').exists()).toBe(true);
    expect(dola.find("details.adv").attributes("open")).toBeUndefined();
    expect(dola.find("details.adv").find('[data-key="dola.render_deadline_s"]').exists()).toBe(true);
    expect(dola.find("details.adv summary").text()).toContain("1 settings");
    // General marks none basic: its settings are all shown, none folded
    expect(card("core").find("details.adv").exists()).toBe(false);
    expect(card("core").find('[data-key="session_ttl_s"]').exists()).toBe(true);
  });

  it("shows each setting with its value, source and default, and the boot-only values with - for none", async () => {
    await open();
    const text = field("session_ttl_s").text();
    expect(text).toContain("Session ttl (s)");
    expect(text).toContain("How long a session lasts");
    expect(text).toContain("Allowed: at least 60");
    expect(text).toContain("db · default 1800");
    expect((valueInput("session_ttl_s").element as HTMLInputElement).value).toBe("3600");
    expect((valueInput("billing_label").element as HTMLInputElement).value).toBe("relay");
    expect(wrapper!.text()).toContain("Asia/Saigon");
    const env = wrapper!
      .find('table[aria-label="Boot-only values"]')
      .findAll("tbody tr")
      .find((r) => r.text().includes("PUBLIC_BASE_URL"));
    expect(env!.findAll("td")[1]!.text()).toBe("-");
  });

  it("Save is disabled until a value changed, the field says edited, and an edit back disables it again", async () => {
    await open();
    const save = () => card("core").find('[aria-label="Save General"]');
    expect(save().attributes("disabled")).toBeDefined();
    expect(wrapper!.text()).not.toContain("edited");
    await valueInput("session_ttl_s").setValue("7200");
    expect(save().attributes("disabled")).toBeUndefined();
    expect(field("session_ttl_s").text()).toContain("edited");
    expect(card("dola").find('[aria-label="Save dola"]').attributes("disabled")).toBeDefined();
    await valueInput("session_ttl_s").setValue("3600");
    expect(save().attributes("disabled")).toBeDefined();
  });

  it("Save sends the parsed value, reloads both lists and says Saved", async () => {
    await open();
    await valueInput("session_ttl_s").setValue("7200");
    await valueInput("billing_label").setValue("my label");
    vi.mocked(api.putSetting).mockResolvedValue(items()[0]!);
    vi.mocked(api.getSettings).mockClear();
    vi.mocked(api.getPrices).mockClear();
    await card("core").find('[aria-label="Save General"]').trigger("click");
    await flushPromises();
    expect(api.putSetting).toHaveBeenCalledWith("session_ttl_s", 7200);
    // text that is not JSON goes as the string it is
    expect(api.putSetting).toHaveBeenCalledWith("billing_label", "my label");
    expect(api.getSettings).toHaveBeenCalledTimes(1);
    expect(api.getPrices).toHaveBeenCalledTimes(1);
    expect(notify.success).toHaveBeenCalledWith("Saved");
  });

  it("Discard puts the stored values back", async () => {
    await open();
    await valueInput("session_ttl_s").setValue("7200");
    await card("core").find('[aria-label="Discard General changes"]').trigger("click");
    expect((valueInput("session_ttl_s").element as HTMLInputElement).value).toBe("3600");
    expect(card("core").find('[aria-label="Save General"]').attributes("disabled")).toBeDefined();
  });

  it("Reset is offered for a stored value only, deletes it and says Reset", async () => {
    await open();
    expect(button("Reset session_ttl_s").exists()).toBe(true);
    expect(button("Reset billing_label").exists()).toBe(false);
    expect(button("Reset dola.disabled_models").exists()).toBe(true);
    vi.mocked(api.deleteSetting).mockResolvedValue({});
    await button("Reset session_ttl_s").trigger("click");
    await flushPromises();
    expect(api.deleteSetting).toHaveBeenCalledWith("session_ttl_s");
    expect(notify.success).toHaveBeenCalledWith("Reset");
  });

  it("a refused value is a toast and a line under the field, keeps what was typed, and no Saved", async () => {
    await open();
    await valueInput("session_ttl_s").setValue("nope");
    vi.mocked(api.putSetting).mockRejectedValue(
      new ApiError(400, { error: { code: "input.invalid", message: "bad value", param: "value" } }),
    );
    await card("core").find('[aria-label="Save General"]').trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith("bad value (value)");
    expect(notify.success).not.toHaveBeenCalled();
    expect(field("session_ttl_s").find("[role=alert]").text()).toBe("bad value (value)");
    expect((valueInput("session_ttl_s").element as HTMLInputElement).value).toBe("nope");
    await card("core").find('[aria-label="Discard General changes"]').trigger("click");
    expect(field("session_ttl_s").find("[role=alert]").exists()).toBe(false);
  });

  it("shows an empty list and a failed load with Retry", async () => {
    vi.mocked(api.getSettings).mockResolvedValueOnce({ items: [], env_only: [], fixed: [], containers: [] });
    await open();
    expect(wrapper!.text()).toContain("No settings");
    expect(wrapper!.text()).toContain("None");
    wrapper!.unmount();
    vi.mocked(api.getSettings).mockRejectedValueOnce(
      new ApiError(500, { error: { code: "server.error", message: "db down" } }),
    );
    vi.mocked(api.getPrices).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await open();
    // each card's failed read, worded as B-States draws a failed load
    const alerts = wrapper!.findAll('[role="alert"]').map((a) => a.find(".state__text").text());
    expect(alerts).toContain("Could not load the settings: the relay answered 500 (db down).");
    expect(alerts).toContain("Could not load the measured prices: network error, please try again.");
    expect(wrapper!.text()).toContain("Retry");
  });
});

describe("the card rules", () => {
  it("label a setting from its name, its unit apart", () => {
    expect(fieldLabel({ key: "dola.render_deadline_s", group: "dola" })).toBe("Render deadline (s)");
    expect(fieldLabel({ key: "alert_disk_free_gb", group: "core" })).toBe("Alert disk free (GB)");
    expect(fieldLabel({ key: "log_retention_days", group: "core" })).toBe("Log retention days");
  });

  it("put the claims switch, the models and the prices in their own place, basic first, the rest under Advanced", () => {
    const [core, dola] = cardsOf(items());
    expect(core!.group).toBe("core");
    expect(core!.basic.map((i) => i.key)).toEqual(["session_ttl_s", "billing_label"]);
    expect(Object.keys(dola!.own).sort()).toEqual(["claims_enabled", "disabled_models", "prices"]);
    expect(dola!.basic.map((i) => i.key)).toEqual(["dola.video_budget"]);
    expect(dola!.advanced.map((i) => i.key)).toEqual(["dola.render_deadline_s"]);
  });

  it("find a model's prices by its id or an alias, a tier after @, and the keys no model has", () => {
    const table = items()[4]!.value as Record<string, unknown>;
    expect(priceInputs(models()[0]!, table).map((p) => [p.key, p.label, p.text])).toEqual([
      ["seedance_v2.5@5s", "5 s", "2"],
      ["seedance_v2.5@10s", "10 s", "4"],
    ]);
    expect(priceInputs(models()[1]!, table)).toEqual([]);
    expect(otherPrices(models(), table).map((p) => p.key)).toEqual(["old-model"]);
  });

  it("send a whole price as a number and anything else as typed", () => {
    const prices = items()[4]!;
    expect(JSON.parse(withPrice(undefined, prices, "seedream-4.5", " 7 "))["seedream-4.5"]).toBe(7);
    expect(JSON.parse(withPrice(undefined, prices, "seedream-4.5", "1.5"))["seedream-4.5"]).toBe("1.5");
  });

  it("keep the paused models in the catalog's order, and a switch off and on again is no change", () => {
    const paused = items()[3]!;
    const ids = models().map((m) => m.id);
    const off = withModelOn(undefined, paused, ids, "seedance-2.5", false);
    expect(JSON.parse(off)).toEqual(["seedance-2.5", "seedance-1.0"]);
    expect(withModelOn(off, paused, ids, "seedance-2.5", true)).toBe('["seedance-1.0"]');
    // Stored out of the catalog's order: off and on again gives back the stored text, not the reordered list.
    const stored = { ...paused, value: ["seedance-1.0", "seedance-2.5"] };
    const offAgain = withModelOn(undefined, stored, ids, "seedream-4.5", false);
    expect(withModelOn(offAgain, stored, ids, "seedream-4.5", true)).toBe('["seedance-1.0","seedance-2.5"]');
  });
});

describe("the General links", () => {
  const links = () => card("core").findAll(".scard__links a");
  it("lists the five other sections in order, marks the first as current and moves the mark on a click", async () => {
    await open();
    expect(links().map((a) => a.text())).toEqual([
      "Change password",
      "Boot-only values",
      "Measured prices",
      "Fixed limits",
      "Containers",
    ]);
    expect(links().map((a) => a.attributes("aria-current"))).toEqual([
      "true",
      undefined,
      undefined,
      undefined,
      undefined,
    ]);
    vi.stubGlobal("scrollTo", vi.fn());
    await links()[2]!.trigger("click");
    expect(links().map((a) => a.attributes("aria-current"))).toEqual([
      undefined,
      undefined,
      "true",
      undefined,
      undefined,
    ]);
  });

  let wrapEl: HTMLElement | null = null;
  afterEach(() => {
    wrapEl?.remove();
    wrapEl = null;
  });
  // The card sits 700 px below the top of the viewport and keeps 16 px of scroll margin.
  function geometry(withArea: boolean): void {
    const target = document.getElementById("prices")!;
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue({ top: 700 } as DOMRect);
    const real = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation(((el: Element) =>
      el === target
        ? { scrollMarginTop: "16px" }
        : el.classList.contains("app-scroll__wrap")
          ? { overflowY: withArea ? "auto" : "visible" }
          : real(el)) as typeof window.getComputedStyle);
  }
  function inWrap(): HTMLElement {
    const wrap = document.createElement("div");
    wrap.className = "app-scroll__wrap";
    wrap.scrollTo = vi.fn() as typeof wrap.scrollTo;
    document.body.appendChild(wrap);
    wrap.appendChild(wrapper!.element);
    wrapEl = wrap;
    return wrap;
  }
  const pricesLink = () => links()[2]!;

  it("scrolls the page's scroll area to the card minus the anchor, and nothing else", async () => {
    await open();
    const wrap = inWrap();
    wrap.scrollTop = 500;
    vi.spyOn(wrap, "getBoundingClientRect").mockReturnValue({ top: 60 } as DOMRect);
    const intoView = vi.spyOn(Element.prototype, "scrollIntoView");
    const windowScroll = vi.fn();
    vi.stubGlobal("scrollTo", windowScroll);
    geometry(true);
    await pricesLink().trigger("click");
    expect(wrap.scrollTo).toHaveBeenCalledExactlyOnceWith({ top: 500 + (700 - 60 - 16) });
    expect(intoView).not.toHaveBeenCalled();
    expect(windowScroll).not.toHaveBeenCalled();
  });

  it("scrolls the window where the page has no scroll area of its own (a phone)", async () => {
    await open();
    const wrap = inWrap();
    const windowScroll = vi.fn();
    vi.stubGlobal("scrollTo", windowScroll);
    vi.stubGlobal("scrollY", 40);
    geometry(false);
    await pricesLink().trigger("click");
    expect(windowScroll).toHaveBeenCalledExactlyOnceWith({ top: 40 + (700 - 16) });
    expect(wrap.scrollTo).not.toHaveBeenCalled();
  });
});

describe("measured prices", () => {
  it("shows duration as Ns or -, flags a disagreement and shows 0 for none", async () => {
    await open();
    const rows = wrapper!.findAll("tr").filter((r) => r.text().includes("seedance"));
    expect(rows).toHaveLength(2);
    expect(rows[0]!.text()).toContain("8 s");
    expect(rows[0]!.find(".status-chip").text()).toBe("Upstream disagreed 2");
    expect(rows[0]!.find(".status-chip").attributes("data-tone")).toBe("bad");
    // no disagreement is a neutral pill with 0, and a missing duration or figure is a dash
    expect(rows[1]!.find(".status-chip").text()).toBe("0");
    expect(rows[1]!.find(".status-chip").attributes("data-tone")).toBe("neutral");
    expect(rows[1]!.text()).toContain("—");
  });

  it("is empty with its own text", async () => {
    vi.mocked(api.getPrices).mockResolvedValue({ items: [] });
    await open();
    expect(wrapper!.text()).toContain("No job has an upstream figure yet");
  });

  it("follows the global provider filter and reloads only the prices when it changes", async () => {
    await open();
    expect(api.getPrices).toHaveBeenLastCalledWith("");
    vi.mocked(api.getSettings).mockClear();
    useScopeStore().provider = "dola";
    await flushPromises();
    expect(api.getPrices).toHaveBeenLastCalledWith("dola");
    expect(api.getSettings).not.toHaveBeenCalled();
  });
});

describe("change password", () => {
  const errorTexts = () => wrapper!.findAll('[role="alert"]').map((e) => e.text());
  const field = (label: string) => wrapper!.find(`input[autocomplete="${label}"]`);
  const submit = () => wrapper!.find('button[type="submit"]');

  it("sends both passwords, ends the session here and lands on login", async () => {
    sessionStorage.setItem(TOKEN_KEY, "tok");
    sessionStorage.setItem(USER_KEY, "owner");
    sessionStorage.setItem(TEST_KEY, "rk-1");
    const router = await open();
    const auth = useAuthStore();
    const ended = vi.fn();
    auth.onSignOut(ended);
    expect(submit().attributes("disabled")).toBeDefined();
    await field("current-password").setValue("old password");
    await field("new-password").setValue("a much longer new one");
    vi.mocked(api.changePassword).mockResolvedValue({});
    await submit().trigger("click");
    await flushPromises();
    expect(api.changePassword).toHaveBeenCalledWith("old password", "a much longer new one");
    expect(ended).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(sessionStorage.getItem(TEST_KEY)).toBeNull();
    expect(router.currentRoute.value.name).toBe("login");
  });

  it("shows a refusal that names old_password on that field, and stays", async () => {
    const router = await open();
    await field("current-password").setValue("wrong");
    await field("new-password").setValue("a much longer new one");
    vi.mocked(api.changePassword).mockRejectedValue(
      new ApiError(400, {
        error: { code: "input.invalid", message: "Current password is wrong", param: "old_password" },
      }),
    );
    await submit().trigger("click");
    await flushPromises();
    await vi.waitFor(() => expect(errorTexts()).toEqual(["Current password is wrong"]));
    expect(router.currentRoute.value.name).toBe("settings");
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("shows a refusal that names new_password on that field", async () => {
    await open();
    await field("current-password").setValue("right password");
    await field("new-password").setValue("an example one");
    vi.mocked(api.changePassword).mockRejectedValue(
      new ApiError(400, { error: { code: "input.invalid", message: "Too short", param: "new_password" } }),
    );
    await submit().trigger("click");
    await flushPromises();
    await vi.waitFor(() => expect(errorTexts()).toEqual(["Too short"]));
    // the error describes its field, and only that one
    const error = wrapper!.find('[role="alert"]');
    expect(field("new-password").attributes("aria-describedby")).toBe(error.attributes("id"));
    expect(field("new-password").attributes("aria-invalid")).toBe("true");
    expect(field("current-password").attributes("aria-describedby")).toBeUndefined();
  });

  it("clears the field error on the next attempt", async () => {
    await open();
    await field("current-password").setValue("x");
    await field("new-password").setValue("a long new password");
    vi.mocked(api.changePassword)
      .mockRejectedValueOnce(
        new ApiError(400, { error: { code: "input.invalid", message: "Too short", param: "new_password" } }),
      )
      .mockResolvedValueOnce({});
    await submit().trigger("click");
    await flushPromises();
    await vi.waitFor(() => expect(errorTexts()).toHaveLength(1));
    await submit().trigger("click");
    await flushPromises();
    await vi.waitFor(() => expect(errorTexts()).toHaveLength(0));
  });

  it("checks the length in the form before sending, in the drawn words: at least 10, at most 256", async () => {
    await open();
    await field("current-password").setValue("right password");
    await field("new-password").setValue("short-pw!");
    await submit().trigger("click");
    await flushPromises();
    expect(errorTexts()).toEqual(["At least 10 characters"]);
    expect(field("new-password").attributes("aria-invalid")).toBe("true");
    await field("new-password").setValue("x".repeat(257));
    await submit().trigger("click");
    await flushPromises();
    expect(errorTexts()).toEqual(["At most 256 characters"]);
    expect(api.changePassword).not.toHaveBeenCalled();
    vi.mocked(api.changePassword).mockResolvedValue({});
    await field("new-password").setValue("short-pass");
    await submit().trigger("click");
    await flushPromises();
    expect(api.changePassword).toHaveBeenCalledWith("right password", "short-pass");
  });

  it("any other error is a toast and the session stays", async () => {
    sessionStorage.setItem(TOKEN_KEY, "tok");
    await open();
    await field("current-password").setValue("a");
    await field("new-password").setValue("b long enough");
    vi.mocked(api.changePassword).mockRejectedValue(
      new ApiError(500, { error: { code: "server.error", message: "boom" } }),
    );
    await submit().trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith("boom");
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe("tok");
  });
});
