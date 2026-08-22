import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";
import { renderApp, server, signedIn, useMockApi } from "../../../test/harness";

useMockApi();
afterEach(cleanup);

function provider(overrides: Record<string, unknown> = {}) {
  return {
    id: "provider-1",
    name: "Company sign-in",
    issuer: "https://issuer.example.com",
    client_id: "client-id",
    scopes: ["openid", "profile", "email"],
    username_claim: "preferred_username",
    role_claim: "groups",
    role_mapping: {},
    default_role: "user",
    required_claim: null,
    required_claim_value: null,
    enabled: true,
    discovery_fetched_at: null,
    ...overrides,
  };
}

test("a provider is configured with a secret the screen never shows again", async () => {
  signedIn();
  const created = vi.fn();
  let providers: ReturnType<typeof provider>[] = [];
  server.use(
    http.get("/api/admin/oidc", () => HttpResponse.json(providers)),
    http.post("/api/admin/oidc", async ({ request }) => {
      created(await request.json());
      const added = provider();
      providers = [...providers, added];
      return HttpResponse.json(added, { status: 201 });
    }),
  );
  const user = userEvent.setup();

  renderApp("/settings/oidc");

  await screen.findByRole("heading", { name: "Single sign-on", level: 1 });
  expect(
    screen.getByText("No provider is configured yet, so nobody can sign in this way."),
  ).not.toBeNull();

  await user.type(screen.getByLabelText("Name"), "Company sign-in");
  await user.type(screen.getByLabelText("Issuer"), "https://issuer.example.com");
  await user.type(screen.getByLabelText("Client ID"), "client-id");
  await user.type(screen.getByLabelText("Client secret"), "super-secret");
  await user.click(screen.getByRole("button", { name: "Add provider" }));

  await waitFor(() => expect(created).toHaveBeenCalledOnce());
  expect(created.mock.calls[0]?.[0]).toEqual({
    name: "Company sign-in",
    issuer: "https://issuer.example.com",
    client_id: "client-id",
    client_secret: "super-secret",
    default_role: "user",
    role_claim: "groups",
    username_claim: "preferred_username",
    enabled: true,
  });
  // The secret is write-only: nothing renders it back into the form.
  await waitFor(() =>
    expect((screen.getByLabelText("Client secret") as HTMLInputElement).value).toBe(""),
  );
  await screen.findByRole("heading", { name: "Company sign-in", level: 3 });
});

test("testing a provider reports what the server found", async () => {
  signedIn();
  server.use(
    http.get("/api/admin/oidc", () => HttpResponse.json([provider()])),
    http.post("/api/admin/oidc/provider-1/test", () =>
      HttpResponse.json(provider({ discovery_fetched_at: new Date().toISOString() })),
    ),
  );
  const user = userEvent.setup();

  renderApp("/settings/oidc");

  expect(await screen.findByText("Never")).not.toBeNull();

  await user.click(screen.getByRole("button", { name: "Test the connection to Company sign-in" }));

  await waitFor(() => expect(screen.queryByText("Never")).toBeNull());
});

test("removing a provider is confirmed before anything is deleted", async () => {
  signedIn();
  const deleted = vi.fn();
  server.use(
    http.get("/api/admin/oidc", () => HttpResponse.json([provider()])),
    http.delete("/api/admin/oidc/provider-1", () => {
      deleted();
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const user = userEvent.setup();

  renderApp("/settings/oidc");

  await user.click(await screen.findByRole("button", { name: "Remove Company sign-in" }));

  expect(deleted).not.toHaveBeenCalled();
  expect(screen.getByText(/needs another way in/)).not.toBeNull();

  await user.click(screen.getByRole("button", { name: "Keep provider" }));
  expect(deleted).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Remove Company sign-in" }));
  await user.click(screen.getByRole("button", { name: "Remove provider" }));

  await waitFor(() => expect(deleted).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Company sign-in", level: 3 })).toBeNull(),
  );
});
