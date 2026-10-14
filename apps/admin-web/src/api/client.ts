// One typed fetch client for the admin API (/admin). The session token is read from sessionStorage on every call.
import { clearSession, getToken } from "@/lib/session";

const BASE = "/admin";

// The server's error envelope is {error: {code, message, param}}.
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly param: string;
  // Set by the regenerate flow on a 5xx: trying the same action again with the same key is safe.
  retrySafe = false;

  constructor(status: number, body: unknown) {
    const envelope = (body as { error?: { code?: string; message?: string; param?: string } } | null)?.error ?? {};
    super(envelope.message || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = envelope.code || "";
    this.param = envelope.param || "";
  }
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  query?: Query;
  // The login call: a 401 there is a wrong password, not an ended session.
  noRedirect?: boolean;
}

let onUnauthorized: (() => void) | null = null;

// main.ts wires this to the sign-out hooks and the login route; the client itself knows nothing of stores or routes.
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

function queryString(query: Query | undefined): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

// The request never reached an answer (offline, DNS, reset, aborted). The server may or may not have acted.
export class NetworkError extends Error {
  // Set by the regenerate flow: trying the same action again with the same key is safe.
  retrySafe = false;

  constructor(cause: unknown) {
    super("Network error, please try again", { cause });
    this.name = "NetworkError";
  }
}

// The session header is attached only to /admin or /admin/... on this origin. The final URL is resolved first, so a
// path that climbs out (`/../x`, `%2e%2e`, a backslash) is refused before anything is sent.
function adminUrl(path: string, query: Query | undefined): string {
  if (!path.startsWith("/") || path.startsWith("//")) throw new Error(`Not an admin API path: ${path}`);
  const url = new URL(BASE + path + queryString(query), location.origin);
  if (url.origin !== location.origin || (url.pathname !== BASE && !url.pathname.startsWith(`${BASE}/`))) {
    throw new Error(`Not an admin API path: ${path}`);
  }
  return url.pathname + url.search;
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    return {};
  }
}

const SESSION_ENDED = { error: { code: "auth.invalid_session", message: "Your session has ended, sign in again" } };

async function send(path: string, options: RequestOptions): Promise<Response> {
  const url = adminUrl(path, options.query);
  const sentToken = getToken();
  const headers: Record<string, string> = { "X-Admin-Session": sentToken };
  const init: RequestInit = { method: options.method ?? "GET", headers, credentials: "same-origin" };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    throw new NetworkError(err);
  }
  if (response.status === 401 && !options.noRedirect) {
    // A slow answer for an old session must not end a newer one: act only if the token is still the one sent.
    if (getToken() === sentToken) {
      clearSession();
      onUnauthorized?.();
    }
    // The server's own envelope is kept; a bare 401 gets the default text.
    const body = await readBody(response);
    throw new ApiError(401, (body as { error?: unknown } | null)?.error ? body : SESSION_ENDED);
  }
  return response;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options);
  const data = await readBody(response);
  if (!response.ok) throw new ApiError(response.status, data);
  return data as T;
}

// For blobs and text (screenshot, download, debug file): the caller reads the body from the returned response.
export async function requestRaw(path: string, options: RequestOptions = {}): Promise<Response> {
  const response = await send(path, options);
  if (!response.ok) throw new ApiError(response.status, await readBody(response));
  return response;
}
