/**
 * First run, proven on instances this file creates and destroys itself.
 *
 * `setup.spec.ts` drives the wizard through the browser, and can only do that on
 * an instance nobody has configured — which the shared stack is exactly once in
 * its life. Everything here is the half of first run that needs a virgin server,
 * or a deliberately misconfigured one, and none of it needs a browser: a health
 * report whose data paths straddle two filesystems and the startup warning that
 * goes with it, a race between eight setup completions, the password rule the API
 * enforces, and what `APP_SECRET` does to a database that was encrypted with a
 * different one.
 *
 * So each group gets its own API. A scratch database in the stack's own Postgres
 * container, a migration, and a container from the image the stack is running,
 * on the same network, published on a port of its own. Nothing here touches the
 * `pornarr` database, the shared stack's containers or `data/`; every scratch
 * database, container and directory is removed in `afterAll`. That is what the
 * campaign's one-shared-stack rule asks for — the shared stack is never the
 * thing under test in this file.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

/** The Compose project whose Postgres, Redis, network and image the probes borrow. */
const PROJECT = process.env.E2E_COMPOSE_PROJECT ?? "pornarr_gauntlet";
const NETWORK = `${PROJECT}_default`;
const API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";
const REPO = process.cwd();

/** A probe is a migration and a container start before the first assertion. */
const PROBE_TIMEOUT_MILLISECONDS = 240_000;

const NO_DOCKER =
  "This file builds its own API instances with docker, and the stack's network did not " +
  "answer. The claims it proves need a virgin or a deliberately misconfigured server, " +
  "which the shared stack is not, and a live health report has no lower-level stand-in.";

