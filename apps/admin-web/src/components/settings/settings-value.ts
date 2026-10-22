import type { SettingItem } from "@/api/settings";

// The server's rule for a new password (relay_core/admin/users.py: 10 to 256 characters), checked in the form before
// it is sent, in the words drawn under the field. Empty when the length is right; a server refusal keeps its own words.
export const PASSWORD_MIN = 10;
export const PASSWORD_MAX = 256;
export function newPasswordProblem(password: string): string {
  if (password.length < PASSWORD_MIN) return `At least ${PASSWORD_MIN} characters`;
  if (password.length > PASSWORD_MAX) return `At most ${PASSWORD_MAX} characters`;
  return "";
}

// A default longer than its column (about 30 mono characters) is one line ending in "…"; its whole value is a note:
// a string as it is, anything else as JSON laid out one entry a line.
export const LONG_DEFAULT = 30;
export const isLongDefault = (shown: string): boolean => shown.length > LONG_DEFAULT;
export const fullDefault = (value: unknown): string =>
  typeof value === "string" ? value : JSON.stringify(value, null, 2);

// A boot-only value as the list shows it: an unset or empty one is a dash, never a blank after its leader.
export const bootValue = (value: unknown): string =>
  value === null || value === undefined || value === "" ? "-" : String(value);

// The text a setting's input starts with: a string as it is, anything else (a number, a boolean, a list) as JSON.
export function valueText(item: Pick<SettingItem, "value">): string {
  return typeof item.value === "string" ? item.value : JSON.stringify(item.value);
}

// What Save sends: the input text parsed as JSON first (so `5`, `true` and `[1]` keep their type); text that is not JSON
// is sent as the string it is.
export function parseValue(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

// One line of the read-only tables (Boot-only values, Fixed limits): `group` is the band it sits under, `reason` the
// third column.
export interface ValueRow {
  group?: string;
  name: string;
  value: string;
  reason?: string;
}
