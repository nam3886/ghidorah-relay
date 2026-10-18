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
import { notify } from "@/lib/notify";
import { TEST_KEY, TOKEN_KEY, USER_KEY } from "@/lib/session";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";
import SettingsView from "@/views/SettingsView.vue";

vi.mock("@/api/settings");
vi.mock("@/api/shell", () => ({ getModels: vi.fn() }));
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

const items = (): api.SettingItem[] => [
  { key: "session_ttl_s", value: 3600, source: "db", default: 1800, description: "How long a session lasts" },
  { key: "billing_label", value: "relay", source: "default", default: "relay", description: "A label" },
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
  }));
  vi.mocked(api.getPrices).mockImplementation(async () => ({ items: priceRows() }));
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
});

describe("the settings page", () => {
  it("lets the page scroll: no table holds a fixed height that would clip a row", async () => {
    await open();
    expect(wrapper!.findAll("table")).toHaveLength(2);
    expect(wrapper!.html()).not.toMatch(/max-height:\s*(\d|calc)/);
  });

  it("draws the first read as the drawn loading rows: each table's header over five rows of bars", async () => {
    vi.mocked(api.getSettings).mockReturnValue(new Promise(() => undefined));
    vi.mocked(api.getPrices).mockReturnValue(new Promise(() => undefined));
    await open();
    const tables = wrapper!.findAll("table");
    expect(tables).toHaveLength(2);
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

describe("the settings table", () => {
  it("shows each setting with its value, source and default, and the boot-only values with - for none", async () => {
    await open();
    const text = wrapper!.text();
    expect(text).toContain("session_ttl_s");
    expect(text).toContain("How long a session lasts");
    expect((valueInput("session_ttl_s").element as HTMLInputElement).value).toBe("3600");
    expect((valueInput("billing_label").element as HTMLInputElement).value).toBe("relay");
    expect(text).toContain("db");
    expect(text).toContain("1800");
    expect(text).toContain("BILLING_TZ");
    expect(text).toContain("Asia/Saigon");
    const env = wrapper!
      .find('dl[aria-label="Boot-only values"]')
      .findAll("div")
      .find((r) => r.text().includes("PUBLIC_BASE_URL"));
    expect(env!.find("dd").text()).toBe("-");
  });

  it("Save is disabled until the value changed, the row says edited, and an edit back disables it again", async () => {
    await open();
    expect(button("Save session_ttl_s").attributes("disabled")).toBeDefined();
    expect(wrapper!.text()).not.toContain("edited");
    await valueInput("session_ttl_s").setValue("7200");
    expect(button("Save session_ttl_s").attributes("disabled")).toBeUndefined();
    expect(wrapper!.text()).toContain("edited");
    expect(button("Save billing_label").attributes("disabled")).toBeDefined();
    await valueInput("session_ttl_s").setValue("3600");
    expect(button("Save session_ttl_s").attributes("disabled")).toBeDefined();
  });

  it("Save sends the parsed value, reloads both lists and says Saved", async () => {
    await open();
    await valueInput("session_ttl_s").setValue("7200");
    vi.mocked(api.putSetting).mockResolvedValue(items()[0]!);
    vi.mocked(api.getSettings).mockClear();
    await button("Save session_ttl_s").trigger("click");
    await flushPromises();
    expect(api.putSetting).toHaveBeenCalledWith("session_ttl_s", 7200);
    expect(api.getSettings).toHaveBeenCalledTimes(1);
    expect(notify.success).toHaveBeenCalledWith("Saved");
    expect(button("Save session_ttl_s").attributes("disabled")).toBeDefined();
  });

  it("Save falls back to the raw string for text that is not JSON", async () => {
    await open();
    await valueInput("billing_label").setValue("my label");
    vi.mocked(api.putSetting).mockResolvedValue(items()[1]!);
    await button("Save billing_label").trigger("click");
    await flushPromises();
    expect(api.putSetting).toHaveBeenCalledWith("billing_label", "my label");
  });

  it("Reset is on for a stored value or an edit, and off for a value that is only the environment or default", async () => {
    await open();
    expect(button("Reset session_ttl_s").attributes("disabled")).toBeUndefined();
    expect(button("Reset billing_label").attributes("disabled")).toBeDefined();
    await valueInput("billing_label").setValue("changed");
    expect(button("Reset billing_label").attributes("disabled")).toBeUndefined();
    await valueInput("billing_label").setValue("relay");
    expect(button("Reset billing_label").attributes("disabled")).toBeDefined();
  });

  it("Reset deletes the stored value and says Reset", async () => {
    await open();
    vi.mocked(api.deleteSetting).mockResolvedValue({});
    await button("Reset session_ttl_s").trigger("click");
    await flushPromises();
    expect(api.deleteSetting).toHaveBeenCalledWith("session_ttl_s");
    expect(notify.success).toHaveBeenCalledWith("Reset");
  });

  it("a refused value is a toast with the field and no Saved", async () => {
    await open();
    await valueInput("session_ttl_s").setValue("nope");
    vi.mocked(api.putSetting).mockRejectedValue(
      new ApiError(400, { error: { code: "input.invalid", message: "bad value", param: "value" } }),
    );
    await button("Save session_ttl_s").trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith("bad value (value)");
    expect(notify.success).not.toHaveBeenCalled();
  });

  it("shows an empty list and a failed load with Retry", async () => {
    vi.mocked(api.getSettings).mockResolvedValueOnce({ items: [], env_only: [] });
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

describe("the sub-navigation", () => {
  it("lists the four sections in order, marks the first as current and moves the mark on a click", async () => {
    await open();
    const nav = wrapper!.find('nav[aria-label="Settings sections"]');
    const links = nav.findAll("a");
    expect(links.map((a) => a.text())).toEqual(["Settings", "Boot-only values", "Measured prices", "Change password"]);
    expect(links.map((a) => a.attributes("aria-current"))).toEqual(["true", undefined, undefined, undefined]);
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    await links[2]!.trigger("click");
    expect(wrapper!.findAll('nav[aria-label="Settings sections"] a').map((a) => a.attributes("aria-current"))).toEqual([
      undefined,
      undefined,
      "true",
      undefined,
    ]);
    expect(scroll).toHaveBeenCalledTimes(1);
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