function docker(...args: readonly string[]): string {
  return execFileSync("docker", args, { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
}

/** `docker`, with the exit status and both streams kept, for the cases that must fail. */
function dockerAttempt(...args: readonly string[]): { code: number; output: string } {
  try {
    return { code: 0, output: docker(...args) };
  } catch (error) {
    const failure = error as { status?: number; stdout?: Buffer; stderr?: Buffer };
    return {
      code: failure.status ?? -1,
      output: `${failure.stdout?.toString() ?? ""}${failure.stderr?.toString() ?? ""}`,
    };
  }
}

/**
 * The container's own log, kept apart by stream. Uvicorn writes its access log
 * to stdout and everything the application logs — including the lifespan's own
 * lines — to stderr, so a startup claim can only be made about stderr.
 */
function containerLog(name: string): { readonly stdout: string; readonly stderr: string } {
  const result = spawnSync("docker", ["logs", name], {
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  });
  return { stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
}

function detectDocker(): boolean {
  try {
    docker("network", "inspect", NETWORK, "--format", "{{.Name}}");
    docker("inspect", `${PROJECT}-api-1`, "--format", "{{.Config.Image}}");
    return true;
  } catch {
    return false;
  }
}

const DOCKER = detectDocker();

/** The image the stack itself is running, so a probe cannot test a stale build. */
function apiImage(): string {
  return docker("inspect", `${PROJECT}-api-1`, "--format", "{{.Config.Image}}").trim();
}

/** One statement against a database in the stack's Postgres, unaligned and headerless. */
function psql(database: string, sql: string): string {
  return execFileSync(
    "docker",
    [
      "compose",
      "-p",
      PROJECT,
      "exec",
      "-T",
      "postgres",
      "psql",
      "-U",
      "pornarr",
      "-d",
      database,
      "-At",
      "-c",
      sql,
    ],
    { encoding: "utf8" },
  ).trim();
}

/**
 * The sentence a person would read for a code, out of the locale files the
 * product ships.
 *
 * Read here rather than hardcoded twice: what these assertions claim is that a
 * code the *live* server just produced has a translated sentence in the shipped
 * build, not that two constants in two test files agree.
 */
type Locale = { errors: Record<string, string>; errorSteps: Record<string, string> };

function locale(name: "en" | "de"): Locale {
  return JSON.parse(
    readFileSync(join(REPO, "apps", "web", "src", "i18n", `${name}.json`), "utf8"),
  ) as Locale;
}

function englishFor(code: string): string {
  return locale("en").errors[code] ?? "";
}

function englishStepFor(code: string): string {
  return locale("en").errorSteps[code] ?? "";
}

/** What the product rendered before a code had a sentence of its own. */
function unknownCodeFallback(code: string): string {
  return locale("en").errors.unknown?.replace("{{code}}", code) ?? "";
}

type Probe = {
  /** Container name, scratch database suffix and log prefix, all the same word. */
  readonly name: string;
  readonly url: string;
  readonly database: string;
  readonly secret: string;
  readonly dataDirectory: string;
};

const probes: Probe[] = [];

/**
 * A migrated, freshly started API built from this working tree, on its own
 * database, its own Redis database and its own port.
 *
 * `crossFilesystemLibrary` mounts a tmpfs over `${DATA_PATH}/library`. tmpfs is
 * the one mount type guaranteed to be a different device from the directory it
 * is mounted inside, which is exactly the arrangement ADR 0004 tells operators
 * to avoid and the only way to make the `st_dev` check fire for real.
 */
function startProbe(options: {
  name: string;
  port: number;
  redisDatabase: number;
  crossFilesystemLibrary?: boolean;
}): Probe {
  const name = `pornarr_probe_${options.name}`;
  const database = `probe_${options.name}`;
  const secret = randomBytes(32).toString("hex");
  const dataDirectory = mkdtempSync(join(tmpdir(), `${name}-`));
  chmodSync(dataDirectory, 0o777);
  const probe: Probe = {
    name,
    url: `http://127.0.0.1:${options.port}`,
    database,
    secret,
    dataDirectory,
  };
  probes.push(probe);

  // Left over from a run that was killed rather than finished.
  dockerAttempt("rm", "-f", name);
  psql("postgres", `DROP DATABASE IF EXISTS ${database}`);
  psql("postgres", `CREATE DATABASE ${database}`);

  const environment = [
    "-e",
    `APP_SECRET=${secret}`,
    "-e",
    `DATABASE_URL=postgresql+psycopg://pornarr:pornarr@postgres:5432/${database}`,
    "-e",
    `REDIS_URL=redis://redis:6379/${options.redisDatabase}`,
    "-e",
    "DATA_PATH=/probe-data",
  ];
  const image = apiImage();

  docker(
    "run",
    "--rm",
    "--network",
    NETWORK,
    ...environment,
    "-v",
    `${REPO}/apps:/app/apps`,
    "-v",
    `${REPO}/packages:/app/packages`,
    "-v",
    `${REPO}/alembic:/app/alembic`,
    "-v",
    `${REPO}/alembic.ini:/app/alembic.ini`,
    image,
    "migrate",
  );

  docker(
    "run",
    "-d",
    "--name",
    name,
    "--network",
    NETWORK,
    "-p",
    `${options.port}:8000`,
    ...environment,
    "-v",
    `${REPO}/apps:/app/apps`,
    "-v",
    `${REPO}/packages:/app/packages`,
    "-v",
    `${dataDirectory}:/probe-data`,
    ...(options.crossFilesystemLibrary === true
      ? ["--tmpfs", "/probe-data/library:rw,mode=1777"]
      : []),
    image,
    "api",
  );
  return probe;
}

/** One reading of a probe's setup status, or `null` when it is not listening. */
async function probeStatus(probe: Probe): Promise<{ status: number; json: unknown } | null> {
  try {
    const response = await fetch(`${probe.url}/api/setup/status`);
    return { status: response.status, json: await response.json() };
  } catch {
    return null;
  }
}

async function waitForProbe(probe: Probe): Promise<void> {
  await expect
    .poll(
      async () => {
        try {
          return (await fetch(`${probe.url}/api/setup/status`)).status;
        } catch {
          return 0;
        }
      },
      {
        timeout: 120_000,
        intervals: [500],
        message: `${probe.name} never answered /api/setup/status`,
      },
    )
    .toBe(200);
}

function stopProbe(probe: Probe): void {
  dockerAttempt("rm", "-f", probe.name);
  try {
    psql("postgres", `DROP DATABASE IF EXISTS ${probe.database}`);
  } catch {
    // The stack may already be gone; the scratch database goes with it.
  }
  rmSync(probe.dataDirectory, { recursive: true, force: true });
}

test.afterAll(() => {
  while (probes.length > 0) {
    const probe = probes.pop();
    if (probe !== undefined) stopProbe(probe);
  }
});

type ComponentHealth = { readonly status: string; readonly detail?: string };
type HealthReport = {
  readonly status: string;
  readonly database: ComponentHealth;
  readonly redis: ComponentHealth;
  readonly worker: ComponentHealth;
  readonly filesystem: ComponentHealth;
};

async function completeSetup(
  probe: Probe,
  body: Record<string, unknown>,
): Promise<{ status: number; json: unknown }> {
  const response = await fetch(`${probe.url}/api/setup/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: response.status, json: await response.json() };
}

async function signIn(
  probe: Probe,
  username: string,
  password: string,
): Promise<{ status: number; json: unknown }> {
  const response = await fetch(`${probe.url}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return { status: response.status, json: await response.json() };
}

/** One rule per `FilterRuleKind`, which is what alembic 0004 seeds. Alphabetical. */
const FILTER_RULE_KINDS = [
  "minimum_confidence",
  "performer",
  "tag",
  "term",
  "unknown_file_type",
  "unknown_performer_age",
];

test.describe
  .serial("an unconfigured instance", () => {
    test.skip(!DOCKER, NO_DOCKER);
    let probe: Probe;

    test.beforeAll(async () => {
      test.setTimeout(PROBE_TIMEOUT_MILLISECONDS);
      probe = startProbe({ name: "virgin", port: 8101, redisDatabase: 10 });
      await waitForProbe(probe);
    });

    test("answers the routes first run needs and gates the rest, health report included", async () => {
      const status = await fetch(`${probe.url}/api/setup/status`);
      expect(status.status).toBe(200);
      expect(await status.json()).toEqual({ configured: false });

      // Which routes are exempt is itself the claim, so the gated half is
      // sampled across three routers rather than once, and the body is read: a
      // machine holding an API key is told why it was turned away, not merely
      // that it was.
      for (const path of ["/api/library", "/api/admin/indexers", "/api/queue", "/api/auth/me"]) {
        const gated = await fetch(`${probe.url}${path}`);
        expect(gated.status, `${path} should be gated until setup is done`).toBe(503);
        expect(await gated.json()).toEqual({ code: "SETUP_REQUIRED", status: 503, context: {} });
      }

      // The health report is deliberately *not* gated with them. installation.md
      // and backup.md tell an operator to verify a deployment by reading a health
      // report, and it used to answer `503 SETUP_REQUIRED` for exactly as long as
      // the deployment was unverified — which is also the moment the mounts it
      // checks are most likely to be wrong, and most cheaply fixed.
      const response = await fetch(`${probe.url}/api/health`);
      expect(response.status).toBe(200);
      const report = (await response.json()) as HealthReport;
      expect(report.database.status).toBe("healthy");
      expect(report.redis.status).toBe("healthy");
      // Every path this instance was given is under one tmpfs-free mount, so the
      // ADR 0004 comparison declines to fire and says nothing.
      expect(report.filesystem).toEqual({ status: "healthy" });

      // And `/health`, the address docker-compose.yml curls and backup.md ends a
      // restore with, is the same report rather than a literal.
      const containerProbe = await fetch(`${probe.url}/health`);
      expect(containerProbe.status).toBe(200);
      expect(await containerProbe.json()).toEqual(report);

      // Contradiction C7: the endpoint the operations documentation tells
      // operators to depend on is absent from the document ADR 0009 makes the
      // boundary of what exists.
      const schema = await fetch(`${probe.url}/api/openapi.json`);
      expect(schema.status).toBe(200);
      const paths = Object.keys(
        ((await schema.json()) as { paths: Record<string, unknown> }).paths,
      );
      expect(paths).toContain("/api/health");
      expect(paths).not.toContain("/health");
    });

    test("ships every content filter rule disabled and empty, before any request", async () => {
      // Read out of the database the migration just built, so this is what the
      // product ships rather than what some request left behind. ADR 0018 says
      // every rule ships disabled *and empty*, and the pattern is the half an
      // enabled flag hides. `kind::text` because a Postgres enum orders by
      // declaration, and the assertion should not depend on that order.
      const rules = psql(
        probe.database,
        "SELECT r.kind, r.enabled, r.action, '[' || r.pattern || ']' " +
          "FROM content_filter_profiles p JOIN content_filter_rules r ON r.profile_id = p.id " +
          "WHERE p.scope = 'global' ORDER BY r.kind::text",
      )
        .split("\n")
        .filter((line) => line !== "");
      expect(rules).toEqual(FILTER_RULE_KINDS.map((kind) => `${kind}|f|reject|[]`));

      // ADR 0017: no built-in blocklist. One global profile, nothing enabled,
      // and nothing else on the instance that could filter anything.
      expect(psql(probe.database, "SELECT count(*) FROM content_filter_profiles")).toBe("1");
      expect(psql(probe.database, "SELECT count(*) FROM content_filter_rules WHERE enabled")).toBe(
        "0",
      );

      // ADR 0005: no metadata provider key configured at all — and no indexer,
      // client or root folder either, because none of them comes from the
      // environment. Nothing has been asked of this server except its status.
      for (const table of [
        "users",
        "metadata_providers",
        "indexers",
        "download_clients",
        "root_folders",
      ]) {
        expect(
          psql(probe.database, `SELECT count(*) FROM ${table}`),
          `${table} on an instance nobody has configured`,
        ).toBe("0");
      }
    });

    test("verifies every target it is handed before setup can be completed", async () => {
      // Radarr's RootFolderFixture, IndexerFixture and DownloadClientFixture,
      // against live targets rather than a schema — and asserting the reason,
      // which the wizard's one sentence flattens and Radarr's fixtures do not
      // check at all. `setup.spec.ts` proves what the operator is *shown*; this
      // proves what the endpoints actually distinguish.
      const validate = async (libraryPath: string) => {
        const response = await fetch(`${probe.url}/api/setup/validate-library-path`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ library_path: libraryPath }),
        });
        return { status: response.status, json: await response.json() };
      };

      expect(await validate("/nope/not/here")).toEqual({
        status: 422,
        json: expect.objectContaining({
          code: "SETUP_PATH_INVALID",
          status: 422,
          context: { reason: "unavailable" },
        }),
      });
      // A path that exists but is a file, which is a different failure from a
      // path that is not there.
      expect((await validate("/etc/hostname")).json).toMatchObject({
        code: "SETUP_PATH_INVALID",
        context: { reason: "not_directory" },
      });
      // And a directory the API can read but not write. Made here rather than
      // found, because a container that runs as uid 1000 has no such directory
      // by accident — and `not_accessible` is the reason an operator hits when
      // they point the wizard at a mount with the wrong ownership.
      mkdirSync(join(probe.dataDirectory, "read-only"), { mode: 0o555 });
      chmodSync(join(probe.dataDirectory, "read-only"), 0o555);
      expect((await validate("/probe-data/read-only")).json).toMatchObject({
        code: "SETUP_PATH_INVALID",
        context: { reason: "not_accessible" },
      });

      expect(await validate("/probe-data/library")).toEqual({
        status: 200,
        json: { same_filesystem_as_downloads: true, warning: null },
      });

      const testIndexer = async (baseUrl: string) => {
        const response = await fetch(`${probe.url}/api/setup/test-indexer`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ implementation: "torznab", base_url: baseUrl, api_key: "fake" }),
        });
        return { status: response.status, json: await response.json() };
      };

      // The same host on a port nothing listens on: refused at once, so this is
      // a real failed connection rather than a resolver timeout.
      expect((await testIndexer("http://fake-indexer:9999/api")).json).toMatchObject({
        code: "INDEXER_CONNECTION_FAILED",
        status: 422,
      });
      // The live fake indexer, and the capabilities it actually answered with: a
      // test that returned no categories has not really talked to anything.
      const indexer = await testIndexer("http://fake-indexer:9117/api");
      expect(indexer.status).toBe(200);
      const categories = (indexer.json as { categories: { id: string; name: string }[] })
        .categories;
      expect(categories.length).toBeGreaterThan(0);
      expect(categories[0]).toEqual({ id: expect.any(String), name: expect.any(String) });

      const testClient = async (port: number) => {
        const response = await fetch(`${probe.url}/api/setup/test-download-client`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            implementation: "qbittorrent",
            host: "qbittorrent",
            port,
            credentials: JSON.stringify({ username: "admin", password: "adminadmin" }),
          }),
        });
        return {
          status: response.status,
          json: response.status === 204 ? null : await response.json(),
        };
      };

      expect((await testClient(9999)).json).toMatchObject({
        code: "DOWNLOAD_CLIENT_CONNECTION_FAILED",
        status: 422,
      });
      // The live qBittorrent. 204: it logged in, and there is nothing to say.
      expect((await testClient(8080)).status).toBe(204);

      // None of that configured anything. The endpoints test; only /complete
      // stores.
      expect(psql(probe.database, "SELECT count(*) FROM indexers")).toBe("0");
      expect(psql(probe.database, "SELECT count(*) FROM download_clients")).toBe("0");
      expect(psql(probe.database, "SELECT count(*) FROM root_folders")).toBe("0");
    });

    test("has a translated cause and next step for every code it just produced", async () => {
      // PRODUCT.md: "Errors state the cause and the next step, never just that
      // something failed." Both codes above were among the 36 of 50 with no entry
      // in `ERROR_CODES`, so the wizard rendered
      // "Something went wrong (INDEXER_CONNECTION_FAILED)." — a code, a full stop,
      // and nothing an operator can act on.
      //
      // The codes here are the ones the *live* server answered with two cases
      // earlier, checked against the locale files the build ships. The complete
      // walk over every backend code is `apps/web/src/lib/api-error-codes.test.ts`;
      // this is the end of that chain that a real server is standing at.
      for (const code of [
        "INDEXER_CONNECTION_FAILED",
        "DOWNLOAD_CLIENT_CONNECTION_FAILED",
        "SETUP_PATH_INVALID",
        "SETUP_REQUIRED",
      ]) {
        const cause = englishFor(code);
        const step = englishStepFor(code);
        expect(cause, `no sentence for ${code}`).not.toBe("");
        expect(step, `no next step for ${code}`).not.toBe("");
        expect(cause, `${code} still renders the unknown-code fallback`).not.toBe(
          unknownCodeFallback(code),
        );
        expect(cause).not.toContain("{{code}}");
        // German too — the whole reason the contract is a code rather than a
        // server-supplied English string.
        expect(locale("de").errors[code], `${code} is untranslated in German`).toBeTruthy();
        expect(locale("de").errorSteps[code], `${code} has no German next step`).toBeTruthy();
      }

      // And the two the wizard shows, verbatim, so a reworded sentence has to be
      // reworded here too rather than passing on being non-empty.
      expect(englishFor("INDEXER_CONNECTION_FAILED")).toBe(
        "The indexer did not answer, or answered with something unusable.",
      );
      expect(englishStepFor("INDEXER_CONNECTION_FAILED")).toBe(
        "Check the base URL and API key, and that the indexer is running, then test it again.",
      );
      expect(englishFor("DOWNLOAD_CLIENT_CONNECTION_FAILED")).toBe(
        "The download client did not answer, or refused these credentials.",
      );
      expect(englishStepFor("DOWNLOAD_CLIENT_CONNECTION_FAILED")).toBe(
        "Check the host, port and credentials, and that the client is running, then test it again.",
      );
    });

    test("creates exactly one administrator when eight setup completions race", async () => {
      // The guard in routers/setup.py:176-183 takes `FOR UPDATE` on the global
      // filter profile and then asks whether a user exists. Round 1 of this
      // piece reported, from reading alone, that the lock takes nothing on a
      // virgin instance because the profile row does not exist yet. It does
      // exist — alembic 0004 seeds it inside `upgrade()`, which the test above
      // reads out of the database before any request is made. This is the
      // reproduction that retracts that report.
      const attempts = [1, 2, 3, 4, 5, 6, 7, 8];
      const results = await Promise.all(
        attempts.map((n) =>
          completeSetup(probe, {
            username: `race-${n}`,
            password: "Race-Gauntlet-2026!",
            library_path: "/probe-data/library",
          }),
        ),
      );

      const created = results.filter((result) => result.status === 201);
      const refused = results.filter((result) => result.status === 409);
      expect(created.length, `statuses: ${results.map((result) => result.status).join(",")}`).toBe(
        1,
      );
      expect(refused).toHaveLength(7);
      for (const result of refused) {
        expect(result.json).toMatchObject({ code: "SETUP_ALREADY_COMPLETED", status: 409 });
      }

      // The status codes are what the eight callers were told. This is what the
      // database holds, which is the claim.
      const winner = (created[0]?.json as { username: string }).username;
      expect(psql(probe.database, "SELECT count(*) FROM users")).toBe("1");
      expect(psql(probe.database, "SELECT username FROM users")).toBe(winner);
      // The seven refusals rolled back whole: `/complete` creates an automation
      // rule and a root folder alongside the administrator, and there is one of
      // each rather than eight.
      expect(psql(probe.database, "SELECT count(*) FROM automation_rules")).toBe("1");
      expect(psql(probe.database, "SELECT count(*) FROM root_folders")).toBe("1");
      // `if profile is None` in complete_setup would have inserted a second
      // global profile. It is dead code on a migrated database; this says so.
      expect(psql(probe.database, "SELECT count(*) FROM content_filter_profiles")).toBe("1");

      // And an account one of the refusals asked for is not an account. One
      // attempt, not seven: six failures from one address trip the login rate
      // limiter (auth.py:32) and a 429 would prove nothing about the user.
      const loser = attempts.map((n) => `race-${n}`).find((username) => username !== winner);
      const login = await signIn(probe, loser ?? "race-1", "Race-Gauntlet-2026!");
      expect(login.status).toBe(401);
      expect(login.json).toMatchObject({ code: "INVALID_CREDENTIALS", status: 401 });
    });
  });

