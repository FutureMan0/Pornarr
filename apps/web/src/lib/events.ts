/**
 * The live event stream, folded into the query cache.
 *
 * `GET /api/events` is a cookie-authenticated `text/event-stream`. Three
 * properties of it shape this file:
 *
 * 1. Frames are *named* events, so `onmessage` never fires. Each type needs its
 *    own `addEventListener`, which is why the twelve names below are a list and
 *    not a comment.
 * 2. `data` is a JSON string and `openapi.json` does not type it. The envelope
 *    is therefore validated here with zod before anything reaches the cache — a
 *    malformed frame must be dropped, never rendered.
 * 3. Event ids are Redis stream ids. EventSource replays `Last-Event-ID` on
 *    reconnect by itself, so there is no resume logic to write and none here.
 *
 * Nothing publishes these events yet, so in practice the stream is open and
 * silent. That is a supported state, not a failure: the hook subscribes, says
 * nothing, and costs nothing.
 */
import type { QueryClient } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { z } from "zod";

export const EVENTS_URL = "/api/events";

/** The twelve documented frame names. docs/api-contract.md is the source. */
export const EVENT_TYPES = [
  "search.started",
  "search.result_added",
  "search.completed",
  "request.created",
  "download.queued",
  "download.started",
  "download.progress",
  "download.completed",
  "download.failed",
  "import.started",
  "import.completed",
  "media.available",
] as const;

export type PornarrEventType = (typeof EVENT_TYPES)[number];

/**
 * The envelope, defined here because the contract does not define it. The
 * payload is an object with unknown members: asserting fields we have never
 * seen a server emit would be fiction, and dropping a frame for failing a
 * guessed shape would be worse than ignoring it.
 */
export const eventPayloadSchema = z.record(z.string(), z.unknown());

export type PornarrEventPayload = z.infer<typeof eventPayloadSchema>;

export interface PornarrEvent {
  readonly type: PornarrEventType;
  /** Redis stream id, as delivered in the SSE `id:` field. May be empty. */
  readonly id: string;
  readonly payload: PornarrEventPayload;
}

type QueryKeyPrefix = readonly string[];

/**
 * What each frame makes stale. Keys are prefixes: invalidating `["downloads"]`
 * reaches `["downloads", "list", filters]` and everything else beneath it, so
 * screens can key however they need without editing this table.
 */
const INVALIDATED_BY: Readonly<Record<PornarrEventType, readonly QueryKeyPrefix[]>> = {
  "search.started": [["search"]],
  "search.result_added": [["search"]],
  "search.completed": [["search"]],
  "request.created": [["requests"]],
  "download.queued": [["downloads"]],
  "download.started": [["downloads"]],
  "download.progress": [["downloads"]],
  "download.completed": [["downloads"], ["imports"]],
  "download.failed": [["downloads"]],
  "import.started": [["imports"]],
  "import.completed": [["imports"], ["library"]],
  "media.available": [["library"]],
};

/**
 * What gets announced. DESIGN.md wants state changes announced politely, and
 * "politely" includes not narrating every progress tick — only the four frames
 * that mean a piece of work reached its end are worth interrupting a reader
 * for. The sentences are ours; the server never supplies prose.
 */
const ANNOUNCEMENTS: Readonly<Partial<Record<PornarrEventType, string>>> = {
  "download.completed": "A download finished.",
  "download.failed": "A download failed.",
  "import.completed": "An import finished.",
  "media.available": "New media is available.",
};

/** Parse one frame. Returns null for anything that is not a valid envelope. */
export function parseEventFrame(type: PornarrEventType, event: MessageEvent): PornarrEvent | null {
  if (typeof event.data !== "string") return null;

  let decoded: unknown;
  try {
    decoded = JSON.parse(event.data);
  } catch {
    return null;
  }

  const parsed = eventPayloadSchema.safeParse(decoded);
  if (!parsed.success) return null;

  return { type, id: event.lastEventId, payload: parsed.data };
}

export function applyEvent(queryClient: QueryClient, event: PornarrEvent): void {
  for (const queryKey of INVALIDATED_BY[event.type]) {
    void queryClient.invalidateQueries({ queryKey });
  }
}

export interface EventStreamState {
  /** Latest polite announcement, or "" when nothing has happened yet. */
  readonly announcement: string;
}

/**
 * Subscribe for as long as the component lives.
 *
 * EventSource reconnects on its own, so there is no retry loop here and no
 * connection state to report. The hook is a no-op where EventSource does not
 * exist (jsdom), which keeps every other test in this app from needing a stub.
 */
export function useEventStream(): EventStreamState {
  const queryClient = useQueryClient();
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    if (typeof EventSource === "undefined") return undefined;

    const source = new EventSource(EVENTS_URL);
    const unsubscribes = EVENT_TYPES.map((type) => {
      const listener = (event: Event): void => {
        if (!(event instanceof MessageEvent)) return;
        const parsed = parseEventFrame(type, event);
        if (parsed === null) return;

        applyEvent(queryClient, parsed);

        const message = ANNOUNCEMENTS[type];
        // Re-announce an identical sentence by making the node's text differ:
        // a live region that receives the same string twice says it once.
        if (message !== undefined)
          setAnnouncement((previous) => nextAnnouncement(previous, message));
      };

      source.addEventListener(type, listener);
      return () => source.removeEventListener(type, listener);
    });

    return () => {
      for (const unsubscribe of unsubscribes) unsubscribe();
      source.close();
    };
  }, [queryClient]);

  return { announcement };
}

/** Alternate a trailing space so repeats of one sentence still announce. */
function nextAnnouncement(previous: string, message: string): string {
  return previous === message ? `${message} ` : message;
}
