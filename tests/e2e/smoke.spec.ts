/**
 * What the pipeline runs the stack for, and nothing beyond it.
 *
 * The end-to-end suite is a local instrument: it drives fourteen flows against
 * real containers, takes a quarter of an hour on a developer machine and the
 * better part of an hour on the runner, and several of its cases cannot be run
 * there at all. Paying that on every pull request bought a red pipeline nobody
 * could act on. See `docs/contributing/testing.md` for how to run the whole
 * suite, which is where its findings belong.
 *
 * What a pull request does need from a real stack is the one thing no unit test
 * can tell it: that the containers come up and the product serves. #300 shipped
 * an entrypoint the runtime image could not execute, and every check was green.
 * That is the class of failure this file exists to catch, and it is cheap:
 * `docker compose up --wait` has already gated on the healthchecks by the time
 * these run, so all that is left is to ask the application itself.
 */
import { expect, test } from "@playwright/test";
import { loginAsAdmin, waitForWebServer } from "./helpers";

type ComponentHealth = { readonly status: string; readonly detail?: string };
type HealthReport = {
  readonly status: string;
  readonly database: ComponentHealth;
  readonly redis: ComponentHealth;
  readonly worker: ComponentHealth;
  readonly filesystem: ComponentHealth;
};

test.describe("smoke", () => {
  test("the API is up and reaches its own dependencies", async ({ page }) => {
    await waitForWebServer(page);

    const response = await page.request.get("/api/health");
    // 503 is the report's own answer for unhealthy, so it is a readable
    // failure rather than a transport one: read the body either way.
    const report = (await response.json()) as HealthReport;
    expect(
      report.status,
      `The stack came up and reports ${report.status}: ` +
        `database ${report.database.status}, redis ${report.redis.status}, ` +
        `worker ${report.worker.status}, filesystem ${report.filesystem.status}.`,
    ).not.toBe("unhealthy");
    expect(response.status()).toBe(200);

    // The worker is a separate container from the API and answers only through
    // a heartbeat it writes to Redis. Named on its own because "the API is up"
    // has been true with every worker dead behind it.
    expect(report.worker.status, "The API is up and no worker is behind it.").toBe("healthy");
  });

  test("the interface is served and an administrator can sign in", async ({ page }) => {
    // Sets the instance up when it is virgin, which on a stack this workflow
    // has just created it always is -- so this drives the first-run wizard end
    // to end as well, and a build whose migrations did not run cannot pass it.
    await loginAsAdmin(page);
    await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
  });

  test("an unknown client route is the SPA, not a 404 from the API", async ({ page }) => {
    // The single-page application and the API are one origin and one server.
    // A build that mounts the static files wrongly serves this as JSON, which
    // no unit test sees because it is the container's own layout that decides.
    const response = await page.goto("/library/some/deep/route");
    expect(response?.status()).toBe(200);
    expect(response?.headers()["content-type"]).toContain("text/html");
  });
});
