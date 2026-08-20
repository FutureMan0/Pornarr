/**
 * The top bar: the screen's name, global search, the artwork control, the live
 * status cluster and the account menu. Navigation is not in it — the sidebar
 * owns it above the phone breakpoint and the tab bar below one.
 *
 * Search lives in `global-search.tsx`. It grew a suggestion list and a keyboard
 * shortcut hint of its own, which is more state than a bar that is otherwise a
 * row of buttons should be holding.
 *
 * The screen's name is a real `<h1>` here rather than a copy of one rendered by
 * the route; see `page-title.tsx` for why the heading moved with the design
 * instead of being duplicated.
 *
 * The bar wraps rather than shrinking. On a 390px phone the controls and a
 * usable search field measured 412px of minimum width, so a single row could
 * only be paid for out of the one item that shrinks — leaving the search 26px
 * wide, about one character of its own placeholder. Search takes a line of its
 * own instead.
 */
import { Menu } from "@pornarr/ui";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { useLogout, useSession } from "../auth/session";
import type { ConnectionState } from "../errors/connection-status";
import { LOCALES, setLocale } from "../i18n";
import { localeName } from "../i18n/format";
import { messageForError } from "../lib/api-error";
import { setArtVisible, useArtVisible } from "../lib/art-visibility";
import { ConnectionPip } from "./connection-pip";
import { GlobalSearch } from "./global-search";
import { Notifications } from "./notifications";
import { usePublishedTitle } from "./page-title";
import type { SidebarLayout } from "./sidebar";
import { StatusCluster } from "./status-cluster";

export interface TopBarProps {
  readonly layout: SidebarLayout;
  readonly connection: ConnectionState;
}

export function TopBar({ layout, connection }: TopBarProps): JSX.Element {
  const { t } = useTranslation();
  const session = useSession();
  const logout = useLogout();
  const page = usePublishedTitle();
  const artVisible = useArtVisible();
  const phone = layout === "drawer";

  /**
   * On a phone the language lives in the account menu.
   *
   * Two menu buttons side by side in a 390-pixel header is two thirds of the
   * header, and "which language" and "which account" are the same kind of
   * question — a thing about you rather than about the screen. On a desktop
   * there is room for both, and a menu with two unrelated halves would be worse
   * than two menus.
   */
  const accountItems = [
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
    ...(phone
      ? LOCALES.map((locale) => ({
          id: locale,
          label: localeName(locale),
          onSelect: () => {
            void setLocale(locale);
          },
        }))
      : []),
  ];

  return (
    <header
      // The layout is named on the element for the same reason the sidebar
      // names its own: one viewport width produces one structure, and the name
      // is what a test and a reader can hold onto where jsdom lays nothing out.
      data-layout={layout}
      className={
        phone
          ? // Two rows on a phone, which is what fits: the screen's name and the
            // controls that belong to you, then the search across the full width.
            "sticky top-0 z-[var(--z-sticky)] flex flex-col gap-2 border-b border-border bg-surface-2 px-4 py-3"
          : "sticky top-0 z-[var(--z-sticky)] flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-surface-2 px-4 py-3"
      }
    >
      <div className={phone ? "flex items-center gap-2" : "contents"}>
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

        {phone ? null : <GlobalSearch />}

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

          {phone ? null : <StatusCluster />}

          <Notifications />

          <ConnectionPip state={connection} />

          {logout.error !== null ? (
            <p className={"text-sm text-ink"} role="alert">
              {messageForError(logout.error)}
            </p>
          ) : null}

          {/* The override. Each language names itself, so the entry a reader needs
            is legible even when the interface currently is not. On a phone it is
            folded into the account menu; see `accountItems`. */}
          {phone ? null : (
            // Hard against the right edge of the window, so the surface opens
            // leftwards. Growing rightwards from here means growing into the edge.
            <Menu
              align="end"
              label={t("locale.label")}
              items={LOCALES.map((locale) => ({
                id: locale,
                label: localeName(locale),
                onSelect: () => {
                  void setLocale(locale);
                },
              }))}
            />
          )}

          <Menu
            align="end"
            label={session.data?.username ?? t("account.menu")}
            items={accountItems}
            loading={logout.isPending}
          />
        </div>
      </div>

      {phone ? <GlobalSearch /> : null}
    </header>
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
