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
  it("lists All then the registered providers in the drawn order, each with its unfinished jobs, paused marked", async () => {
    await useProvidersStore().refresh();
    const w = mount(ProviderStrip);
    const tabs = w.findAll("button.ptab");
    expect(tabs.map((t) => t.text().replace(/\s+/g, " "))).toEqual([
      "All 7",
      "D dola 5",
      "V vibes 2",
      "F flow 0paused",
    ]);
    expect(tabs[3]!.get(".ptab__paused").text()).toBe("paused");
    expect(tabs[3]!.attributes("aria-label")).toBe("flow, 0 unfinished jobs, paused");
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

  it("draws the catalog's providers without counts until the summary arrives", async () => {
    useScopeStore().models = [{ id: "m1", owned_by: "meta" }];
    const w = mount(ProviderStrip);
    await flushPromises();
    expect(w.findAll("button.ptab").map((t) => t.text().replace(/\s+/g, " "))).toEqual(["All", "M meta"]);
  });

  it("is shown on Jobs only: the other tabs keep the top bar's Provider list", () => {
    const routes = createAppRouter().getRoutes();
    const strip = routes.filter((r) => r.meta.providerStrip).map((r) => r.name);
    expect(strip).toEqual(["jobs"]);
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
