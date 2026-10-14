// Settings tab: the live settings, the measured prices and the password change.
import { request } from "@/api/client";

export interface SettingItem {
  key: string;
  value: unknown;
  source: "db" | "env" | "default" | string;
  default: unknown;
  description: string;
}

export interface EnvOnlyItem {
  name: string;
  value: string | null;
}

export interface SettingsList {
  items: SettingItem[];
  env_only: EnvOnlyItem[];
}

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
