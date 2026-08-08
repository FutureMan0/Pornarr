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
import type { JSX } from "react";
import { useEffect, useRef, useState } from "react";
import { Outlet } from "react-router-dom";
import { useEventStream } from "../lib/events";
import { Sidebar, useSidebarLayout } from "./sidebar";
import { TopBar } from "./top-bar";

const CONTENT_OFFSET = {
  full: "pl-[var(--sidebar-width)]",
  rail: "pl-[var(--sidebar-rail-width)]",
  drawer: "pl-0",
} as const;

export function AppShell(): JSX.Element {
  const layout = useSidebarLayout();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);
  const { announcement } = useEventStream();

  // Growing past the drawer breakpoint with the drawer open would leave an
  // overlay on top of a sidebar that is already visible.
  useEffect(() => {
    if (layout !== "drawer") setDrawerOpen(false);
  }, [layout]);

  const closeDrawer = (): void => {
    setDrawerOpen(false);
    // DESIGN.md: focus is returned to the trigger on close. The trigger is a
    // Button inside a ref'd wrapper, hence the query rather than a direct ref.
    triggerRef.current?.querySelector("button")?.focus();
  };

  return (
    <div className="min-h-full">
      <a
        href="#main"
        className={
          "text-sm sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-[var(--z-toast)] focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:text-ink"
        }
      >
        Skip to content
      </a>

      <Sidebar layout={layout} open={drawerOpen} onClose={closeDrawer} />

      <div className={CONTENT_OFFSET[layout]}>
        <TopBar
          layout={layout}
          drawerOpen={drawerOpen}
          onOpenDrawer={() => setDrawerOpen(true)}
          triggerRef={triggerRef}
        />

        <main id="main" className="mx-auto w-full max-w-[var(--content-max-width)] p-6">
          <Outlet />
        </main>
      </div>

      {/* Polite, and never focused: state that arrives over SSE is reported,
          not thrust in front of whatever the user is doing. */}
      <div aria-live="polite" aria-atomic="true" className="visually-hidden">
        {announcement}
      </div>
    </div>
  );
}
