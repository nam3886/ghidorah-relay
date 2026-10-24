// The alert groups admin routes. Titles and reply texts hold customer-influenced words: show them as text only.
import { request } from "@/api/client";

export type AlertStatus = "open" | "acknowledged" | "all";

export interface AlertGroup {
  kind: string;
  group: string;
  title: string;
  severity: string;
  provider: string;
  first_ms: number | null;
  last_ms: number | null;
  count: number;
  open_count: number;
  keys: number;
  newest_id: number;
  // an unclear group's newest stored reply (cut at 200 characters); null for a billing group
  reply?: string | null;
  // a billing group's key: its label, else its prefix; null for an unclear group
  key_label?: string | null;
  // the end of its kind's mute (Alert rules), null when the kind is not muted now
  muted_until_ms?: number | null;
}

export interface UnclearItem {
  id: number;
  at_ms: number | null;
  text: string;
  job_id: string;
  job_status: string;
  job_error_code: string | null;
  account_label: string | null;
  key_id: string;
  job_title: string | null;
  key_label: string | null;
  acknowledged_ms: number | null;
  acknowledged_by: string | null;
}

export interface BillingItem {
  id: number;
  at_ms: number | null;
  alert: string;
  // "*" (the key's points wallet) or a provider's name (its cap)
  scope: string;
  amount: number;
  used_day: number;
  used_total: number;
  detail: string | null;
  key_label: string | null;
  job_id: string | null;
  job_title: string | null;
  acknowledged_ms: number | null;
  acknowledged_by: string | null;
}

export interface AlertDetail extends AlertGroup {
  items: (UnclearItem | BillingItem)[];
  // unclear_reply groups only
  outcomes?: { done: number; failed: number; open: number };
  failed_codes?: Record<string, number>;
  // the one media type of all the group's jobs (video, image); null when they are mixed
  media?: string | null;
  // unclear_reply groups only: the distinct accounts that heard the wording
  accounts?: number;
}

export interface GroupRef {
  kind: string;
  group: string;
}

const groupPath = (g: GroupRef): string =>
  `/alerts/groups/${encodeURIComponent(g.kind)}/${encodeURIComponent(g.group)}`;

export const listAlertGroups = (status: AlertStatus, kind: string, limit: number): Promise<{ items: AlertGroup[] }> =>
  request("/alerts/groups", { query: { status, kind, limit } });

export const getAlertGroup = (g: GroupRef, limit: number): Promise<AlertDetail> =>
  request(groupPath(g), { query: { limit } });

// `through_id` is the newest row the operator saw: a row that arrives later keeps the group open.
export const acknowledgeGroup = (g: GroupRef, throughId: number): Promise<{ acknowledged: number }> =>
  request(`${groupPath(g)}/acknowledge`, { method: "POST", body: { through_id: throughId } });

// Alert rules: every kind the relay raises, `stored` (the groups above) or `live` (a /stats condition). A kind is on
// and unmuted until the admin changes it. `thresholds` are the settings that set it (edited with PUT /settings),
// `fixed` a figure that is code.
export type RuleSource = "stored" | "live";

export interface AlertRule {
  source: RuleSource;
  kind: string;
  severity: string;
  text: string;
  fixed: string | null;
  thresholds: Array<{ setting: string; value: unknown }>;
  // a money-integrity kind (clamp, wallet drift): never off, muted `max_mute_minutes` (a day) at most
  money: boolean;
  max_mute_minutes: number;
  enabled: boolean;
  muted_until_ms: number | null;
  updated_by: string | null;
  updated_ms: number | null;
}

export const listAlertRules = (): Promise<{ items: AlertRule[] }> => request("/alerts/rules");

// `mute_minutes` 0 ends a mute; what is not given stays as it was.
export const changeAlertRule = (
  source: RuleSource,
  kind: string,
  body: { enabled?: boolean; mute_minutes?: number },
): Promise<Pick<AlertRule, "enabled" | "muted_until_ms" | "updated_by" | "updated_ms">> =>
  request(`/alerts/rules/${encodeURIComponent(source)}/${encodeURIComponent(kind)}`, { method: "POST", body });

// The groups acknowledged since the relay's billing day began (`day_start_ms`), the newest first.
export interface ResolvedGroup extends AlertGroup {
  resolved_ms: number;
  resolved_by: string | null;
  resolved_rows: number;
}

export interface ResolvedToday {
  count: number;
  day_start_ms: number;
  tz: string;
  items: ResolvedGroup[];
}

export const getResolvedToday = (): Promise<ResolvedToday> => request("/alerts/resolved");
