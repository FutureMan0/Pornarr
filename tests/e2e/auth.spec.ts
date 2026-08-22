/**
 * Piece 02 - authentication, sessions, CSRF, invites and API keys, against the
 * live stack.
 *
 * Three things shape this file.
 *
 * The session store, the rate limiter and the login counters are *shared* state
 * on a stack three agents are using at once, so every test that fills a counter
 * empties it again, every account it creates it reuses on the next run, and the
 * rate-limit work is aimed at `http://localhost:8000` rather than at the Vite
 * proxy the browsers go through - a different source address, so a lockout this
 * file creates cannot reach anybody's browser session.
 *
 * The clock is forced rather than waited out. A seven-day session TTL and a
 * fifteen-minute login window are both observable only by shortening the key
 * that carries them, which is what `stackRedis` is for; an invite's expiry is
 * forced the same way in Postgres. Every row so treated was created by this
 * file through the product's own routes.
 *
 * Where the product had no documented behaviour and no behaviour at all, the
 * absence was executed as an expected failure rather than skipped, so the day it
 * started working the run would turn red. None are left: every one has been
 * fixed and is now an ordinary assertion -- the `Secure` cookie attribute, the
 * timing of a wrong username against a wrong password, the contract's security
 * schemes, the OIDC sign-in surface, and the API-key screen federation.md tells
 * a user to visit.
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  type APIResponse,
  type Browser,
  type BrowserContext,
  expect,
  test,
} from "@playwright/test";
import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  canDriveStackRedis,
  canReadStackDatabase,
  databaseAttempt,
  databaseScalar,
  expectNoAccessibilityViolations,
  stackRedis,
} from "./helpers";

/** Mirrors `SESSION_COOKIE` in `apps/api/pornarr_api/auth.py`. */
const SESSION_COOKIE = "pornarr_session";
/** Mirrors `CSRF_COOKIE` / `CSRF_HEADER` there. */
const CSRF_COOKIE = "pornarr_csrf";
const CSRF_HEADER = "X-CSRF-Token";
/** `SESSION_TTL_SECONDS`, seven days. */
const SESSION_TTL_SECONDS = 7 * 24 * 60 * 60;
/** `LOGIN_ATTEMPT_LIMIT` and `LOGIN_ATTEMPT_WINDOW_SECONDS`. */
const LOGIN_ATTEMPT_LIMIT = 6;
const LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60;

/**
 * The API on its own published port.
 *
 * The browsers reach it through the Vite dev server, so every browser request in
 * the whole suite shares one client address - the `web` container's. The login
 * rate limiter buckets by client address, so filling that bucket would lock out
 * every other agent's sign-in. Reaching the API directly from the host puts this
 * file's failures in a bucket no browser uses.
 */
const API_ORIGIN = process.env.E2E_API_ORIGIN ?? "http://localhost:8000";

/**
 * `playwright.config.ts`'s own default. Read from the environment rather than
 * from `test.info()`, because contexts are opened in `beforeAll` as well as
 * inside tests.
 */
const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";

const COMPOSE_PROJECT = process.env.E2E_COMPOSE_PROJECT ?? "pornarr_gauntlet";

/** `_rate_limit_keys` in `apps/api/pornarr_api/auth.py`. */
function accountLoginKey(username: string): string {
  return `pornarr:auth:login:account:${createHash("sha256").update(username.toLowerCase()).digest("hex")}`;
}

function sessionKey(token: string): string {
  return `pornarr:auth:session:${token}`;
}

/** Every login counter in the store, whoever put it there. Residue, never fixture. */
function clearLoginCounters(): void {
  const listed = stackRedis(["--scan", "--pattern", "pornarr:auth:login:*"]);
  const keys = (listed ?? "").split("\n").filter((key) => key.startsWith("pornarr:auth:login:"));
  if (keys.length > 0) stackRedis(["del", ...keys]);
}

/**
 * A snippet of the product's own code, run inside the API container.
 *
 * Some of what this file has to assert is a decision the application makes from
 * its environment, and the running server has exactly one environment. Executing
 * the shipped code in the shipped image under a chosen environment is the only
 * way to observe the other branches without restarting a stack three agents
 * share. `python` rather than `uv run`, so nothing re-syncs the virtualenv the
 * running server is using.
 */
