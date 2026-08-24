import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";
import { renderApp, server, signedIn, useMockApi } from "../../../test/harness";

useMockApi();
afterEach(cleanup);

function key(overrides: Record<string, unknown> = {}) {
  return {
    id: "key-1",
    label: "Backup script",
    prefix: "pnr_abc123",
    created_at: "2024-01-01T00:00:00Z",
    last_used_at: null,
    ...overrides,
  };
}

test("a created key is shown once and never again", async () => {
  signedIn();
  const created = vi.fn();
  let keys: ReturnType<typeof key>[] = [];
  server.use(
    http.get("/api/account/api-keys", () => HttpResponse.json(keys)),
    http.post("/api/account/api-keys", async ({ request }) => {
      created(await request.json());
      const added = key();
      keys = [...keys, added];
      return HttpResponse.json({ ...added, key: "pnr_abc123def456" }, { status: 201 });
    }),
  );
  const user = userEvent.setup();

  renderApp("/settings/api-keys");

  await screen.findByRole("heading", { name: "API keys", level: 1 });
  expect(screen.getByText("No keys yet.")).not.toBeNull();

  await user.type(screen.getByLabelText("Name"), "Backup script");
  await user.click(screen.getByRole("button", { name: "Create key" }));

  await waitFor(() => expect(created).toHaveBeenCalledOnce());
  expect(created.mock.calls[0]?.[0]).toEqual({ label: "Backup script" });

  // Shown once, in its own block.
  await screen.findByText("pnr_abc123def456");
  expect(screen.getByRole("heading", { name: "The key", level: 2 })).not.toBeNull();
  // And the list it just joined never carries the secret.
  await screen.findByRole("heading", { name: "Backup script", level: 3 });
  expect(document.body.textContent?.match(/pnr_abc123def456/g)?.length).toBe(1);
});

test("a key is revoked", async () => {
  signedIn();
  const deleted = vi.fn();
  let keys = [key()];
  server.use(
    http.get("/api/account/api-keys", () => HttpResponse.json(keys)),
    http.delete("/api/account/api-keys/key-1", () => {
      deleted();
      keys = [];
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const user = userEvent.setup();

  renderApp("/settings/api-keys");

  await user.click(await screen.findByRole("button", { name: "Revoke Backup script" }));

  await waitFor(() => expect(deleted).toHaveBeenCalledOnce());
  await waitFor(() => expect(screen.getByText("No keys yet.")).not.toBeNull());
});
