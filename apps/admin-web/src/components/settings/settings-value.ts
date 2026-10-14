import type { SettingItem } from "@/api/settings";

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