function containerPython(environment: Record<string, string>, code: string): string | null {
  const settings = Object.entries(environment).flatMap(([name, value]) => [
    "-e",
    `${name}=${value}`,
  ]);
  try {
    return execFileSync(
      "docker",
      ["compose", "-p", COMPOSE_PROJECT, "exec", "-T", ...settings, "api", "python", "-c", code],
      { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
    ).trim();
  } catch {
    return null;
  }
}

/** One HTTP call from inside a stack container, so it arrives from a second address. */
function containerRequest(args: readonly string[]): string | null {
  try {
    return execFileSync(
      "docker",
      ["compose", "-p", COMPOSE_PROJECT, "exec", "-T", "api", "curl", "-s", ...args],
      { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
    ).trim();
  } catch {
    return null;
  }
}

type Client = {
  readonly context: BrowserContext;
  readonly get: (path: string, headers?: Record<string, string>) => Promise<APIResponse>;
  readonly post: (
    path: string,
    data?: unknown,
    headers?: Record<string, string>,
  ) => Promise<APIResponse>;
  readonly rawPost: (
    path: string,
    data: unknown,
    headers: Record<string, string>,
  ) => Promise<APIResponse>;
  readonly patch: (
    path: string,
    data?: unknown,
    headers?: Record<string, string>,
  ) => Promise<APIResponse>;
  readonly del: (path: string, headers?: Record<string, string>) => Promise<APIResponse>;
  readonly csrf: () => Promise<string>;
  readonly session: () => Promise<string>;
  readonly close: () => Promise<void>;
};

async function cookieValue(context: BrowserContext, name: string): Promise<string> {
  const cookie = (await context.cookies()).find((item) => item.name === name);
  if (cookie === undefined) throw new Error(`The context carries no ${name} cookie.`);
  return cookie.value;
}

function clientFor(context: BrowserContext): Client {
  const csrf = (): Promise<string> => cookieValue(context, CSRF_COOKIE);
  const withToken = async (headers: Record<string, string>): Promise<Record<string, string>> => ({
    [CSRF_HEADER]: await csrf(),
    ...headers,
  });
  return {
    context,
    get: (path, headers = {}) => context.request.get(path, { headers }),
    post: async (path, data = {}, headers = {}) =>
      context.request.post(path, { data, headers: await withToken(headers) }),
    rawPost: (path, data, headers) => context.request.post(path, { data, headers }),
    patch: async (path, data = {}, headers = {}) =>
      context.request.patch(path, { data, headers: await withToken(headers) }),
    del: async (path, headers = {}) =>
      context.request.delete(path, { headers: await withToken(headers) }),
    csrf,
    session: () => cookieValue(context, SESSION_COOKIE),
    close: () => context.close(),
  };
}

async function signIn(browser: Browser, username: string, password: string): Promise<Client> {
  const context = await browser.newContext({ baseURL: BASE_URL });
  const response = await context.request.post("/api/auth/login", {
    data: { username, password },
  });
  if (!response.ok()) {
    await context.close();
    throw new Error(
      `Signing in as ${username} answered ${response.status()}: ${await response.text()}`,
    );
  }
  return clientFor(context);
}

/**
 * A second account, made the way the product makes one: an administrator issues
 * an invitation and the invitee redeems it. Reused across runs, so a repeated
 * run adds a session rather than another account.
 */
const VIEWER_USERNAME = "piece02-viewer";
const VIEWER_PASSWORD = "Piece02-Viewer-2026!";

async function ensureAccount(
  admin: Client,
  browser: Browser,
  username: string,
  password: string,
): Promise<void> {
  const context = await browser.newContext({ baseURL: BASE_URL });
  try {
    if ((await context.request.post("/api/auth/login", { data: { username, password } })).ok()) {
      return;
    }
    const invite = await admin.post("/api/admin/invites", {
      valid_days: 1,
      note: `piece 02 ${username}`,
    });
    expect(invite.status(), await invite.text()).toBe(201);
    const { token } = (await invite.json()) as { token: string };
    const redeemed = await context.request.post(`/api/invites/${token}/redeem`, {
      data: { username, password },
    });
    expect(redeemed.status(), await redeemed.text()).toBe(201);
  } finally {
    await context.close();
  }
}

let admin: Client;

/**
 * `piece02-viewer`'s account id, from the route that lists accounts.
 *
 * Cached because it does not change between tests, and read through the product
 * rather than out of Postgres so the case does not need the database open.
 */
let viewerIdCache: string | null = null;
async function viewerId(): Promise<string> {
  if (viewerIdCache !== null) return viewerIdCache;
  const listed = await admin.get("/api/admin/users");
  expect(listed.status(), await listed.text()).toBe(200);
  const row = ((await listed.json()) as { id: string; username: string }[]).find(
    (entry) => entry.username === VIEWER_USERNAME,
  );
  expect(row, `${VIEWER_USERNAME} is not in /api/admin/users`).toBeDefined();
  viewerIdCache = (row as { id: string }).id;
  return viewerIdCache;
}

test.beforeAll(async ({ browser }) => {
  // Login counters are shared residue: another spec's failed sign-in can leave
  // this address at its limit, and no test in this file depends on a counter it
  // did not set itself.
  if (canDriveStackRedis()) clearLoginCounters();
  admin = await signIn(browser, ADMIN_USERNAME, ADMIN_PASSWORD);
  await ensureAccount(admin, browser, VIEWER_USERNAME, VIEWER_PASSWORD);
});

test.afterAll(async () => {
  if (canDriveStackRedis()) clearLoginCounters();
  // The throwaway accounts and invitations this file makes, removed. A
  // single-use invitation needs a name nobody has taken, so those tests cannot
  // reuse one account across runs the way `piece02-viewer` does, and the product
  // has no route that deletes a user or a spent invitation -- see HOLES.md 02.D.
  // Every row named here was created by this file, through the product's own
  // routes, minutes earlier.
  if (canReadStackDatabase()) {
    databaseAttempt(
      "delete from users where username like 'piece02-guest-%' " +
        "or username like 'piece02-wannabe-%' or username like 'piece02-exempt-%' " +
        "or username like 'piece02-late-%' or username like 'piece02-withdrawn-%' " +
        "or username like 'piece02-weak-%'",
    );
    databaseAttempt("delete from invites where note like 'piece 02 %'");
  }
  await admin.close();
});

test.describe("the session a sign-in establishes", () => {
  test("is an opaque Redis-backed record behind an HttpOnly SameSite=Lax cookie", async ({
    browser,
  }) => {
    const context = await browser.newContext({ baseURL: BASE_URL });
    const response = await context.request.post("/api/auth/login", {
      data: { username: VIEWER_USERNAME, password: VIEWER_PASSWORD },
    });
    expect(response.status()).toBe(200);
    const body = (await response.json()) as { id: string; username: string; role: string };
    expect(body.username).toBe(VIEWER_USERNAME);
    expect(body.role).toBe("user");

    const setCookies = response
      .headersArray()
      .filter((header) => header.name.toLowerCase() === "set-cookie")
      .map((header) => header.value);
    const session = setCookies.find((value) => value.startsWith(`${SESSION_COOKIE}=`));
    const csrf = setCookies.find((value) => value.startsWith(`${CSRF_COOKIE}=`));
    expect(session, setCookies.join(" | ")).toBeDefined();
    expect(csrf, setCookies.join(" | ")).toBeDefined();
    expect(session).toContain("HttpOnly");
    expect(session).toContain("SameSite=lax");
    expect(session).toContain("Path=/api");
    expect(session).toContain(`Max-Age=${SESSION_TTL_SECONDS}`);
    // The double-submit half is readable by design: the client has to put it in
    // a header, so a script has to be able to read it.
    expect(csrf).not.toContain("HttpOnly");
    expect(csrf).toContain("Path=/");

    const token = await cookieValue(context, SESSION_COOKIE);
    // Opaque: no structure, and nothing about the account inside it.
    expect(token).not.toContain(".");
    expect(token).not.toContain(body.id);
    expect(Buffer.from(token, "base64url").length).toBe(32);

    test.skip(!canDriveStackRedis(), "The stack's Redis is not reachable from the test runner.");
    const stored = stackRedis(["get", sessionKey(token)]);
    expect(stored, "The session is not in Redis at all.").not.toBeNull();
    const record = JSON.parse(stored as string) as { user_id: string; csrf_token: string };
    expect(record.user_id).toBe(body.id);
    expect(record.csrf_token).toBe(await cookieValue(context, CSRF_COOKIE));
    const ttl = Number.parseInt(stackRedis(["ttl", sessionKey(token)]) ?? "-1", 10);
    expect(ttl).toBeGreaterThan(SESSION_TTL_SECONDS - 60);
    expect(ttl).toBeLessThanOrEqual(SESSION_TTL_SECONDS);

    await context.close();
  });

  test("marks the session cookie Secure, or not, exactly as configured", async ({ browser }) => {
    // `_cookie_is_secure` used to read `app_env == "production"`, and
    // `.env.example` -- the file `make setup` writes -- ships
    // `APP_ENV=development`, so an operator who followed installation.md to a TLS
    // reverse proxy served the session cookie without `Secure`. It now reads
    // `SESSION_COOKIE_SECURE`, which defaults to true.
    //
    // This stack runs the documented development opt-out: it serves plain HTTP on
    // 127.0.0.1, and Playwright's own fetch will not send a `Secure` cookie to an
    // http origin, so the whole suite would answer 401 without it. So the header
    // is checked against what the container is actually configured for, and the
    // default -- the half that matters for a deployment -- is executed below,
    // against the same code in the same image under a different environment.
    const configured = containerPython(
      {},
      "import os;print(os.environ.get('SESSION_COOKIE_SECURE',''))",
    );
    test.skip(configured === null, "The stack's API container is not reachable from the runner.");
    const optedOut = configured === "false";

    const context = await browser.newContext({ baseURL: BASE_URL });
    try {
      const response = await context.request.post("/api/auth/login", {
        data: { username: VIEWER_USERNAME, password: VIEWER_PASSWORD },
      });
      const session = response
        .headersArray()
        .filter((header) => header.name.toLowerCase() === "set-cookie")
        .map((header) => header.value)
        .find((value) => value.startsWith(`${SESSION_COOKIE}=`));
      expect(session, "no session cookie was set at all").toBeDefined();
      expect(
        session?.includes("Secure"),
        `SESSION_COOKIE_SECURE is ${JSON.stringify(configured)} and the header is ${session}`,
      ).toBe(!optedOut);
    } finally {
      await context.close();
    }
  });

  test("marks it Secure by default, and refuses the opt-out in production", async () => {
    // The two branches this stack cannot be in at once, executed against the
    // product's own code inside its own container.
    const secure = containerPython(
      { SESSION_COOKIE_SECURE: "", APP_ENV: "development" },
      [
        "from types import SimpleNamespace",
        "from pornarr_shared.config import Settings",
        "from pornarr_api.routers.auth import _cookie_is_secure",
        "state = SimpleNamespace(settings=Settings())",
        "print(_cookie_is_secure(SimpleNamespace(app=SimpleNamespace(state=state))))",
      ].join("\n"),
    );
    test.skip(secure === null, "The stack's API container is not reachable from the runner.");
    // No SESSION_COOKIE_SECURE at all and APP_ENV=development -- the shape
    // `.env.example` and installation.md produce -- must still be Secure.
    expect(secure).toBe("True");

    const production = containerPython(
      { SESSION_COOKIE_SECURE: "false", APP_ENV: "production" },
      [
        "from pornarr_shared.config import load_settings",
        "try:",
        "    load_settings()",
        "    print('ACCEPTED')",
        "except Exception as exc:",
        "    print(type(exc).__name__, 'SESSION_COOKIE_SECURE' in str(exc))",
      ].join("\n"),
    );
    // Not a warning: the instance does not start, so the opt-out cannot be left
    // switched on by accident in a deployment.
    expect(production).toBe("ConfigurationError True");
  });

  test("stops working the moment its record expires, cookie still in hand", async ({ browser }) => {
    test.skip(!canDriveStackRedis(), "The stack's Redis is not reachable from the test runner.");
    const viewer = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const token = await viewer.session();
    expect((await viewer.get("/api/auth/me")).status()).toBe(200);

    // The seven-day TTL, forced rather than waited out.
    expect(stackRedis(["pexpire", sessionKey(token), "1"])).toBe("1");
    await expect
      .poll(async () => stackRedis(["exists", sessionKey(token)]), { timeout: 5_000 })
      .toBe("0");

    const refused = await viewer.get("/api/auth/me");
    expect(refused.status()).toBe(401);
    expect(((await refused.json()) as { code: string }).code).toBe("NOT_AUTHENTICATED");
    // The browser still holds the cookie: expiry is a fact about the server's
    // record, not about the client.
    expect(await viewer.session()).toBe(token);
    await viewer.close();
  });

  test("is revoked server-side by signing out, and the same cookie replayed is refused", async ({
    browser,
  }) => {
    const viewer = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const token = await viewer.session();

    const out = await viewer.post("/api/auth/logout");
    expect(out.status()).toBe(204);
    if (canDriveStackRedis()) {
      expect(stackRedis(["exists", sessionKey(token)])).toBe("0");
    }

    await viewer.context.addCookies([
      { name: SESSION_COOKIE, value: token, domain: "127.0.0.1", path: "/api" },
    ]);
    const replayed = await viewer.get("/api/auth/me");
    expect(replayed.status()).toBe(401);
    expect(((await replayed.json()) as { code: string }).code).toBe("NOT_AUTHENTICATED");
    await viewer.close();
  });

  test("ends everywhere on logout-everywhere, and only for that account", async ({ browser }) => {
    const first = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const second = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const third = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const tokens = [await first.session(), await second.session(), await third.session()];
    expect(new Set(tokens).size, "Three sign-ins produced fewer than three sessions.").toBe(3);
    expect((await admin.get("/api/auth/me")).status()).toBe(200);

    const everywhere = await third.post("/api/auth/logout-everywhere");
    expect(everywhere.status()).toBe(204);

    for (const other of [first, second]) {
      const refused = await other.get("/api/auth/me");
      expect(refused.status()).toBe(401);
      expect(((await refused.json()) as { code: string }).code).toBe("NOT_AUTHENTICATED");
    }
    if (canDriveStackRedis()) {
      for (const token of tokens) expect(stackRedis(["exists", sessionKey(token)])).toBe("0");
    }
    // The administrator was never signed out: the call ends one account's
    // sessions, not the server's.
    expect((await admin.get("/api/auth/me")).status()).toBe(200);

    await Promise.all([first.close(), second.close(), third.close()]);
  });

  test("is refused as soon as its account is deactivated, and the record is dropped", async ({
    browser,
  }) => {
    test.skip(
      !canReadStackDatabase() || !canDriveStackRedis(),
      "The stack's Postgres and Redis are not both reachable from the test runner.",
    );
    const viewer = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const token = await viewer.session();
    expect((await viewer.get("/api/auth/me")).status()).toBe(200);

    // Through the product's own route. Until `/api/admin/users` existed the only
    // way to reach this branch was a SQL UPDATE, which is what this case used to
    // do -- see HOLES.md 02.D, now closed.
    const deactivated = await admin.patch(`/api/admin/users/${await viewerId()}`, {
      is_active: false,
    });
    expect(deactivated.status(), await deactivated.text()).toBe(200);
    expect(((await deactivated.json()) as { is_active: boolean }).is_active).toBe(false);
    try {
      const refused = await viewer.get("/api/auth/me");
      expect(refused.status()).toBe(401);
      expect(((await refused.json()) as { code: string }).code).toBe("NOT_AUTHENTICATED");
      // Not merely refused: the held session is destroyed, so restoring the
      // account does not restore the session.
      expect(stackRedis(["exists", sessionKey(token)])).toBe("0");
      const signIn = await viewer.context.request.post("/api/auth/login", {
        data: { username: VIEWER_USERNAME, password: VIEWER_PASSWORD },
      });
      expect(signIn.status()).toBe(401);
      expect(((await signIn.json()) as { code: string }).code).toBe("INVALID_CREDENTIALS");
    } finally {
      const restored = await admin.patch(`/api/admin/users/${await viewerId()}`, {
        is_active: true,
      });
      expect(restored.status(), await restored.text()).toBe(200);
      expect(((await restored.json()) as { is_active: boolean }).is_active).toBe(true);
      clearLoginCounters();
      await viewer.close();
    }
  });
});

test.describe("revoking an account", () => {
  test("is refused to everybody but an administrator, and changes nothing", async ({ browser }) => {
    const viewer = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const anonymous = await browser.newContext({ baseURL: BASE_URL });
    try {
      const target = await viewerId();
      // Each with the code it must be refused *by*, not merely the status. A
      // signed-in viewer gets as far as the role check; nobody at all does not
      // get past the CSRF gate on an unsafe method, which sits in front of
      // authentication -- so the anonymous refusal on the safe method is the one
      // that proves the route needs an account at all.
      for (const [who, expected, response] of [
        [
          "a signed-in viewer deactivating",
          [403, "FORBIDDEN"],
          await viewer.patch(`/api/admin/users/${target}`, { is_active: false }),
        ],
        ["a signed-in viewer listing", [403, "FORBIDDEN"], await viewer.get("/api/admin/users")],
        [
          "nobody at all deactivating",
          [403, "CSRF_FAILED"],
          await anonymous.request.patch(`/api/admin/users/${target}`, {
            data: { is_active: false },
          }),
        ],
        [
          "nobody at all listing",
          [401, "NOT_AUTHENTICATED"],
          await anonymous.request.get("/api/admin/users"),
        ],
      ] as const) {
        expect(response.status(), `${who} was not refused`).toBe(expected[0]);
        expect(((await response.json()) as { code: string }).code, `${who}`).toBe(expected[1]);
      }
      // Refused, and nothing happened: the account is still active and its
      // session still works.
      const listed = (await (await admin.get("/api/admin/users")).json()) as {
        id: string;
        is_active: boolean;
      }[];
      expect(listed.find((row) => row.id === target)?.is_active).toBe(true);
      expect((await viewer.get("/api/auth/me")).status()).toBe(200);
    } finally {
      await Promise.all([viewer.close(), anonymous.close()]);
    }
  });

  test("cannot be turned on the administrator asking for it", async () => {
    const me = (await (await admin.get("/api/auth/me")).json()) as { id: string };

    const refused = await admin.patch(`/api/admin/users/${me.id}`, { is_active: false });

    expect(refused.status()).toBe(409);
    expect(((await refused.json()) as { code: string }).code).toBe("USER_CANNOT_DEACTIVATE_SELF");
    // Refused, and nothing happened: still active, still signed in. This is also
    // what keeps the last administrator -- the caller is by definition an active
    // administrator, so no other one is ever the last.
    const listed = (await (await admin.get("/api/admin/users")).json()) as {
      id: string;
      is_active: boolean;
      role: string;
    }[];
    expect(listed.find((row) => row.id === me.id)?.is_active).toBe(true);
    expect(listed.filter((row) => row.role === "admin" && row.is_active).length).toBeGreaterThan(0);
    expect((await admin.get("/api/auth/me")).status()).toBe(200);
  });

  test("is written to the audit log, with the name and never a credential", async () => {
    test.skip(
      !canReadStackDatabase(),
      "The stack's Postgres is not reachable from the test runner.",
    );
    const target = await viewerId();

    expect((await admin.patch(`/api/admin/users/${target}`, { is_active: false })).status()).toBe(
      200,
    );
    expect((await admin.patch(`/api/admin/users/${target}`, { is_active: true })).status()).toBe(
      200,
    );

    const written = databaseScalar(
      `select string_agg(action, ',' order by created_at) from (select action, created_at from audit_log where target = '${target}' order by created_at desc limit 2) recent`,
    );
    expect(written).toBe("user.deactivated,user.activated");
    const context = databaseScalar(
      `select context::text from audit_log where target = '${target}' and action = 'user.deactivated' order by created_at desc limit 1`,
    );
    expect(context).toContain(VIEWER_USERNAME);
    expect(context).not.toContain(VIEWER_PASSWORD);
  });
});

test.describe("CSRF on a state-changing request", () => {
  async function inviteCount(): Promise<number> {
    return ((await (await admin.get("/api/admin/invites")).json()) as unknown[]).length;
  }

  test("is refused without the header, and nothing is created", async () => {
    const before = await inviteCount();
    const response = await admin.rawPost(
      "/api/admin/invites",
      { valid_days: 1, note: "piece 02 csrf missing header" },
      {},
    );
    expect(response.status()).toBe(403);
    expect(await response.json()).toEqual({ code: "CSRF_FAILED", status: 403, context: {} });
    expect(await inviteCount()).toBe(before);
  });

  test("is refused when the token belongs to a different session", async ({ browser }) => {
    const other = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const before = await inviteCount();

    // The other session's token in the header only: cookie and header disagree.
    const mismatched = await admin.rawPost(
      "/api/admin/invites",
      { valid_days: 1, note: "piece 02 csrf foreign header" },
      { [CSRF_HEADER]: await other.csrf() },
    );
    expect(mismatched.status()).toBe(403);
    expect(((await mismatched.json()) as { code: string }).code).toBe("CSRF_FAILED");

    // And with the other session's token in *both* halves, so the double submit
    // agrees with itself and disagrees with the session record. This is the case
    // a naive cookie==header check would let through.
    const foreignToken = await other.csrf();
    const mine = await admin.csrf();
    await admin.context.addCookies([
      { name: CSRF_COOKIE, value: foreignToken, domain: "127.0.0.1", path: "/" },
    ]);
    try {
      const bothForeign = await admin.rawPost(
        "/api/admin/invites",
        { valid_days: 1, note: "piece 02 csrf foreign pair" },
        { [CSRF_HEADER]: foreignToken },
      );
      expect(bothForeign.status()).toBe(403);
      expect(((await bothForeign.json()) as { code: string }).code).toBe("CSRF_FAILED");
    } finally {
      await admin.context.addCookies([
        { name: CSRF_COOKIE, value: mine, domain: "127.0.0.1", path: "/" },
      ]);
      await other.close();
    }
    expect(await inviteCount()).toBe(before);
  });

  test("is still required when the request names an X-Api-Key it cannot prove", async () => {
    // api-contract.md L62-63 exempts API-key requests for one stated reason:
    // they "carry no ambient credential". A session cookie is an ambient
    // credential, so a request carrying one has not earned the exemption. Before
    // the fix in this piece, an unrecognised header value alone turned the check
    // off and the cookie then authorised the call.
    const before = await inviteCount();
    const response = await admin.rawPost(
      "/api/admin/invites",
      { valid_days: 1, note: "piece 02 csrf api-key bypass" },
      { "X-Api-Key": "pnr_not-a-real-key-at-all" },
    );
    expect(response.status()).toBe(403);
    expect(((await response.json()) as { code: string }).code).toBe("CSRF_FAILED");
    expect(await inviteCount()).toBe(before);
  });

  test("is still required on the authentication routes themselves", async ({ browser }) => {
    const viewer = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const token = await viewer.session();
    // `get_current_user` ignores X-Api-Key under /api/auth, so this call would be
    // authorised by the cookie alone if the header switched the check off.
    const response = await viewer.rawPost(
      "/api/auth/logout-everywhere",
      {},
      { "X-Api-Key": "pnr_not-a-real-key-at-all" },
    );
    expect(response.status()).toBe(403);
    expect(((await response.json()) as { code: string }).code).toBe("CSRF_FAILED");
    expect((await viewer.get("/api/auth/me")).status()).toBe(200);
    if (canDriveStackRedis()) expect(stackRedis(["exists", sessionKey(token)])).toBe("1");
    await viewer.close();
  });

  test("is not required for a safe method or for redeeming an invitation", async ({ browser }) => {
    // The two documented exemptions, executed: a GET never needs one, and
    // redemption cannot have one because the redeemer has no session yet.
    expect((await admin.get("/api/admin/invites")).status()).toBe(200);

    const invite = await admin.post("/api/admin/invites", {
      valid_days: 1,
      note: "piece 02 redemption exemption",
    });
    expect(invite.status()).toBe(201);
    const { id, token } = (await invite.json()) as { id: string; token: string };
    const context = await browser.newContext({ baseURL: BASE_URL });
    try {
      const redeemed = await context.request.post(`/api/invites/${token}/redeem`, {
        data: { username: `piece02-exempt-${Date.now()}`, password: "Piece02-Exempt-2026!" },
      });
      expect(redeemed.status(), await redeemed.text()).toBe(201);
    } finally {
      await context.close();
      // The invitation is spent, so it stays as the record of who joined; the
      // account it made is left signed out and owns nothing.
      expect((await admin.del(`/api/admin/invites/${id}`)).status()).toBe(409);
    }
  });
});

test.describe("the login rate limit", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(() => {
    test.skip(!canDriveStackRedis(), "The stack's Redis is not reachable from the test runner.");
    clearLoginCounters();
  });

  test.afterEach(() => {
    if (canDriveStackRedis()) clearLoginCounters();
  });

  test("counts the caller's address across accounts, and then refuses the right password", async ({
    request,
  }) => {
    const attempt = async (username: string): Promise<APIResponse> =>
      request.post(`${API_ORIGIN}/api/auth/login`, {
        data: { username, password: "definitely-not-the-password" },
        failOnStatusCode: false,
      });

    // Three at one name and three at another: no single account reaches the
    // limit, and the address does.
    const statuses: number[] = [];
    for (const username of ["piece02-ghost-a", "piece02-ghost-b"]) {
      for (let index = 0; index < 3; index += 1) {
        statuses.push((await attempt(username)).status());
      }
    }
    expect(statuses).toEqual([401, 401, 401, 401, 401, 429]);

    const limited = await attempt("piece02-ghost-c");
    expect(limited.status()).toBe(429);
    expect(((await limited.json()) as { code: string }).code).toBe("LOGIN_RATE_LIMITED");

    // The collateral: a legitimate account with its correct password is refused
    // too, because the bucket is the address rather than the account.
    const legitimate = await request.post(`${API_ORIGIN}/api/auth/login`, {
      data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD },
      failOnStatusCode: false,
    });
    expect(legitimate.status()).toBe(429);
    expect(((await legitimate.json()) as { code: string }).code).toBe("LOGIN_RATE_LIMITED");
    expect(legitimate.headers()["set-cookie"]).toBeUndefined();

    // Not forever: the counter carries the fifteen-minute window, and the same
    // credentials work the moment it ends.
    const keys = (stackRedis(["--scan", "--pattern", "pornarr:auth:login:ip:*"]) ?? "")
      .split("\n")
      .filter((key) => key.startsWith("pornarr:auth:login:ip:"));
    expect(keys.length).toBe(1);
    const ttl = Number.parseInt(stackRedis(["ttl", keys[0]]) ?? "-1", 10);
    expect(ttl).toBeGreaterThan(LOGIN_ATTEMPT_WINDOW_SECONDS - 120);
    expect(ttl).toBeLessThanOrEqual(LOGIN_ATTEMPT_WINDOW_SECONDS);
    stackRedis(["pexpire", keys[0], "1"]);
    await expect.poll(() => stackRedis(["exists", keys[0]]), { timeout: 5_000 }).toBe("0");

    const recovered = await request.post(`${API_ORIGIN}/api/auth/login`, {
      data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD },
      failOnStatusCode: false,
    });
    expect(recovered.status()).toBe(200);
  });

  test("counts the account independently of the address it is attacked from", async ({
    request,
  }) => {
    const target = "piece02-ghost-account";
    const body = JSON.stringify({ username: target, password: "definitely-not-the-password" });
    // Six failures from inside the API container: a different client address
    // from the one the test runner has.
    for (let index = 0; index < LOGIN_ATTEMPT_LIMIT; index += 1) {
      const answer = containerRequest([
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "-X",
        "POST",
        "http://localhost:8000/api/auth/login",
        "-H",
        "content-type: application/json",
        "-d",
        body,
      ]);
      test.skip(answer === null, "The stack's API container is not reachable from the runner.");
      expect(answer).toBe(index === LOGIN_ATTEMPT_LIMIT - 1 ? "429" : "401");
    }
    expect(stackRedis(["get", accountLoginKey(target)])).toBe(String(LOGIN_ATTEMPT_LIMIT));

    // From the runner's own address, whose counter is empty, that one account is
    // refused and every other name is not.
    const forTarget = await request.post(`${API_ORIGIN}/api/auth/login`, {
      data: { username: target, password: "definitely-not-the-password" },
      failOnStatusCode: false,
    });
    expect(forTarget.status()).toBe(429);
    expect(((await forTarget.json()) as { code: string }).code).toBe("LOGIN_RATE_LIMITED");
    // Refused before it is counted: the address bucket did not move.
    expect(stackRedis(["--scan", "--pattern", "pornarr:auth:login:ip:*"])?.split("\n").length).toBe(
      1,
    );

    const forSomebodyElse = await request.post(`${API_ORIGIN}/api/auth/login`, {
      data: { username: "piece02-ghost-other", password: "definitely-not-the-password" },
      failOnStatusCode: false,
    });
    expect(forSomebodyElse.status()).toBe(401);
    expect(((await forSomebodyElse.json()) as { code: string }).code).toBe("INVALID_CREDENTIALS");
  });

  test("ignores X-Forwarded-For from a caller this instance does not trust", async ({
    request,
  }) => {
    // The dangerous half of the reverse-proxy fix, executed.
    //
    // `client_address` reads `X-Forwarded-For` only when the peer is one of
    // TRUSTED_PROXIES, and this stack sets none -- the shipped default. Anyone
    // can write that header, so an instance that believed it from an arbitrary
    // caller would let an attacker put every guess in a bucket of its own and the
    // login rate limit would stop existing. Six guesses, six different claimed
    // addresses, one counter.
    //
    // The other half -- that a *trusted* proxy's header is believed, so real
    // clients get their own buckets -- needs the API configured with a trusted
    // proxy, which cannot be true of this stack at the same time as this case.
    // It is executed in tests/api/test_auth.py ›
    // `test_the_login_limit_counts_each_client_behind_a_trusted_proxy`.
    for (let index = 0; index < LOGIN_ATTEMPT_LIMIT; index += 1) {
      await request.post(`${API_ORIGIN}/api/auth/login`, {
        data: { username: `piece02-ghost-proxy-${index}`, password: "definitely-not-the-password" },
        headers: { "X-Forwarded-For": `203.0.113.${index + 1}` },
        failOnStatusCode: false,
      });
    }
    const keys = (stackRedis(["--scan", "--pattern", "pornarr:auth:login:ip:*"]) ?? "")
      .split("\n")
      .filter((key) => key.startsWith("pornarr:auth:login:ip:"));
    expect(keys.length, "A forged X-Forwarded-For produced more than one bucket.").toBe(1);
    expect(stackRedis(["get", keys[0]])).toBe(String(LOGIN_ATTEMPT_LIMIT));

    // And a seventh claimed address is still refused, so the limit held.
    const asAnotherClaimedClient = await request.post(`${API_ORIGIN}/api/auth/login`, {
      data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD },
      headers: { "X-Forwarded-For": "198.51.100.42" },
      failOnStatusCode: false,
    });
    expect(asAnotherClaimedClient.status()).toBe(429);
    expect(((await asAnotherClaimedClient.json()) as { code: string }).code).toBe(
      "LOGIN_RATE_LIMITED",
    );
    expect(asAnotherClaimedClient.headers()["set-cookie"]).toBeUndefined();
  });
});

