/**
 * The top bar: global search, the live status cluster, the account menu, and —
 * in the drawer layout only — the control that opens the navigation.
 *
 * The global form owns only navigation. The route owns fetching, filters and
 * progressive result state, so the same search address works from the shell,
 * a bookmark and a browser history entry.
 *
 * The bar is one row of controls until the drawer breakpoint and two below it.
 * On a 390px phone the four controls and a usable search field measured 412px
 * of minimum width, so a single row could only be paid for out of the one item
 * that shrinks — leaving the search 26px wide, about one character of its own
 * placeholder. Wrapping gives search the full width of a line of its own, which
 * is what every reference does at that width, and the drawer trigger becomes
 * the icon it already is everywhere else so the remaining row fits.
 */
import { Input, Menu, cx } from "@pornarr/ui";
import type { FormEvent, JSX, RefObject } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useLogout, useSession } from "../auth/session";
import { LOCALES, setLocale } from "../i18n";
import { localeName } from "../i18n/format";
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
  const { t } = useTranslation();
  const navigate = useNavigate();
  const session = useSession();
  const logout = useLogout();
  const searchFormRef = useRef<HTMLFormElement>(null);
  const [query, setQuery] = useState("");

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
      className={cx(
        "sticky top-0 z-[var(--z-sticky)] flex items-center border-b border-border bg-surface-2 px-4 py-3",
        isDrawer ? "flex-wrap gap-2" : "gap-4",
      )}
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

      {/* The element, not the role: `<search>` carries it natively. Last in the
          wrapped bar, and a full line wide, so it is never the item that pays
          for the row. */}
      <search className={cx("min-w-0", isDrawer ? "order-last w-full" : "flex-1")}>
        <form ref={searchFormRef} onSubmit={onSearchSubmit}>
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
          />
        </form>
      </search>

      <StatusCluster layout={layout} />

      {logout.error !== null ? (
        <p className={"text-sm min-w-0 text-ink"} role="alert">
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
    </header>
  );
}
