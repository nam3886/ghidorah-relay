// The API keys admin routes. The raw key is in the answer of a create and never again.
import { request } from "@/api/client";

export type WalletMode = "closed" | "limit" | "unlimited";

// scope "*" is the key's one points wallet; any other scope is a provider's cap on top of it (a job needs both open).
export interface Wallet {
  scope: string;
  day_limit: number | null;
  total_limit: number | null;
  used_day: number;
  used_total: number;
  mode: WalletMode;
}

export interface ApiKey {
  id: string;
  prefix: string;
  label: string;
  enabled: boolean;
  models: string[];
  max_unfinished: number | null;
  max_unfinished_effective: number;
  created_ms: number | null;
  // the key's last /v1 call (written at most once a minute) and the address it came from; null when never used
  last_used_ms: number | null;
  last_used_ip: string | null;
  jobs: number;
  unfinished: number;
  wallets: Wallet[];
}

export interface WalletBody {
  scope: string;
  mode: WalletMode;
  day_limit?: number;
  total_limit?: number;
}

export interface KeyBody {
  label: string;
  models: string[];
  max_unfinished: number | null;
  wallets: WalletBody[];
}

export const listKeys = (): Promise<{ items: ApiKey[] }> => request("/keys");

// The one place the raw key travels. Callers keep `key` in local state only.
export const createKey = (body: KeyBody): Promise<ApiKey & { key: string }> =>
  request("/keys", { method: "POST", body });

// Only the fields sent change (a wallet change sends `wallets` alone).
export const patchKey = (id: string, body: Partial<KeyBody>): Promise<ApiKey> =>
  request(`/keys/${encodeURIComponent(id)}`, { method: "PATCH", body });

export const setKeyEnabled = (id: string, enabled: boolean): Promise<unknown> =>
  request(`/keys/${encodeURIComponent(id)}/${enabled ? "enable" : "disable"}`, { method: "POST" });

// result is "deleted", or anything else when the key has jobs and was only disabled.
export const deleteKey = (id: string): Promise<{ id: string; result: string }> =>
  request(`/keys/${encodeURIComponent(id)}`, { method: "DELETE" });