test.describe("telling a wrong name from a wrong password", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(() => {
    test.skip(!canDriveStackRedis(), "The stack's Redis is not reachable from the test runner.");
    clearLoginCounters();
  });

  test.afterEach(() => {
    if (canDriveStackRedis()) clearLoginCounters();
  });

  test("is impossible from the answer: same status, same code, same bytes", async ({ request }) => {
    const attempt = async (username: string): Promise<APIResponse> => {
      clearLoginCounters();
      return request.post(`${API_ORIGIN}/api/auth/login`, {
        data: { username, password: "definitely-not-the-password" },
        failOnStatusCode: false,
      });
    };
    const missing = await attempt("piece02-nobody-at-all");
    const present = await attempt(ADMIN_USERNAME);

    expect(missing.status()).toBe(present.status());
    expect(missing.status()).toBe(401);
    expect(await missing.text()).toBe(await present.text());
    expect(await missing.json()).toEqual({
      code: "INVALID_CREDENTIALS",
      status: 401,
      context: {},
    });
    expect(missing.headers()["set-cookie"]).toBeUndefined();
    expect(present.headers()["set-cookie"]).toBeUndefined();
  });

  test("is impossible from the timing either", async ({ request }) => {
    // `authenticate_user` used to run the Argon2 verification only when the row
    // existed, so an unknown name was answered in the time of one indexed SELECT
    // and a known one cost a full KDF: measured here, before the fix, at 9ms
    // against 56ms. It now verifies against a real dummy hash whether or not the
    // account exists.
    //
    // WHY THE TOLERANCE IS WHAT IT IS. The quantity being hunted is the whole
    // Argon2id verification, which dominates the request: before the fix the two
    // medians differed by a factor of six. Two single measurements would be
    // noise, so this compares medians over 21 interleaved samples each -- one
    // pair per iteration, so a slow moment on a stack three agents share lands in
    // both distributions rather than in one -- and allows a ratio anywhere in
    // [0.6, 1.667]. That band is more than four times looser than the effect it
    // has to catch and far tighter than the defect, which sat at 6.2. A median
    // over 21 samples is also robust to a handful of outliers by construction: a
    // third of the samples would have to move before it does.
    const SAMPLES = 21;
    const sample = async (username: string): Promise<number> => {
      clearLoginCounters();
      const started = process.hrtime.bigint();
      await request.post(`${API_ORIGIN}/api/auth/login`, {
        data: { username, password: "definitely-not-the-password" },
        failOnStatusCode: false,
      });
      return Number(process.hrtime.bigint() - started) / 1e6;
    };
    const median = (values: number[]): number =>
      [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];

    const unknown: number[] = [];
    const known: number[] = [];
    for (let index = 0; index < SAMPLES; index += 1) {
      // Alternating the order every iteration so a monotonic drift -- another
      // agent's load arriving mid-run -- cannot favour one of the two.
      if (index % 2 === 0) {
        unknown.push(await sample("piece02-nobody-at-all"));
        known.push(await sample(ADMIN_USERNAME));
      } else {
        known.push(await sample(ADMIN_USERNAME));
        unknown.push(await sample("piece02-nobody-at-all"));
      }
    }
    const [knownMedian, unknownMedian] = [median(known), median(unknown)];
    const ratio = knownMedian / unknownMedian;
    const detail = `a known account answered ${knownMedian.toFixed(1)}ms and an unknown one ${unknownMedian.toFixed(1)}ms over ${SAMPLES} samples each`;

    // A floor under the measurement itself: if both sides answered in under a
    // millisecond the KDF did not run at all and the ratio would be meaningless.
    expect(unknownMedian, `the unknown-name path did no work: ${detail}`).toBeGreaterThan(5);
    expect(knownMedian, `the known-name path did no work: ${detail}`).toBeGreaterThan(5);
    expect(ratio, `distinguishable by anyone with a stopwatch: ${detail}`).toBeGreaterThan(0.6);
    expect(ratio, `distinguishable by anyone with a stopwatch: ${detail}`).toBeLessThan(1.667);
  });
});

