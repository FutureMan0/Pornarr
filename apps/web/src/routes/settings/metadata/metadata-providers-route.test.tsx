import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";
import { renderApp, server, signedIn, useMockApi } from "../../../test/harness";

useMockApi();
afterEach(cleanup);

test("a provider is configured with a key the screen never shows again", async () => {
  signedIn();
  const configured = vi.fn();
  server.use(
    http.get("/api/admin/metadata-providers", () => HttpResponse.json([])),
    http.post("/api/admin/metadata-providers", async ({ request }) => {
      configured(await request.json());
      return HttpResponse.json(
        { id: "provider-1", implementation: "stashdb", endpoint: null, priority: 0, enabled: true },
        { status: 201 },
      );
    }),
  );

  renderApp("/settings/metadata");
  const user = userEvent.setup();

  await screen.findByRole("heading", { name: "Metadata providers", level: 1 });
  expect(
    screen.getByText("No provider is configured, so imports fall back to what the file name says."),
  ).toBeTruthy();

  await user.type(screen.getByLabelText("API key"), "stash-key");
  await user.click(screen.getByRole("button", { name: "Save provider" }));

  await waitFor(() => expect(configured).toHaveBeenCalledOnce());
  expect(configured.mock.calls[0]?.[0]).toEqual({
    implementation: "stashdb",
    api_key: "stash-key",
    endpoint: null,
    priority: 0,
    enabled: true,
  });
  // The key is write-only: nothing renders it back into the form.
  await waitFor(() =>
    expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe(""),
  );
});
