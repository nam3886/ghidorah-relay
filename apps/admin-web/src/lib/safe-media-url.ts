// Media is shown only from this origin's /m/ share links. Anything else (a script URL, data:, blob:, another host) is
// refused and the caller shows text instead. Returns the same-origin path to use.
export function safeMediaUrl(url: unknown): string | null {
  if (typeof url !== "string" || !url) return null;
  let parsed: URL;
  try {
    parsed = new URL(url, location.origin);
  } catch {
    return null;
  }
  if (parsed.origin !== location.origin || !parsed.pathname.startsWith("/m/")) return null;
  return parsed.pathname + parsed.search;
}
