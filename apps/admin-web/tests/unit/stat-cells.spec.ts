import { describe, expect, it } from "vitest";
import type { Stats } from "@/api/shell";
import { statCells } from "@/lib/stat-cells";

const stats = (over: Partial<Stats> = {}): Stats => ({
  jobs: { queued: 2, in_progress: 3, done: 40, failed: 0, pre_job_failures_24h: 0 },
  made: { videos: 11, images: 29 },
  accounts: { usable: 4, available: 6, dead: 0, disabled: 1 },
  keys: 3,
  alerts: [],
  ...over,
});

describe("statCells", () => {
  it("lists the nine cells in order with their exact values", () => {
    const cells = statCells(stats());
    expect(cells.map((c) => [c.key, c.label, c.value])).toEqual([
      ["in-progress", "In progress", "5"],
      ["completed", "Completed", "40"],
      ["failed", "Failed", "0"],
      ["pre-job", "Failed before a job (24 h)", "0"],
      ["accounts-ready", "Accounts ready", "4 / 6"],
      ["accounts-dead", "Accounts dead", "0"],
      ["accounts-disabled", "Accounts disabled", "1"],
      ["videos", "Videos made", "11"],
      ["images", "Images made", "29"],
    ]);
  });

  it("links each cell to the tab it counts", () => {
    const to = Object.fromEntries(statCells(stats()).map((c) => [c.key, c.to]));
    expect(to["in-progress"]).toEqual({ name: "jobs" });
    expect(to["completed"]).toEqual({ name: "jobs", query: { status: "done" } });
    expect(to["failed"]).toEqual({ name: "jobs", query: { status: "failed" } });
    expect(to["pre-job"]).toEqual({ name: "logs", query: { status: "pre_job" } });
    expect(to["accounts-ready"]).toEqual({ name: "accounts" });
    expect(to["accounts-dead"]).toEqual({ name: "accounts" });
    expect(to["accounts-disabled"]).toEqual({ name: "accounts" });
  });

  it("marks failed, failed-before-a-job and dead red only when above zero", () => {
    const quiet = statCells(stats());
    expect(quiet.filter((c) => c.danger)).toEqual([]);
    const loud = statCells(
      stats({
        jobs: { queued: 0, in_progress: 0, done: 0, failed: 2, pre_job_failures_24h: 5 },
        accounts: { usable: 0, available: 0, dead: 1, disabled: 7 },
      }),
    );
    expect(loud.filter((c) => c.danger).map((c) => c.key)).toEqual(["failed", "pre-job", "accounts-dead"]);
  });
});
