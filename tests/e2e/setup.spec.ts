/**
 * First run: a virgin instance becoming a usable product.
 *
 * This is the one file in the suite that owns global state. Everything the
 * first group asserts is true exactly once per instance — an unconfigured
 * server, a library path nobody has accepted yet, a first administrator — so
 * the flow is driven once, in file order, against a stack that has never been
 * set up.
 *
 * The wizard is driven through the browser rather than through the API, because
 * what the operator is promised is a wizard: that a bad path is refused with a
 * sentence, that a connection is tested before it is stored, and that the step
 * does not advance when the test fails. The API calls here only read back what
 * the browser caused, or ask a question the browser has no way to ask (the
 * status code of a request nobody's UI will ever make).
 *
 * Running the virgin group again needs an instance nobody has configured. Two
 * ways, and neither is "reset the shared stack while other suites are using it":
 *
 *   - point the suite at a throwaway Compose project on other ports —
 *     `E2E_BASE_URL` and `E2E_API_URL` are all it takes, and the project can be
 *     brought up from the same three compose files with a ports/data override;
 *   - or, when this piece owns the stack outright, `E2E_ALLOW_STACK_RESET=1`,
 *     which destroys the shared stack's state first.
 *
 * Without either, the virgin cases skip and say so, and the rest still runs.
 *
 * The claims of first run that need a virgin server but no browser — the race
 * between concurrent setup completions, the API's own password rule, the health
 * report of an instance whose mounts disagree — are in `setup-probe.spec.ts`,
 * which builds a throwaway API per case and needs nothing of this stack but its
 * Postgres, its Redis and its network. Those run every time.
 */
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { type Page, expect, test } from "@playwright/test";
import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  apiDelete,
  apiGet,
  expectNoAccessibilityViolations,
  waitForWebServer,
} from "./helpers";

/** The web server the browser talks to; the API is reached through its proxy. */
const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";
/** `/health` is not under `/api`, so it is not proxied and needs the API direct. */
const API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";
/**
 * The library root the instance ends up with. `/data` is what
 * `setUpAdministrator` in helpers.ts has always chosen, and the rest of the
 * suite locates its fixtures through the enabled root folder, so this file
 * cannot pick a different one without moving every other piece's media.
 */
const LIBRARY_PATH = "/data";
/** A path with nothing behind it, for the rejection case. */
const MISSING_LIBRARY_PATH = "/nope/not/here";
/**
 * A directory on the container's own image layer rather than on the bind mount
 * that carries `/data`. Two devices, so the hardlink warning is a real property
 * of this stack and not a fixture pretending to be one. See ADR 0004.
 */
const CROSS_FILESYSTEM_LIBRARY_PATH = "/tmp";

const INDEXER_URL = process.env.E2E_FAKE_INDEXER_URL ?? "http://fake-indexer:9117/api";
/**
 * The same host on a port nothing listens on: the connection is refused at once,
 * which is a real failed test rather than a name that takes a resolver timeout
 * to fail.
 */
const UNREACHABLE_INDEXER_URL = "http://fake-indexer:9999/api";
const INDEXER_API_KEY = "fake";

const CLIENT_HOST = process.env.E2E_QBITTORRENT_HOST ?? "qbittorrent";
const CLIENT_PORT = process.env.E2E_QBITTORRENT_PORT ?? "8080";
const UNREACHABLE_CLIENT_PORT = "9999";
const CLIENT_USERNAME = "admin";
const CLIENT_PASSWORD = "adminadmin";

/** The names `POST /api/setup/complete` gives what it creates, from the implementation. */
const INDEXER_NAME = "Torznab";
const CLIENT_NAME = "qBittorrent";

/** An administrator that must never come to exist. */
const SECOND_ADMIN_USERNAME = "second-administrator";
const SECOND_ADMIN_PASSWORD = "Second-Administrator-2026!";

/**
 * Twelve characters from two character classes is the rule the account step
 * states; this misses it by one character, so the rule is what refuses it
 * rather than emptiness.
 */
