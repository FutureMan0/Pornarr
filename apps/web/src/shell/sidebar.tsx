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
import { NavLink } from "react-router-dom";

export type SidebarLayout = "full" | "rail" | "drawer";

export interface NavItem {
  readonly id: string;
  readonly path: string;
  readonly label: string;
}

/** The shell's destinations. `routes.tsx` builds the route table from this. */
export const NAV_ITEMS: readonly NavItem[] = [
  { id: "library", path: "/library", label: "Library" },
  { id: "requests", path: "/requests", label: "Requests" },
  { id: "downloads", path: "/downloads", label: "Downloads" },
  { id: "settings", path: "/settings", label: "Settings" },
];

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
  const drawerRef = useRef<HTMLDivElement>(null);
  const isDrawer = layout === "drawer";

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
      aria-label="Primary"
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
          Close
        </button>
      ) : null}

      <ul className="flex flex-col gap-1">
        {NAV_ITEMS.map((item) => (
          <li key={item.id}>
            <NavLink
              to={item.path}
              onClick={isDrawer ? onClose : undefined}
              className={({ isActive }) =>
                cx(
                  "text-sm",
                  "flex items-center gap-3 rounded-md px-2 py-2 text-ink-muted",
                  "transition-colors duration-[var(--duration-fast)] ease-out",
                  "hover:bg-surface-3 hover:text-ink",
                  layout === "rail" && "justify-center",
                  isActive && "bg-[var(--primary-weak)] text-ink",
                )
              }
            >
              <NavIcon id={item.id} label={item.label} />
              {/* The rail keeps the label for assistive technology; only the
                  pixels go away. */}
              <span className={layout === "rail" ? "visually-hidden" : undefined}>
                {item.label}
              </span>
            </NavLink>
          </li>
        ))}
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
        <span className="visually-hidden">Close navigation</span>
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
        aria-label="Navigation"
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
