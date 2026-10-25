// Settings tab: the live settings, the measured prices and the password change.
import { request } from "@/api/client";

export interface SettingItem {
  key: string;
  // the provider of a namespaced key (`<p>.<name>`), else "core"
  group: string;
  // one the admin changes often: drawn first on its card, the others under Advanced
  basic: boolean;
  // the value's type: "int", "float", "str", "bool", "list" or "dict"
  type: string;
  value: unknown;
  source: "db" | "default";
  default: unknown;
  description: string;
  // What the value may be, in words ("at least 60", "1–12", "true or false"); null when anything of its type goes.
  allowed: string | null;
  // the keys a dict setting may hold (a provider's price options), null for any other setting
  keys?: string[] | null;
  // the whole list of allowed values (each item of a list setting is one of them; its order is kept), null when any
  choices?: string[] | null;
  // a sealed setting (a solver's key): `value` and `default` are always null; `source` says whether it is set and
  // `hint` is its last characters ("" for a short one, null when not set). Save sends the typed text; Reset clears it.
  secret?: boolean;
  hint?: string | null;
}

export interface EnvOnlyItem {
  name: string;
  value: string | null;
}

// A limit that is code, not a setting. `group` is "core" or a provider's name; the server orders them.
export interface FixedLimit {
  group: string;
  name: string;
  value: string;
  reason: string;
}

// One service's container limits, as text. The four texts are null when a worker has not reported (an older version);
// "unreadable" and "no limit" come from the server.
export interface ContainerRow {
  service: string;
  provider: string | null;
  this_process: boolean;
  reported: boolean;
  stale: boolean;
  cpus: string | null;
  memory: string | null;
  pids: string | null;
  shm: string | null;
  reported_ms: number;
  started_ms: number;
}

export interface SettingsList {
  items: SettingItem[];
  env_only: EnvOnlyItem[];
  fixed: FixedLimit[];
  containers: ContainerRow[];
  // the newest change of any setting, null before the first one
  last_saved?: { at_ms: number; by: string | null } | null;
}

// One change: who changed which key, from what stored value to what (null = none stored: the default applies), and
// when. A secret setting's row says only that it changed (`secret`, both values null).
export interface SettingChange {
  id: number;
  at_ms: number;
  key: string;
  by: string | null;
  old: unknown;
  new: unknown;
  secret: boolean;
}

// Newest first; `before` (a row id) pages back, `next` is null at the end.
export const getSettingsHistory = (before?: number): Promise<{ items: SettingChange[]; next: number | null }> =>
  request("/settings/history", { query: { limit: 100, before } });

export interface PriceRow {
  provider: string;
  model: string;
  duration: number | null;
  charged: number;
  jobs: number;
  disagreed: number;
  lowest_upstream: number | null;
  substituted: number;
}

export const getSettings = (): Promise<SettingsList> => request("/settings");

export const putSetting = (key: string, value: unknown): Promise<SettingItem> =>
  request(`/settings/${encodeURIComponent(key)}`, { method: "PUT", body: { value } });

export const deleteSetting = (key: string): Promise<unknown> =>
  request(`/settings/${encodeURIComponent(key)}`, { method: "DELETE" });

// An empty provider is dropped from the query by the client.
export const getPrices = (provider: string): Promise<{ items: PriceRow[] }> =>
  request("/prices", { query: { provider } });

// The server ends every session on success. A wrong current password is `input.invalid` with param `old_password`, a
// weak new one with param `new_password` (a 400, so the client does not read it as an ended session).
export const changePassword = (oldPassword: string, newPassword: string): Promise<unknown> =>
  request("/password", { method: "POST", body: { old_password: oldPassword, new_password: newPassword } });
