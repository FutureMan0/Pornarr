/**
 * Whether the application is still talking to anything, and what it is doing
 * about it.
 *
 * TWO SIGNALS, NOT ONE. The browser being offline (`navigator.onLine`) and the
 * event stream dropping (`EventSource` erroring) are different failures with
 * different remedies, and collapsing them into one boolean would tell a user on
 * a live network that their device is offline. Offline wins when both are true,
 * because a device with no network is the cause and the dead stream is only the
 * symptom.
 *
 * NEITHER IS RETRIED HERE. The browser reconnects an EventSource on its own and
 * restores the network on its own; this hook watches. What it does own is the
 * consequence: when the connection comes back, everything cached was fetched
 * before an unknown gap in the event stream, so the cache is invalidated and the
 * screen refills. Recovering the connection but leaving a stale screen is the
 * failure mode that looks like it worked.
 *
 * COLOUR IS NOT THE SIGNAL. DESIGN.md: never signal state by colour alone. Each
 * state carries a distinct icon shape and a text label, and reads correctly in
 * greyscale.
 */
import { cx } from "@pornarr/ui";
import { useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useEffect, useRef, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import type { EventStreamStatus } from "../lib/events";

export type ConnectionState = "online" | "offline" | "reconnecting";

function readOnline(): boolean {
  return typeof navigator === "undefined" ? true : navigator.onLine;
}

function subscribeToOnline(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

/** Whether the browser believes it has a network. Server snapshot is `true`. */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribeToOnline, readOnline, () => true);
}

/**
 * The single state the interface shows, and the cache invalidation that follows
 * a recovery. One owner, so a refill cannot happen twice or not at all.
 */
export function useConnectionState(status: EventStreamStatus): ConnectionState {
  const online = useOnline();
  const queryClient = useQueryClient();

  const state: ConnectionState = !online
    ? "offline"
    : status === "reconnecting"
      ? "reconnecting"
      : "online";

  const previous = useRef<ConnectionState>(state);

  useEffect(() => {
    if (previous.current === state) return;
    const recovered = state === "online";
    previous.current = state;
    // No key: an outage has no scope, and anything fetched before it may have
    // missed an event that would otherwise have invalidated it.
    if (recovered) void queryClient.invalidateQueries();
  }, [state, queryClient]);

  return state;
}

/**
 * Keys, not sentences — named `*Key` so that the check in
 * `no-hardcoded-strings.test.ts` is not asked to tell the difference.
 */
const COPY = {
  offline: { titleKey: "connection.offlineTitle", bodyKey: "connection.offlineBody" },
  reconnecting: {
    titleKey: "connection.reconnectingTitle",
    bodyKey: "connection.reconnectingBody",
  },
} as const;

export interface ConnectionStatusProps {
  /**
   * The already-derived state, not the raw stream status. `useConnectionState`
   * invalidates the cache when a connection recovers, so it must have exactly
   * one caller; the shell owns that call and hands the answer to everything
   * that displays it.
   */
  readonly state: ConnectionState;
}

/**
 * The live region is always mounted and usually empty. A region inserted into
 * the document with its text already in it is announced inconsistently; a region
 * that is already there and then changes is announced everywhere. Nothing here
 * takes focus — DESIGN.md wants this reported, not thrust in front of the reader.
 */
export function ConnectionStatus({ state }: ConnectionStatusProps): JSX.Element {
  const { t } = useTranslation();

  return (
    <div
      // biome-ignore lint/a11y/useSemanticElements: <output> is the element with this
      // role, but its content model is phrasing content and the strip below is a flow
      // container. A div carrying the role is valid HTML with the same accessibility
      // tree; an <output> wrapping a <div> is neither.
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      {state === "online" ? null : (
        <div
          className={cx(
            "mb-6 flex items-center gap-3 rounded-md border border-border-control",
            "bg-surface-2 px-3 py-2",
          )}
        >
          <ConnectionIcon state={state} />
          <span className={"text-sm text-ink"}>{t(COPY[state].titleKey)}</span>
          <span className={"text-xs text-ink-muted"}>{t(COPY[state].bodyKey)}</span>
        </div>
      )}
    </div>
  );
}

/**
 * Geometry only, and deliberately two different shapes rather than two tints: a
 * struck-through circle for no network, an open arc for a retry in progress.
 * Hidden from assistive technology because the label beside it says the same
 * thing in words.
 */
function ConnectionIcon({ state }: { readonly state: "offline" | "reconnecting" }): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 16 16"
      className="size-4 flex-none text-ink-muted"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {state === "offline" ? (
        <>
          <circle cx="8" cy="8" r="6" />
          <path d="M3.8 12.2 12.2 3.8" />
        </>
      ) : (
        <>
          <path d="M13.5 8a5.5 5.5 0 1 1-1.7-3.9" />
          <path d="M13.5 2.5v3h-3" />
        </>
      )}
    </svg>
  );
}