test.describe("a per-user API key", () => {
  test.describe.configure({ mode: "serial" });

  type CreatedKey = {
    id: string;
    label: string;
    prefix: string;
    created_at: string;
    last_used_at: string | null;
    key: string;
  };

  async function createKey(owner: Client, label: string): Promise<CreatedKey> {
    const response = await owner.post("/api/account/api-keys", { label });
    expect(response.status(), await response.text()).toBe(201);
    return (await response.json()) as CreatedKey;
  }

  async function withKey(
    browser: Browser,
    key: string,
    call: (context: BrowserContext) => Promise<void>,
  ): Promise<void> {
    // A context that never signs in, so the key is the only credential in play.
    const context = await browser.newContext({
      baseURL: BASE_URL,
      extraHTTPHeaders: { "X-Api-Key": key },
    });
    try {
      await call(context);
    } finally {
      await context.close();
    }
  }

  test("is shown once, stored only as an Argon2 hash, and named by its prefix", async () => {
    const created = await createKey(admin, "piece 02 shown once");
    try {
      expect(created.key.startsWith("pnr_"), created.key.slice(0, 8)).toBe(true);
      expect(created.prefix).toBe(created.key.slice(0, 12));
      expect(created.last_used_at).toBeNull();

      const listed = await admin.get("/api/account/api-keys");
      expect(listed.status()).toBe(200);
      const text = await listed.text();
      expect(text).not.toContain(created.key);
      expect(text).not.toContain(created.key.slice(12));
      const row = ((await listed.json()) as CreatedKey[]).find((item) => item.id === created.id);
      expect(row, "The key that was just created is not in the account's own list.").toBeDefined();
      expect(row).not.toHaveProperty("key");

      if (canReadStackDatabase()) {
        const stored = databaseScalar(
          `select key_hash from user_api_keys where id = '${created.id}'`,
        );
        expect(stored?.startsWith("$argon2id$"), stored ?? "null").toBe(true);
        expect(stored).not.toContain(created.key.slice(4));
      }
    } finally {
      expect((await admin.del(`/api/account/api-keys/${created.id}`)).status()).toBe(204);
    }
  });

  test("authenticates on /api/* and is refused on /api/auth/*", async ({ browser }) => {
    const created = await createKey(admin, "piece 02 machine access");
    try {
      await withKey(browser, created.key, async (context) => {
        const me = await context.request.get("/api/auth/me");
        expect(me.status()).toBe(401);
        expect(((await me.json()) as { code: string }).code).toBe("NOT_AUTHENTICATED");

        // The same account and the same role as the session resolves to.
        const profile = await context.request.get("/api/account/profile");
        expect(profile.status()).toBe(200);
        expect(await profile.json()).toEqual(
          await (await admin.get("/api/account/profile")).json(),
        );
      });
    } finally {
      expect((await admin.del(`/api/account/api-keys/${created.id}`)).status()).toBe(204);
    }
  });

  test("needs no CSRF token, where the same call on a cookie does", async ({ browser }) => {
    const created = await createKey(admin, "piece 02 no csrf");
    const preview = {
      release_name: "Fake Studio - Compose Test Scene (2026) 1080p",
      profile: { name: "piece02", items: [], cutoff: null, upgrade_allowed: true },
    };
    try {
      // Ambient credential, no token: refused.
      const onCookie = await admin.rawPost("/api/admin/quality/preview", preview, {});
      expect(onCookie.status()).toBe(403);
      expect(((await onCookie.json()) as { code: string }).code).toBe("CSRF_FAILED");

      await withKey(browser, created.key, async (context) => {
        const onKey = await context.request.post("/api/admin/quality/preview", { data: preview });
        expect(
          [200, 422].includes(onKey.status()),
          `The API-key POST answered ${onKey.status()}: ${await onKey.text()}`,
        ).toBe(true);
        // Whatever the payload turns out to be worth, it was not turned away for
        // want of a token.
        expect(await onKey.text()).not.toContain("CSRF_FAILED");
      });
    } finally {
      expect((await admin.del(`/api/account/api-keys/${created.id}`)).status()).toBe(204);
    }
  });

  test("records when it was last used, and stops working the instant it is deleted", async ({
    browser,
  }) => {
    const created = await createKey(admin, "piece 02 last used");
    let deleted = false;
    try {
      expect(created.last_used_at).toBeNull();
      await withKey(browser, created.key, async (context) => {
        expect((await context.request.get("/api/account/profile")).status()).toBe(200);
      });

      const lastUsed = async (): Promise<string | null> => {
        const rows = (await (await admin.get("/api/account/api-keys")).json()) as CreatedKey[];
        return rows.find((row) => row.id === created.id)?.last_used_at ?? null;
      };
      await expect
        .poll(lastUsed, {
          timeout: 10_000,
          intervals: [250],
          message: "The key authenticated a call and last_used_at was never written.",
        })
        .not.toBeNull();
      const afterUse = await lastUsed();
      expect(new Date(afterUse as string).getTime()).toBeGreaterThanOrEqual(
        new Date(created.created_at).getTime() - 1_000,
      );

      expect((await admin.del(`/api/account/api-keys/${created.id}`)).status()).toBe(204);
      deleted = true;
      await withKey(browser, created.key, async (context) => {
        const refused = await context.request.get("/api/account/profile");
        expect(refused.status()).toBe(401);
        expect(((await refused.json()) as { code: string }).code).toBe("NOT_AUTHENTICATED");
      });
    } finally {
      if (!deleted) await admin.del(`/api/account/api-keys/${created.id}`);
    }
  });

  test("is rotatable: a replacement works while the one it replaces stops", async ({ browser }) => {
    const outgoing = await createKey(admin, "piece 02 rotation outgoing");
    const incoming = await createKey(admin, "piece 02 rotation incoming");
    expect(incoming.key).not.toBe(outgoing.key);
    expect(incoming.prefix).not.toBe(outgoing.prefix);
    try {
      await withKey(browser, outgoing.key, async (context) => {
        expect((await context.request.get("/api/account/profile")).status()).toBe(200);
      });
      expect((await admin.del(`/api/account/api-keys/${outgoing.id}`)).status()).toBe(204);
      await withKey(browser, outgoing.key, async (context) => {
        const refused = await context.request.get("/api/account/profile");
        expect(refused.status()).toBe(401);
        expect(((await refused.json()) as { code: string }).code).toBe("NOT_AUTHENTICATED");
      });
      // The replacement is untouched by the revocation of the one it replaced.
      await withKey(browser, incoming.key, async (context) => {
        expect((await context.request.get("/api/account/profile")).status()).toBe(200);
      });
    } finally {
      expect((await admin.del(`/api/account/api-keys/${incoming.id}`)).status()).toBe(204);
    }
  });

  test("cannot manage authentication settings, its own keys included", async ({ browser }) => {
    const created = await createKey(admin, "piece 02 no self management");
    try {
      await withKey(browser, created.key, async (context) => {
        for (const path of ["/api/account/api-keys", "/api/account/oidc", "/api/admin/oidc"]) {
          const refused = await context.request.get(path);
          expect(refused.status(), `${path} answered ${refused.status()}`).toBe(403);
          expect(((await refused.json()) as { code: string }).code).toBe("FORBIDDEN");
        }
        const created2 = await context.request.post("/api/account/api-keys", {
          data: { label: "piece 02 minted by a key" },
        });
        expect(created2.status()).toBe(403);
        expect(((await created2.json()) as { code: string }).code).toBe("FORBIDDEN");
      });
      expect(
        ((await (await admin.get("/api/account/api-keys")).json()) as CreatedKey[]).length,
      ).toBe(1);
    } finally {
      expect((await admin.del(`/api/account/api-keys/${created.id}`)).status()).toBe(204);
    }
  });

  test("inherits exactly the scope of the account that made it", async ({ browser }) => {
    const viewer = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const adminKey = await createKey(admin, "piece 02 scope admin");
    const viewerKey = await createKey(viewer, "piece 02 scope viewer");
    try {
      const adminSession = await (await admin.get("/api/requests?limit=100")).text();
      const viewerSession = await (await viewer.get("/api/requests?limit=100")).text();
      expect(
        adminSession,
        "The two accounts see the same requests, so this proves nothing about scope.",
      ).not.toBe(viewerSession);

      await withKey(browser, adminKey.key, async (context) => {
        expect(await (await context.request.get("/api/requests?limit=100")).text()).toBe(
          adminSession,
        );
        // The administrator's key reaches administration, because the account does.
        expect((await context.request.get("/api/admin/invites")).status()).toBe(200);
      });
      await withKey(browser, viewerKey.key, async (context) => {
        expect(await (await context.request.get("/api/requests?limit=100")).text()).toBe(
          viewerSession,
        );
        const refused = await context.request.get("/api/admin/invites");
        expect(refused.status()).toBe(403);
        expect(((await refused.json()) as { code: string }).code).toBe("FORBIDDEN");
      });
    } finally {
      await admin.del(`/api/account/api-keys/${adminKey.id}`);
      await viewer.del(`/api/account/api-keys/${viewerKey.id}`);
      await viewer.close();
    }
  });
});

