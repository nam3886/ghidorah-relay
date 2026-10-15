// Pure rules of the Accounts view: what each cell says and what an edit or an import sends.
import type { Account, AccountPatch, ImportBody, ImportResult } from "@/api/accounts";
import { fmtTime } from "@/lib/format";

export const PAGE_SIZE = 50;
export const AUTO_REFRESH_MS = 10000;
export const WAKE_LABEL = "Wake: clear cooldown and strikes";

// `host:port`, empty when the account has no proxy. The password is never shown.
export function proxyText(a: Pick<Account, "proxy_host" | "proxy_port">): string {
  return a.proxy_host ? `${a.proxy_host}:${a.proxy_port}` : "";
}

// Only what changed: a label that differs, and a proxy that differs (`null` when it was emptied, which removes it).
// Returns null when nothing changed, so the dialog just closes.
export function accountChanges(a: Account, label: string, proxy: string): AccountPatch | null {
  const body: AccountPatch = {};
  if (label !== a.label) body.label = label;
  const next = proxy.trim();
  if (next !== proxyText(a)) body.proxy = next || null;
  return Object.keys(body).length ? body : null;
}

// One credential per line; blank lines are dropped. The proxy, when given, applies to every line.
// Returns null when there is no credential at all.
export function importBody(provider: string, text: string, proxy: string): ImportBody | null {
  const lines = text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (!lines.length) return null;
  const shared = proxy.trim();
  return { provider, items: lines.map((credential) => (shared ? { credential, proxy: shared } : { credential })) };
}

export function importSummary(out: ImportResult): string {
  const base = `Imported ${out.imported.length}, rejected ${out.rejected.length}`;
  if (!out.rejected.length) return base;
  return `${base} (${out.rejected.map((r) => `line ${r.index + 1}: ${r.reason}`).join("; ")})`;
}

// The sleep lines of an account: its own cooldown and the pools that rest, each only while still in the future.
export function asleepLines(a: Account, now: number = Date.now()): string[] {
  const lines: string[] = [];
  if (a.cooldown_ms && a.cooldown_ms > now) {
    lines.push(`cooldown until ${fmtTime(a.cooldown_ms)}${a.cooldown_reason ? ` (${a.cooldown_reason})` : ""}`);
  }
  for (const c of a.capacity) {
    if (c.rest_until_ms && c.rest_until_ms > now) {
      lines.push(`${c.pool} rests until ${fmtTime(c.rest_until_ms)}${c.rest_reason ? ` (${c.rest_reason})` : ""}`);
    }
  }
  return lines;
}

// `left / budget`, with `?` when the count is unknown. The cell shows it right-aligned after the pool name.
export function pointsFigure(c: Account["capacity"][number]): string {
  const left = c.left_today === null || c.left_today === undefined ? "?" : c.left_today;
  return `${left}${c.budget !== null && c.budget !== undefined ? ` / ${c.budget}` : ""}`;
}

// `pool: left / budget`.
export function pointsText(c: Account["capacity"][number]): string {
  return `${c.pool}: ${pointsFigure(c)}`;
}

// How long ago, for a "checked" stamp: the table stays short, the title carries the exact time. A time in the future
// (clock skew) reads as just now.
export function ago(epochMs: number | null | undefined, now: number = Date.now()): string {
  if (!epochMs) return "never";
  const minutes = Math.floor(Math.max(0, now - epochMs) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

export function proxyCheck(a: Account): { label: string; tone: "ok" | "bad" | "neutral" } {
  if (a.proxy_ok === true) return { label: "ok", tone: "ok" };
  if (a.proxy_ok === false) return { label: "failed", tone: "bad" };
  return { label: "not checked", tone: "neutral" };
}

// What a finished check found, from the command's outcome and the account row it just changed.
export function checkSummary(outcome: string | undefined, a: Pick<Account, "state" | "state_reason">): string {
  return `Check: ${outcome || "done"} - account ${a.state}${a.state_reason ? ` (${a.state_reason})` : ""}`;
}