test.describe
  .serial("an instance whose data paths straddle two filesystems", () => {
    test.skip(!DOCKER, NO_DOCKER);
    let probe: Probe;

    test.beforeAll(async () => {
      test.setTimeout(PROBE_TIMEOUT_MILLISECONDS);
      probe = startProbe({
        name: "mismatch",
        port: 8102,
        redisDatabase: 11,
        crossFilesystemLibrary: true,
      });
      await waitForProbe(probe);
      // The health report is behind the setup gate, so there is no way to read
      // it about an unconfigured instance. See defect 4.
      const completed = await completeSetup(probe, {
        username: "probe",
        password: "Probe-Gauntlet-2026!",
        library_path: "/probe-data/library",
      });
      expect(completed.status).toBe(201);
      expect(completed.json).toMatchObject({
        same_filesystem_as_downloads: false,
        warning: "different filesystem from downloads; imports cannot hardlink",
      });
    });

    test("names both mounts in the health report, and fails the whole report on it", async () => {
      // The devices, from the kernel, before any claim about them: five of the
      // six data directories are on the container's own layer and `library` is a
      // tmpfs, so the mismatch is a property of this instance rather than a
      // fixture asserting itself.
      const devices = Object.fromEntries(
        docker(
          "exec",
          probe.name,
          "/app/.venv/bin/python",
          "-c",
          "import os\n" +
            "for d in ['torrents','usenet','library','quarantine','thumbnails','transcodes']:\n" +
            "    print(d, os.stat('/probe-data/' + d).st_dev)\n",
        )
          .trim()
          .split("\n")
          .map((line) => line.split(" ") as [string, string]),
      );
      expect(devices.library).not.toBe(devices.torrents);
      for (const directory of ["usenet", "quarantine", "thumbnails", "transcodes"]) {
        expect(devices[directory], `${directory} should share the data mount`).toBe(
          devices.torrents,
        );
      }

      const response = await fetch(`${probe.url}/api/health`);
      const report = (await response.json()) as HealthReport;

      // ADR 0004 asks for a comparison of `st_dev` across the configured paths
      // that warns loudly when they differ. This is the loud part: the path it
      // measured everything against, and the one that disagrees — the difference
      // between a warning an operator can act on and one that leaves them
      // guessing which of six directories is on the wrong device.
      expect(report.filesystem).toEqual({
        status: "unhealthy",
        detail: "separate mounts: /probe-data/torrents and /probe-data/library",
      });
      // Nothing but the filesystem check turned this report red. A 503 with a
      // healthy filesystem would have proven nothing about ADR 0004.
      expect(report.database.status).toBe("healthy");
      expect(report.redis.status).toBe("healthy");
      expect(report.status).toBe("unhealthy");
      expect(response.status).toBe(503);
    });

    test("warns loudly at startup, before it has served a single request", async () => {
      // ADR 0004 L12, deployment.md L31-32 and troubleshooting.md L4-6 all
      // describe a *startup* check that compares the device identifiers and warns
      // loudly. There was none: this instance used to reach `running` with nothing
      // in its log, and the mismatch was named only in the body of a health
      // response nobody had asked for yet — while troubleshooting.md was telling
      // its operator to go and read the startup warning.
      expect(docker("inspect", probe.name, "--format", "{{.State.Status}}").trim()).toBe("running");

      const log = containerLog(probe.name);
      const [startup, afterwards] = log.stderr.split("Application startup complete");
      expect(afterwards, "the API never reported itself started").toBeDefined();
      expect(startup).toContain("api started in development mode");

      const warnings = (startup ?? "")
        .split("\n")
        .filter((line) => line.includes("separate mounts"));
      expect(warnings, "no startup line names the mount mismatch").toHaveLength(1);
      // Which two of the six paths disagree, not merely that two of them do, and
      // what the consequence is — the difference between a warning an operator
      // can act on and one that leaves them guessing.
      // At warning level, not info: troubleshooting.md sends operators looking for
      // a warning, and a line they have to have `LOG_LEVEL=debug` to see is not one.
      expect(warnings[0]).toContain('"level":"warning"');
      expect(warnings[0]).toContain("/probe-data/torrents");
      expect(warnings[0]).toContain("/probe-data/library");
      expect(warnings[0]).toContain("hardlink");
      // Before the application announced itself ready, which is what makes it a
      // startup check rather than a late log line.
      expect((startup ?? "").indexOf("separate mounts")).toBeLessThan(
        (startup ?? "").indexOf("api started in development mode"),
      );
    });

    test("fails the container probe too, so an orchestrator sees it", async () => {
      // `/health` is what docker-compose.yml curls and what installation.md and
      // backup.md tell operators to verify a deployment with. It was
      // `lambda: {"status": "ok"}` — proven here, on the one genuinely unhealthy
      // instance in this suite, to have had no way of saying no. An orchestrator
      // watching it would have kept this instance in service for ever.
      const containerProbe = await fetch(`${probe.url}/health`);
      expect(containerProbe.status).toBe(503);
      const body = (await containerProbe.json()) as HealthReport;
      expect(body.status).toBe("unhealthy");
      expect(body.filesystem).toEqual({
        status: "unhealthy",
        detail: "separate mounts: /probe-data/torrents and /probe-data/library",
      });

      const report = await fetch(`${probe.url}/api/health`);
      expect(report.status).toBe(503);
      expect(await report.json()).toEqual(body);
    });
  });

