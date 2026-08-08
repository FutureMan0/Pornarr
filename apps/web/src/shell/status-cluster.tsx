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
import type { JSX } from "react";
import { useId, useState } from "react";

export function StatusCluster(): JSX.Element {
  const panelId = useId();
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <Button
        variant="ghost"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((previous) => !previous)}
      >
        Activity
      </Button>

      {open ? (
        <section
          id={panelId}
          aria-label="Activity"
          className="absolute right-0 top-full z-[var(--z-dropdown)] mt-2 flex w-[calc(var(--space-16)*5)] flex-col gap-2 rounded-lg bg-surface p-4 shadow-[var(--shadow-floating)]"
        >
          <p className={"text-sm text-ink"}>Nothing is transferring right now.</p>
          <p className={"text-xs text-ink-muted"}>
            Downloads, imports and their speeds appear here as they start.
          </p>
        </section>
      ) : null}
    </div>
  );
}
