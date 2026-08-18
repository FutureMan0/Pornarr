/**
 * The left navigation, in its three structural forms.
 *
 * DESIGN.md: 240px fixed, a 56px icon rail below 1280px, an overlay drawer
 * below 768px. "Below" is read strictly — at exactly 1280px the sidebar is
 * still full width — so each query stops just short of its breakpoint.
 *
 * The form is decided in JavaScript rather than by CSS media queries alone,
 * because the rail hides labels and the drawer changes the element's role, its
 * layer and its focus behaviour. Those are not things a media query can
 * express, and deriving all three from one value is what keeps the class, the
 * ARIA and the focus trap from disagreeing.
 */
import { cx } from "@pornarr/ui";
import type { JSX } from "react";
import { useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";

import { useSession } from "../auth/session";
import { useNavCounts } from "./nav-counts";
import { SidebarBrand, SidebarIdentity } from "./sidebar-identity";

export type SidebarLayout = "full" | "rail" | "drawer";

export interface NavItem {
  readonly id: string;
  readonly path: string;
  /**
   * Hidden from guests. Not a security measure — the endpoints behind it check
   * the role themselves — but a destination that answers 403 has no business
   * being in someone's navigation.
   */
  readonly adminOnly?: boolean;
}

/**
 * The shell's destinations. `routes.tsx` builds the route table from this.
 *
 * No label here: the id *is* the key under `nav` in the locale files, so a
 * destination cannot be added without a translated name, and the check is the
 * compiler's rather than a reviewer's.
 */
export const NAV_ITEMS = [
  { id: "admin", path: "/admin", adminOnly: true },
  { id: "moderation", path: "/admin/moderation", adminOnly: true },
  { id: "scan", path: "/admin/scan", adminOnly: true },
  { id: "tags", path: "/admin/tags", adminOnly: true },
  { id: "invites", path: "/admin/invites", adminOnly: true },
  { id: "feed", path: "/feed" },
  { id: "continue", path: "/continue" },
  { id: "library", path: "/library" },
  { id: "shorts", path: "/shorts" },
  { id: "collections", path: "/collections" },
  { id: "watchlist", path: "/watchlist" },
  { id: "requests", path: "/requests" },
  // Search is a destination, not only the field in the top bar. A field is a
  // shortcut for someone who already knows they want to search; it is not how
  // the screen is discovered, and until it was listed here `/search` could be
  // reached only by typing or by URL.
  { id: "search", path: "/search" },
  { id: "monitors", path: "/monitors" },
  { id: "recommendations", path: "/recommendations" },
  // `/api/queue` and `/api/queue/summary` both require the admin role, so this
  // screen answers a guest with 403 and nothing else. It sat in everyone's
  // navigation until today, which is the same mistake the five entries above
  // avoid by declaring it.
  { id: "downloads", path: "/downloads", adminOnly: true },
  { id: "settings", path: "/settings" },
] as const satisfies readonly NavItem[];

export type NavId = (typeof NAV_ITEMS)[number]["id"];

/**
 * Entries whose path is a prefix of another entry's, and which therefore have to
 * match exactly.
 *
 * `NavLink` treats a prefix as active. `/admin` is a prefix of `/admin/tags`,
 * `/admin/scan`, `/admin/moderation` and `/admin/invites`, so Dashboard stayed
 * marked on every administrator screen and two entries read as current at once.
 *
 * Derived rather than hand-marked. A list of exceptions maintained by hand goes
 * stale the first time somebody adds a destination under an existing one, and it
 * goes stale silently — the symptom is a highlight, not an error. This cannot.
 *
 * Only nav entries count. `/library/:mediaId` is not one, so Library correctly
 * stays marked while you are reading a title; `/shorts/browse` is not one either,
 * for the same reason.
 */
const NEEDS_EXACT_MATCH: ReadonlySet<string> = new Set(
  NAV_ITEMS.filter((item) =>
    NAV_ITEMS.some((other) => other !== item && other.path.startsWith(`${item.path}/`)),
  ).map((item) => item.id),
);

/** Whether this destination has to match the address exactly. See above. */
export function needsExactMatch(id: string): boolean {
  return NEEDS_EXACT_MATCH.has(id);
}

const RAIL_QUERY = "(max-width: 1279.98px)";
const DRAWER_QUERY = "(max-width: 767.98px)";

function readLayout(): SidebarLayout {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "full";
  if (window.matchMedia(DRAWER_QUERY).matches) return "drawer";
  if (window.matchMedia(RAIL_QUERY).matches) return "rail";
  return "full";
}

function subscribeToLayout(onChange: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
  const lists = [window.matchMedia(RAIL_QUERY), window.matchMedia(DRAWER_QUERY)];
  for (const list of lists) list.addEventListener("change", onChange);
  return () => {
    for (const list of lists) list.removeEventListener("change", onChange);
  };
}

/** The current structural form of the sidebar. */
export function useSidebarLayout(): SidebarLayout {
  return useSyncExternalStore(subscribeToLayout, readLayout, () => "full");
}

/**
 * Is this a phone?
 *
 * One definition, shared. Screens that have to *choose their markup* rather than
 * restyle it — a table that becomes a list of cards, and nothing else so far —
 * need the same answer the shell used to decide on a tab bar, or the two will
 * disagree at the boundary and something will render twice or not at all.
 *
 * Restyling is still CSS's job. Reach for this only when the two layouts are
 * different elements, because rendering both and hiding one doubles what a
 * screen reader walks.
 */
export function usePhoneLayout(): boolean {
  return useSidebarLayout() === "drawer";
}

export interface SidebarProps {
  readonly layout: SidebarLayout;
}

/**
 * The sidebar, full or as a rail.
 *
 * It used to have a third shape: an overlay drawer for phones, opened from a
 * button in the top bar. A phone navigates from a tab bar now, so the drawer was
 * unreachable — and an unreachable overlay carrying its own focus trap, its own
 * Escape handling and its own backdrop is exactly the kind of thing that rots
 * into a bug nobody can reproduce. It is gone rather than left in place.
 */
export function Sidebar({ layout }: SidebarProps): JSX.Element | null {
  const { t } = useTranslation();
  const isRail = layout === "rail";
  const counts = useNavCounts();
  const session = useSession();
  // Until the session answers, show the guest set. Rendering the admin entry
  // first and withdrawing it is worse than adding it a beat late.
  const isAdmin = session.data?.role === "admin";
  // `as const` narrows each entry, so the ones without the flag do not have the
  // property at all — hence the `in` rather than a plain read.
  const items = NAV_ITEMS.filter((item) => isAdmin || !("adminOnly" in item && item.adminOnly));

  const nav = (
    <nav
      aria-label={t("nav.primary")}
      data-layout={layout}
      className={cx(
        "flex h-full flex-col gap-1 p-2",
        layout === "rail"
          ? "w-[var(--layout-sidebar-rail-width)]"
          : "w-[var(--layout-sidebar-width)]",
        // DESIGN.md: a floating layer takes the shadow, a docked one takes the
        // rule. This one is docked.
        "border-r border-border",
        "bg-surface-2",
      )}
    >
      <SidebarBrand compact={isRail} />
      <SidebarIdentity compact={isRail} />

      <ul className="flex flex-col gap-0.5">
        {items.map((item) => {
          const label = t(`nav.${item.id}`);
          const count = counts[item.id];
          return (
            <li key={item.id}>
              <NavLink
                to={item.path}
                end={NEEDS_EXACT_MATCH.has(item.id)}
                className={({ isActive }) =>
                  cx(
                    "text-sm",
                    // `pa-nav` owns the leading accent bar; see utilities.css for
                    // why it is a scaled pseudo-element keyed off aria-current
                    // rather than a class this file toggles.
                    "pa-nav",
                    "flex items-center gap-2 rounded-md px-2 py-2",
                    "transition-colors duration-[var(--duration-fast)] ease-out",
                    "hover:bg-surface-3 hover:text-ink",
                    isRail && "justify-center",
                    // Exactly one text colour, chosen here rather than layered.
                    // Emitting both `text-ink-muted` and the accent and letting
                    // the cascade decide is how the active label ended up muted
                    // on its own wash at 4.45:1 — the two utilities share a
                    // specificity band and source order picks the winner.
                    isActive ? "text-[var(--pa-accent-300)]" : "text-ink-muted",
                    // Accent as a line, not a flood.
                    isActive && "bg-[var(--primary-weak)]",
                  )
                }
              >
                <NavIcon id={item.id} label={label} />
                {/* The rail keeps the label for assistive technology; only the
                    pixels go away. */}
                <span className={isRail ? "visually-hidden" : undefined}>{label}</span>
                {count === undefined ? null : (
                  <span
                    className={cx(
                      "text-2xs tabular-nums text-ink-muted",
                      isRail ? "visually-hidden" : "ml-auto",
                    )}
                  >
                    {count}
                  </span>
                )}
              </NavLink>
            </li>
          );
        })}
      </ul>
    </nav>
  );

  return <div className="fixed inset-y-0 left-0 z-[var(--z-sticky)]">{nav}</div>;
}

/**
 * Geometry only. The rail is 56px of icons, so an icon has to stand in for its
 * destination; the label travels beside it, hidden, for readers.
 */
export function NavIcon({
  id,
  label,
  className,
}: {
  readonly id: string;
  readonly label: string;
  /** The rail draws these at 16px, the tab bar at 20. */
  readonly className?: string | undefined;
}): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 16 16"
      className={cx("flex-none", className ?? "size-4")}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <title>{label}</title>
      {NAV_ICON_PATHS[id]}
    </svg>
  );
}

