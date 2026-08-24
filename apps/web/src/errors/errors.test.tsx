/**
 * What the application does when something is wrong.
 *
 * The acceptance criteria of issue #138, as assertions: no unhandled error
 * produces a blank screen, a lost connection shows a state and recovers by
 * itself, and a retry affordance actually repeats the request rather than
 * clearing an error message and hoping.
 *
 * Connection loss is exercised through both of its signals, separately, because
 * they are separate: `navigator.onLine` for the device, and an `EventSource`
 * error for the stream. A test that only covered one would let the other
 * regress silently.
 */
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { JSX } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "../i18n";
import de from "../i18n/de.json";
import en from "../i18n/en.json";
import {
  ApiRequestError,
  isRetryableError,
  nextStepForError,
  nextStepForErrorCode,
} from "../lib/api-error";
import { renderApp, server, setViewportWidth, signedIn, useMockApi } from "../test/harness";
import { ErrorBoundary } from "./error-boundary";

useMockApi();
afterEach(cleanup);

beforeEach(() => {
  setViewportWidth(1440);
  signedIn();
});

/* ------------------------------------------------------------------ boundary */

/** Throws until the module-level switch is flipped, so recovery is observable. */
let explodes = true;

function Fragile(): JSX.Element {
  if (explodes) throw new Error("render blew up");
  return <p>recovered</p>;
}

describe("the error boundary", () => {
  beforeEach(() => {
    explodes = true;
    // React logs every caught render error itself. Silencing it keeps the run
    // readable without hiding a failure — the assertions below are the check.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test("a component that throws produces a screen, not a blank page", () => {
    render(
      <ErrorBoundary>
        <Fragile />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("heading", { name: en.errors.brokenTitle })).toBeTruthy();
    // The cause is not the whole story: DESIGN.md wants the next step too.
    expect(screen.getByText(en.errors.brokenStep)).toBeTruthy();
    expect(document.body.textContent?.trim().length).toBeGreaterThan(0);
  });

  test("the recovery action remounts the subtree and the screen comes back", async () => {
    render(
      <ErrorBoundary>
        <Fragile />
      </ErrorBoundary>,
    );
    const user = userEvent.setup();

    explodes = false;
    await user.click(screen.getByRole("button", { name: en.errors.retry }));

    expect(screen.getByText("recovered")).toBeTruthy();
    expect(screen.queryByText(en.errors.brokenTitle)).toBeNull();
  });

  test("the sentence lands in a polite live region and nothing takes focus", async () => {
    const active = document.activeElement;
    render(
      <ErrorBoundary>
        <Fragile />
      </ErrorBoundary>,
    );

    await waitFor(() => {
      const region = document.querySelector("[aria-live='polite']");
      expect(region?.textContent).toContain(en.errors.brokenTitle);
    });
    expect(document.activeElement).toBe(active);
  });
});

/* --------------------------------------------------------------- 404 and 403 */

describe("addresses that are not a failure", () => {
  test("404 offers a destination", async () => {
    renderApp("/nothing-here");

    expect(await screen.findByRole("heading", { name: en.screen.notFoundTitle })).toBeTruthy();
    const link = screen.getByRole("link", { name: en.screen.backToLibrary });
    expect(link.getAttribute("href")).toBe("/library");
  });

  test("403 offers a destination", async () => {
    renderApp("/forbidden");

    expect(await screen.findByRole("heading", { name: en.screen.forbiddenTitle })).toBeTruthy();
    const link = screen.getByRole("link", { name: en.screen.backToLibrary });
    expect(link.getAttribute("href")).toBe("/library");
  });

  test("both are translated rather than written into the component", async () => {
    renderApp("/nothing-here");
    await screen.findByRole("heading", { name: en.screen.notFoundTitle });

    await act(async () => {
      await setLocale("de");
    });

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: de.screen.notFoundTitle })).toBeTruthy(),
    );
    await setLocale("en");
  });
});

/* ---------------------------------------------------------------- retrying */

