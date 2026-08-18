import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";
import { renderApp, server, signedIn, useMockApi } from "../../../test/harness";

useMockApi();

afterEach(cleanup);

function peer(overrides: Record<string, unknown> = {}) {
  return {
    id: "peer-1",
    name: "Anna's Pornarr",
    base_url: "https://pornarr.example.net",
    enabled: true,
    health: "unknown",
    health_reason: null,
    last_tested_at: null,
    media_count: 42,
    ...overrides,
  };
}

test("settings offers the shared libraries section and explains what a peer is", async () => {
  signedIn();
  server.use(http.get("/api/admin/peers", () => HttpResponse.json([])));
  const user = userEvent.setup();

  renderApp("/settings/root-folders");

  await user.click(await screen.findByRole("link", { name: "Shared libraries" }));

  await screen.findByRole("heading", { name: "Shared libraries", level: 1 });
  // "Peer" means nothing on its own, so the screen says what one is.
  expect(screen.getByText(/another person's Pornarr, reachable over the network/)).not.toBeNull();
  expect(screen.getByText(/No shared library is connected yet/)).not.toBeNull();
});

test("lists what the API reports about a peer and adds another", async () => {
  signedIn();
  const created = vi.fn();
  let peers = [peer({ health: "unhealthy", health_reason: "connection refused" })];
  server.use(
    http.get("/api/admin/peers", () => HttpResponse.json(peers)),
    http.post("/api/admin/peers", async ({ request }) => {
      created(await request.json());
      const added = peer({ id: "peer-2", name: "Ben's Pornarr", media_count: 7 });
      peers = [...peers, added];
      return HttpResponse.json(added, { status: 201 });
    }),
  );
  const user = userEvent.setup();

  renderApp("/settings/peers");

  await screen.findByRole("heading", { name: "Anna's Pornarr", level: 3 });
  expect(screen.getByText("https://pornarr.example.net")).not.toBeNull();
  expect(screen.getByText("Unreachable")).not.toBeNull();
  expect(screen.getByText("The last test reported: connection refused")).not.toBeNull();
  expect(screen.getByText("42")).not.toBeNull();
  expect(screen.getByText("Never")).not.toBeNull();

  await user.type(screen.getByLabelText("Name"), "Ben's Pornarr");
  await user.type(screen.getByLabelText("Address"), "https://ben.example.net");
  await user.type(screen.getByLabelText("API key"), "sekrit");
  await user.click(screen.getByRole("button", { name: "Add peer" }));

  await waitFor(() => expect(created).toHaveBeenCalledOnce());
  // The key travels once, in the body, and never comes back on the peer.
  expect(created.mock.calls[0]?.[0]).toEqual({
    name: "Ben's Pornarr",
    base_url: "https://ben.example.net",
    api_key: "sekrit",
    enabled: true,
  });
  await screen.findByRole("heading", { name: "Ben's Pornarr", level: 3 });
});

test("testing a peer reports what the server found", async () => {
  signedIn();
  server.use(
    http.get("/api/admin/peers", () => HttpResponse.json([peer()])),
    http.post("/api/admin/peers/peer-1/test", () =>
      HttpResponse.json(peer({ health: "healthy", last_tested_at: new Date().toISOString() })),
    ),
  );
  const user = userEvent.setup();

  renderApp("/settings/peers");

  expect(await screen.findByText("Not tested yet")).not.toBeNull();

  await user.click(screen.getByRole("button", { name: "Test the connection to Anna's Pornarr" }));

  await screen.findByText("Reachable");
  expect(screen.queryByText("Not tested yet")).toBeNull();
});

test("removing a peer is confirmed before anything is deleted", async () => {
  signedIn();
  const deleted = vi.fn();
  server.use(
    http.get("/api/admin/peers", () => HttpResponse.json([peer()])),
    http.delete("/api/admin/peers/peer-1", () => {
      deleted();
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const user = userEvent.setup();

  renderApp("/settings/peers");

  await user.click(await screen.findByRole("button", { name: "Remove Anna's Pornarr" }));

  // The first press asks rather than acts, and says what removal does and does
  // not do while the server is still on screen.
  expect(deleted).not.toHaveBeenCalled();
  expect(screen.getByText(/Nothing on that server is touched/)).not.toBeNull();

  await user.click(screen.getByRole("button", { name: "Keep peer" }));
  expect(deleted).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Remove Anna's Pornarr" }));
  await user.click(screen.getByRole("button", { name: "Remove peer" }));

  await waitFor(() => expect(deleted).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Anna's Pornarr", level: 3 })).toBeNull(),
  );
});
