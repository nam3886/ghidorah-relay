// The Test tab calls the PUBLIC routes (/v1/*) with the API key typed in the tab: `Authorization: Bearer <key>` and no
// admin session header. It has its own small wrapper because the admin client adds that header and treats a 401 as an
// ended admin session. The key is only ever sent from here; it is never logged or put in a URL.

// Real keys are long; redacting a one-letter stand-in would only mangle the text.
const MIN_KEY_REDACT = 8;

export type MediaKind = "image" | "video";

export interface PublicModel {
  id: string;
  kind: MediaKind | string;
  owned_by: string;
  prices?: unknown;
  limits?: unknown;
  [key: string]: unknown;
}

export interface ProviderOption {
  [key: string]: unknown;
}

export interface Template {
  show_value?: string;
  prompt: string;
  [key: string]: unknown;
}

export interface ProviderOptions {
  ratios?: ProviderOption[];
  styles?: ProviderOption[];
  templates?: Template[];
}

export interface PublicJob {
  id: string;
  status: string;
  stage?: string | null;
  result_urls?: string[];
  images?: string[];
  [key: string]: unknown;
}

// The text of the status line: `code: message`, as the old tab showed it.
export class TestApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super((code ? `${code}: ` : "") + (message || `HTTP ${status}`));
    this.name = "TestApiError";
    this.status = status;
    this.code = code;
  }
}

async function call<T>(key: string, path: string, init: { method?: "GET" | "POST"; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${key}` };
  const request: RequestInit = { method: init.method ?? "GET", headers, credentials: "omit" };
  if (init.body !== undefined) {
    headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, request);
  const text = await response.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const envelope = (data as { error?: { code?: string; message?: string } } | null)?.error ?? {};
    // A gateway may echo the key it refused ("invalid key rk-..."): the status line must never show it.
    const redact = (text: string): string => (key.length >= MIN_KEY_REDACT ? text.split(key).join("[redacted]") : text);
    throw new TestApiError(response.status, redact(envelope.code || ""), redact(envelope.message || ""));
  }
  return data as T;
}

export const listModels = (key: string): Promise<{ data: PublicModel[] }> => call(key, "/v1/models");

export const getPoints = (key: string): Promise<unknown> => call(key, "/v1/points");

export const getOptions = (key: string, provider: string): Promise<ProviderOptions> =>
  call(key, `/v1/providers/${encodeURIComponent(provider)}/options`);

const jobsPath = (kind: string): string => (kind === "image" ? "/v1/images/generations" : "/v1/video/generations");

export const createJob = (key: string, kind: string, body: unknown): Promise<PublicJob> =>
  call(key, jobsPath(kind), { method: "POST", body });

export const getJob = (key: string, kind: string, id: string): Promise<PublicJob> =>
  call(key, `${jobsPath(kind)}/${encodeURIComponent(id)}`);