describe("a retryable error", () => {
  test("exposes a retry that fires the request again", async () => {
    let attempts = 0;
    server.use(
      http.get("/api/auth/me", () => {
        attempts += 1;
        if (attempts === 1) {
          return HttpResponse.json(
            { code: "SERVICE_UNAVAILABLE", status: 503, context: {} },
            { status: 503 },
          );
        }
        return HttpResponse.json({ id: "u-1", username: "ada", role: "admin" });
      }),
    );

    renderApp("/library");
    const user = userEvent.setup();

    // The cause, and a step that is not "sign in again" — the session query
    // failing is not the same thing as being signed out.
    expect(
      await screen.findByRole("heading", { name: en.errors.SERVICE_UNAVAILABLE }),
    ).toBeTruthy();
    expect(screen.getByText(en.errorSteps.SERVICE_UNAVAILABLE)).toBeTruthy();
    expect(screen.queryByRole("button", { name: en.login.submit })).toBeNull();
    expect(attempts).toBe(1);

    await user.click(screen.getByRole("button", { name: en.errors.retry }));

    await waitFor(() => expect(attempts).toBe(2));
    expect(await screen.findByRole("navigation", { name: en.nav.primary })).toBeTruthy();
  });

  test("an answer that repeating cannot change offers no retry", async () => {
    server.use(
      http.get("/api/auth/me", () =>
        HttpResponse.json({ code: "FORBIDDEN", status: 403, context: {} }, { status: 403 }),
      ),
    );
    renderApp("/library");

    expect(await screen.findByRole("heading", { name: en.errors.FORBIDDEN })).toBeTruthy();
    expect(screen.getByText(en.errorSteps.FORBIDDEN)).toBeTruthy();
    expect(screen.queryByRole("button", { name: en.errors.retry })).toBeNull();
  });

  test("classification follows the code, and an unknown code follows the status", () => {
    expect(isRetryableError(new ApiRequestError("SERVICE_UNAVAILABLE", 503))).toBe(true);
    expect(isRetryableError(new ApiRequestError("NOT_FOUND", 404))).toBe(false);
    expect(isRetryableError(new ApiRequestError("TEAPOT_ON_FIRE", 418))).toBe(false);
    expect(isRetryableError(new ApiRequestError("TEAPOT_ON_FIRE", 502))).toBe(true);
  });

  test("an unmapped code still carries a next step", () => {
    expect(nextStepForErrorCode("TEAPOT_ON_FIRE")).toBe(en.errorSteps.unknown);
    expect(nextStepForError(new TypeError("boom"))).toBe(en.errorSteps.generic);
  });
});

/* --------------------------------------------------------------- connection */

/** Enough EventSource for the hook: named listeners, `open` and `error`. */
class StubEventSource {
  static instances: StubEventSource[] = [];

  readonly listeners = new Map<string, Set<(event: Event) => void>>();

  constructor(readonly url: string) {
    StubEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }

  removeEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {}

  emit(type: string): void {
    act(() => {
      for (const listener of this.listeners.get(type) ?? []) listener(new Event(type));
    });
  }
}

function setOnline(value: boolean): void {
  Object.defineProperty(navigator, "onLine", { configurable: true, value });
  act(() => {
    window.dispatchEvent(new Event(value ? "online" : "offline"));
  });
}

describe("connection loss", () => {
  beforeEach(() => {
    StubEventSource.instances = [];
    Object.defineProperty(globalThis, "EventSource", {
      configurable: true,
      writable: true,
      value: StubEventSource,
    });
  });

  afterEach(() => {
    setOnline(true);
    Reflect.deleteProperty(globalThis, "EventSource");
  });

  test("says nothing while the first connection is still being made", async () => {
    renderApp("/library");
    await screen.findByRole("navigation", { name: en.nav.primary });

    expect(screen.queryByText(en.connection.reconnectingTitle)).toBeNull();
    expect(screen.queryByText(en.connection.offlineTitle)).toBeNull();
  });

  test("going offline shows the state; coming back clears it and refetches", async () => {
    const { queryClient } = renderApp("/library");
    await screen.findByRole("navigation", { name: en.nav.primary });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    setOnline(false);

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain(en.connection.offlineTitle),
    );
    // Cause and next step, in a region that was already mounted and empty.
    expect(screen.getByText(en.connection.offlineBody)).toBeTruthy();
    expect(invalidate).not.toHaveBeenCalled();

    setOnline(true);

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).not.toContain(en.connection.offlineTitle),
    );
    expect(invalidate).toHaveBeenCalled();
  });

  test("a dropped event stream reports the reconnection the browser is making", async () => {
    const { queryClient } = renderApp("/library");
    await screen.findByRole("navigation", { name: en.nav.primary });
    const source = StubEventSource.instances[0];
    if (source === undefined) throw new Error("the shell did not open a stream");
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    source.emit("error");

    await waitFor(() => expect(screen.getByText(en.connection.reconnectingTitle)).toBeTruthy());
    expect(screen.getByText(en.connection.reconnectingBody)).toBeTruthy();

    source.emit("open");

    await waitFor(() => expect(screen.queryByText(en.connection.reconnectingTitle)).toBeNull());
    expect(invalidate).toHaveBeenCalled();
  });

  test("no device network beats a dead stream: one cause, one message", async () => {
    renderApp("/library");
    await screen.findByRole("navigation", { name: en.nav.primary });
    const source = StubEventSource.instances[0];
    if (source === undefined) throw new Error("the shell did not open a stream");

    setOnline(false);
    source.emit("error");

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain(en.connection.offlineTitle),
    );
    // The whole point: one cause wins, so the other state is nowhere on screen —
    // not in the banner and not in the top bar's indicator either.
    expect(screen.queryByText(en.connection.reconnectingTitle)).toBeNull();
  });
});
