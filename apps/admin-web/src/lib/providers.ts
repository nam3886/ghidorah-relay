// The providers that have a tone of their own (theme.css `--p-<name>`), in the order a provider list is drawn.
export const PROVIDER_ORDER = ["dola", "meta", "vibes", "flow", "qwen", "gemini", "muse"];

// The class that gives an element a provider's colours (--pc tone, --ps tint, --pi ink); any other provider is neutral.
export const providerClass = (name: string): string => (PROVIDER_ORDER.includes(name) ? `p-${name}` : "p-other");

// The drawn order first, then any other provider sorted by name.
export function byProviderOrder(names: readonly string[]): string[] {
  const rank = (n: string) => (PROVIDER_ORDER.includes(n) ? PROVIDER_ORDER.indexOf(n) : PROVIDER_ORDER.length);
  return [...names].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}
