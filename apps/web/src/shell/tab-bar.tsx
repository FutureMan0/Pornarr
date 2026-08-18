/**
 * The phone's navigation: a bar of tabs against the bottom edge.
 *
 * Section C of the delivered design has no sidebar on a phone and no drawer
 * behind a hamburger — four tabs along the bottom, where a thumb already is. The
 * application answered a phone with the desktop sidebar hidden behind a menu
 * button, which is a navigation you have to open before you can read.
 *
 * WHY FIVE AND NOT FOUR. The design's four are Feed, Library, Shorts and
 * Watchlist for a guest, and Server, Library, Queue and Settings for an
 * administrator. Between them they cannot reach Collections, Requests, Continue —
 * or, for a guest, Settings, which is where the accent and the language live. A
 * navigation that cannot reach a destination is a worse failure than one extra
 * tap, so a fifth entry opens a sheet with everything the four leave out. The
 * four keep the design's ordering and its icons.
 *
 * THE SETS ARE DERIVED, NOT LISTED TWICE. Which four appear comes from `NAV_ITEMS`
 * by id, so a destination cannot be in the tab bar without being a real one, and
 * "everywhere else" is literally everything else — a hand-kept second list would
 * silently stop mentioning a screen the first time somebody added one.
 */
import type { JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";

import { Sheet } from "@pornarr/ui";
import { useSession } from "../auth/session";
import { useNavCounts } from "./nav-counts";
import { NAV_ITEMS, NavIcon, type NavId, needsExactMatch } from "./sidebar";

/** The four the design shows, in its order. */
const GUEST_TABS: readonly NavId[] = ["feed", "library", "shorts", "watchlist"];
const ADMIN_TABS: readonly NavId[] = ["admin", "library", "downloads", "settings"];

export function TabBar(): JSX.Element {
  const { t } = useTranslation();
  const session = useSession();
  const counts = useNavCounts();
  const [moreOpen, setMoreOpen] = useState(false);

  const isAdmin = session.data?.role === "admin";
  // `in` rather than a property read: NAV_ITEMS is `as const`, so the entries
  // without the flag do not have the property at all.
  const visible = NAV_ITEMS.filter((item) => isAdmin || !("adminOnly" in item && item.adminOnly));
  const wanted = isAdmin ? ADMIN_TABS : GUEST_TABS;

  // Ordered by the design's list rather than by `NAV_ITEMS`, and silently short
  // if one is not available to this reader — which cannot happen for either set
  // as written, and is not worth crashing over if a set is ever edited.
  const tabs = wanted
    .map((id) => visible.find((item) => item.id === id))
    .filter((item) => item !== undefined);
  const rest = visible.filter((item) => !wanted.includes(item.id));

  return (
    <>
      {/* In flow, at the bottom of the shell's column. It was `fixed`, which
          meant every screen had to reserve its height and get that number right;
          as a flex child it simply takes the room it needs and the scrolling
          content above gets the rest. */}
      <nav
        aria-label={t("nav.tabs")}
        className="flex flex-none items-stretch gap-1 border-t border-border bg-[var(--pa-bg-00)] px-2 pt-2 pb-[calc(var(--space-3)+env(safe-area-inset-bottom,0px))]"
      >
        {tabs.map((item) => {
          const label = t(`nav.${item.id}`);
          const count = counts[item.id];
          return (
            <NavLink
              key={item.id}
              to={item.path}
              end={needsExactMatch(item.id)}
              className={({ isActive }) =>
                [
                  "relative flex flex-1 flex-col items-center gap-1 rounded-lg px-1 py-1.5",
                  "transition-colors duration-[var(--duration-fast)] ease-out",
                  isActive
                    ? "bg-[var(--primary-weak)] text-[var(--pa-accent-300)]"
                    : "text-ink-muted",
                ].join(" ")
              }
            >
              <NavIcon id={item.id} label={label} className="size-5" />
              <span className="text-2xs leading-none">{label}</span>
              {/* A dot, not a number. There is no room for "12" under a 20px
                  glyph, and what the reader needs from a tab bar is "something
                  is waiting", not how much. */}
              {count === undefined ? null : (
                <span
                  aria-hidden="true"
                  className="absolute right-[22%] top-1 size-1.5 rounded-full bg-[var(--pa-accent-400)]"
                />
              )}
            </NavLink>
          );
        })}

        <button
          type="button"
          onClick={() => setMoreOpen(true)}
          aria-expanded={moreOpen}
          className="flex flex-1 flex-col items-center gap-1 rounded-lg px-1 py-1.5 text-ink-muted transition-colors duration-[var(--duration-fast)] ease-out"
        >
          <MoreIcon />
          <span className="text-2xs leading-none">{t("nav.more")}</span>
        </button>
      </nav>

      <Sheet open={moreOpen} onClose={() => setMoreOpen(false)} title={t("nav.allDestinations")}>
        <ul className="flex flex-col gap-0.5">
          {rest.map((item) => {
            const label = t(`nav.${item.id}`);
            const count = counts[item.id];
            return (
              <li key={item.id}>
                <NavLink
                  to={item.path}
                  end={needsExactMatch(item.id)}
                  onClick={() => setMoreOpen(false)}
                  className={({ isActive }) =>
                    [
                      "flex items-center gap-3 rounded-lg px-2 py-3 text-sm",
                      "transition-colors duration-[var(--duration-fast)] ease-out",
                      isActive
                        ? "bg-[var(--primary-weak)] text-[var(--pa-accent-300)]"
                        : "text-ink",
                    ].join(" ")
                  }
                >
                  <NavIcon id={item.id} label={label} className="size-5 text-ink-muted" />
                  {label}
                  {count === undefined ? null : (
                    <span className="ml-auto text-2xs tabular-nums text-ink-muted">{count}</span>
                  )}
                </NavLink>
              </li>
            );
          })}
        </ul>
      </Sheet>
    </>
  );
}

function MoreIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 16 16" className="size-5" fill="currentColor" aria-hidden="true">
      <circle cx="3" cy="8" r="1.4" />
      <circle cx="8" cy="8" r="1.4" />
      <circle cx="13" cy="8" r="1.4" />
    </svg>
  );
}
