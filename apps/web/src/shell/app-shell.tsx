/**
 * The application shell: sidebar, top bar, content region.
 *
 * DESIGN.md: content capped at 1600px and centred, offset by whichever width
 * the sidebar currently occupies. The offset is derived from the same value the
 * sidebar renders from, so the two cannot drift.
 *
 * The live region lives here because it must outlive every screen: an import
 * finishing while the user is in settings is still worth announcing, and a
 * region that unmounts with the route announces nothing.
 */
import type { CSSProperties, JSX } from "react";
import { useTranslation } from "react-i18next";
import { Outlet } from "react-router-dom";
import { ConnectionStatus, useConnectionState } from "../errors/connection-status";
import { ErrorBoundary } from "../errors/error-boundary";
import { useEventStream } from "../lib/events";
import { PageTitleProvider } from "./page-title";
import { useScreenKey } from "./screen-key";
import { Sidebar, useSidebarLayout } from "./sidebar";
import { TabBar } from "./tab-bar";
import { TopBar } from "./top-bar";

const CONTENT_OFFSET = {
  full: "pl-[var(--layout-sidebar-width)]",
  rail: "pl-[var(--layout-sidebar-rail-width)]",
  drawer: "pl-0",
} as const;

export function AppShell(): JSX.Element {
  const { t } = useTranslation();
  const layout = useSidebarLayout();
  const { announcement, status } = useEventStream();
  // Derived once, here. The hook refills the cache when a connection recovers,
  // so a second caller would refill twice; both the pip and the banner take the
  // answer as a prop.
  const connection = useConnectionState(status);
  const screenKey = useScreenKey();
  const phone = layout === "drawer";

  return (
    <div className="min-h-full">
      <a
        href="#main"
        className={
          "text-sm sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-[var(--z-toast)] focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:text-ink"
        }
      >
        {t("shell.skipToContent")}
      </a>

      {/* No sidebar on a phone, and no drawer behind a button either. Section C
          of the design navigates from the bottom edge — see `tab-bar.tsx`. */}
      {phone ? null : <Sidebar layout={layout} />}

      {/* Wraps both the bar and the content: the screen inside publishes its
          name and the bar above renders it, so the provider has to contain
          the two of them. */}
      <PageTitleProvider>
        <div className={CONTENT_OFFSET[layout]}>
          <TopBar layout={layout} connection={connection} />

          {/* The tab bar is fixed to the window, so the content reserves its
              height rather than scrolling under it. */}
          <main
            id="main"
            /**
             * How much of the window the shell has already taken.
             *
             * A screen that wants to be exactly as tall as what is left — the
             * shorts feed is the only one so far — cannot work that out for
             * itself: the header is one row on a desktop and two on a phone, and
             * a phone also carries a tab bar and a home indicator. It used to
             * subtract a flat 11rem, which was right on a desktop and cut the
             * bottom off every clip on a phone.
             *
             * Declared here because this is the element that knows.
             */
            style={
              {
                "--shell-chrome": phone
                  ? "calc(11.5rem + var(--space-16) + var(--space-5) + env(safe-area-inset-bottom, 0px))"
                  : "9.5rem",
              } as CSSProperties
            }
            className={
              phone
                ? "mx-auto w-full max-w-[var(--layout-content-max-width)] px-4 pt-4 pb-[calc(var(--space-16)+var(--space-5)+env(safe-area-inset-bottom,0px))]"
                : "mx-auto w-full max-w-[var(--layout-content-max-width)] p-6"
            }
          >
            {/* Two registers of the same fact, on purpose: the pip in the bar
                is always present and says which state we are in, this says what
                it means and what is being done about it. The banner stays
                absent while the connection is healthy. */}
            <ConnectionStatus state={connection} />

            {/* Inside the shell rather than around it: a screen that throws takes
                the screen down, and the navigation out of it stays usable. The
                boundary in `app.tsx` is the one that catches everything else. */}
            <ErrorBoundary>
              {/* The key is what makes the screen animate: `@starting-style`
                  fires on insertion, so arriving somewhere new has to be a new
                  element. `useScreenKey` is careful about what counts as new —
                  see it for why this is not `location.pathname`. */}
              <div key={screenKey} className="pa-enter">
                <Outlet />
              </div>
            </ErrorBoundary>
          </main>
        </div>
      </PageTitleProvider>

      {phone ? <TabBar /> : null}

      {/* Polite, and never focused: state that arrives over SSE is reported,
          not thrust in front of whatever the user is doing. */}
      <div aria-live="polite" aria-atomic="true" className="visually-hidden">
        {announcement}
      </div>
    </div>
  );
}