test.describe
  .serial("credentials an instance was configured with", () => {
    test.skip(!DOCKER, NO_DOCKER);
    const PROVIDER_KEY = "probe-stashdb-key-2026";
    /**
     * Passwords the account step's rule refuses, each for a different half of it:
     * one character, eleven characters, and a long passphrase from one character
     * class. The rule is twelve characters from at least two of lower case, upper
     * case, digits and symbols.
     */
    const REFUSED_PASSWORDS = ["x", "Short1Weak!", "correct horse battery staple"];
    /** Twelve characters, three classes. What the wizard would let through. */
    const ADMIN_PASSWORD = "Probe-Gauntlet-2026!";
    let probe: Probe;

    test.beforeAll(async () => {
      test.setTimeout(PROBE_TIMEOUT_MILLISECONDS);
      probe = startProbe({ name: "secret", port: 8103, redisDatabase: 12 });
      await waitForProbe(probe);
    });

    test("refuses an administrator password the wizard would refuse, and creates nothing", async () => {
      // The rule the account step states used to be enforced only by
      // `passwordStrength` in the browser (setup-route.tsx:53-58).
      // `/api/setup/complete` is exempt from CSRF, because nobody holds a session
      // during a first run, so anything that could see the port could post a
      // one-character password and then sign in with it. Reproduced by this file
      // before it was fixed: `201`, then `200 {"username":"weak","role":"admin"}`.
      for (const password of REFUSED_PASSWORDS) {
        const refused = await completeSetup(probe, {
          username: "weak",
          password,
          library_path: "/probe-data/library",
        });
        expect(refused.status, `${password.length} characters should be refused`).toBe(422);
        // Its own code, so the frontend can say what the rule is and what to do
        // about it. A bare 422 VALIDATION_FAILED renders "check the highlighted
        // fields" on a request that has no fields on screen.
        expect(refused.json).toEqual({
          code: "SETUP_PASSWORD_TOO_WEAK",
          status: 422,
          context: { minimum_length: 12, minimum_character_classes: 2 },
        });
      }

      // Refused is not the claim; unusable is. No administrator, and no session
      // to be had from one.
      expect(psql(probe.database, "SELECT count(*) FROM users")).toBe("0");
      expect(psql(probe.database, "SELECT count(*) FROM root_folders")).toBe("0");
      expect((await signIn(probe, "weak", REFUSED_PASSWORDS[0] as string)).status).toBe(503);

      // And the sentence a person reads for that code is a cause and a next step,
      // not "Something went wrong (SETUP_PASSWORD_TOO_WEAK)."
      expect(englishFor("SETUP_PASSWORD_TOO_WEAK")).toBe(
        "That administrator password is shorter than 12 characters, or uses only one kind of character.",
      );
      expect(englishStepFor("SETUP_PASSWORD_TOO_WEAK")).toBe(
        "Use at least 12 characters from two kinds: lower case, upper case, digits, symbols.",
      );
    });

    test("takes the password the wizard would take, and signs in with it", async () => {
      // The other side of the boundary, so the refusals above are the rule rather
      // than a wall. This is also what configures the instance the rest of this
      // group reads credentials out of.
      const completed = await completeSetup(probe, {
        username: "operator",
        password: ADMIN_PASSWORD,
        library_path: "/probe-data/library",
        metadata_provider: { implementation: "stashdb", api_key: PROVIDER_KEY },
      });
      expect(completed.status).toBe(201);
      expect(completed.json).toMatchObject({ username: "operator" });

      // Polled past a 503. A sign-in writes a session, so it needs the probe's
      // Redis as well as its database, and a container that has only just
      // answered `/api/setup/status` may still be reaching for one of them -
      // "not ready" is what 503 means and what a poll is for. Any other status
      // is reported as itself.
      let login = await signIn(probe, "operator", ADMIN_PASSWORD);
      for (let attempt = 0; attempt < 20 && login.status === 503; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        login = await signIn(probe, "operator", ADMIN_PASSWORD);
      }
      expect(login.status).toBe(200);
      expect(login.json).toMatchObject({ username: "operator", role: "admin" });
    });

    test("stores the provider key as ciphertext, not as the operator typed it", async () => {
      // deployment.md L75-78 and ADR 0005 L9: integration credentials are
      // entered by the administrator and stored encrypted. Read as the database
      // holds it, not through the model that decrypts on the way out.
      const [implementation, ciphertext] = psql(
        probe.database,
        "SELECT implementation, api_key FROM metadata_providers",
      ).split("|");
      expect(implementation).toBe("stashdb");
      expect(ciphertext).not.toBe(PROVIDER_KEY);
      expect(ciphertext).not.toContain(PROVIDER_KEY);
      // `v1:` is the version prefix `CredentialCipher` writes; without it a
      // later secret rotation cannot tell its own output apart.
      expect(ciphertext).toMatch(/^v1:/);
      expect((ciphertext ?? "").length).toBeGreaterThan(PROVIDER_KEY.length);
    });

    test("cannot read that credential back without the secret it was written with", async () => {
      const ciphertext = psql(probe.database, "SELECT api_key FROM metadata_providers");
      const wrongSecret = randomBytes(32).toString("hex");
      const script =
        "import sys\n" +
        "from pornarr_shared.crypto import CredentialCipher\n" +
        "from pornarr_shared.errors import DecryptionError\n" +
        "ciphertext, right, wrong = sys.argv[1], sys.argv[2], sys.argv[3]\n" +
        "print('right:' + CredentialCipher(right).decrypt(ciphertext))\n" +
        "try:\n" +
        "    print('wrong:' + CredentialCipher(wrong).decrypt(ciphertext))\n" +
        "except DecryptionError as error:\n" +
        "    print('wrong-refused:' + str(error))\n";
      const output = docker(
        "run",
        "--rm",
        "--entrypoint",
        "/app/.venv/bin/python",
        "-v",
        `${REPO}/packages:/app/packages`,
        apiImage(),
        "-c",
        script,
        ciphertext,
        probe.secret,
        wrongSecret,
      );

      // backup.md L9-12: a database restored without the original secret has
      // unreadable credentials. Both halves, because "cannot be decrypted" is
      // only a claim if the same ciphertext decrypts with the right secret.
      expect(output).toContain(`right:${PROVIDER_KEY}`);
      expect(output).not.toContain(`wrong:${PROVIDER_KEY}`);
      expect(output).toContain("wrong-refused:Stored credential could not be decrypted.");
      expect(output).toContain("APP_SECRET does not match");
    });

    test("refuses to start against a database written with a different APP_SECRET", async () => {
      // backup.md L60-61: the API refuses to start with a clear error rather
      // than letting integrations fail later. Same database, same code, one
      // configuration value different.
      const attempt = dockerAttempt(
        "run",
        "--rm",
        "--network",
        NETWORK,
        "-e",
        `APP_SECRET=${randomBytes(32).toString("hex")}`,
        "-e",
        `DATABASE_URL=postgresql+psycopg://pornarr:pornarr@postgres:5432/${probe.database}`,
        "-e",
        "REDIS_URL=redis://redis:6379/12",
        "-e",
        "DATA_PATH=/probe-data",
        "-v",
        `${REPO}/apps:/app/apps`,
        "-v",
        `${REPO}/packages:/app/packages`,
        "-v",
        `${probe.dataDirectory}:/probe-data`,
        apiImage(),
        "api",
      );

      expect(attempt.code, `the API started anyway:\n${attempt.output}`).not.toBe(0);
      expect(attempt.output).toContain(
        "APP_SECRET does not match the database. Restore the APP_SECRET that was " +
          "backed up with this database before starting Pornarr.",
      );
      expect(attempt.output).toContain("Application startup failed");
      // It never gets as far as serving anything, which is the difference
      // between this and an integration that fails on first use.
      expect(attempt.output).not.toContain("Application startup complete");

      // And the original secret still runs against the same database, so what
      // refused was the secret rather than the state the database is in.
      // Retried while the container comes back. It was just restarted with the
      // original secret, and a socket that is not listening yet answers by
      // closing - "not ready", which is what `waitForProbe` above polls for at
      // every other start.
      let again = await probeStatus(probe);
      for (let attempt = 0; attempt < 40 && again === null; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        again = await probeStatus(probe);
      }
      expect(again, `${probe.name} never came back after the secret was restored.`).not.toBeNull();
      expect((again as { status: number }).status).toBe(200);
      expect((again as { json: unknown }).json).toEqual({ configured: true });
    });
  });

