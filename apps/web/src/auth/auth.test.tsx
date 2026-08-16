/**
 * The auth flow, end to end through the real router and the real client.
 *
 * The one rule these tests exist to hold: a server error code never reaches the
 * screen. Every assertion about a failure checks both that the mapped sentence
 * is shown and that the code is nowhere in the document.
 */
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { CSRF_COOKIE, CSRF_HEADER } from "../lib/api";
import { messageForError, messageForErrorCode } from "../lib/api-error";
import {
  TEST_USER,
  VALID_PASSWORD,
  renderApp,
  server,
  setViewportWidth,
  signedIn,
  useMockApi,
} from "../test/harness";

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  setViewportWidth(1440);
  // Cookies persist across tests in one jsdom document.
  document.cookie = `${CSRF_COOKIE}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
});

async function signIn(password: string): Promise<void> {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Username"), "ada");
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("session restore", () => {
  test("a valid /api/auth/me lands on the shell", async () => {
    signedIn();
    renderApp("/library");

    expect(await screen.findByRole("navigation", { name: "Primary" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Library" })).toBeTruthy();
  });

  test("a 401 lands on the login screen", async () => {
    renderApp("/library");

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
  });

  test("an unavailable session endpoint is shown as an error, not a logout", async () => {
    server.use(http.get("/api/auth/me", () => HttpResponse.error()));
    renderApp("/library");

    expect(
      await screen.findByRole("heading", {
        name: messageForError(new TypeError("offline")),
      }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
  });
});

describe("login", () => {
  test("succeeds and lands on the shell", async () => {
    renderApp("/library");
    await signIn(VALID_PASSWORD);

    expect(await screen.findByRole("navigation", { name: "Primary" })).toBeTruthy();
    // Returned to where the guard interrupted, not to the default route.
    expect(screen.getByRole("heading", { name: "Library" })).toBeTruthy();
  });

  test("bad credentials show a mapped message and never the code", async () => {
    renderApp("/");
    await signIn("wrong");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(messageForErrorCode("INVALID_CREDENTIALS"));
    expect(document.body.textContent).not.toContain("INVALID_CREDENTIALS");
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
  });

  test("a rate-limited attempt maps 429 to its own message", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json(
          { code: "LOGIN_RATE_LIMITED", status: 429, context: {} },
          { status: 429 },
        ),
      ),
    );
    renderApp("/");
    await signIn(VALID_PASSWORD);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(messageForErrorCode("LOGIN_RATE_LIMITED"));
    expect(document.body.textContent).not.toContain("LOGIN_RATE_LIMITED");
  });
});

describe("logout", () => {
  test("returns to the login screen", async () => {
    signedIn();
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: TEST_USER.username }));
    // /api/auth/me still answers 200; the cache being emptied is what has to
    // move the router, not a refetch.
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
  });

  test("sends the CSRF header", async () => {
    signedIn();
    document.cookie = `${CSRF_COOKIE}=token-abc; path=/`;
    let sent: string | null = null;
    server.use(
      http.post("/api/auth/logout", ({ request }) => {
        sent = request.headers.get(CSRF_HEADER);
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderApp("/library");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: TEST_USER.username }));
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));

    await waitFor(() => expect(sent).toBe("token-abc"));
  });

  test("a CSRF_FAILED logout keeps the user in and shows the mapped message", async () => {
    signedIn();
    server.use(
      http.post("/api/auth/logout", () =>
        HttpResponse.json({ code: "CSRF_FAILED", status: 403, context: {} }, { status: 403 }),
      ),
    );

    renderApp("/library");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: TEST_USER.username }));
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(messageForErrorCode("CSRF_FAILED"));
    expect(document.body.textContent).not.toContain("CSRF_FAILED");
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeTruthy();
  });
});

describe("401 on any request", () => {
  test("drops the application onto the login screen", async () => {
    signedIn();
    renderApp("/library");
    await screen.findByRole("navigation", { name: "Primary" });

    // Any endpoint at all: the middleware does not care which one.
    server.use(
      http.post("/api/auth/logout", () =>
        HttpResponse.json({ code: "NOT_AUTHENTICATED", status: 401, context: {} }, { status: 401 }),
      ),
      http.get("/api/auth/me", () =>
        HttpResponse.json({ code: "NOT_AUTHENTICATED", status: 401, context: {} }, { status: 401 }),
      ),
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: TEST_USER.username }));
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
  });
});
