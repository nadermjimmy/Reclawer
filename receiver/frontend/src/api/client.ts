/* Typed fetch wrapper for /api. Same-origin cookie session; a 401 tells the session layer to show Login. */

export class ApiError extends Error {
  status: number;
  code: string;
  detail: unknown;
  body: Record<string, unknown>;
  constructor(status: number, body: Record<string, unknown>) {
    super(typeof body.detail === "string" ? body.detail : String(body.error ?? `HTTP ${status}`));
    this.status = status;
    this.code = String(body.error ?? "http_error");
    this.detail = body.detail;
    this.body = body;
  }
}

type UnauthorizedListener = () => void;
const unauthorized = new Set<UnauthorizedListener>();

/** The session provider subscribes here so any 401 sends the person back to Login. */
export function onUnauthorized(fn: UnauthorizedListener) {
  unauthorized.add(fn);
  return () => {
    unauthorized.delete(fn);
  };
}

export type Query = Record<string, string | number | boolean | undefined | null>;

export function withQuery(path: string, query?: Query) {
  if (!query) return path;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `${path}?${s}` : path;
}

export async function request<T>(path: string, init: RequestInit & { query?: Query } = {}): Promise<T> {
  const { query, ...rest } = init;
  let res: Response;
  try {
    res = await fetch(withQuery(`/api${path}`, query), { credentials: "same-origin", ...rest });
  } catch {
    throw new ApiError(0, { error: "network", detail: "Couldn't reach the server. Check your connection." });
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { error: "bad_response", detail: text.slice(0, 200) };
  }
  if (res.status === 401 && path !== "/login") unauthorized.forEach((fn) => fn());
  if (!res.ok) throw new ApiError(res.status, (body as Record<string, unknown>) ?? {});
  return body as T;
}

export const get = <T>(path: string, query?: Query) => request<T>(path, { query });

export const post = <T>(path: string, json?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: json === undefined ? undefined : JSON.stringify(json),
  });

export const upload = <T>(path: string, file: File, field = "file") => {
  const form = new FormData();
  form.append(field, file);
  return request<T>(path, { method: "POST", body: form });
};

/** Fire-and-forget POST that survives page unload (used to commit pending decisions on pagehide). */
export function postKeepalive(path: string, json: unknown) {
  try {
    void fetch(`/api${path}`, {
      method: "POST",
      keepalive: true,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(json),
    });
  } catch {
    /* page is going away; nothing else to do */
  }
}

/** Human message for an error, never blaming the person. */
export function errorText(e: unknown, fallback = "Something went wrong on our side. Try again.") {
  if (e instanceof ApiError) {
    if (e.status === 0) return "Couldn't reach the server. Check your connection and try again.";
    if (e.code === "busy") return "Another job is running. Try again when it has finished.";
    if (typeof e.detail === "string" && e.detail) return e.detail;
  }
  return fallback;
}
