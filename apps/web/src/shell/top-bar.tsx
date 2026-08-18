/**
 * The top bar: the screen's name, global search, the artwork control, the live
 * status cluster, the account menu, and — in the drawer layout only — the
 * control that opens the navigation.
 *
 * The global form owns only navigation. The route owns fetching, filters and
 * progressive result state, so the same search address works from the shell,
 * a bookmark and a browser history entry.
 *
 * The screen's name is a real `<h1>` here rather than a copy of one rendered by
 * the route; see `page-title.tsx` for why the heading moved with the design
 * instead of being duplicated.
 *
 * The bar wraps rather than shrinking. On a 390px phone the controls and a
 * usable search field measured 412px of minimum width, so a single row could
 * only be paid for out of the one item that shrinks — leaving the search 26px
 * wide, about one character of its own placeholder. Search takes a line of its
 * own below `sm`, and the drawer trigger is the icon it already is everywhere
 * else so the remaining row fits.
 */
import { Input, Menu } from "@pornarr/ui";
import type { FormEvent, JSX, RefObject } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useLogout, useSession } from "../auth/session";
import type { ConnectionState } from "../errors/connection-status";
import { LOCALES, setLocale } from "../i18n";
import { localeName } from "../i18n/format";
import { messageForError } from "../lib/api-error";
import { setArtVisible, useArtVisible } from "../lib/art-visibility";
import { ConnectionPip } from "./connection-pip";
import { usePublishedTitle } from "./page-title";
import { DRAWER_ID, type SidebarLayout } from "./sidebar";
import { StatusCluster } from "./status-cluster";

export interface TopBarProps {
  readonly layout: SidebarLayout;
  readonly drawerOpen: boolean;
  readonly onOpenDrawer: () => void;
  readonly connection: ConnectionState;
  /**
   * Wraps the drawer trigger so the shell can put focus back on it when the
   * drawer closes. A ref on the wrapper rather than the button, because
   * `@pornarr/ui`'s Button does not take one.
   */
  readonly triggerRef: RefObject<HTMLDivElement | null>;
}

