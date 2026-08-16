/**
 * The top bar: global search, the live status cluster, the account menu, and —
 * in the drawer layout only — the control that opens the navigation.
 *
 * The global form owns only navigation. The route owns fetching, filters and
 * progressive result state, so the same search address works from the shell,
 * a bookmark and a browser history entry.
 */
import { Input, Menu } from "@pornarr/ui";
import type { FormEvent, JSX, RefObject } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useLogout, useSession } from "../auth/session";
import { LOCALES, setLocale } from "../i18n";
import { localeName } from "../i18n/format";
import { messageForError } from "../lib/api-error";
import { NotificationMenu } from "../components/notifications/notification-menu";
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
            {t("nav.navigation")}
          </button>
        </div>
      ) : null}

      {/* The element, not the role: `<search>` carries it natively. */}
      <search className="min-w-0 flex-1">
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

      <StatusCluster />
      <NotificationMenu />

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
    </header>
  );
}
