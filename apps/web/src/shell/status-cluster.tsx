/**
 * The global status cluster — placeholder.
 *
 * DESIGN.md: persistent in the top bar, showing active downloads, aggregate
 * speed and the quarantine count when non-zero; clicking opens a *panel*, not a
 * page. The panel behaviour is the part worth building now, because it is the
 * part later work would otherwise get wrong: this is a disclosure beside the
 * trigger, not a route, and it must never become one.
 *
 * The numbers are absent rather than faked. Nothing emits download events yet
 * (see `lib/events.ts`), and a hard-coded "3 active" is a screenshot, not a
 * component.
 */
import { Button, cx } from "@pornarr/ui";
import type { FocusEvent, JSX } from "react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { SidebarLayout } from "./sidebar";

export interface StatusClusterProps {
  /**
   * Only to place the panel. Its 320px hung off the trigger's right edge, and
   * the trigger sits mid-row, so on a 390px phone 126px of the panel was left
   * of the viewport and unreadable. Below the drawer breakpoint it becomes a
   * sheet spanning the bar instead — the trigger is still what opens it.
   */
  readonly layout: SidebarLayout;
}

export function StatusCluster({ layout }: StatusClusterProps): JSX.Element {
  const { t } = useTranslation();
  const panelId = useId();
  const [open, setOpen] = useState(false);

  // Focus leaving the cluster closes it — the same rule `@pornarr/ui`'s Menu
  // already follows, which is what makes the top bar show one panel at a time.
  // Without it, opening Activity and then a menu left both on screen: the menu
  // dismisses itself when the cluster takes focus, but nothing dismissed the
  // cluster when a menu took it back.
  const onBlur = (event: FocusEvent<HTMLDivElement>): void => {
    const next = event.relatedTarget;
    if (next instanceof Node && event.currentTarget.contains(next)) return;
    setOpen(false);
  };

  const isDrawer = layout === "drawer";

  return (
    // React implements onBlur with focusout, so it bubbles from the trigger.
    // Unpositioned in the drawer layout on purpose: that hands the panel's
    // containing block to the sticky top bar, which is the width it should span.
    <div className={isDrawer ? undefined : "relative"} onBlur={onBlur}>
      <Button
        variant="ghost"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((previous) => !previous)}
      >
        {t("activity.title")}
      </Button>

      {open ? (
        <section
          id={panelId}
          aria-label={t("activity.title")}
          className={cx(
            "absolute top-full z-[var(--z-dropdown)] mt-2 flex flex-col gap-2 rounded-lg bg-surface p-4 shadow-[var(--shadow-floating)]",
            isDrawer ? "inset-x-4" : "right-0 w-[calc(var(--space-16)*5)]",
          )}
        >
          <p className={"text-sm text-ink"}>{t("activity.idle")}</p>
          <p className={"text-xs text-ink-muted"}>{t("activity.hint")}</p>
        </section>
      ) : null}
    </div>
  );
}
