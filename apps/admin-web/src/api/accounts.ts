// The accounts admin routes. Delete, check, wake, proxy re-check and the two provider commands answer 202 with a
// command id; the page polls it (useCommandPoll).
import { request, requestRaw } from "@/api/client";

export interface Capacity {
  pool: string;
  left_n: number | null;
  left_today: number | null;
  budget: number | null;
  drift: boolean | null;
  source: string;
  day_anchor: string | null;
  rest_until_ms: number | null;
  rest_reason: string | null;
}

export interface Account {
  id: string;
  provider: string;
  label: string;
  identity: string | null;
  display_name: string | null;
  state: "available" | "dead" | "disabled" | string;
  state_reason: string | null;
  proxy_host: string | null;
  proxy_port: number | null;
  has_proxy_auth: boolean;
  proxy_ok: boolean | null;
  proxy_checked_ms: number | null;
  strikes: number;
  cooldown_ms: number | null;
  cooldown_reason: string | null;
  last_error: string | null;
  last_used_ms: number | null;
  last_checked_ms: number | null;
  in_use: boolean;
  has_screenshot: boolean;
  composer: string | null;
  capacity: Capacity[];
  made: { video: number; image: number };
}

export interface AccountPage {
  items: Account[];
  total: number;
}

export interface AccountPatch {
  label?: string;
  // null (or empty) removes the proxy
  proxy?: string | null;
  enabled?: boolean;
}

export interface QueuedCommand {
  command_id: string;
}

export interface ImportBody {
  provider: string;
  items: { credential: string; proxy?: string }[];
}

export interface ImportResult {
  imported: { index: number; id: string; label: string }[];
  rejected: { index: number; reason: string }[];
}

export const listAccounts = (provider: string, limit: number, offset: number): Promise<AccountPage> =>
  request("/accounts", { query: { provider, limit, offset } });

// A proxy change also queues a proxy re-check, whose id comes back as command_id.
export const patchAccount = (id: string, body: AccountPatch): Promise<{ id: string; command_id: string | null }> =>
  request(`/accounts/${encodeURIComponent(id)}`, { method: "PATCH", body });

export const deleteAccount = (id: string): Promise<QueuedCommand> =>
  request(`/accounts/${encodeURIComponent(id)}`, { method: "DELETE" });

const accountCommand = (id: string, name: "check" | "wake" | "proxy-recheck"): Promise<QueuedCommand> =>
  request(`/accounts/${encodeURIComponent(id)}/${name}`, { method: "POST" });

export const checkAccount = (id: string) => accountCommand(id, "check");
export const wakeAccount = (id: string) => accountCommand(id, "wake");
export const recheckProxy = (id: string) => accountCommand(id, "proxy-recheck");

export const accountScreenshot = async (id: string): Promise<Blob> =>
  (await requestRaw(`/accounts/${encodeURIComponent(id)}/screenshot`)).blob();

export const importAccounts = (body: ImportBody): Promise<ImportResult> =>
  request("/accounts/import", { method: "POST", body });

const providerCommand = (provider: string, name: "check-all" | "refresh-catalog"): Promise<QueuedCommand> =>
  request(`/providers/${encodeURIComponent(provider)}/${name}`, { method: "POST" });

export const checkAll = (provider: string) => providerCommand(provider, "check-all");
export const refreshCatalog = (provider: string) => providerCommand(provider, "refresh-catalog");