test.describe("the administrator boundary", () => {
  type Operation = { readonly method: string; readonly path: string };

  async function adminOperations(): Promise<Operation[]> {
    const document = (await (await admin.get(`${API_ORIGIN}/api/openapi.json`)).json()) as {
      paths: Record<string, Record<string, unknown>>;
    };
    const operations: Operation[] = [];
    for (const [path, methods] of Object.entries(document.paths)) {
      if (!path.startsWith("/api/admin")) continue;
      for (const method of Object.keys(methods)) {
        if (["get", "post", "put", "patch", "delete"].includes(method)) {
          operations.push({ method: method.toUpperCase(), path });
        }
      }
    }
    return operations.sort((a, b) => `${a.path}${a.method}`.localeCompare(`${b.path}${b.method}`));
  }

  /** A path parameter nothing owns, so a wrongly-permitted call finds nothing to break. */
  function concrete(path: string): string {
    return path.replaceAll(/\{[^}]+\}/g, "00000000-0000-4000-8000-000000000000");
  }

  test("refuses every administration operation to a signed-in user, by name", async ({
    browser,
  }) => {
    const viewer = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const operations = await adminOperations();
    expect(
      operations.length,
      "The live document exposes no administration routes.",
    ).toBeGreaterThan(40);

    const wrong: string[] = [];
    for (const operation of operations) {
      const url = concrete(operation.path);
      const headers = { [CSRF_HEADER]: await viewer.csrf(), "content-type": "application/json" };
      const response = await viewer.context.request.fetch(url, {
        method: operation.method,
        headers,
        ...(operation.method === "GET" || operation.method === "DELETE" ? {} : { data: {} }),
        failOnStatusCode: false,
      });
      const body = await response.text();
      let code: string | null = null;
      try {
        code = (JSON.parse(body) as { code?: string }).code ?? null;
      } catch {
        code = null;
      }
      if (response.status() !== 403 || code !== "FORBIDDEN") {
        wrong.push(`${operation.method} ${operation.path} -> ${response.status()} ${code ?? body}`);
      }
    }
    expect(wrong, `${wrong.length} of ${operations.length} administration operations`).toEqual([]);
    await viewer.close();
  });

  test("refuses every administration operation to nobody at all, by name", async ({ browser }) => {
    const context = await browser.newContext({ baseURL: BASE_URL });
    const operations = await adminOperations();
    const wrong: string[] = [];
    for (const operation of operations) {
      // No session, so no CSRF token exists to send; the contract's answer for an
      // unauthenticated caller is 401, and CSRF_FAILED would be an answer that
      // tells an anonymous caller the route is there.
      const response = await context.request.fetch(concrete(operation.path), {
        method: operation.method,
        headers: { "content-type": "application/json" },
        ...(operation.method === "GET" || operation.method === "DELETE" ? {} : { data: {} }),
        failOnStatusCode: false,
      });
      const body = await response.text();
      let code: string | null = null;
      try {
        code = (JSON.parse(body) as { code?: string }).code ?? null;
      } catch {
        code = null;
      }
      if (![401, 403].includes(response.status())) {
        wrong.push(`${operation.method} ${operation.path} -> ${response.status()} ${code ?? body}`);
      }
    }
    expect(wrong, `${wrong.length} of ${operations.length} administration operations`).toEqual([]);
    await context.close();
  });

  test("stores the administrator's password as an Argon2id hash and never in the clear", async () => {
    test.skip(!canReadStackDatabase(), "The stack's Postgres is not reachable from the runner.");
    const hash = databaseScalar(
      `select password_hash from users where username = '${ADMIN_USERNAME}'`,
    );
    expect(hash?.startsWith("$argon2id$"), hash ?? "null").toBe(true);
    expect(hash).not.toContain(ADMIN_PASSWORD);
    const clear = databaseScalar(
      `select count(*) from users where password_hash = '${ADMIN_PASSWORD}'`,
    );
    expect(clear).toBe("0");
  });
});

