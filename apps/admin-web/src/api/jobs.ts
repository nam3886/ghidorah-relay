// The Jobs list and its bulk actions (/admin/jobs).
import { request } from "@/api/client";
import { bulkAction, regenerateCall } from "@/lib/operation-key";

// The statuses the filter offers. `submitting` exists server-side and is open work like the others.
export const JOB_STATUSES = ["queued", "submitting", "waiting", "fetching", "unknown", "done", "failed"] as const;
// The grouped chips (Ledger): what needs a look and what is on its way; `status` takes either as a comma list.
export const NEEDS_A_LOOK = "failed,unknown";
export const IN_FLIGHT = "queued,submitting,waiting,fetching";

export interface JobSummary {
  id: string;
  provider: string;
  key_id: string;
  key_label: string | null;
  operation: string;
  media_type: string;
  model: string;
  title: string | null;
  status: string;
  error_code: string | null;
  price: number;
  account_id: string | null;
  account_label: string | null;
  external_ref: string | null;
  created_ms: number | null;
  finished_ms: number | null;
  error_status: number | null;
  can_recheck: boolean;
  outputs: number;
  inputs: number;
  net_charged: number;
  model_substituted: boolean;
}

export interface JobPage {
  items: JobSummary[];
  // every job the filters match, not only this page
  total: number;
  next_cursor: string | null;
  // only when `tz` was sent: "YYYY-MM-DD" (local day in tz) -> every job that day under the same filters,
  // one entry for each day that has a row on this page
  day_totals?: Record<string, number>;
  // the same filters but the status: status -> its jobs (a status with none is absent)
  status_counts?: Record<string, number>;
  // the relay holds at least one job, whatever the filters: an empty page under a range is then "nothing matches"
  has_jobs?: boolean;
}

// The Jobs page's range switch: the list's window and the Today section's figures (local days of dashboard_tz).
export const JOB_RANGES = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
] as const;
export type JobRange = (typeof JOB_RANGES)[number]["value"];

export interface JobListQuery {
  status: string;
  provider: string;
  keyId: string;
  offset: number;
  limit: number;
  // created_at window, epoch ms: from inclusive, to exclusive (one UTC hour for the band's columns)
  createdFromMs?: number;
  createdToMs?: number;
  // IANA zone (Asia/Ho_Chi_Minh): asks for day_totals
  tz?: string;
  range?: JobRange;
  // the start of the job id, the prompt or title, the key's or the account's label
  q?: string;
}

// Newest first. Empty filters are dropped by the client's query builder.
export const listJobs = (q: JobListQuery): Promise<JobPage> =>
  request("/jobs", {
    query: {
      limit: q.limit,
      offset: q.offset,
      status: q.status,
      provider: q.provider,
      key_id: q.keyId,
      created_from_ms: q.createdFromMs,
      created_to_ms: q.createdToMs,
      tz: q.tz,
      range: q.range,
      q: q.q,
    },
  });

export interface KeyOption {
  id: string;
  label: string;
  prefix?: string;
}

// The key filter's options. Only id, label and prefix are read (a key with no label is named by its prefix).
export const listKeyOptions = (): Promise<{ items: KeyOption[] }> => request("/keys");

export interface BulkRecheckResult {
  rechecked: string[];
  skipped: Record<string, string>;
}

export const bulkRecheck = (jobIds: string[]): Promise<BulkRecheckResult> =>
  request("/jobs/bulk-recheck", { method: "POST", body: { job_ids: jobIds } });

export interface BulkRegenerateResult {
  created: { from: string; id: string; status: string; replayed: boolean }[];
  failed: Record<string, string>;
}

// One key per confirmed selection: the action name is the sorted ids, so the same selection retried sends the same key.
export const bulkRegenerate = (jobIds: string[]): Promise<BulkRegenerateResult> =>
  regenerateCall(bulkAction(jobIds), (operationKey) =>
    request<BulkRegenerateResult>("/jobs/bulk-regenerate", {
      method: "POST",
      body: { job_ids: jobIds, operation_key: operationKey },
    }),
  );
