/**
 * The application's API client: the generated client from `@pornarr/api-client`
 * plus the two things a browser session needs and a generated client cannot
 * know about — the CSRF double-submit header, and what a 401 means to the app.
 *
 * Both are middleware rather than call-site code, because "every mutation
 * carries the token" and "every 401 logs you out" are properties of the client,
 * and a rule enforced at each call site is a rule that is eventually forgotten.
 */
import { type ApiClient, createApiClient } from "@pornarr/api-client";

/** Cookie the API sets, and the header it expects to see it echoed in. */
export const CSRF_COOKIE = "pornarr_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

/** Methods the API treats as state-changing and therefore CSRF-protected. */
const UNSAFE_METHODS: ReadonlySet<string> = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * Login is exempt: it is the request that establishes the session, so there is
 * no cookie to echo yet. Matched on the contract path rather than the URL so a
 * base path or proxy prefix cannot silently defeat it.
 */
const CSRF_EXEMPT_PATHS: ReadonlySet<string> = new Set(["/api/auth/login"]);

/**
 * Read the double-submit token out of `document.cookie`.
 *
 * The API scopes this readable cookie to the SPA root, while the session cookie
 * remains HttpOnly and restricted to `/api`. Decode it here rather than at each
 * mutation call site, so every unsafe request follows the same CSRF rule.
 */
export function readCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${CSRF_COOKIE}=([^;]*)`));
  const value = match?.[1];
  return value === undefined || value === "" ? null : decodeURIComponent(value);
}

type UnauthorizedHandler = () => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;

/**
 * Register what happens when any request comes back 401.
 *
 * The client cannot navigate — routing belongs to the router, and a
 * `window.location` assignment would throw away the query cache and the SPA
 * with it. So the client reports, and the application decides: `App` empties
 * the session query, which makes the authenticated boundary re-render into a
 * redirect on the next paint.
 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

/**
 * Same origin, spelled absolutely.
 *
 * The contract wants an empty base URL so that the dev server's `/api` proxy
 * and the production FastAPI mount are the same code path. An empty base URL
 * produces a relative request URL, which browsers resolve against the document
 * and Node's fetch refuses outright — so the origin is filled in when there is
 * one. It is the document's own origin, so this is the same request either way.
 */
function sameOriginBaseUrl(): string {
  return typeof location === "undefined" ? "" : location.origin;
}

export function createAppApiClient(baseUrl: string = sameOriginBaseUrl()): ApiClient {
  const client = createApiClient({ baseUrl });

  client.use({
    onRequest({ request, schemaPath }) {
      if (!UNSAFE_METHODS.has(request.method.toUpperCase())) return request;
      if (CSRF_EXEMPT_PATHS.has(schemaPath)) return request;

      const token = readCsrfToken();
      if (token !== null) request.headers.set(CSRF_HEADER, token);
      return request;
    },

    onResponse({ response }) {
      if (response.status === 401) unauthorizedHandler?.();
      // Nothing is rewritten; the response travels on untouched.
      return undefined;
    },
  });

  return client;
}

let sharedClient: ApiClient | null = null;

/**
 * The one client the application uses, built on first call.
 *
 * Lazy rather than module-scope because construction reads two pieces of
 * environment — `location` for the base URL, and `globalThis.fetch`, which
 * openapi-fetch captures once and keeps. Both are wrong to sample at import
 * time: a module can be imported before either is the one that will be used.
 */
export function getApiClient(): ApiClient {
  sharedClient ??= createAppApiClient();
  return sharedClient;
}
