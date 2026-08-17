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

describe("first-run setup", () => {
  test("locks an unconfigured instance to the keyboard-operable account step", async () => {
    unconfiguredInstance();
    renderApp("/library");
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "Set up Pornarr" })).toBeTruthy();
    expect(screen.getByText("Step 1 of 5")).toBeTruthy();
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

    expect(await screen.findByRole("heading", { name: "Review content filters" })).toBeTruthy();
  });

  test("keeps every filter off and completes without a metadata provider", async () => {
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

    await reachLibraryStep(user);
    await user.type(screen.getByLabelText("Library path"), "/media/library");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { name: "Review content filters" })).toBeTruthy();
    expect(screen.getAllByText("Off")).toHaveLength(6);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Metadata providers" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Skip for now" }));
    expect(await screen.findByRole("heading", { name: "Review setup" })).toBeTruthy();
    expect(screen.getByText("All filters start off")).toBeTruthy();
    expect(screen.getByText("No provider configured")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Complete setup" }));

    await waitFor(() => expect(complete).toHaveBeenCalledOnce());
    expect(complete.mock.calls[0]?.[0]).toEqual({
      username: "ada",
      password: "Correct horse battery staple! 2026",
      library_path: "/media/library",
    });
    expect(await screen.findByRole("heading", { name: "Setup complete" })).toBeTruthy();
  });
});
