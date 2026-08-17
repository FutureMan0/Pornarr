import { transferableAbortController } from "node:util";

/**
 * The test harness: a mock API, a query client that does not shout, and one
 * way to mount the real application.
 *
 * Handlers are written against the contract rather than generated from it here,
 * because these tests are about auth *behaviour* — the shapes are three fields
 * wide and the interesting part is which status comes back when.
 *
 * `renderApp` mounts `AppProviders` with a memory router over the real route
 * table, so what is under test is the application's own wiring rather than a
 * rehearsal of it assembled in the test file.
 */
import type { QueryClient } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { RenderResult } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { createMemoryRouter } from "react-router-dom";
import { afterAll, afterEach, beforeAll } from "vitest";
import { AppProviders } from "../app";
import type { SessionUser } from "../auth/session";
import { createQueryClient } from "../lib/query-client";
import { appRoutes } from "../routes";

export const TEST_USER: SessionUser = { id: "u-1", username: "ada", role: "admin" };

export const VALID_PASSWORD = "correct horse";

/** The API's error envelope. Never prose — only a code. */
function apiError(code: string, status: number) {
  return HttpResponse.json({ code, status, context: {} }, { status });
}

/** Signed out: `/api/auth/me` answers 401, login accepts the right password. */
export const defaultHandlers = [
  http.get("/api/setup/status", () => HttpResponse.json({ configured: true })),
  http.get("/api/library", () => HttpResponse.json({ items: [], next_offset: null })),
  // The shell reads both of these for its navigation counts, so every screen
  // test pays for them whether or not it cares about the numbers.
  http.get("/api/queue", () => HttpResponse.json([])),
  http.get("/api/requests", () => HttpResponse.json([])),
  http.get("/api/auth/me", () => apiError("NOT_AUTHENTICATED", 401)),
  http.post("/api/auth/login", async ({ request }) => {
    const body = (await request.json()) as { username?: string; password?: string };
    if (body.password !== VALID_PASSWORD) return apiError("INVALID_CREDENTIALS", 401);
    return HttpResponse.json(TEST_USER);
  }),
  http.post("/api/auth/logout", () => new HttpResponse(null, { status: 204 })),
  http.post("/api/auth/logout-everywhere", () => new HttpResponse(null, { status: 204 })),
];

export const server = setupServer(...defaultHandlers);

/**
 * React Router passes navigation signals to Node's fetch implementation. In
 * jsdom its controller comes from a different realm, which Undici rejects.
 * Use Node's transferable controller only in the test environment so routed
 * requests exercise their normal path.
 */
class NodeAbortController {
  readonly signal: AbortSignal;

  #controller = transferableAbortController();

  constructor() {
    this.signal = this.#controller.signal;
  }

  abort(reason?: unknown): void {
    this.#controller.abort(reason);
  }
}

/** Signed in: `/api/auth/me` returns the test user. */
export function signedIn(): void {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(TEST_USER)));
}

/** Registers the msw lifecycle. Call once at the top level of a test file. */
export function useMockApi(): void {
  beforeAll(() => {
    Object.defineProperty(globalThis, "AbortController", {
      configurable: true,
      writable: true,
      value: NodeAbortController,
    });
    server.listen({ onUnhandledRequest: "error" });
  });
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());
}

/**
 * A client with the app's defaults but no console reporting: a test that
 * asserts an error path should not also print it.
 */
export function createTestQueryClient(): QueryClient {
  return createQueryClient(() => {});
}

export interface RenderAppResult extends RenderResult {
  readonly queryClient: QueryClient;
  /**
   * The memory router, so a test can read where the application went. Screens
   * that keep state in the address — search filters, above all — are asserting
   * something real about being bookmarkable, and `window.location` never moves
   * under a memory router.
   */
  readonly router: ReturnType<typeof createMemoryRouter>;
}

export function renderApp(initialEntry = "/"): RenderAppResult {
  const queryClient = createTestQueryClient();
  const router = createMemoryRouter(appRoutes, { initialEntries: [initialEntry] });
  const result = render(<AppProviders queryClient={queryClient} router={router} />);
  return Object.assign(result, { queryClient, router });
}

/** The path the application is currently on. */
export function currentPath(router: ReturnType<typeof createMemoryRouter>): string {
  return router.state.location.pathname;
}

/** The query string the application is currently on. */
export function currentParams(router: ReturnType<typeof createMemoryRouter>): URLSearchParams {
  return new URLSearchParams(router.state.location.search);
}

/**
 * jsdom has no `matchMedia`, so the sidebar would always read "full". This
 * evaluates `(max-width: Npx)` against a width the test chooses, which is the
 * only part of the API the shell uses.
 */
export function setViewportWidth(width: number): void {
  const matchMedia = (query: string): MediaQueryList => {
    const max = /\(max-width:\s*([\d.]+)px\)/.exec(query);
    const matches = max?.[1] !== undefined && width <= Number.parseFloat(max[1]);
    return {
      matches,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    } as unknown as MediaQueryList;
  };
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: matchMedia,
  });
}

/**
 * jsdom has no IntersectionObserver and no `scrollIntoView`.
 *
 * The shorts feed uses both: the observer to name the pane on screen, and
 * `scrollIntoView` to move between them. Rather than assert against a fake
 * that pretends to observe, the stub hands back a `notify` — a test says which
 * pane is in view, which is what a real scroll would have caused anyway.
 */
export interface ObserverHarness {
  /** Report that the pane at this index is now on screen. */
  readonly notify: (index: number) => void;
  /** Indexes `scrollIntoView` was called on, oldest first. */
  readonly scrolledTo: readonly number[];
  readonly restore: () => void;
}

export function stubIntersectionObserver(): ObserverHarness {
  const callbacks: IntersectionObserverCallback[] = [];
  const observed: Element[] = [];
  const scrolledTo: number[] = [];

  class Stub implements IntersectionObserver {
    readonly root = null;
    readonly rootMargin = "";
    readonly thresholds: readonly number[] = [];

    constructor(callback: IntersectionObserverCallback) {
      callbacks.push(callback);
    }
    observe(target: Element): void {
      observed.push(target);
    }
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): IntersectionObserverEntry[] {
      return [];
    }
  }

  const previousObserver = globalThis.IntersectionObserver;
  const previousScroll = Element.prototype.scrollIntoView;

  Object.defineProperty(globalThis, "IntersectionObserver", {
    configurable: true,
    writable: true,
    value: Stub,
  });
  Element.prototype.scrollIntoView = function scrollIntoView(this: Element): void {
    const index = Number((this as HTMLElement).dataset?.index ?? "-1");
    if (index >= 0) scrolledTo.push(index);
  };

  return {
    notify: (index) => {
      const target = observed.find(
        (element) => (element as HTMLElement).dataset.index === String(index),
      );
      if (target === undefined) return;
      const entry = { isIntersecting: true, target } as unknown as IntersectionObserverEntry;
      for (const callback of callbacks) {
        callback([entry], {} as IntersectionObserver);
      }
    },
    scrolledTo,
    restore: () => {
      Object.defineProperty(globalThis, "IntersectionObserver", {
        configurable: true,
        writable: true,
        value: previousObserver,
      });
      Element.prototype.scrollIntoView = previousScroll;
    },
  };
}
