// Pure rules of the Settings page (B Ledger): which setting goes where on a tab, how the model switches and the price
// and cost inputs of a provider edit the text draft of `<p>.disabled_models`, `<p>.prices` and `<p>.costs`, the model
// tables' columns and margins, and the words of the unsaved-changes bar.
import type { SettingItem } from "@/api/settings";
import type { CatalogModel } from "@/api/shell";
import { byProviderOrder } from "@/lib/providers";
import { valueText } from "./settings-value";

// The settings a provider tab draws in its own place: the "Claim new jobs" switch, the model switches, the price inputs
// (points), the cost inputs (what one job costs the upstream account), the pool budgets and the send path. They are
// never plain fields.
export const OWN_PLACE = ["claims_enabled", "disabled_models", "prices", "costs", "pools", "send_path"] as const;
type Own = (typeof OWN_PLACE)[number];

// The name of a setting inside its group: `dola.render_deadline_s` is `render_deadline_s`.
export const shortKey = (item: Pick<SettingItem, "key" | "group">): string =>
  item.group !== "core" && item.key.startsWith(`${item.group}.`) ? item.key.slice(item.group.length + 1) : item.key;

// "render_deadline_s" -> "Render deadline (s)": the field's label, from its name (the description is the help line).
const UNITS: Record<string, string> = { s: "s", ms: "ms", h: "h", gb: "GB", mb: "MB" };
export function fieldLabel(item: Pick<SettingItem, "key" | "group">): string {
  const words = shortKey(item).split("_");
  const unit = words.length > 1 ? UNITS[words[words.length - 1]!] : undefined;
  if (unit) words.pop();
  const text = words.join(" ");
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}${unit ? ` (${unit})` : ""}`;
}

export interface Card {
  // "core" for the General card, else the provider
  group: string;
  own: Partial<Record<Own, SettingItem>>;
  basic: SettingItem[];
  advanced: SettingItem[];
}

// One card per group, General ("core") first, then the providers in the drawn order. A card with no basic setting
// (General: the core marks none) shows its settings unfolded, as its basic ones. A card's keys (its secret settings)
// come first among its basic ones, the settings that use them after, each part in the order read.
export function cardsOf(items: readonly SettingItem[]): Card[] {
  const by = new Map<string, Card>();
  for (const item of items) {
    const card = by.get(item.group) ?? { group: item.group, own: {}, basic: [], advanced: [] };
    by.set(item.group, card);
    const name = shortKey(item);
    if (item.group !== "core" && (OWN_PLACE as readonly string[]).includes(name)) card.own[name as Own] = item;
    else (item.basic ? card.basic : card.advanced).push(item);
  }
  for (const card of by.values()) {
    if (!card.basic.length) [card.basic, card.advanced] = [card.advanced, []];
    card.basic = [...card.basic.filter((i) => i.secret), ...card.basic.filter((i) => !i.secret)];
  }
  const core = by.get("core");
  by.delete("core");
  return [...(core ? [core] : []), ...byProviderOrder([...by.keys()]).map((g) => by.get(g)!)];
}

// A draft's value: the JSON it holds, or the stored value when it holds none (never thrown away).
function parsed<T>(draft: string | undefined, item: SettingItem): T {
  try {
    return JSON.parse(draft ?? "") as T;
  } catch {
    return item.value as T;
  }
}

export interface PriceInput {
  // the price table's key: "seedance_v2.5@5s", "seedream-4.5"
  key: string;
  // "5 s", or "points" for a model with one price (a cost input says "cost" instead: `costLabel`)
  label: string;
  // what the input shows: "" for a key the table does not hold yet (an option with no price), to be filled in
  text: string;
}

// The keys a price or cost input is drawn for: every key the setting declares it may hold (`keys`, so an option the
// table does not hold yet gets an empty input), then any other key the table holds.
export const tableKeys = (item: SettingItem | undefined, table: Record<string, unknown>): string[] => [
  ...new Set([...(item?.keys ?? []), ...Object.keys(table)]),
];

const textOf = (table: Record<string, unknown>, key: string): string => (key in table ? String(table[key]) : "");

// The price inputs of one model: every key (of the table, or the declared `keys`) that names the model (its id or an
// alias), alone or with a tier after "@" ("seedance_v2.5@5s" is the 5 s tier of seedance-2.5).
export function priceInputs(
  model: Pick<CatalogModel, "id"> & { aliases?: unknown },
  table: Record<string, unknown>,
  keys: readonly string[] = Object.keys(table),
): PriceInput[] {
  const names = [model.id, ...(Array.isArray(model.aliases) ? (model.aliases as string[]) : [])];
  return keys
    .filter((k) => names.includes(k.split("@")[0]!))
    .map((key) => {
      const tier = key.includes("@") ? key.slice(key.indexOf("@") + 1) : "";
      return { key, label: tier ? tier.replace(/^(\d+)s$/, "$1 s") : "points", text: textOf(table, key) };
    });
}

// A cost input's label: "5 s cost", or "cost" for a model with one option.
export const costLabel = (input: PriceInput): string => (input.label === "points" ? "cost" : `${input.label} cost`);

// The keys that name no model of the catalog (a model since retired, or an option the provider prices ahead of
// selling it): drawn on a row of their own.
export function otherPrices(
  models: readonly CatalogModel[],
  table: Record<string, unknown>,
  keys: readonly string[] = Object.keys(table),
): PriceInput[] {
  const owned = new Set(models.flatMap((m) => priceInputs(m, table, keys).map((p) => p.key)));
  return keys.filter((k) => !owned.has(k)).map((key) => ({ key, label: key, text: textOf(table, key) }));
}

export const priceTable = (draft: string | undefined, item: SettingItem): Record<string, unknown> =>
  parsed<Record<string, unknown>>(draft, item) ?? {};

// The draft after one price input changed: a whole number is sent as a number, anything else as the text typed, so
// the server names the fault ("the price for … is not a whole number"). An emptied input leaves the key out: the
// option takes its shipped default again, or stays unpriced when it has none.
export function withPrice(draft: string | undefined, item: SettingItem, key: string, text: string): string {
  const { [key]: _old, ...rest } = priceTable(draft, item);
  if (!text.trim()) return JSON.stringify(rest);
  const table = { ...rest, [key]: /^\d+$/.test(text.trim()) ? Number(text.trim()) : text };
  return JSON.stringify(table);
}

export const pausedModels = (draft: string | undefined, item: SettingItem | undefined): string[] =>
  item ? (parsed<unknown>(draft, item) as string[]) : [];

// The draft after a model's switch: the paused ids in the catalog's order (any id the catalog no longer has kept at
// the end). The same set as stored is the stored text, so switching a model off and on again is no change.
export function withModelOn(
  draft: string | undefined,
  item: SettingItem,
  catalog: readonly string[],
  id: string,
  on: boolean,
): string {
  const now = new Set(pausedModels(draft, item));
  if (on) now.delete(id);
  else now.add(id);
  const next = [...catalog.filter((m) => now.has(m)), ...[...now].filter((m) => !catalog.includes(m))];
  const stored = Array.isArray(item.value) ? (item.value as string[]) : [];
  const same = next.length === stored.length && next.every((m) => stored.includes(m));
  return same ? valueText(item) : JSON.stringify(next);
}

// ---- the Models and prices tables ------------------------------------------------------------------------------

// The words of a model's operations: "Text and image to video" (text_to_video, image_to_video); its kind when it has none.
export function kindsText(m: Record<string, unknown>): string {
  const ops = Array.isArray(m.operations) ? (m.operations as string[]) : [];
  const by = new Map<string, string[]>();
  for (const op of ops) {
    const [from, to] = op.split("_to_");
    if (!from || !to) continue;
    by.set(to, [...(by.get(to) ?? []), from]);
  }
  const text = [...by].map(([to, from]) => `${from.join(" and ")} to ${to}`).join(" · ");
  return text ? `${text.charAt(0).toUpperCase()}${text.slice(1)}` : typeof m.kind === "string" ? m.kind : "";
}

// A tier as a column head: "5 s", "4 s · 720p"; the one price of a whole job is `whole` ("Per job").
export const tierHead = (label: string, whole: string): string =>
  label === "points" ? whole : label.replace(/(\d+)s\b/g, "$1 s").replace(/\|/g, " · ");

// The columns of a model table: every tier any of its models has a price or cost input for, shortest first.
export function tierColumns(
  models: readonly CatalogModel[],
  tables: ReadonlyArray<[Record<string, unknown>, string[]]>,
): string[] {
  const labels = new Set<string>();
  for (const m of models) {
    for (const [table, keys] of tables) for (const p of priceInputs(m, table, keys)) labels.add(p.label);
  }
  const lead = (s: string): number => (/^\d+/.test(s) ? parseInt(s, 10) : Number.MAX_SAFE_INTEGER);
  return [...labels].sort((a, b) => lead(a) - lead(b) || a.localeCompare(b));
}

const wholeOf = (text: string | undefined): number | null => (text && /^\d+$/.test(text.trim()) ? Number(text) : null);

// A model's margin: sale points less the upstream cost at each tier that has both (1 credit or 1 use is 1 point),
// "+1 to +3 pts" over its tiers, toned by the lowest: below 0 bad, 0 warn, above ok. No tier with both: a dash.
export function marginOf(pairs: ReadonlyArray<[string | undefined, string | undefined]>): {
  text: string;
  tone: "ok" | "warn" | "bad" | "";
} {
  const d: number[] = [];
  for (const [s, c] of pairs) {
    const a = wholeOf(s);
    const b = wholeOf(c);
    if (a !== null && b !== null) d.push(a - b);
  }
  if (!d.length) return { text: "—", tone: "" };
  const lo = Math.min(...d);
  const hi = Math.max(...d);
  const f = (x: number): string => `${x >= 0 ? "+" : ""}${x}`;
  const unit = Math.abs(lo) === 1 ? "pt" : "pts";
  const text = lo === hi ? `${f(lo)} ${unit}` : `${f(lo)} to ${f(hi)} pts`;
  return { text, tone: lo < 0 ? "bad" : lo === 0 ? "warn" : "ok" };
}

// ---- the unsaved-changes bar ------------------------------------------------------------------------------------

const shortText = (v: unknown): string => {
  const s = v === undefined ? "—" : typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 24 ? `${s.slice(0, 23)}…` : s;
};
const objectOf = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
type PoolsShape = Record<string, { windows?: Array<{ tz?: string; budget?: Record<string, unknown> }> }>;

// One line per change a Save would send, in words: "seedance_v2.0@10s: 2 → 3 points", "seedance-1.0 offered: on →
// off", "dola video free budget: 4 → 5", "Log retention days: 30 → 45".
export function changeLines(item: SettingItem, draft: string): string[] {
  const name = shortKey(item);
  const own = item.group !== "core";
  // a secret's typed value is never written out, not even here
  if (item.secret)
    return [`${own ? `${item.group} · ` : ""}${fieldLabel(item)}: ${item.source === "db" ? "replaced" : "set"}`];
  let next: unknown;
  try {
    next = JSON.parse(draft);
  } catch {
    next = draft;
  }
  if (own && (name === "prices" || name === "costs")) {
    const a = objectOf(item.value);
    const b = objectOf(next);
    const what = name === "costs" ? " cost" : "";
    const unit = name === "prices" ? " points" : "";
    return [...new Set([...Object.keys(a), ...Object.keys(b)])]
      .filter((k) => a[k] !== b[k])
      .map((k) => `${k}${what}: ${shortText(a[k])} → ${shortText(b[k])}${unit}`);
  }
  if (own && name === "disabled_models" && Array.isArray(next)) {
    const was = Array.isArray(item.value) ? (item.value as string[]) : [];
    const now = next as string[];
    return [
      ...now.filter((m) => !was.includes(m)).map((m) => `${m} offered: on → off`),
      ...was.filter((m) => !now.includes(m)).map((m) => `${m} offered: off → on`),
    ];
  }
  if (own && name === "pools") {
    const a = objectOf(item.value) as PoolsShape;
    const b = objectOf(next) as PoolsShape;
    const out: string[] = [];
    for (const pool of new Set([...Object.keys(a), ...Object.keys(b)])) {
      const wa = a[pool]?.windows?.[0];
      const wb = b[pool]?.windows?.[0];
      for (const tier of new Set([...Object.keys(wa?.budget ?? {}), ...Object.keys(wb?.budget ?? {})])) {
        const x = wa?.budget?.[tier];
        const y = wb?.budget?.[tier];
        if (x !== y) out.push(`${item.group} ${pool} ${tier} budget: ${shortText(x)} → ${shortText(y)}`);
      }
      if (wa && wb && wa.tz !== wb.tz)
        out.push(`${item.group} ${pool} reset: ${shortText(wa.tz)} → ${shortText(wb.tz)}`);
    }
    return out.length ? out : [`${item.group} pools changed`];
  }
  const word = (v: unknown): unknown =>
    v === true ? "on" : v === false ? "off" : Array.isArray(v) && item.choices ? v.join(", ") : v;
  return [
    `${own ? `${item.group} · ` : ""}${fieldLabel(item)}: ${shortText(word(item.value))} → ${shortText(word(next))}`,
  ];
}