const WEAK_PASSWORD = "Short1Weak!";

const RESET_SCRIPT = join(process.cwd(), ".gauntlet", "stack-reset.sh");
const RESET_TIMEOUT_MILLISECONDS = 420_000;

/** Set once, in `beforeAll`: whether this run got an instance nobody has configured. */
let virgin = false;

const VIRGIN_ONLY =
  "The instance is already configured, and a first run happens once. Point E2E_BASE_URL " +
  "and E2E_API_URL at a throwaway Compose project, or re-run with E2E_ALLOW_STACK_RESET=1 " +
  "to destroy this stack's state, to prove it again.";
const CONFIGURED_ONLY =
  "The instance is not configured yet, so there is no completed setup to re-enter.";

/** `null` when the server did not answer at all, which is what waiting looks like. */
async function configuredNow(): Promise<boolean | null> {
  try {
    const response = await fetch(`${BASE_URL}/api/setup/status`);
    if (!response.ok) return null;
    return ((await response.json()) as { configured: boolean }).configured;
  } catch {
    return null;
  }
}

async function setupStatus(page: Page): Promise<boolean> {
  const response = await page.request.get("/api/setup/status");
  expect(response.ok()).toBe(true);
  return ((await response.json()) as { configured: boolean }).configured;
}

test.beforeAll(async () => {
  test.setTimeout(RESET_TIMEOUT_MILLISECONDS);
  if (process.env.E2E_ALLOW_STACK_RESET === "1" && existsSync(RESET_SCRIPT)) {
    execFileSync(RESET_SCRIPT, { stdio: "inherit" });
  }
  await expect.poll(configuredNow, { timeout: 180_000, intervals: [2_000] }).not.toBeNull();
  virgin = (await configuredNow()) === false;
});

/** The account step, which every later step is reached through. */
async function passAccountStep(page: Page): Promise<void> {
  await page.goto("/setup");
  await expect(page.getByRole("heading", { name: "Set up Pornarr" })).toBeVisible();
  await page.getByLabel("Username").fill(ADMIN_USERNAME);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Choose the library path" })).toBeVisible();
}

async function passLibraryStep(page: Page): Promise<void> {
  await page.getByRole("textbox", { name: "Library path" }).fill(LIBRARY_PATH);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Add a search indexer" })).toBeVisible();
}

async function fillIndexer(page: Page, baseUrl: string): Promise<void> {
  await page.getByLabel("Base URL").fill(baseUrl);
  await page.getByLabel("API key").fill(INDEXER_API_KEY);
}

async function fillDownloadClient(page: Page, port: string): Promise<void> {
  await page.getByLabel("Host").fill(CLIENT_HOST);
  await page.getByLabel("Port").fill(port);
  await page.getByLabel("Username").fill(CLIENT_USERNAME);
  await page.getByLabel("Password").fill(CLIENT_PASSWORD);
}

type ComponentHealth = { readonly status: string; readonly detail?: string };
type HealthReport = {
  readonly status: string;
  readonly database: ComponentHealth;
  readonly redis: ComponentHealth;
  readonly worker: ComponentHealth;
  readonly filesystem: ComponentHealth;
  /** Present only when the request was refused, which is what the gate test reads. */
  readonly code?: string;
};

/**
 * The first run itself. Serial, because these are steps of one flow against one
 * server: the completion case can only be reached from an instance the earlier
 * cases left unconfigured.
 */
