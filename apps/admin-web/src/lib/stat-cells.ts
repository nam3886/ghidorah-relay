import type { RouteLocationRaw } from "vue-router";
import type { Stats } from "@/api/shell";

export interface StatCell {
  key: string;
  // Short enough to stay on one line in a cell.
  label: string;
  // The full wording, shown as a tooltip.
  title: string;
  value: string;
  // Each cell links to the tab it counts. Jobs reads `status`, Logs reads `status` (see the views).
  to: RouteLocationRaw;
  // Red only when the number is above zero.
  danger: boolean;
}

export function statCells(stats: Stats): StatCell[] {
  const { jobs, made, accounts } = stats;
  const cell = (
    key: string,
    label: string,
    value: number | string,
    to: RouteLocationRaw,
    redWhenNonZero = false,
    title = label,
  ): StatCell => ({
    key,
    label,
    title,
    value: String(value),
    to,
    danger: redWhenNonZero && Number(value) > 0,
  });
  return [
    cell("in-progress", "In progress", jobs.in_progress + jobs.queued, { name: "jobs" }),
    cell("completed", "Completed", jobs.done, { name: "jobs", query: { status: "done" } }),
    cell("failed", "Failed", jobs.failed, { name: "jobs", query: { status: "failed" } }, true),
    cell(
      "pre-job",
      "Failed pre-job (24 h)",
      jobs.pre_job_failures_24h,
      { name: "logs", query: { status: "pre_job" } },
      true,
      "Failed before a job was created (last 24 hours)",
    ),
    cell("accounts-ready", "Accounts ready", `${accounts.usable} / ${accounts.available}`, { name: "accounts" }),
    cell("accounts-dead", "Accounts dead", accounts.dead, { name: "accounts" }, true),
    cell("accounts-disabled", "Accounts disabled", accounts.disabled, { name: "accounts" }),
    cell("videos", "Videos made", made.videos, { name: "jobs", query: { status: "done" } }),
    cell("images", "Images made", made.images, { name: "jobs", query: { status: "done" } }),
  ];
}
