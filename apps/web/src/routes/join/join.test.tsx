/**
 * B5 — joining, and issuing the link that lets somebody join.
 *
 * The page is reached by somebody with no account, so the properties worth
 * pinning are about what it refuses to do: take a password for a dead link, and
 * tell a stranger anything about the household behind the URL they hold.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import {
  currentPath,
  renderApp,
  server,
  setViewportWidth,
  signedIn,
  useMockApi,
} from "../../test/harness";

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  setViewportWidth(1280);
});

const VALID = { valid: true, expires_at: "2026-08-24T12:00:00Z" };
const DEAD = { valid: false, expires_at: null };

describe("the join page", () => {
  test("checks the link before asking for anything", async () => {
    server.use(http.get("/api/invites/:token", () => HttpResponse.json(DEAD)));
    renderApp("/join/dead-token");

    expect(await screen.findByText("This link no longer works.")).toBeTruthy();
    // A page that takes a password and then refuses it has taken a password
    // for nothing — on a shared machine, possibly an autofilled one.
    expect(screen.queryByLabelText("Password")).toBeNull();
  });

  test("says nothing about the household behind the link", async () => {
    server.use(http.get("/api/invites/:token", () => HttpResponse.json(VALID)));
    renderApp("/join/good-token");

    await screen.findByLabelText("Username");

    // The design's "You've been invited to Kai's library" would name the owner
    // to anyone holding a URL, including one found in a browser history. The
    // possessive is the tell; "Enter the library" is the button and is fine.
    expect(document.body.textContent).not.toMatch(/\w+'s library/);
  });

  test("refuses a short password before the server has to", async () => {
    server.use(http.get("/api/invites/:token", () => HttpResponse.json(VALID)));
    renderApp("/join/good-token");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Username"), "mira");
    await user.type(screen.getByLabelText("Password"), "short");

    expect(screen.getByRole("alert").textContent).toContain("At least 12 characters");
    expect(screen.getByRole("button", { name: "Enter the library" }).hasAttribute("disabled")).toBe(
      true,
    );
  });

  test("redeeming sends what was typed and goes to the login screen", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.get("/api/invites/:token", () => HttpResponse.json(VALID)),
      http.post("/api/invites/:token/redeem", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(
          { username: "mira", display_name: "Mira", role: "user" },
          { status: 201 },
        );
      }),
    );
    const { router } = renderApp("/join/good-token");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Username"), "mira");
    await user.type(screen.getByLabelText("Display name"), "Mira");
    await user.type(screen.getByLabelText("Password"), "a long enough passphrase");
    await user.click(screen.getByRole("button", { name: "Enter the library" }));

    await waitFor(() =>
      expect(bodies).toStrictEqual([
        { username: "mira", password: "a long enough passphrase", display_name: "Mira" },
      ]),
    );
    // The account exists but no session does. Signing somebody in from a
    // redemption would be a second way to mint one.
    await waitFor(() => expect(currentPath(router)).toBe("/login"));
  });

  test("a name already taken is reported without losing what was typed", async () => {
    server.use(
      http.get("/api/invites/:token", () => HttpResponse.json(VALID)),
      http.post("/api/invites/:token/redeem", () =>
        HttpResponse.json({ code: "CONFLICT", status: 409 }, { status: 409 }),
      ),
    );
    renderApp("/join/good-token");
    const user = userEvent.setup();

    const name = await screen.findByLabelText("Username");
    await user.type(name, "mira");
    await user.type(screen.getByLabelText("Password"), "a long enough passphrase");
    await user.click(screen.getByRole("button", { name: "Enter the library" }));

    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
    expect((name as HTMLInputElement).value).toBe("mira");
  });
});

describe("issuing an invitation", () => {
  beforeEach(() => {
    signedIn();
  });

  test("shows the link once and says that is once", async () => {
    server.use(
      http.get("/api/admin/invites", () => HttpResponse.json([])),
      http.post("/api/admin/invites", () =>
        HttpResponse.json(
          {
            id: "i-1",
            token: "secret-token",
            expires_at: "2026-08-24T12:00:00Z",
            note: null,
          },
          { status: 201 },
        ),
      ),
    );
    renderApp("/admin/invites");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Create link" }));

    // The server keeps only a hash. A link lost before it is copied is gone.
    expect(await screen.findByText(/Shown once/)).toBeTruthy();
    expect(screen.getByText(/\/join\/secret-token$/)).toBeTruthy();
  });

  test("a used invitation cannot be withdrawn, because it is the record", async () => {
    server.use(
      http.get("/api/admin/invites", () =>
        HttpResponse.json([
          {
            id: "i-1",
            expires_at: "2026-08-24T12:00:00Z",
            redeemed_at: "2026-08-17T12:00:00Z",
            redeemed_username: "mira",
            note: "for Lea",
          },
        ]),
      ),
    );
    renderApp("/admin/invites");

    expect(await screen.findByText(/used by mira/)).toBeTruthy();
    // Usually the only answer to "how did this account get here".
    expect(screen.queryByRole("button", { name: "Withdraw" })).toBeNull();
  });

  test("an unused one can be", async () => {
    const revoked: string[] = [];
    server.use(
      http.get("/api/admin/invites", () =>
        HttpResponse.json([
          {
            id: "i-2",
            expires_at: "2099-01-01T00:00:00Z",
            redeemed_at: null,
            redeemed_username: null,
            note: null,
          },
        ]),
      ),
      http.delete("/api/admin/invites/:id", ({ params }) => {
        revoked.push(String(params.id));
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/admin/invites");

    await userEvent.setup().click(await screen.findByRole("button", { name: "Withdraw" }));

    await waitFor(() => expect(revoked).toStrictEqual(["i-2"]));
  });
});