test.describe
  .serial("a virgin instance", () => {
    test("answers only the routes first run needs, and gates the rest", async ({ page }) => {
      test.skip(!virgin, VIRGIN_ONLY);
      await waitForWebServer(page);

      // Which routes are exempt is itself the claim, so both halves are sampled.
      // The API refuses with a code rather than a redirect: a machine holding an
      // API key needs to know why it was turned away.
      for (const path of ["/api/library", "/api/admin/indexers", "/api/queue"]) {
        const gated = await page.request.get(path);
        expect(gated.status(), `${path} should be gated until setup is done`).toBe(503);
        expect(await gated.json()).toMatchObject({ code: "SETUP_REQUIRED", status: 503 });
      }

      // And the health report is not one of them any more. installation.md and
      // backup.md tell an operator to verify a deployment before they configure
      // it; it used to answer `503 SETUP_REQUIRED` for exactly as long as the
      // deployment was unverified, which is also when the mounts it checks are
      // most likely to be wrong.
      const report = await page.request.get("/api/health");
      const body = (await report.json()) as HealthReport;
      expect(body.code, "the health report is still behind the setup gate").toBeUndefined();
      expect(body.database.status).toBe("healthy");
      expect(body.filesystem.status).toBe("healthy");

      // `/health` is the address docker-compose.yml curls and backup.md ends a
      // restore with. It answers the same report rather than a literal, so
      // `curl --fail` on it is a verification and not a formality.
      const probe = await page.request.get(`${API_URL}/health`);
      expect(probe.status()).toBe(200);
      expect(await probe.json()).toEqual(body);

      // The gate covers the API and nothing else. This suite reaches the
      // frontend through the Vite dev server, so until now nothing here asked
      // the gated instance itself for a document -- and in a deployment that is
      // exactly where the wizard comes from. It answered `503 SETUP_REQUIRED`
      // for `/` and for every hashed asset, so the one screen that can unlock
      // an instance could not load in a browser. The development image carries
      // no web build, so the claim is that the document is not gated, not that
      // it is there.
      const document = await page.request.get(`${API_URL}/`);
      const gatedDocument = await document.json().catch(() => ({}) as { code?: string });
      expect(
        (gatedDocument as { code?: string }).code,
        "the setup screen must not sit behind the setup gate",
      ).not.toBe("SETUP_REQUIRED");

      // The three the wizard itself cannot work without.
      const status = await page.request.get("/api/setup/status");
      expect(status.status()).toBe(200);
      expect(await status.json()).toEqual({ configured: false });
      const schema = await page.request.get("/api/openapi.json");
      expect(schema.status()).toBe(200);

      // Every browser route ends at the wizard, including the sign-in screen:
      // an account to sign in with does not exist yet, and a login form that
      // cannot succeed is a dead end.
      for (const route of ["/library", "/login", "/settings", "/admin", "/shorts"]) {
        await page.goto(route);
        await expect(page, `${route} should send an unconfigured instance to the wizard`).toHaveURL(
          /\/setup$/,
        );
        await expect(page.getByRole("heading", { name: "Set up Pornarr" })).toBeVisible();
      }
      await expectNoAccessibilityViolations(page);
    });

    test("refuses a password weaker than the rule the step states", async ({ page }) => {
      test.skip(!virgin, VIRGIN_ONLY);
      await page.goto("/setup");

      // The rule is stated before it is enforced, so it can be read rather than
      // discovered by being refused.
      await expect(
        page.getByText(
          "At least 12 characters from two kinds: lower case, upper case, digits, symbols.",
        ),
      ).toBeVisible();
      await page.getByLabel("Username").fill(ADMIN_USERNAME);
      await page.getByLabel("Password").fill(WEAK_PASSWORD);
      await expect(page.getByText("Password strength: weak")).toBeVisible();
      await page.getByRole("button", { name: "Continue" }).click();

      await expect(page.getByRole("alert")).toHaveText(
        "Use at least 12 characters from two character types.",
      );
      await expect(page.getByLabel("Password")).toHaveAttribute("aria-invalid", "true");
      await expect(page.getByRole("heading", { name: "Set up Pornarr" })).toBeVisible();
      await expectNoAccessibilityViolations(page);

      // And the same field accepts the rule being met, so the refusal was the
      // rule and not the button.
      await page.getByLabel("Password").fill(ADMIN_PASSWORD);
      await expect(page.getByText("Password strength: strong")).toBeVisible();
      await page.getByRole("button", { name: "Continue" }).click();
      await expect(page.getByRole("heading", { name: "Choose the library path" })).toBeVisible();
    });

    test("refuses a library path that is not there, and takes one that is", async ({ page }) => {
      test.skip(!virgin, VIRGIN_ONLY);
      await passAccountStep(page);

      await page.getByRole("textbox", { name: "Library path" }).fill(MISSING_LIBRARY_PATH);
      await page.getByRole("button", { name: "Continue" }).click();

      // The sentence, not just a red border: the code the API answered with is
      // mapped to something an operator can act on.
      await expect(page.getByRole("alert")).toHaveText(
        "That library path is unavailable, not a folder, or cannot be written to.",
      );
      await expect(page.getByRole("textbox", { name: "Library path" })).toHaveAttribute(
        "aria-invalid",
        "true",
      );
      await expect(page.getByRole("heading", { name: "Choose the library path" })).toBeVisible();
      await expectNoAccessibilityViolations(page);

      // The API separates the reasons the one sentence flattens, and a path that
      // is a file is a different failure from a path that is not there.
      const file = await page.request.post("/api/setup/validate-library-path", {
        data: { library_path: "/etc/hostname" },
      });
      expect(file.status()).toBe(422);
      expect(await file.json()).toMatchObject({
        code: "SETUP_PATH_INVALID",
        context: { reason: "not_directory" },
      });

      await passLibraryStep(page);
    });

    test("warns that a cross-filesystem library copies, then lets it through", async ({ page }) => {
      test.skip(!virgin, VIRGIN_ONLY);
      await passAccountStep(page);

      await page.getByRole("textbox", { name: "Library path" }).fill(CROSS_FILESYSTEM_LIBRARY_PATH);
      await page.getByRole("button", { name: "Continue" }).click();

      await expect(page.getByRole("alert")).toHaveText(
        "This library is on a different filesystem from downloads. Imports will copy instead of hardlinking.",
      );
      await expectNoAccessibilityViolations(page);

      // It warns and then lets the operator through under the consequence, which
      // the action itself now states. Pressing it is the half that proves the
      // step is not a wall.
      await page.getByRole("button", { name: "Continue with copy imports" }).click();
      await expect(page.getByRole("heading", { name: "Add a search indexer" })).toBeVisible();
    });

    test("tests an indexer against the real indexer before accepting it", async ({ page }) => {
      test.skip(!virgin, VIRGIN_ONLY);
      await passAccountStep(page);
      await passLibraryStep(page);

      await fillIndexer(page, UNREACHABLE_INDEXER_URL);
      await page.getByRole("button", { name: "Continue" }).click();

      // The cause, in words, on the screen where it happened. This used to read
      // "Something went wrong (INDEXER_CONNECTION_FAILED)." — the unknown-code
      // fallback — because the code had no entry in the frontend's error map.
      // PRODUCT.md: errors state the cause and the next step, never just that
      // something failed.
      await expect(page.getByRole("alert")).toHaveText(
        "The indexer did not answer, or answered with something unusable.",
      );
      await expect(page.getByRole("heading", { name: "Add a search indexer" })).toBeVisible();
      await expectNoAccessibilityViolations(page);

      await fillIndexer(page, INDEXER_URL);
      await page.getByRole("button", { name: "Continue" }).click();
      await expect(page.getByRole("heading", { name: "Add a download client" })).toBeVisible();
    });

    test("tests a download client against the real client before accepting it", async ({
      page,
    }) => {
      test.skip(!virgin, VIRGIN_ONLY);
      await passAccountStep(page);
      await passLibraryStep(page);
      await page.getByRole("button", { name: "Skip for now" }).click();
      await expect(page.getByRole("heading", { name: "Add a download client" })).toBeVisible();

      await fillDownloadClient(page, UNREACHABLE_CLIENT_PORT);
      await page.getByRole("button", { name: "Continue" }).click();

      // Same code-to-sentence path as the indexer step above, on the other code
      // the wizard can produce.
      await expect(page.getByRole("alert")).toHaveText(
        "The download client did not answer, or refused these credentials.",
      );
      await expect(page.getByRole("heading", { name: "Add a download client" })).toBeVisible();
      await expectNoAccessibilityViolations(page);

      await page.getByLabel("Port").fill(CLIENT_PORT);
      await page.getByRole("button", { name: "Continue" }).click();
      await expect(page.getByRole("heading", { name: "Review content filters" })).toBeVisible();
    });

    test("completes, and leaves an instance that can be signed into and used", async ({ page }) => {
      test.skip(!virgin, VIRGIN_ONLY);
      await passAccountStep(page);
      await passLibraryStep(page);

      await fillIndexer(page, INDEXER_URL);
      await page.getByRole("button", { name: "Continue" }).click();
      await expect(page.getByRole("heading", { name: "Add a download client" })).toBeVisible();

      await fillDownloadClient(page, CLIENT_PORT);
      await page.getByRole("button", { name: "Continue" }).click();

      // ADR 0017 puts every filtering decision on this screen, so the screen has
      // controls on it: one switch per rule, all six off, and no field until one
      // is turned on. Defect 3 was that it had no control of any kind and its own
      // copy said the rules were configured "after setup", which was nowhere.
      await expect(page.getByRole("heading", { name: "Review content filters" })).toBeVisible();
      await expect(page.getByRole("checkbox")).toHaveCount(6);
      await expect(page.getByRole("checkbox", { checked: true })).toHaveCount(0);
      await expect(page.getByRole("textbox")).toHaveCount(0);
      await expect(
        page.getByText(
          "Every filter starts off. Turn on the ones this instance should apply; they act on what is searched, grabbed and imported from now on, and never on what is already in the library.",
        ),
      ).toBeVisible();

      // A rule that is on asks what it matches, and one that is off asks nothing.
      // Turned back off again so this run still completes with the shipped
      // profile — what the wizard writes is `filters.spec.ts`'s subject.
      await page.getByRole("checkbox", { name: "Words and phrases" }).check();
      await expect(page.getByRole("textbox", { name: "Word or phrase" })).toBeVisible();
      await page.getByRole("checkbox", { name: "Words and phrases" }).uncheck();
      await expect(page.getByRole("textbox")).toHaveCount(0);
      await page.getByRole("button", { name: "Continue" }).click();

      await expect(page.getByRole("heading", { name: "Metadata providers" })).toBeVisible();
      // ADR 0005: the product runs with no metadata provider key at all, and the
      // step says so rather than making one mandatory.
      await expect(
        page.getByText("External metadata providers are optional. Pornarr can start without one."),
      ).toBeVisible();
      await page.getByRole("button", { name: "Skip for now" }).click();

      await expect(page.getByRole("heading", { name: "Review setup" })).toBeVisible();
      await expect(page.getByText(ADMIN_USERNAME, { exact: true })).toBeVisible();
      await expect(page.getByText(LIBRARY_PATH, { exact: true })).toBeVisible();
      await expect(page.getByText("All filters start off")).toBeVisible();
      await expect(page.getByText("No provider configured")).toBeVisible();
      await expectNoAccessibilityViolations(page);

      await page.getByRole("button", { name: "Complete setup" }).click();
      await expect(page.getByRole("heading", { name: "Setup complete" })).toBeVisible();
      await expectNoAccessibilityViolations(page);
      expect(await setupStatus(page)).toBe(true);

      // The administrator exists because signing in as it works, which is the
      // only claim the wizard actually made.
      await page.getByRole("link", { name: "Sign in" }).click();
      await page.getByLabel("Username").fill(ADMIN_USERNAME);
      await page.getByLabel("Password").fill(ADMIN_PASSWORD);
      await page.getByRole("button", { name: "Sign in" }).click();
      await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();

      type RootFolder = {
        readonly path: string;
        readonly enabled: boolean;
        readonly free_space_bytes: number;
        readonly same_filesystem_as_downloads: boolean;
      };
      const folders = await apiGet<RootFolder[]>(page, "/api/admin/library/root-folders");
      const folder = folders.find((item) => item.path === LIBRARY_PATH);
      expect(folder, `no root folder at ${LIBRARY_PATH}: ${JSON.stringify(folders)}`).toBeDefined();
      expect(folder?.enabled).toBe(true);
      // Radarr's RootFolderFixture makes the same claim of its own POST: a root
      // folder that reports no free space has not really been measured.
      expect(folder?.free_space_bytes).toBeGreaterThan(0);
      expect(folder?.same_filesystem_as_downloads).toBe(true);

      type Indexer = {
        readonly id: string;
        readonly name: string;
        readonly base_url: string;
        readonly health: string;
        readonly categories: readonly { id: string; name: string }[];
        readonly last_tested_at: string | null;
      };
      const indexers = await apiGet<Indexer[]>(page, "/api/admin/indexers");
      const indexer = indexers.find((item) => item.name === INDEXER_NAME);
      expect(
        indexer,
        `no indexer named ${INDEXER_NAME}: ${JSON.stringify(indexers)}`,
      ).toBeDefined();
      expect(indexer?.base_url).toBe(INDEXER_URL);
      expect(indexer?.health).toBe("healthy");
      expect(indexer?.last_tested_at).not.toBeNull();
      // The capabilities the live indexer answered with, kept rather than
      // re-guessed: a stored indexer with no categories can be searched for
      // nothing.
      expect(indexer?.categories.length).toBeGreaterThan(0);
      // The key went in and does not come back out: the response model has no
      // field for it at all.
      expect(indexer).not.toHaveProperty("api_key");

      type DownloadClient = {
        readonly id: string;
        readonly name: string;
        readonly host: string;
        readonly port: number;
        readonly health: string;
        readonly enabled: boolean;
      };
      const clients = await apiGet<DownloadClient[]>(page, "/api/admin/download-clients");
      const client = clients.find((item) => item.name === CLIENT_NAME);
      expect(
        client,
        `no download client named ${CLIENT_NAME}: ${JSON.stringify(clients)}`,
      ).toBeDefined();
      expect(client?.host).toBe(CLIENT_HOST);
      expect(client?.port).toBe(Number(CLIENT_PORT));
      expect(client?.health).toBe("healthy");
      expect(client?.enabled).toBe(true);
      expect(client).not.toHaveProperty("credentials");

      // ADR 0005: a working instance with no metadata provider configured at all.
      expect(await apiGet<unknown[]>(page, "/api/admin/metadata-providers")).toEqual([]);

      // Cleanup. The rest of the suite manages its own indexer and download
      // client by name through helpers.ts, and a second indexer pointing at the
      // same fake would put every release into its results twice.
      expect((await apiDelete(page, `/api/admin/indexers/${indexer?.id}`)).status()).toBe(204);
      expect((await apiDelete(page, `/api/admin/download-clients/${client?.id}`)).status()).toBe(
        204,
      );
    });
  });