export function TopBar({
  layout,
  drawerOpen,
  onOpenDrawer,
  connection,
  triggerRef,
}: TopBarProps): JSX.Element {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const session = useSession();
  const logout = useLogout();
  const searchFormRef = useRef<HTMLFormElement>(null);
  const [query, setQuery] = useState("");
  const page = usePublishedTitle();
  const artVisible = useArtVisible();

  useEffect(() => {
    const focusSearch = (event: KeyboardEvent): void => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "k") return;
      event.preventDefault();
      searchFormRef.current?.querySelector<HTMLInputElement>("input")?.focus();
    };
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);

  const onSearchSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const trimmed = query.trim();
    navigate(trimmed === "" ? "/search" : `/search?q=${encodeURIComponent(trimmed)}`);
  };

  const isDrawer = layout === "drawer";

  return (
    <header
      // The layout is named on the element for the same reason the sidebar names
      // its own: one viewport width produces one structure, and the name is what
      // a test and a reader can hold onto where jsdom lays nothing out.
      data-layout={layout}
      className="sticky top-0 z-[var(--z-sticky)] flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-surface-2 px-4 py-3"
    >
      {isDrawer ? (
        <div ref={triggerRef} className="contents">
          <button
            type="button"
            aria-expanded={drawerOpen}
            aria-controls={drawerOpen ? DRAWER_ID : undefined}
            // The name stays; only the pixels go away. The word "Navigation" is
            // 90px of a 358px row that has to hold three more controls.
            aria-label={t("nav.navigation")}
            onClick={onOpenDrawer}
            className={
              "me-auto rounded-md border border-border-control p-2 text-ink-muted transition-colors duration-[var(--duration-fast)] ease-out hover:bg-surface-3 hover:text-ink"
            }
          >
            <svg
              aria-hidden="true"
              focusable="false"
              viewBox="0 0 16 16"
              className="size-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            >
              <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />
            </svg>
          </button>
        </div>
      ) : null}

      {/* The page's only h1. A screen declares it with `usePageTitle`; until one
          does there is nothing to render, and rendering the product name here
          instead would give every screen the same heading. */}
      {page === null ? null : (
        <div className="flex min-w-0 flex-none flex-col gap-px sm:min-w-[10.5rem]">
          <h1 className="text-lg leading-tight tracking-[-0.02em] text-ink">{page.title}</h1>
          {page.subtitle === undefined ? null : (
            <span className="text-2xs text-ink-muted">{page.subtitle}</span>
          )}
        </div>
      )}

      {/* The element, not the role: `<search>` carries it natively. It wraps to
          its own line on a phone rather than squeezing every control into one
          row, so it is never the item that pays for the row. Nothing is hidden
          at any width: a control that vanishes below a breakpoint is a control
          somebody cannot find. */}
      <search className="order-last w-full min-w-0 basis-full sm:order-none sm:w-auto sm:max-w-[26rem] sm:flex-1 sm:basis-auto">
        <form ref={searchFormRef} onSubmit={onSearchSubmit} className="relative">
          <label className="visually-hidden" htmlFor="global-search">
            {t("search.label")}
          </label>
          <Input
            id="global-search"
            name="q"
            type="search"
            value={query}
            placeholder={t("search.placeholder")}
            onChange={(event) => setQuery(event.target.value)}
            leading={<SearchIcon />}
          />
        </form>
      </search>

      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
        {/* Not a decorative eye: every tile in the application is blurred until
            this is pressed. The label states the current state rather than the
            action, which is what the design shows and what a screen reader
            needs from a control whose effect is purely visual. */}
        <button
          type="button"
          aria-pressed={artVisible}
          onClick={() => setArtVisible(!artVisible)}
          className="flex items-center gap-2 rounded-md border border-border-control px-2 py-1 text-xs text-ink-muted transition-colors duration-[var(--duration-fast)] ease-out hover:bg-surface-3 hover:text-ink"
        >
          <EyeIcon open={artVisible} />
          {/* One element, not two: the words are hidden visually on a phone
              and shown from `sm` up, while the accessible name is the same at
              every width. Two spans toggled by breakpoint would put the label
              in the accessibility tree twice wherever CSS has not loaded. */}
          <span className="sr-only sm:not-sr-only">
            {artVisible ? t("artwork.shown") : t("artwork.hidden")}
          </span>
        </button>

        <StatusCluster layout={layout} />

        <ConnectionPip state={connection} />

        {logout.error !== null ? (
          <p className={"text-sm text-ink"} role="alert">
            {messageForError(logout.error)}
          </p>
        ) : null}

        {/* The override. Each language names itself, so the entry a reader needs
            is legible even when the interface currently is not. */}
        <Menu
          label={t("locale.label")}
          items={LOCALES.map((locale) => ({
            id: locale,
            label: localeName(locale),
            onSelect: () => {
              void setLocale(locale);
            },
          }))}
        />

        <Menu
          label={session.data?.username ?? t("account.menu")}
          items={[
            {
              id: "logout",
              label: t("account.signOut"),
              onSelect: () => logout.mutate({}),
            },
            {
              id: "logout-everywhere",
              label: t("account.signOutEverywhere"),
              onSelect: () => logout.mutate({ everywhere: true }),
            },
          ]}
          loading={logout.isPending}
        />
      </div>
    </header>
  );
}

function SearchIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
      className="size-3.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    >
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5 14 14" />
    </svg>
  );
}

/**
 * Two shapes, not one shape in two tints: an open eye and a struck-through one.
 * Hidden from assistive technology because the button's own label already says
 * which state it is in.
 */
function EyeIcon({ open }: { readonly open: boolean }): JSX.Element {
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
      className="size-4 flex-none"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M1.5 8S3.9 3.8 8 3.8 14.5 8 14.5 8 12.1 12.2 8 12.2 1.5 8 1.5 8Z" />
      <circle cx="8" cy="8" r="1.9" />
      {open ? null : <path d="M3 13 13 3" />}
    </svg>
  );
}
