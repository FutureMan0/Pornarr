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
import { Button } from "@pornarr/ui";
import type { FocusEvent, JSX } from "react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

export function StatusCluster(): JSX.Element {
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

  return (
    // React implements onBlur with focusout, so it bubbles from the trigger.
    // The panel's 320px hangs off the trigger's right edge, which is why the
    // phone layout leaves the cluster out of the bar altogether rather than
    // placing it: the tab bar owns that row now.
    <div className="relative" onBlur={onBlur}>
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
          className="absolute top-full right-0 z-[var(--z-dropdown)] mt-2 flex w-[calc(var(--space-16)*5)] flex-col gap-2 rounded-lg bg-surface p-4 shadow-[var(--shadow-floating)]"
        >
          <p className={"text-sm text-ink"}>{t("activity.idle")}</p>
          <p className={"text-xs text-ink-muted"}>{t("activity.hint")}</p>
        </section>
      ) : null}
    </div>
  );
}