/**
 * What the instance does about setup once setup is over. These run on their own
 * against any configured stack, so they are not part of the serial group above.
 */
test.describe("an instance that has already been set up", () => {
  test("sends a visitor who returns to /setup to the sign-in screen", async ({ page }) => {
    await waitForWebServer(page);
    test.skip(!(await setupStatus(page)), CONFIGURED_ONLY);

    await page.goto("/setup");

    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Set up Pornarr" })).toBeHidden();
    await expectNoAccessibilityViolations(page);
  });

  test("refuses a second administrator, and never creates one", async ({ page }) => {
    await waitForWebServer(page);
    test.skip(!(await setupStatus(page)), CONFIGURED_ONLY);

    // Straight at the endpoint, not through the wizard: the wizard will not
    // render the form any more, and the endpoint is exempt from CSRF because
    // nobody holds a session during a first run. This 409 is the whole of what
    // keeps that exemption safe, so it is asserted where an attacker would
    // stand rather than where the UI stands.
    const complete = await page.request.post("/api/setup/complete", {
      data: {
        username: SECOND_ADMIN_USERNAME,
        password: SECOND_ADMIN_PASSWORD,
        library_path: LIBRARY_PATH,
      },
    });
    expect(complete.status()).toBe(409);
    expect(await complete.json()).toMatchObject({ code: "SETUP_ALREADY_COMPLETED", status: 409 });

    // The rest of the first-run surface is closed too, so a configured instance
    // cannot be probed for paths it can read or for a working indexer.
    const validate = await page.request.post("/api/setup/validate-library-path", {
      data: { library_path: LIBRARY_PATH },
    });
    expect(validate.status()).toBe(409);
    const testIndexer = await page.request.post("/api/setup/test-indexer", {
      data: { implementation: "torznab", base_url: INDEXER_URL, api_key: INDEXER_API_KEY },
    });
    expect(testIndexer.status()).toBe(409);
    const testClient = await page.request.post("/api/setup/test-download-client", {
      data: {
        implementation: "qbittorrent",
        host: CLIENT_HOST,
        port: Number(CLIENT_PORT),
        credentials: JSON.stringify({ username: CLIENT_USERNAME, password: CLIENT_PASSWORD }),
      },
    });
    expect(testClient.status()).toBe(409);

    // And the account the refused request asked for does not exist, which is
    // the claim a status code alone does not make.
    await page.goto("/login");
    await page.getByLabel("Username").fill(SECOND_ADMIN_USERNAME);
    await page.getByLabel("Password").fill(SECOND_ADMIN_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    // Cause and next step, in that order: the sign-in screen states both, so
    // asserting only the cause would break the moment the step is reworded and
    // asserting the concatenation would read as one sentence.
    const alert = page.getByRole("alert");
    await expect(alert.getByText("That username and password do not match.")).toBeVisible();
    await expect(
      alert.getByText("Check the username and password, then sign in again."),
    ).toBeVisible();
  });

  test("reports every dependency, and compares the data paths' devices", async ({ page }) => {
    await waitForWebServer(page);
    test.skip(!(await setupStatus(page)), CONFIGURED_ONLY);

    // The report an operator is told to read, not the unconditional stub at
    // `/health`. Every component is named, because a health endpoint that only
    // answers with a status is one an operator cannot act on.
    const response = await page.request.get("/api/health");
    const report = (await response.json()) as HealthReport;
    expect(report.database.status).toBe("healthy");
    expect(report.redis.status).toBe("healthy");

    // ADR 0004: the six configured data paths are compared by `st_dev`. All of
    // them are under one mount on this stack, which is the arrangement the ADR
    // demands, so the check must decline to fire and say nothing. The other half
    // of the matrix — an instance whose paths really do straddle two devices —
    // is in `setup-probe.spec.ts`, which builds one.
    expect(report.filesystem).toEqual({ status: "healthy" });

    // The worker's heartbeat is deliberately not asserted healthy: four other
    // suites are editing source under `worker --watch`, and a worker that is
    // mid-reload has no heartbeat for a few seconds. What is asserted is the
    // rule `dependency_health` derives the overall verdict by (health.py:117-127),
    // which holds whatever the worker happens to be doing.
    expect(["healthy", "unhealthy"]).toContain(report.worker.status);
    if (report.worker.status === "unhealthy") {
      expect(report.worker.detail).toBe("worker heartbeat missing");
    }
    const degraded = report.redis.status === "unhealthy" || report.worker.status === "unhealthy";
    expect(report.status).toBe(degraded ? "degraded" : "healthy");
    // A degraded instance still answers 200: only the database and the
    // filesystem are allowed to take the whole report down.
    expect(response.status()).toBe(200);

    // The container probe and the documented report are the same answer. It was
    // a lambda returning `{"status": "ok"}` — an endpoint installation.md and
    // backup.md tell operators to verify a deployment with, that had no way to
    // say no.
    const probe = await page.request.get(`${API_URL}/health`);
    expect(probe.status()).toBe(response.status());
    expect(await probe.json()).toMatchObject({
      status: report.status,
      database: report.database,
      filesystem: report.filesystem,
    });
  });
});
