import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as shell from "@/api/shell";
import ProviderStrip from "@/components/layout/ProviderStrip.vue";
import ProviderTile from "@/components/shared/ProviderTile.vue";
import { byProviderOrder, providerClass } from "@/lib/providers";
import { createAppRouter } from "@/router";
import { useProvidersStore } from "@/stores/providers";
import { useScopeStore } from "@/stores/scope";

vi.mock("@/api/shell", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/shell")>()),
  getProvidersSummary: vi.fn(),
}));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const row = (provider: string, running: number, queued: number, claims = true): shell.ProviderSummary => ({
  provider,
  execution: "http",
  claims_enabled: claims,
  running,
  queued,
  done_today: 0,
  failed_today: 0,
  accounts: { usable: 0, total: 0 },
  pools: [],
});

beforeEach(() => {
  setActivePinia(createPinia());
  vi.mocked(shell.getProvidersSummary).mockResolvedValue({
    day_tz: "UTC",
    day_start_ms: 0,
    hours: [],
    providers: [row("vibes", 1, 1), row("dola", 3, 2), row("flow", 0, 0, false)],
  });
});

describe("the provider strip", () => {
  it("lists All then the registered providers in the drawn order with no count, a paused one marked", async () => {
    await useProvidersStore().refresh();
    const w = mount(ProviderStrip);
    const tabs = w.findAll("button.ptab");
    expect(tabs.map((t) => t.text().replace(/\s+/g, " "))).toEqual(["All", "D dola", "V vibes", "F flow paused"]);
    expect(tabs[3]!.get(".ptab__paused").text()).toBe("paused");
    expect(tabs.map((t) => t.attributes("aria-label"))).toEqual(["All providers", "dola", "vibes", "flow, paused"]);
    expect(tabs[0]!.attributes("aria-pressed")).toBe("true");
  });

  it("is the provider filter: a tab sets the scope store, All clears it", async () => {
    await useProvidersStore().refresh();
    const w = mount(ProviderStrip);
    const scope = useScopeStore();
    await w.get('[data-testid="provider-tab-vibes"]').trigger("click");
    expect(scope.provider).toBe("vibes");
    expect(w.get('[data-testid="provider-tab-vibes"]').attributes("aria-pressed")).toBe("true");
    await w.get('[data-testid="provider-tab-all"]').trigger("click");
    expect(scope.provider).toBe("");
  });

  it("draws the catalog's providers until the summary arrives", async () => {
    vi.mocked(shell.getProvidersSummary).mockReturnValue(new Promise(() => {}));
    useScopeStore().models = [{ id: "m1", owned_by: "meta" }];
    const w = mount(ProviderStrip);
    await flushPromises();
    expect(w.findAll("button.ptab").map((t) => t.text().replace(/\s+/g, " "))).toEqual(["All", "M meta"]);
  });

  it("is shown on the tabs that filter by provider, in each one's mode; the others keep the top bar's list", () => {
    const routes = createAppRouter().getRoutes();
    const strip = Object.fromEntries(
      routes.filter((r) => r.meta.providerStrip).map((r) => [r.name, r.meta.providerStrip]),
    );
    expect(strip).toEqual({
      jobs: "all",
      accounts: "all",
      keys: "all",
      logs: "all",
      alerts: "all",
      frames: "only",
      settings: "general",
      test: "only",
    });
  });

  it("on Settings starts with All providers and General, draws no counts, and General is its own pick", async () => {
    await useProvidersStore().refresh();
    const w = mount(ProviderStrip, { props: { mode: "general" } });
    const scope = useScopeStore();
    expect(w.findAll("button.ptab").map((t) => t.text().replace(/\s+/g, " "))).toEqual([
      "All providers",
      "General",
      "D dola",
      "V vibes",
      "F flow paused",
    ]);
    await w.get('[data-testid="provider-tab-general"]').trigger("click");
    expect([scope.provider, scope.general]).toEqual(["", true]);
    expect(w.get('[data-testid="provider-tab-general"]').attributes("aria-pressed")).toBe("true");
    expect(w.get('[data-testid="provider-tab-all"]').attributes("aria-pressed")).toBe("false");
    await w.get('[data-testid="provider-tab-dola"]').trigger("click");
    expect([scope.provider, scope.general]).toEqual(["dola", false]);
    await w.get('[data-testid="provider-tab-general"]').trigger("click");
    await w.get('[data-testid="provider-tab-all"]').trigger("click");
    expect([scope.provider, scope.general]).toEqual(["", false]);
  });

  it("for a one-provider page lists the providers alone, picks `only` (not the filter) and is off while locked", async () => {
    await useProvidersStore().refresh();
    const scope = useScopeStore();
    const w = mount(ProviderStrip, { props: { mode: "only" } });
    await flushPromises();
    expect(w.find('[data-testid="provider-tab-all"]').exists()).toBe(false);
    expect(w.findAll("button.ptab").map((t) => t.text().replace(/\s+/g, " "))).toEqual([
      "D dola",
      "V vibes",
      "F flow paused",
    ]);
    await w.get('[data-testid="provider-tab-vibes"]').trigger("click");
    expect(scope.only).toBe("vibes");
    expect(scope.provider).toBe("");
    expect(w.get('[data-testid="provider-tab-vibes"]').attributes("aria-pressed")).toBe("true");
    scope.stripLocked = true;
    await flushPromises();
    expect(w.findAll("button.ptab").every((t) => t.attributes("disabled") !== undefined)).toBe(true);
  });

  it("reads the summary itself when nobody has yet (a page other than Jobs)", async () => {
    vi.mocked(shell.getProvidersSummary).mockClear();
    mount(ProviderStrip, { props: { mode: "all" } });
    await flushPromises();
    expect(shell.getProvidersSummary).toHaveBeenCalledTimes(1);
  });
});

describe("provider marks", () => {
  it("a provider is a tile with its first letter in its own tone; one with no tone is neutral", () => {
    const w = mount(ProviderTile, { props: { provider: "meta" } });
    expect(w.text()).toBe("M");
    expect(w.classes()).toContain("p-meta");
    expect(w.attributes("aria-hidden")).toBe("true");
    expect(providerClass("fake")).toBe("p-other");
  });

  it("orders dola, meta, vibes, flow, qwen, gemini, then any other by name", () => {
    expect(byProviderOrder(["zeta", "flow", "alpha", "dola", "vibes", "meta"])).toEqual([
      "dola",
      "meta",
      "vibes",
      "flow",
      "alpha",
      "zeta",
    ]);
  });
});
