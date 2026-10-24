import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import type { ProviderSummary, ProvidersSummary } from "@/api/shell";
import ProviderTable from "@/components/jobs/ProviderTable.vue";
import { hourBars, pathText, poolText, rowsOf, totalsOf } from "@/components/jobs/provider-table";

const H = 3_600_000;
const START = Date.UTC(2026, 9, 23, 0);
const provider = (name: string, over: Partial<ProviderSummary> = {}): ProviderSummary => ({
  provider: name,
  execution: "http",
  claims_enabled: true,
  running: 0,
  queued: 0,
  done_today: 0,
  failed_today: 0,
  accounts: { usable: 0, total: 0 },
  pools: [],
  ...over,
});
// three hours so far: dola 2 done at 00:00, vibes 3 done + 1 failed at 02:00
const SUMMARY: ProvidersSummary = {
  day_tz: "UTC",
  day_start_ms: START,
  providers: [
    provider("vibes", { running: 1, queued: 2, done_today: 3, failed_today: 1, accounts: { usable: 4, total: 5 } }),
    provider("dola", {
      execution: "browser",
      running: 2,
      done_today: 2,
      accounts: { usable: 6, total: 8 },
      pools: [
        { pool: "video", unit: "credits", left: 11, budget: 32 },
        { pool: "image", unit: "jobs", left: 120, budget: 320 },
      ],
    }),
    provider("flow", { claims_enabled: false }),
  ],
  hours: [
    { h: START, done: { dola: 2, vibes: 0, flow: 0 }, failed: { dola: 0, vibes: 0, flow: 0 } },
    { h: START + H, done: { dola: 0, vibes: 0, flow: 0 }, failed: { dola: 0, vibes: 0, flow: 0 } },
    { h: START + 2 * H, done: { dola: 0, vibes: 3, flow: 0 }, failed: { dola: 0, vibes: 1, flow: 0 } },
  ],
};

describe("the Jobs today table", () => {
  it("draws the providers in the drawn order: dola, meta, vibes, flow", () => {
    expect(rowsOf(SUMMARY).map((p) => p.provider)).toEqual(["dola", "vibes", "flow"]);
  });

  it("draws 24 hour bars: the hours so far on one scale for every row, then flat stubs for the hours to come", () => {
    const vibes = hourBars(SUMMARY, "vibes");
    const dola = hourBars(SUMMARY, "dola");
    expect(vibes).toHaveLength(24);
    expect(vibes.map((b) => b.kind).slice(0, 4)).toEqual(["zero", "zero", "bar", "future"]);
    expect(vibes[2]!.pct).toBe(100); // 3 done + 1 failed is the busiest hour of any provider
    expect(dola[0]!.pct).toBe(50); // 2 of 4, on the same scale
    expect(vibes[2]!.title).toBe("02:00: 4 finished, 1 failed");
    expect(vibes[23]!).toEqual({ kind: "future", pct: 0, title: "23:00: not yet" });
  });

  it("words a pool as left over budget, its name and its unit", () => {
    const [video, image] = rowsOf(SUMMARY)[0]!.pools;
    expect(poolText(video!)).toBe("11 / 32 video pts");
    expect(poolText(image!)).toBe("120 / 320 image jobs");
    expect(poolText({ ...image!, unit: "uses" })).toBe("120 / 320 image uses");
  });

  it("names the send path, or paused when the provider takes no new job", () => {
    const [dola, , flow] = rowsOf(SUMMARY);
    expect([pathText(dola!), pathText(flow!)]).toEqual(["browser", "paused"]);
  });

  it("sums every provider in the totals row", () => {
    expect(totalsOf(SUMMARY.providers)).toEqual({
      running: 3,
      queued: 2,
      done: 5,
      failed: 1,
      usable: 10,
      accounts: 13,
    });
  });

  it("shows a row per provider and a totals row, the failed count in red only when above zero", () => {
    const w = mount(ProviderTable, { props: { summary: SUMMARY, selected: "" } });
    const row = (n: string) => w.get(`[data-testid="provider-row-${n}"]`);
    expect(row("vibes").text()).toContain("1 +2");
    expect(row("vibes").find(".failn").classes()).not.toContain("failn--none");
    expect(row("dola").find(".failn").classes()).toContain("failn--none");
    expect(row("dola").text()).toContain("6/8");
    expect(row("flow").find(".via").text()).toBe("paused");
    expect(w.get(".ptable__sum").text()).toContain("10/13");
    expect(w.get(".ptable__tot").text()).toBe("6 finished today · 1 failed");
  });

  it("a click on a row picks its provider, a second click on the picked row clears it, and the others dim", async () => {
    const w = mount(ProviderTable, { props: { summary: SUMMARY, selected: "" } });
    await w.get('[data-testid="provider-row-vibes"]').trigger("click");
    expect(w.emitted("pick")).toEqual([["vibes"]]);
    await w.setProps({ selected: "vibes" });
    expect(w.get('[data-testid="provider-row-dola"]').classes()).toContain("is-dim");
    expect(w.get('[data-testid="provider-row-vibes"]').classes()).toContain("is-on");
    await w.get('[data-testid="provider-row-vibes"]').trigger("click");
    await w.get('[data-testid="provider-card-dola"]').trigger("keydown", { key: "Enter" });
    expect(w.emitted("pick")).toEqual([["vibes"], [""], ["dola"]]);
  });

  it("draws nothing until the summary arrives", () => {
    const w = mount(ProviderTable, { props: { summary: null, selected: "" } });
    expect(w.find('[data-testid="provider-table"]').exists()).toBe(false);
  });
});