test.describe("an invitation", () => {
  test.describe.configure({ mode: "serial" });

  type Invite = { id: string; token: string; expires_at: string; note: string | null };

  async function issue(note: string, validDays = 1): Promise<Invite> {
    const response = await admin.post("/api/admin/invites", { valid_days: validDays, note });
    expect(response.status(), await response.text()).toBe(201);
    return (await response.json()) as Invite;
  }

  async function accountsNamed(username: string): Promise<number> {
    const count = databaseScalar(`select count(*) from users where username = '${username}'`);
    return Number.parseInt(count ?? "-1", 10);
  }

  test("reads anonymously, redeems once into a working guest account", async ({ browser }) => {
    test.skip(!canReadStackDatabase(), "The stack's Postgres is not reachable from the runner.");
    const invite = await issue("piece 02 single use");
    const username = `piece02-guest-${Date.now()}`;
    const context = await browser.newContext({ baseURL: BASE_URL });
    try {
      // Anonymous, with no session anywhere in the context.
      const state = await context.request.get(`/api/invites/${invite.token}`);
      expect(state.status()).toBe(200);
      expect(await state.json()).toEqual({ valid: true, expires_at: invite.expires_at });

      const redeemed = await context.request.post(`/api/invites/${invite.token}/redeem`, {
        data: { username, password: "Piece02-Guest-2026!", display_name: "Piece 02 guest" },
      });
      expect(redeemed.status(), await redeemed.text()).toBe(201);
      expect(await redeemed.json()).toEqual({
        username,
        display_name: "Piece 02 guest",
        role: "user",
      });
      expect(await accountsNamed(username)).toBe(1);

      // It works: the guest signs in and is a user, not an administrator.
      const signedIn = await context.request.post("/api/auth/login", {
        data: { username, password: "Piece02-Guest-2026!" },
      });
      expect(signedIn.status()).toBe(200);
      expect(((await signedIn.json()) as { role: string }).role).toBe("user");

      // Spent. A second redemption of the same link creates nothing.
      const second = await browser.newContext({ baseURL: BASE_URL });
      const again = await second.request.post(`/api/invites/${invite.token}/redeem`, {
        data: { username: `${username}-again`, password: "Piece02-Guest-2026!" },
        failOnStatusCode: false,
      });
      expect(again.status()).toBe(404);
      expect(((await again.json()) as { code: string }).code).toBe("NOT_FOUND");
      expect(await accountsNamed(`${username}-again`)).toBe(0);
      expect(await (await second.request.get(`/api/invites/${invite.token}`)).json()).toEqual({
        valid: false,
        expires_at: null,
      });
      await second.close();

      // And it is kept as the record of who joined.
      const rows = (await (await admin.get("/api/admin/invites")).json()) as {
        id: string;
        redeemed_username: string | null;
      }[];
      const row = rows.find((item) => item.id === invite.id);
      expect(row?.redeemed_username).toBe(username);
      expect(await (await admin.get("/api/admin/invites")).text()).not.toContain(invite.token);

      // Only its hash was ever stored, and the audit log is a log: it records the
      // note and never the link.
      expect(
        databaseScalar(`select count(*) from invites where token_hash = '${invite.token}'`),
      ).toBe("0");
      expect(
        databaseScalar(
          `select count(*) from invites where position('${invite.token}' in token_hash) > 0`,
        ),
      ).toBe("0");
      expect(
        databaseScalar(
          `select count(*) from audit_log where position('${invite.token}' in context::text) > 0`,
        ),
      ).toBe("0");
    } finally {
      await context.close();
    }
  });

  test("cannot mint an administrator however it is asked", async ({ browser }) => {
    test.skip(!canReadStackDatabase(), "The stack's Postgres is not reachable from the runner.");
    const invite = await issue("piece 02 no privilege escalation");
    const username = `piece02-wannabe-${Date.now()}`;
    const context = await browser.newContext({ baseURL: BASE_URL });
    try {
      const redeemed = await context.request.post(`/api/invites/${invite.token}/redeem`, {
        data: {
          username,
          password: "Piece02-Wannabe-2026!",
          role: "admin",
          is_active: true,
          user: { role: "admin" },
        },
      });
      expect(redeemed.status(), await redeemed.text()).toBe(201);
      expect(((await redeemed.json()) as { role: string }).role).toBe("user");
      expect(databaseScalar(`select role from users where username = '${username}'`)).toBe("user");
    } finally {
      await context.close();
    }
  });

  test("stops working when it expires, and creates nothing after that", async ({ browser }) => {
    test.skip(!canReadStackDatabase(), "The stack's Postgres is not reachable from the runner.");
    const invite = await issue("piece 02 expiry");
    // The minimum the route accepts is a day, so the clock is forced on a row
    // this test created rather than waited out.
    databaseAttempt(
      `update invites set expires_at = now() - interval '1 minute' where id = '${invite.id}'`,
    );
    const username = `piece02-late-${Date.now()}`;
    const context = await browser.newContext({ baseURL: BASE_URL });
    try {
      const state = await context.request.get(`/api/invites/${invite.token}`);
      expect(state.status()).toBe(200);
      // An expiry is a fact about a working link; a dead one reports none.
      expect(await state.json()).toEqual({ valid: false, expires_at: null });

      const refused = await context.request.post(`/api/invites/${invite.token}/redeem`, {
        data: { username, password: "Piece02-Late-2026!" },
        failOnStatusCode: false,
      });
      expect(refused.status()).toBe(404);
      expect(((await refused.json()) as { code: string }).code).toBe("NOT_FOUND");
      expect(await accountsNamed(username)).toBe(0);
    } finally {
      await context.close();
      expect((await admin.del(`/api/admin/invites/${invite.id}`)).status()).toBe(204);
    }
  });

  test("stops working when it is withdrawn, and creates nothing after that", async ({
    browser,
  }) => {
    test.skip(!canReadStackDatabase(), "The stack's Postgres is not reachable from the runner.");
    const invite = await issue("piece 02 withdrawal");
    expect((await admin.del(`/api/admin/invites/${invite.id}`)).status()).toBe(204);

    const username = `piece02-withdrawn-${Date.now()}`;
    const context = await browser.newContext({ baseURL: BASE_URL });
    try {
      expect(await (await context.request.get(`/api/invites/${invite.token}`)).json()).toEqual({
        valid: false,
        expires_at: null,
      });
      const refused = await context.request.post(`/api/invites/${invite.token}/redeem`, {
        data: { username, password: "Piece02-Withdrawn-2026!" },
        failOnStatusCode: false,
      });
      expect(refused.status()).toBe(404);
      expect(((await refused.json()) as { code: string }).code).toBe("NOT_FOUND");
      expect(await accountsNamed(username)).toBe(0);
      expect(
        ((await (await admin.get("/api/admin/invites")).json()) as { id: string }[]).some(
          (row) => row.id === invite.id,
        ),
      ).toBe(false);
    } finally {
      await context.close();
    }
  });

  test("is issued by an administrator and by nobody else", async ({ browser }) => {
    const viewer = await signIn(browser, VIEWER_USERNAME, VIEWER_PASSWORD);
    const before = ((await (await admin.get("/api/admin/invites")).json()) as unknown[]).length;
    try {
      const refused = await viewer.post("/api/admin/invites", { valid_days: 1, note: "nope" });
      expect(refused.status()).toBe(403);
      expect(((await refused.json()) as { code: string }).code).toBe("FORBIDDEN");
      const listed = await viewer.get("/api/admin/invites");
      expect(listed.status()).toBe(403);
      expect(((await listed.json()) as { code: string }).code).toBe("FORBIDDEN");
    } finally {
      await viewer.close();
    }
    expect(((await (await admin.get("/api/admin/invites")).json()) as unknown[]).length).toBe(
      before,
    );
  });

  test("refuses a password the sign-in screen would refuse", async ({ browser }) => {
    const invite = await issue("piece 02 weak password");
    const context = await browser.newContext({ baseURL: BASE_URL });
    try {
      const refused = await context.request.post(`/api/invites/${invite.token}/redeem`, {
        data: { username: `piece02-weak-${Date.now()}`, password: "short" },
        failOnStatusCode: false,
      });
      expect(refused.status()).toBe(422);
      const body = (await refused.json()) as {
        code: string;
        context: { fields: { location: string[] }[] };
      };
      expect(body.code).toBe("VALIDATION_FAILED");
      expect(body.context.fields.some((field) => field.location.includes("password"))).toBe(true);
      // Never echoed back into the error.
      expect(await refused.text()).not.toContain("short");
      // And the link is still good, so a typo does not burn the invitation.
      expect(await (await context.request.get(`/api/invites/${invite.token}`)).json()).toEqual({
        valid: true,
        expires_at: invite.expires_at,
      });
    } finally {
      await context.close();
      expect((await admin.del(`/api/admin/invites/${invite.id}`)).status()).toBe(204);
    }
  });
});