/**
 * What the repository ships, rather than what a running instance does: claims
 * about the Compose file, the Makefile and the environment surface.
 */
test.describe("the shipped deployment", () => {
  test("brings up PostgreSQL 17 and Redis 8, so no database has to be installed", () => {
    test.skip(!DOCKER, NO_DOCKER);
    // installation.md L13-15 promises the versions, not just "a database". The
    // running servers are asked, because a pinned tag in a YAML file is a
    // promise and the server is the fact.
    const version = psql("pornarr", "SELECT version()");
    expect(version, `PostgreSQL 17 is the claim: ${version}`).toMatch(/^PostgreSQL 17\./);

    const info = execFileSync(
      "docker",
      ["compose", "-p", PROJECT, "exec", "-T", "redis", "redis-cli", "INFO", "server"],
      { encoding: "utf8" },
    );
    const redisVersion = /^redis_version:(\S+)$/m.exec(info)?.[1];
    expect(redisVersion, `Redis 8 is the claim: ${redisVersion}`).toMatch(/^8\./);

    // And both are pinned in the Compose file the documentation tells the
    // operator to use, not only in whatever this machine happens to be running.
    const compose = readFileSync(join(REPO, "docker-compose.yml"), "utf8");
    expect(compose).toContain("image: postgres:17-alpine");
    expect(compose).toContain("image: redis:8-alpine");
  });

  test("make setup writes .env with a generated APP_SECRET, and never overwrites one", () => {
    // installation.md L28. Run in a copy of the tree, so a developer's own .env
    // is neither read nor written.
    const scratch = mkdtempSync(join(tmpdir(), "pornarr-make-setup-"));
    try {
      for (const file of ["Makefile", ".env.example"]) {
        writeFileSync(join(scratch, file), readFileSync(join(REPO, file)));
      }
      // The example ships the variable empty: the secret is generated per
      // instance, never a default every deployment would share.
      expect(readFileSync(join(scratch, ".env.example"), "utf8")).toMatch(/^APP_SECRET=$/m);

      execFileSync("make", ["setup"], { cwd: scratch, encoding: "utf8" });

      expect(existsSync(join(scratch, ".env"))).toBe(true);
      const generated = /^APP_SECRET=(.*)$/m.exec(readFileSync(join(scratch, ".env"), "utf8"))?.[1];
      // `openssl rand -hex 32`: 32 bytes as 64 hex characters, above the
      // 32-character minimum `Settings._secret_is_long_enough` refuses below.
      expect(generated, "no APP_SECRET was written").toMatch(/^[0-9a-f]{64}$/);

      // Running it again must not rotate the secret. Every stored credential is
      // encrypted with it, and the previous test in this file is what happens
      // when it changes.
      execFileSync("make", ["setup"], { cwd: scratch, encoding: "utf8" });
      expect(/^APP_SECRET=(.*)$/m.exec(readFileSync(join(scratch, ".env"), "utf8"))?.[1]).toBe(
        generated,
      );
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  });

  test("keeps indexers, clients, providers, profiles and filters out of the environment", () => {
    // deployment.md L75-78: only bootstrap values live in the environment, and
    // the five things it names are configured in the UI. The falsifiable half is
    // the negative space, so both the example file and the settings model are
    // searched for a variable that would configure one of them.
    const configuredInTheUi = /indexer|download_?client|metadata|provider|quality|profile|filter/i;

    const exampleKeys = [
      ...readFileSync(join(REPO, ".env.example"), "utf8").matchAll(/^([A-Z0-9_]+)=/gm),
    ].map((match) => match[1] as string);
    expect(exampleKeys.length).toBeGreaterThan(10);
    expect(exampleKeys).toContain("APP_SECRET");
    expect(exampleKeys.filter((key) => configuredInTheUi.test(key))).toEqual([]);

    // The model is the real surface: a variable absent from the example file is
    // still read if `Settings` declares it.
    const fields = [
      ...readFileSync(join(REPO, "packages/shared/pornarr_shared/config.py"), "utf8").matchAll(
        /^ {4}([a-z][a-z0-9_]*): \S/gm,
      ),
    ].map((match) => match[1] as string);
    expect(fields).toContain("app_secret");
    expect(fields).toContain("database_url");
    // Three fields name one of the five, and all three are scoring weights for
    // the automatic-download ranker rather than configuration of anything: no
    // credential, no URL, no enablement. Listed exactly, so an `INDEXER_API_KEY`
    // or a `STASHDB_KEY` appearing in the environment breaks this line.
    expect(fields.filter((field) => configuredInTheUi.test(field))).toEqual([
      "auto_download_metadata_weight",
      "auto_download_indexer_reliability_weight",
      "recommendation_quality_weight",
    ]);
  });

  test("exposes somewhere to configure a filter profile, which ADR 0017 needs", async () => {
    // Contradiction C8, re-read. ADR 0017 L9 says all filtering is configured by
    // the operator; when this piece first ran there was no endpoint to configure
    // it with, at global or user scope, anywhere in the contract, and the wizard's
    // filter step was read-only. The endpoint half has since been built by another
    // builder, so this asserts what the contract now has rather than the hole it
    // used to have.
    const response = await fetch(`${API_URL}/api/openapi.json`);
    expect(response.status).toBe(200);
    const contract = (await response.json()) as {
      paths: Record<string, Record<string, unknown>>;
    };
    expect(Object.keys(contract.paths).length).toBeGreaterThan(100);
    // Named exactly, now that the route has settled. When this piece first ran
    // the contract carried nothing matching /filter/i at all, at global or user
    // scope, which is why ADR 0017 L9 — "all filtering is configured by the
    // operator in the setup wizard" — could not hold: there was nowhere to
    // configure it, during setup or after. A loose /filter/i match would go on
    // passing if the route were renamed to something the wizard does not call,
    // which is the failure this test exists to catch.
    expect(Object.keys(contract.paths)).toContain("/api/admin/filters/profile");
    expect(Object.keys(contract.paths["/api/admin/filters/profile"] ?? {}).sort()).toEqual([
      "get",
      "put",
    ]);
  });
});
