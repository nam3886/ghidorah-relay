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
  acknowledged_ms: number | null;
  acknowledged_by: string | null;
}

export interface BillingItem {
  id: number;
  at_ms: number | null;
  alert: string;
  provider: string;
  kind: string;
  amount: number;
  used_day: number;
  used_total: number;
  detail: string | null;
  acknowledged_ms: number | null;
  acknowledged_by: string | null;
}

export interface AlertDetail extends AlertGroup {
  items: (UnclearItem | BillingItem)[];
  // unclear_reply groups only
  outcomes?: { done: number; failed: number; open: number };
  failed_codes?: Record<string, number>;
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
