/**
 * The persistent connection indicator in the top bar.
 *
 * The design keeps this visible in all three states rather than only when
 * something is wrong, and that is the point of it: a dot that appears only on
 * failure cannot be trusted to be absent, because "no indicator" and "indicator
 * not rendering" look identical. One that is always there and reads *Live* is a
 * claim the interface is making continuously.
 *
 * It does not replace `ConnectionStatus`. That component explains an outage and
 * says what is being done about it, and appears only when there is one; this
 * one is the glance that is always there.
 *
 * The dot is never the signal on its own. DESIGN.md forbids colour-only state,
 * so the label is always rendered, and the dot's ring gives the three states
 * distinct shapes in greyscale.
 */
import type { JSX } from "react";
import { useTranslation } from "react-i18next";

import type { ConnectionState } from "../errors/connection-status";

const APPEARANCE = {
  online: {
    labelKey: "connection.pip.live",
    dot: "bg-[var(--pa-accent-500)] ring-[3px] ring-[color-mix(in_oklch,var(--pa-accent-500)_22%,transparent)]",
    text: "text-ink-muted",
  },
  reconnecting: {
    labelKey: "connection.pip.reconnecting",
    dot: "bg-[var(--pa-accent-400)] ring-[3px] ring-[color-mix(in_oklch,var(--pa-accent-400)_30%,transparent)]",
    text: "text-[var(--pa-accent-300)]",
  },
  offline: {
    // No ring: the one state that is not a live connection is also the one
    // without the halo, so the three read apart with the colour removed.
    labelKey: "connection.pip.offline",
    dot: "bg-[var(--pa-bg-4)]",
    text: "text-ink-muted",
  },
} as const satisfies Record<ConnectionState, { labelKey: string; dot: string; text: string }>;

export interface ConnectionPipProps {
  /** Derived by the shell; see the note on `ConnectionStatusProps`. */
  readonly state: ConnectionState;
}

export function ConnectionPip({ state }: ConnectionPipProps): JSX.Element {
  const { t } = useTranslation();
  const appearance = APPEARANCE[state];

  return (
    <span className={`flex items-center gap-2 px-1 text-2xs ${appearance.text}`}>
      <span className={`size-[7px] flex-none rounded-full ${appearance.dot}`} aria-hidden="true" />
      {t(appearance.labelKey)}
    </span>
  );
}
