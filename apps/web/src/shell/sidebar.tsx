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
import type { JSX, KeyboardEvent } from "react";
import { useEffect, useRef, useSyncExternalStore } from "react";
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
  { id: "downloads", path: "/downloads" },
  { id: "settings", path: "/settings" },
] as const satisfies readonly NavItem[];

export type NavId = (typeof NAV_ITEMS)[number]["id"];

const RAIL_QUERY = "(max-width: 1279.98px)";
const DRAWER_QUERY = "(max-width: 767.98px)";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

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

/** The drawer's element id, so the top bar's trigger can point at it. */
export const DRAWER_ID = "app-drawer";

export interface SidebarProps {
  readonly layout: SidebarLayout;
  /** Only meaningful in the drawer layout. */
  readonly open: boolean;
  /** Closes the drawer. The caller returns focus to the trigger it owns. */
  readonly onClose: () => void;
}

export function Sidebar({ layout, open, onClose }: SidebarProps): JSX.Element | null {
  const { t } = useTranslation();
  const drawerRef = useRef<HTMLDivElement>(null);
  const isDrawer = layout === "drawer";
  const isRail = layout === "rail";
  const counts = useNavCounts();
  const session = useSession();
  // Until the session answers, show the guest set. Rendering the admin entry
  // first and withdrawing it is worse than adding it a beat late.
  const isAdmin = session.data?.role === "admin";
  // `as const` narrows each entry, so the ones without the flag do not have the
  // property at all — hence the `in` rather than a plain read.
  const items = NAV_ITEMS.filter((item) => isAdmin || !("adminOnly" in item && item.adminOnly));

  // An overlay covers what the reader was reading, so focus moves into it and
  // stays until it closes. Returning focus afterwards belongs to the caller,
  // because the caller is what owns the trigger.
  useEffect(() => {
    if (!isDrawer || !open) return undefined;
    drawerRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    return undefined;
  }, [isDrawer, open]);

  const onDrawerKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;

    const node = drawerRef.current;
    if (node === null) return;
    const focusable = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE));
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (first === undefined || last === undefined) return;

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  if (isDrawer && !open) return null;

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
        // rule. Never both on one element.
        isDrawer ? "shadow-[var(--shadow-floating)]" : "border-r border-border",
        "bg-surface-2",
      )}
    >
      {isDrawer ? (
        <button
          type="button"
          className={cx(
            "text-sm",
            "self-end rounded-md border border-border-control px-2 py-1 text-ink-muted",
            "transition-colors duration-[var(--duration-fast)] ease-out",
            "hover:bg-surface-3 hover:text-ink",
          )}
          onClick={onClose}
        >
          {t("nav.close")}
        </button>
      ) : null}

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
                onClick={isDrawer ? onClose : undefined}
                className={({ isActive }) =>
                  cx(
                    "text-sm",
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

  if (!isDrawer) {
    return <div className="fixed inset-y-0 left-0 z-[var(--z-sticky)]">{nav}</div>;
  }

  return (
    <>
      {/* A control, not a decorated div: dismissing by clicking away is an
          action, and an action a pointer can take still needs a name. Keyboard
          users close with Escape or the Close button inside the drawer. */}
      <button
        type="button"
        className="fixed inset-0 z-[var(--z-backdrop)] bg-[color-mix(in_oklch,var(--bg)_72%,transparent)]"
        onClick={onClose}
      >
        <span className="visually-hidden">{t("nav.closeNavigation")}</span>
      </button>
      <div
        id={DRAWER_ID}
        ref={drawerRef}
        // biome-ignore lint/a11y/useSemanticElements: a native <dialog> is the better
        // element, but showModal() is unimplemented in the jsdom this workspace pins
        // (see packages/ui overlays.test.tsx), which would leave the drawer untestable.
        // The trap, Escape handling and focus return below supply what it would give.
        role="dialog"
        aria-modal="true"
        aria-label={t("nav.navigation")}
        className="fixed inset-y-0 left-0 z-[var(--z-modal)]"
        onKeyDown={onDrawerKeyDown}
      >
        {nav}
      </div>
    </>
  );
}

/**
 * Geometry only. The rail is 56px of icons, so an icon has to stand in for its
 * destination; the label travels beside it, hidden, for readers.
 */
function NavIcon({ id, label }: { readonly id: string; readonly label: string }): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 16 16"
      className="size-4 flex-none"
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

const NAV_ICON_PATHS: Readonly<Record<string, JSX.Element>> = {
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
