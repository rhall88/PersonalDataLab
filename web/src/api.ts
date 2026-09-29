// All API access goes through here: session bootstrap, the write header the server requires,
// and the server's single error envelope.

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

let sessionReady: Promise<void> | null = null;
function ensureSession(force = false): Promise<void> {
  if (!sessionReady || force) {
    sessionReady = fetch("/api/v1/session", { credentials: "same-origin" }).then(() => undefined);
  }
  return sessionReady;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  await ensureSession();
  const method = opts.method ?? "GET";
  const doFetch = () =>
    fetch(`/api/v1${path}`, {
      method,
      credentials: "same-origin",
      headers: {
        ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" ? { "X-PDL-Request": "1" } : {}),
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  let res = await doFetch();
  if (res.status === 401) {
    await ensureSession(true); // server restarted: new session token
    res = await doFetch();
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const e = data?.error;
    throw new ApiError(res.status, e?.code ?? "http_error", e?.message ?? `HTTP ${res.status}`);
  }
  return data as T;
}

export async function withSession(): Promise<void> {
  await ensureSession();
}

export const qs = (params: Record<string, unknown>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};
