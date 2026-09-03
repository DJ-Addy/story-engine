// HTTP transport for the FastAPI backend (`backend/app/api`): base URL, JWT
// bearer, and error normalization. Kept separate from `httpApi.ts` so the
// response mappers there stay readable, and so a future login screen can drive
// auth without importing the whole repository layer.

/**
 * Where the API lives. Defaults to a **same-origin** path: the FastAPI app
 * mounts no CORS middleware, so a direct browser fetch from :3000 to :8000 is
 * blocked. `next.config.ts` rewrites `/api/v1/*` to uvicorn, which keeps every
 * request same-origin. Set an absolute URL here only once the backend serves
 * CORS headers (or sits behind the same origin in production).
 */
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1"
).replace(/\/+$/, "");

// --- token storage --------------------------------------------------------- //
// A memory mirror keeps the token readable during SSR and in environments where
// localStorage throws (private mode, embedded webviews).

const TOKEN_KEY = "story-engine.access-token";
let memoryToken: string | null = null;

export function getToken(): string | null {
  if (memoryToken !== null) return memoryToken;
  if (typeof window === "undefined") return null;
  try {
    memoryToken = window.localStorage.getItem(TOKEN_KEY);
  } catch {
    memoryToken = null;
  }
  return memoryToken;
}

export function setToken(token: string | null): void {
  memoryToken = token;
  if (typeof window === "undefined") return;
  try {
    if (token === null) window.localStorage.removeItem(TOKEN_KEY);
    else window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Non-persistent session: the memory mirror still carries the token.
  }
}

export const clearToken = (): void => setToken(null);

// --- errors ---------------------------------------------------------------- //

/** A non-2xx response, with FastAPI's `detail` flattened into `message`. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly path: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** The stored token was missing, expired, or rejected — re-authenticate. */
  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

/**
 * A UI value the backend's schema cannot represent. Thrown *before* the request
 * so the caller sees the contract gap instead of a Pydantic 422 dump. See the
 * shot-vocabulary divergence documented in `httpApi.ts`.
 */
export class ContractMismatchError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ContractMismatchError";
  }
}

/** FastAPI's `detail` is a string for HTTPException and a list for 422s. */
function detailToMessage(body: unknown, fallback: string): string {
  if (typeof body !== "object" || body === null) return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (typeof d !== "object" || d === null) return String(d);
        const { loc, msg } = d as { loc?: unknown[]; msg?: string };
        const where = Array.isArray(loc) ? loc.join(".") : "";
        return where ? `${where}: ${msg ?? ""}` : (msg ?? "");
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return fallback;
}

// --- requests -------------------------------------------------------------- //

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  /** Multipart uploads (script / novel ingest) pass a FormData body verbatim. */
  form?: FormData;
}

async function send(path: string, opts: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form; // fetch sets the multipart boundary itself
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  return fetch(`${API_BASE_URL}${path}`, {
    method: opts.method ?? (body ? "POST" : "GET"),
    headers,
    body,
    // The token travels in the Authorization header, not a cookie.
    credentials: "same-origin",
  });
}

/** Issue a request and decode JSON, raising `ApiError` on any non-2xx. */
export async function request<T>(
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const res = await send(path, opts);

  if (res.status === 401) {
    // Clean 401: drop the dead token so the next request doesn't resend it,
    // and surface a typed error the caller can route to a login screen.
    clearToken();
    throw new ApiError(401, "Not authenticated — sign in again", path);
  }

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(
      res.status,
      detailToMessage(body, `${res.status} ${res.statusText}`),
      path,
    );
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Like `request`, but a 404 resolves to `null` instead of throwing. Used for
 * resources that legitimately may not exist yet (an unauthored shot list). */
export async function requestOptional<T>(
  path: string,
  opts: RequestOptions = {},
): Promise<T | null> {
  try {
    return await request<T>(path, opts);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

// --- auth ------------------------------------------------------------------ //
// Not part of `StoryEngineApi` (the UI has no login screen yet), but the real
// client is unusable without a token, so the two endpoints live here.

interface TokenPair {
  access_token: string;
  token_type: string;
}

/** POST /auth/login. Stores the returned token and returns it. */
export async function login(email: string, password: string): Promise<string> {
  const pair = await request<TokenPair>("/auth/login", {
    body: { email, password },
  });
  setToken(pair.access_token);
  return pair.access_token;
}

/** POST /auth/register, then log the new user in. */
export async function register(
  email: string,
  password: string,
): Promise<string> {
  await request<{ id: string; email: string }>("/auth/register", {
    body: { email, password },
  });
  return login(email, password);
}
