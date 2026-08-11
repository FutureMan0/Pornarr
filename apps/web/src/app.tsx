/**
 * Providers, and the one wire between them.
 *
 * `lib/api.ts` reports 401s but cannot navigate; the router navigates but knows
 * nothing about fetches. This is where they meet: a 401 empties the session
 * query, `RequireAuth` sees null on the next render, and the application lands
 * on the login route through the router rather than through `window.location`.
 * Losing the SPA and the cache to a full page load is not error handling.
 *
 * `I18nextProvider` is above the router rather than inside a screen: a language
 * change has to reach the sidebar, the top bar and the live region at once, and
 * anything mounted outside it would keep rendering the previous language.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useEffect, useState } from "react";
import { I18nextProvider } from "react-i18next";
import { RouterProvider } from "react-router-dom";
import { SESSION_QUERY_KEY } from "./auth/session";
import { ErrorBoundary } from "./errors/error-boundary";
import i18n from "./i18n";
import { setUnauthorizedHandler } from "./lib/api";
import { createQueryClient } from "./lib/query-client";
import { createAppRouter } from "./routes";

export interface AppProvidersProps {
  readonly queryClient: QueryClient;
  readonly router: ReturnType<typeof createAppRouter>;
}

/** The composed tree, with the router injected so tests can supply a memory one. */
export function AppProviders({ queryClient, router }: AppProvidersProps): JSX.Element {
  useEffect(() => {
    setUnauthorizedHandler(() => {
      queryClient.setQueryData(SESSION_QUERY_KEY, null);
    });
    return () => setUnauthorizedHandler(null);
  }, [queryClient]);

  return (
    <I18nextProvider i18n={i18n}>
      {/* Below i18n so the fallback has words, above the router and the cache so
          that a crash in either still produces a page with a way out of it. The
          shell holds a second, narrower boundary for the common case. */}
      <ErrorBoundary className="m-6">
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </ErrorBoundary>
    </I18nextProvider>
  );
}

export function App(): JSX.Element {
  // Created once per application instance, not per render, and not at module
  // scope — a module-scope client is shared with every test that imports this.
  const [queryClient] = useState(createQueryClient);
  const [router] = useState(createAppRouter);

  return <AppProviders queryClient={queryClient} router={router} />;
}