export const NAV_ICON_PATHS: Readonly<Record<string, JSX.Element>> = {
  /* The administrator's five had no glyph at all. That was invisible in the
     sidebar, where they are indented under a heading and read as a group, and
     obvious the moment the tab bar and the "everywhere else" sheet put them in a
     flat list beside destinations that have one. */
  admin: (
    <>
      <path d="M2.5 11.5a5.5 5.5 0 1 1 11 0" />
      <path d="M8 11.5 10.5 7" />
    </>
  ),
  moderation: (
    <>
      <path d="M13.5 8.5a5 5 0 0 1-5 5H3.5l1.2-2A5 5 0 1 1 13.5 8.5Z" />
      <path d="M8 5.6l.8 1.6 1.7.25-1.25 1.2.3 1.7L8 9.55l-1.55.8.3-1.7L5.5 7.45l1.7-.25z" />
    </>
  ),
  scan: (
    <>
      <path d="M2.5 5.5v-2a1 1 0 0 1 1-1h2M10.5 2.5h2a1 1 0 0 1 1 1v2M13.5 10.5v2a1 1 0 0 1-1 1h-2M5.5 13.5h-2a1 1 0 0 1-1-1v-2" />
      <path d="M2.5 8h11" />
    </>
  ),
  tags: (
    <>
      <path d="M8.3 2.5H3.5a1 1 0 0 0-1 1v4.8a1 1 0 0 0 .3.7l5 5a1 1 0 0 0 1.4 0l4.3-4.3a1 1 0 0 0 0-1.4l-5-5a1 1 0 0 0-.7-.3Z" />
      <circle cx="5.6" cy="5.6" r="0.9" fill="currentColor" stroke="none" />
    </>
  ),
  invites: (
    <>
      <rect x="2.5" y="3.5" width="11" height="9" rx="1" />
      <path d="m2.8 4.3 5.2 4 5.2-4" />
    </>
  ),
  feed: (
    <>
      <circle cx="8" cy="12" r="1.25" fill="currentColor" stroke="none" />
      <path d="M2.5 8.5a6 6 0 0 1 5 4M2.5 4.5a10 10 0 0 1 9 8" />
    </>
  ),
  continue: (
    <>
      <circle cx="8" cy="8" r="6" />
      <path d="M6.75 5.75 10.5 8l-3.75 2.25Z" />
    </>
  ),
  shorts: (
    <>
      <rect x="4.5" y="1.5" width="7" height="13" rx="1.5" />
      <path d="M7 12.5h2" />
    </>
  ),
  collections: (
    <>
      <path d="M2.5 5.5h5l1 1.5h5v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1Z" />
      <path d="M4 5.5V4a1 1 0 0 1 1-1h2.2" />
    </>
  ),
  watchlist: <path d="M4 2.5h8v11l-4-2.75L4 13.5Z" />,
  library: (
    <>
      <rect x="1.5" y="2.5" width="5" height="5" rx="1" />
      <rect x="9.5" y="2.5" width="5" height="5" rx="1" />
      <rect x="1.5" y="9.5" width="5" height="4" rx="1" />
      <rect x="9.5" y="9.5" width="5" height="4" rx="1" />
    </>
  ),
  requests: (
    <>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 5.5v5M5.5 8h5" />
    </>
  ),
  search: (
    <>
      <circle cx="7" cy="7" r="4.5" />
      <path d="m10.25 10.25 3.25 3.25" />
    </>
  ),
  monitors: (
    <>
      <path d="M1.25 8s2.5-4.25 6.75-4.25S14.75 8 14.75 8s-2.5 4.25-6.75 4.25S1.25 8 1.25 8Z" />
      <circle cx="8" cy="8" r="1.75" />
    </>
  ),
  recommendations: (
    <>
      <path d="m8 1.75 1.9 3.85 4.25.62-3.08 3 .73 4.23L8 11.45l-3.8 2 .73-4.23-3.08-3 4.25-.62Z" />
    </>
  ),
  downloads: (
    <>
      <path d="M8 2v7" />
      <path d="M5 6.5 8 9.5l3-3" />
      <path d="M2.5 11.5v1a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-1" />
    </>
  ),
  settings: (
    <>
      <circle cx="8" cy="8" r="2.25" />
      <path d="M8 1.5v1.75M8 12.75v1.75M1.5 8h1.75M12.75 8h1.75M3.4 3.4l1.24 1.24M11.36 11.36l1.24 1.24M12.6 3.4l-1.24 1.24M4.64 11.36 3.4 12.6" />
    </>
  ),
};