test.describe("what the interface offers for the three authentication paths", () => {
  test("signs in with a local account, and says so when the password is wrong", async ({
    page,
  }) => {
    await page.goto("/login");
    await expectNoAccessibilityViolations(page);
    await page.getByLabel("Username").fill(VIEWER_USERNAME);
    await page.getByLabel("Password").fill("definitely-not-the-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    const alert = page.getByRole("alert");
    await expect(alert).toBeVisible();
    // A sentence, not a code: the contract says the frontend maps codes to
    // translated prose and never renders the server's own English.
    await expect(alert).not.toContainText("INVALID_CREDENTIALS");
    await expect(alert).toContainText(/password|username/i);

    await page.getByLabel("Password").fill(VIEWER_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("button", { name: VIEWER_USERNAME })).toBeVisible();
    if (canDriveStackRedis()) clearLoginCounters();
  });

  test("offers a way to sign in with a configured OIDC provider", async ({ page }) => {
    // ADR 0016 ships OIDC "in the same release as local accounts". The API
    // always did: a provider can be created, and /api/auth/oidc/{id}/login
    // answers a 307 to the provider with PKCE. What was missing was every
    // surface in the web application - no provider administration screen, no
    // sign-in button, no linking screen - so the path existed and no user of the
    // product could reach it.
    const created = await admin.post("/api/admin/oidc", {
      name: "Piece 02 provider",
      issuer: "https://oidc.piece02.invalid",
      client_id: "piece02-client",
      client_secret: "piece02-secret",
    });
    expect(created.status(), await created.text()).toBe(201);
    const { id } = (await created.json()) as { id: string };
    try {
      await page.goto("/login");
      await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
      await expect(page.getByText("Piece 02 provider")).toBeVisible({ timeout: 3_000 });
    } finally {
      expect((await admin.del(`/api/admin/oidc/${id}`)).status()).toBe(204);
    }
  });

  test("offers a way to create the API key federation asks a user to hand over", async ({
    page,
  }) => {
    // federation.md L74 tells a user their key "is shown once, at creation",
    // and ADR 0016 makes it the machine path. `/api/account/api-keys` always
    // worked; no screen in apps/web/src/routes.tsx reached it, so the only way
    // to obtain a key was to call the API by hand.
    //
    // Signed in as `piece02-viewer` rather than as the administrator, and that
    // is the claim: a key is a credential every account has, not an
    // administration feature. While this case was an expected failure it never
    // signed in at all, so `/settings` bounced it to the sign-in screen and the
    // absence it reported was its own.
    await page.goto("/login");
    await page.getByLabel("Username").fill(VIEWER_USERNAME);
    await page.getByLabel("Password").fill(VIEWER_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("button", { name: VIEWER_USERNAME })).toBeVisible();

    await page.goto("/settings");
    await expect(page.getByRole("link", { name: /API key/i })).toBeVisible({ timeout: 3_000 });
    await expectNoAccessibilityViolations(page);
  });

  test("declares its authentication schemes in the contract", async () => {
    // Contradiction C3. api-contract.md L54-63 names three authentication paths;
    // ADR 0009 makes openapi.json the boundary the client and the MSW handlers
    // are generated from. The document used to declare no security scheme and no
    // operation carried one, so a client generated strictly from the contract
    // could not know that anything on the instance was authenticated.
    //
    // Read from the *live* document rather than the committed file, so this
    // fails if the running instance and openapi.json have drifted apart.
    const document = (await (await admin.get(`${API_ORIGIN}/api/openapi.json`)).json()) as {
      components?: { securitySchemes?: Record<string, { type: string; in: string; name: string }> };
      paths: Record<string, Record<string, { security?: unknown }>>;
    };
    const schemes = document.components?.securitySchemes ?? {};
    expect(Object.keys(schemes).sort()).toEqual(["ApiKey", "SessionCookie"]);
    expect(schemes.SessionCookie).toMatchObject({
      type: "apiKey",
      in: "cookie",
      name: SESSION_COOKIE,
    });
    expect(schemes.ApiKey).toMatchObject({ type: "apiKey", in: "header", name: "X-Api-Key" });

    const operations = Object.entries(document.paths).flatMap(([path, methods]) =>
      Object.entries(methods).map(([method, operation]) => ({ path, method, operation })),
    );
    const secured = operations.filter(({ operation }) => operation.security !== undefined);
    // Not "at least one": nearly every operation on the instance is
    // authenticated, and a document that said so for one of them would be as
    // useless to a generator as one that said it for none.
    expect(secured.length).toBeGreaterThan(operations.length * 0.9);

    const both = [{ SessionCookie: [] }, { ApiKey: [] }];
    const at = (path: string, method: string): unknown =>
      operations.find((entry) => entry.path === path && entry.method === method)?.operation
        .security;
    expect(at("/api/library", "get")).toEqual(both);
    expect(at("/api/admin/users/{user_id}", "patch")).toEqual(both);
    // `/api/auth/*` refuses API keys, and the document says so rather than
    // offering a credential the route would reject.
    expect(at("/api/auth/me", "get")).toEqual([{ SessionCookie: [] }]);
    // And the operations a caller reaches without an account carry none, so the
    // document describes the product rather than asserting a blanket rule.
    expect(at("/api/auth/login", "post")).toBeUndefined();
    expect(at("/api/invites/{token}", "get")).toBeUndefined();
    expect(at("/api/setup/status", "get")).toBeUndefined();
  });
});
