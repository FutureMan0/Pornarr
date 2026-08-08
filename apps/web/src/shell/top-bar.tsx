/**
 * The top bar: global search, the live status cluster, the account menu, and —
 * in the drawer layout only — the control that opens the navigation.
 *
 * Search is a form with no submit handler yet on purpose: the endpoint does not
 * exist, and a search box that silently does nothing is worse than one that
 * plainly cannot be submitted. It is here because the bar's proportions depend
 * on it, and because DESIGN.md puts it here.
 */
import { Input, Menu } from "@pornarr/ui";
import type { FormEvent, JSX, RefObject } from "react";
import { useLogout, useSession } from "../auth/session";
import { messageForError } from "../lib/api-error";
import { DRAWER_ID, type SidebarLayout } from "./sidebar";
import { StatusCluster } from "./status-cluster";

export interface TopBarProps {
  readonly layout: SidebarLayout;
  readonly drawerOpen: boolean;
  readonly onOpenDrawer: () => void;
  /**
   * Wraps the drawer trigger so the shell can put focus back on it when the
   * drawer closes. A ref on the wrapper rather than the button, because
   * `@pornarr/ui`'s Button does not take one.
   */
  readonly triggerRef: RefObject<HTMLDivElement | null>;
}

export function TopBar({ layout, drawerOpen, onOpenDrawer, triggerRef }: TopBarProps): JSX.Element {
  const session = useSession();
  const logout = useLogout();

  const onSearchSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
  };

  return (
    <header className="sticky top-0 z-[var(--z-sticky)] flex items-center gap-4 border-b border-border bg-surface-2 px-4 py-3">
      {layout === "drawer" ? (
        <div ref={triggerRef} className="contents">
          <button
            type="button"
            aria-expanded={drawerOpen}
            aria-controls={drawerOpen ? DRAWER_ID : undefined}
            onClick={onOpenDrawer}
            className={
              "text-sm rounded-md border border-border-control px-2 py-1 text-ink-muted transition-colors duration-[var(--duration-fast)] ease-out hover:bg-surface-3 hover:text-ink"
            }
          >
            Navigation
          </button>
        </div>
      ) : null}

      {/* The element, not the role: `<search>` carries it natively. */}
      <search className="min-w-0 flex-1">
        <form onSubmit={onSearchSubmit}>
          <label className="visually-hidden" htmlFor="global-search">
            Search
          </label>
          <Input id="global-search" name="q" type="search" placeholder="Search" />
        </form>
      </search>

      <StatusCluster />

      {logout.error !== null ? (
        <p className={"text-sm text-ink"} role="alert">
          {messageForError(logout.error)}
        </p>
      ) : null}

      <Menu
        label={session.data?.username ?? "Account"}
        items={[
          {
            id: "logout",
            label: "Sign out",
            onSelect: () => logout.mutate({}),
          },
          {
            id: "logout-everywhere",
            label: "Sign out everywhere",
            onSelect: () => logout.mutate({ everywhere: true }),
          },
        ]}
        loading={logout.isPending}
      />
    </header>
  );
}
