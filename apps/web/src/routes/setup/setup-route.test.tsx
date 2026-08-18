/** The first-run route is tested through the real router and generated API client. */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, test, vi } from "vitest";
import { renderApp, server, useMockApi } from "../../test/harness";

useMockApi();
afterEach(cleanup);

function unconfiguredInstance(): void {
  server.use(http.get("/api/setup/status", () => HttpResponse.json({ configured: false })));
}

async function reachLibraryStep(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(await screen.findByLabelText("Username"), "ada");
  await user.type(screen.getByLabelText("Password"), "Correct horse battery staple! 2026");
  await user.keyboard("{Tab}{Enter}");

  expect(await screen.findByRole("heading", { name: "Choose the library path" })).toBeTruthy();
}

/** Fills and submits the library path, assuming it validates cleanly. */
async function passLibraryStep(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  server.use(
    http.post("/api/setup/validate-library-path", () =>
      HttpResponse.json({ same_filesystem_as_downloads: true, warning: null }),
    ),
  );
  await reachLibraryStep(user);
  await user.type(screen.getByLabelText("Library path"), "/media/library");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  expect(await screen.findByRole("heading", { name: "Add a search indexer" })).toBeTruthy();
}

/** Past the library step and both integration steps, without configuring either. */
async function reachFiltersStep(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await passLibraryStep(user);
  await user.click(screen.getByRole("button", { name: "Skip for now" }));
  expect(await screen.findByRole("heading", { name: "Add a download client" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Skip for now" }));
  expect(await screen.findByRole("heading", { name: "Review content filters" })).toBeTruthy();
}

describe("first-run setup", () => {
  test("locks an unconfigured instance to the keyboard-operable account step", async () => {
    unconfiguredInstance();
    renderApp("/library");
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "Set up Pornarr" })).toBeTruthy();
    expect(screen.getByText("Step 1 of 7")).toBeTruthy();
    expect(screen.getByText("Password strength: weak")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByText("Enter an administrator username.")).toBeTruthy();
    expect(screen.getByText("Use at least 12 characters from two character types.")).toBeTruthy();
    expect(screen.getByLabelText("Username").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByLabelText("Password").getAttribute("aria-invalid")).toBe("true");

    await reachLibraryStep(user);
  });

  test("stops calling a field wrong once it is filled in correctly", async () => {
    unconfiguredInstance();
    renderApp("/setup");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Continue" }));
    expect(await screen.findByText("Enter an administrator username.")).toBeTruthy();

    await user.type(screen.getByLabelText("Username"), "ada");
    await user.type(screen.getByLabelText("Password"), "Correct horse battery staple! 2026");

    await waitFor(() => {
      expect(screen.queryByText("Enter an administrator username.")).toBeNull();
    });
    expect(screen.queryByText("Use at least 12 characters from two character types.")).toBeNull();
    expect(screen.getByLabelText("Username").getAttribute("aria-invalid")).toBeNull();
    expect(screen.getByLabelText("Password").getAttribute("aria-invalid")).toBeNull();
  });

  test("warns about copy imports before advancing past the library path", async () => {
    unconfiguredInstance();
    server.use(
      http.post("/api/setup/validate-library-path", () =>
        HttpResponse.json({
          same_filesystem_as_downloads: false,
          warning: "different filesystem from downloads; imports cannot hardlink",
        }),
      ),
    );
    renderApp("/setup");
    const user = userEvent.setup();

    await reachLibraryStep(user);
    await user.type(screen.getByLabelText("Library path"), "/media/library");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(
      await screen.findByText(
        "This library is on a different filesystem from downloads. Imports will copy instead of hardlinking.",
      ),
    ).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Continue with copy imports" }));

    expect(await screen.findByRole("heading", { name: "Add a search indexer" })).toBeTruthy();
  });

  test("skips the indexer and download client steps when left blank", async () => {
    unconfiguredInstance();
    renderApp("/setup");
    const user = userEvent.setup();

    await reachFiltersStep(user);
  });

  test("tests an indexer connection before accepting it, and reports a failure", async () => {
    unconfiguredInstance();
    const tested = vi.fn();
    server.use(
      http.post("/api/setup/test-indexer", async ({ request }) => {
        tested(await request.json());
        return HttpResponse.json(
          { code: "INDEXER_CONNECTION_FAILED", status: 422, context: {} },
          {
            status: 422,
          },
        );
      }),
    );
    renderApp("/setup");
    const user = userEvent.setup();

    await passLibraryStep(user);

    await user.type(screen.getByLabelText("Base URL"), "https://indexer.example");
    await user.type(screen.getByLabelText("API key"), "indexer-key");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(tested).toHaveBeenCalledOnce());
    expect(tested.mock.calls[0]?.[0]).toEqual({
      implementation: "torznab",
      base_url: "https://indexer.example",
      api_key: "indexer-key",
    });
    // The failed test keeps the wizard on the indexer step rather than
    // accepting a configuration nobody could reach.
    expect(await screen.findByRole("heading", { name: "Add a search indexer" })).toBeTruthy();
  });

  test("advances past a configured indexer once its connection test passes", async () => {
    unconfiguredInstance();
    server.use(
      http.post("/api/setup/test-indexer", () =>
        HttpResponse.json({ categories: [{ id: "5000", name: "TV" }] }),
      ),
    );
    renderApp("/setup");
    const user = userEvent.setup();

    await passLibraryStep(user);

    await user.type(screen.getByLabelText("Base URL"), "https://indexer.example");
    await user.type(screen.getByLabelText("API key"), "indexer-key");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { name: "Add a download client" })).toBeTruthy();
  });

  test("submits a tested download client's qBittorrent credentials as JSON", async () => {
    unconfiguredInstance();
    const tested = vi.fn();
    const completed = vi.fn();
    server.use(
      http.post("/api/setup/test-download-client", async ({ request }) => {
        tested(await request.json());
        return new HttpResponse(null, { status: 204 });
      }),
      http.post("/api/setup/complete", async ({ request }) => {
        completed(await request.json());
        return HttpResponse.json(
          { username: "ada", same_filesystem_as_downloads: true, warning: null },
          { status: 201 },
        );
      }),
    );
    renderApp("/setup");
    const user = userEvent.setup();

    await passLibraryStep(user);
    await user.click(screen.getByRole("button", { name: "Skip for now" }));

    expect(await screen.findByRole("heading", { name: "Add a download client" })).toBeTruthy();
    await user.type(screen.getByLabelText("Host"), "client.example");
    await user.type(screen.getByLabelText("Port"), "8080");
    await user.type(screen.getByLabelText("Username"), "ada");
    await user.type(screen.getByLabelText("Password"), "hunter2");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(tested).toHaveBeenCalledOnce());
    expect(tested.mock.calls[0]?.[0]).toEqual({
      implementation: "qbittorrent",
      host: "client.example",
      port: 8080,
      credentials: JSON.stringify({ username: "ada", password: "hunter2" }),
      category: null,
    });
    expect(await screen.findByRole("heading", { name: "Review content filters" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Metadata providers" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Skip for now" }));
    expect(await screen.findByRole("heading", { name: "Review setup" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Complete setup" }));

    await waitFor(() => expect(completed).toHaveBeenCalledOnce());
    expect(completed.mock.calls[0]?.[0]).toMatchObject({
      download_client: {
        implementation: "qbittorrent",
        host: "client.example",
        port: 8080,
        credentials: JSON.stringify({ username: "ada", password: "hunter2" }),
        category: null,
      },
    });
    expect(completed.mock.calls[0]?.[0]).not.toHaveProperty("indexer");
  });

  test("keeps every filter off and completes without any optional integration", async () => {
    unconfiguredInstance();
    const complete = vi.fn();
    server.use(
      http.post("/api/setup/validate-library-path", () =>
        HttpResponse.json({ same_filesystem_as_downloads: true, warning: null }),
      ),
      http.post("/api/setup/complete", async ({ request }) => {
        complete(await request.json());
        return HttpResponse.json(
          { username: "ada", same_filesystem_as_downloads: true, warning: null },
          { status: 201 },
        );
      }),
    );
    renderApp("/setup");
    const user = userEvent.setup();

    await reachFiltersStep(user);

    expect(screen.getAllByText("Off")).toHaveLength(6);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Metadata providers" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Skip for now" }));
    expect(await screen.findByRole("heading", { name: "Review setup" })).toBeTruthy();
    expect(screen.getByText("All filters start off")).toBeTruthy();
    expect(screen.getByText("No provider configured")).toBeTruthy();
    expect(screen.getByText("No indexer configured")).toBeTruthy();
    expect(screen.getByText("No download client configured")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Complete setup" }));

    await waitFor(() => expect(complete).toHaveBeenCalledOnce());
    expect(complete.mock.calls[0]?.[0]).toEqual({
      username: "ada",
      password: "Correct horse battery staple! 2026",
      library_path: "/media/library",
    });
    expect(await screen.findByRole("heading", { name: "Setup complete" })).toBeTruthy();
  });

  test("includes a metadata provider in the completion payload once a key is entered", async () => {
    unconfiguredInstance();
    const complete = vi.fn();
    server.use(
      http.post("/api/setup/complete", async ({ request }) => {
        complete(await request.json());
        return HttpResponse.json(
          { username: "ada", same_filesystem_as_downloads: true, warning: null },
          { status: 201 },
        );
      }),
    );
    renderApp("/setup");
    const user = userEvent.setup();

    await reachFiltersStep(user);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Metadata providers" })).toBeTruthy();

    await user.type(screen.getByLabelText("API key"), "metadata-key");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { name: "Review setup" })).toBeTruthy();
    expect(screen.getByText("StashDB")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Complete setup" }));

    await waitFor(() => expect(complete).toHaveBeenCalledOnce());
    expect(complete.mock.calls[0]?.[0]).toMatchObject({
      metadata_provider: { implementation: "stashdb", api_key: "metadata-key", endpoint: null },
    });
  });
});
