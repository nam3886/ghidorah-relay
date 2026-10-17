// The calls the app shell makes: login, session, the model catalog (it feeds the provider filter) and the stats strip.
import type { AlertGroup } from "@/api/alerts";
import { request } from "@/api/client";

export interface LoginResult {
  token: string;
  expires_at: number;
}

// A 401 here is a wrong password, so it must not sign out or redirect.
export const login = (username: string, password: string): Promise<LoginResult> =>
  request("/login", { method: "POST", body: { username, password }, noRedirect: true });

export const logout = (): Promise<unknown> => request("/logout", { method: "POST", noRedirect: true });

export const getMe = (noRedirect = false): Promise<{ username: string }> => request("/me", { noRedirect });

export interface CatalogModel {
  id: string;
  owned_by: string;
  [key: string]: unknown;
}

export const getModels = (): Promise<{ data: CatalogModel[] }> => request("/models");

export interface LiveAlert {
  kind: string;
  severity: string;
  message: string;
}

export interface Stats {
  jobs: { queued: number; in_progress: number; done: number; failed: number; pre_job_failures_24h: number };
  made: { videos: number; images: number };
  // in_use: accounts holding a live lease; asleep: accounts in cooldown or with a resting pool (any state, as the list)
  accounts: { usable: number; available: number; dead: number; disabled: number; in_use?: number; asleep?: number };
  keys: number;
  alerts: LiveAlert[];
}

export const getStats = (provider: string): Promise<Stats> => request("/stats", { query: { provider } });

// One bucket per UTC hour: jobs submitted in that hour, split by their current outcome.
export interface HourBucket {
  hour_ms: number;
  delivered: number;
  failed: number;
  in_progress: number;
}

// 24 buckets, oldest first, the current hour last, zero-filled.
export const getHourlyStats = (provider: string): Promise<{ hours: HourBucket[] }> =>
  request("/stats/hourly", { query: { provider } });

// One entry per pool of the provider(s). Only `available` accounts count (not dead, not disabled); a pool with no
// budget has every account unknown. `drift` counts accounts whose counted points disagree with the ledger.
export interface PoolFigures {
  pool: string;
  left: number;
  budget: number;
  unknown: number;
  drift: number;
}

export interface PoolStats {
  pools: PoolFigures[];
  proxy_failed: number;
  // the newest finished "Check all", epoch ms; null when none has finished
  last_full_check_ms: number | null;
}

export const getPoolStats = (provider: string): Promise<PoolStats> => request("/stats/pool", { query: { provider } });

// The open groups, the most pressing first: the shell counts them (200 is the page cap, shown as "200+") and the Jobs
// banner words the first one.
export const getOpenAlertGroups = (): Promise<{ items: AlertGroup[] }> =>
  request("/alerts/groups", { query: { status: "open", limit: 200 } });

// The newest reply of an unclear-reply group as it was stored (the group's title is its folded wording): the Jobs
// banner quotes it. Customer-influenced words: shown as text only.
export const getNewestReply = (g: Pick<AlertGroup, "kind" | "group">): Promise<string | null> =>
  request<{ items: Array<{ text?: string }> }>(
    `/alerts/groups/${encodeURIComponent(g.kind)}/${encodeURIComponent(g.group)}`,
    { query: { limit: 1 } },
  ).then((d) => d.items[0]?.text ?? null);
