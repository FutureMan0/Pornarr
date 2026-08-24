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
import { cx } from "@pornarr/ui";
import type { JSX } from "react";
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
    // A column exactly one viewport tall, so nothing has to guess how much of
    // it the chrome took. `dvh` because a phone's address bar shrinks the
    // viewport as you scroll and `vh` is the tall version.
    <div className="flex h-dvh flex-col">
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
        <div className={cx("flex min-h-0 flex-1 flex-col", CONTENT_OFFSET[layout])}>
          <TopBar layout={layout} connection={connection} />

          {/* The scroll container. The window no longer scrolls, which is what
              makes `h-full` inside a screen mean "the rest of the window" — the
              shorts feed and the library grid both used to subtract a guessed
              number of rem from `100vh` instead, and both guesses were wrong on
              a phone. */}
          <main
            id="main"
            // Focusable because it scrolls. A pointer can reach content below
            // the fold by dragging; a keyboard can only reach it by focusing
            // something inside, and a screen whose content is all text - the
            // requests list is one - has nothing to focus. WCAG 2.1.1 and
            // 2.1.3, which axe reports as `scrollable-region-focusable`, and
            // technique SCR34 names a tab stop on the container as the remedy.
            // It is also what the skip link already targets, so the stop lands
            // somewhere the reader was going anyway. The lint rule below only
            // knows that `main` is not interactive; axe is reading the standard.
            //
            // biome-ignore lint/a11y/noNoninteractiveTabindex: see above
            tabIndex={0}
            className={cx(
              "mx-auto flex min-h-0 w-full max-w-[var(--layout-content-max-width)] flex-1 flex-col overflow-y-auto",
              phone ? "px-4 pt-4" : "p-6",
            )}
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
              {/* `flex-1` and a column, so a screen can ask for the rest of the
                  height. Without it this wrapper is content-sized — `main` being a
                  flex column stretches its children across, not down — and the
                  shorts feed's `flex-1` resolved against nothing and ran 71px
                  under the tab bar. */}
              <div key={screenKey} className="pa-enter flex min-h-0 flex-1 flex-col">
                <Outlet />
              </div>
            </ErrorBoundary>
          </main>
        </div>
      </PageTitleProvider>

      {/* In flow at the bottom of the column, not fixed over the content. A
          fixed bar means every screen has to reserve its height and get that
          number right; a flex child means none of them do. */}
      {phone ? <TabBar /> : null}

      {/* Polite, and never focused: state that arrives over SSE is reported,
          not thrust in front of whatever the user is doing. */}
      <div aria-live="polite" aria-atomic="true" className="visually-hidden">
        {announcement}
      </div>
    </div>
  );
}
