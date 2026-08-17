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
import { useTranslation } from "react-i18next";
import { z } from "zod";

export const EVENTS_URL = "/api/events";

/**
 * The frame names this client listens for. `docs/api-contract.md` documents
 * twelve; the two scan frames are emitted by the worker and are here because
 * the scan screen watches them — a walk of ten thousand files cannot be
 * reported by holding a request open.
 */
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
  "scan.progress",
  "scan.completed",
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
  // Progress changes nothing that is cached — it is watched directly by the
  // screen. Completion is what makes the folder list and the library stale.
  "scan.progress": [],
  "scan.completed": [["library"], ["root-folders"], ["admin"]],
};

type AnnouncementKey =
  | "events.downloadCompleted"
  | "events.downloadFailed"
  | "events.importCompleted"
  | "events.mediaAvailable";

/**
 * What gets announced. DESIGN.md wants state changes announced politely, and
 * "politely" includes not narrating every progress tick — only the four frames
 * that mean a piece of work reached its end are worth interrupting a reader
 * for. The sentences are ours and live in the locale files; the server never
 * supplies prose.
 *
 * The *key* is what the hook stores, not the sentence: an announcement made
 * before a language switch is still on screen after it, and it has to be in the
 * language the reader just asked for.
 */
const ANNOUNCEMENT_KEYS: Readonly<Partial<Record<PornarrEventType, AnnouncementKey>>> = {
  "download.completed": "events.downloadCompleted",
  "download.failed": "events.downloadFailed",
  "import.completed": "events.importCompleted",
  "media.available": "events.mediaAvailable",
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

/**
 * Watching individual frames.
 *
 * Most screens want the cache invalidated and nothing more, which `applyEvent`
 * does. A few — the scan screen above all — want the frames themselves, because
 * "2,104 of 3,010 files" is not a cached resource; it is a thing happening now.
 *
 * One stream, not one per subscriber. The shell owns the `EventSource`, and
 * this is how anything else reaches it. A screen opening its own connection
 * would double the server's stream count for every open tab.
 */
type EventListener = (event: PornarrEvent) => void;

const listeners = new Map<PornarrEventType, Set<EventListener>>();

export function subscribeToEvent(type: PornarrEventType, listener: EventListener): () => void {
  const existing = listeners.get(type) ?? new Set<EventListener>();
  existing.add(listener);
  listeners.set(type, existing);
  return () => {
    existing.delete(listener);
  };
}

function notify(event: PornarrEvent): void {
  for (const listener of listeners.get(event.type) ?? []) listener(event);
}

/**
 * Where the stream is, from the UI's point of view.
 *
 * `connecting` is the first attempt and is deliberately silent: every cold load
 * passes through it, and a banner that flashes on every load is noise rather
 * than information. `reconnecting` is every attempt after a connection that was
 * once open dropped, or a first attempt that failed — both are a real connection
 * problem and both are worth showing.
 */
export type EventStreamStatus = "connecting" | "open" | "reconnecting";

export interface EventStreamState {
  /** Latest polite announcement, or "" when nothing has happened yet. */
  readonly announcement: string;
  readonly status: EventStreamStatus;
}

/**
 * Subscribe for as long as the component lives.
 *
 * EventSource reconnects on its own — the browser owns the backoff and the
 * `Last-Event-ID` replay — so this hook *reports* the retry rather than
 * implementing one. Reimplementing it would mean closing the stream the browser
 * is already re-opening. The hook is a no-op where EventSource does not exist
 * (jsdom), which keeps every other test in this app from needing a stub.
 */
export function useEventStream(): EventStreamState {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<EventStreamStatus>("connecting");
  const [announced, setAnnounced] = useState<{
    readonly key: AnnouncementKey;
    readonly nonce: number;
  } | null>(null);

  useEffect(() => {
    if (typeof EventSource === "undefined") return undefined;

    const source = new EventSource(EVENTS_URL);

    const onOpen = (): void => setStatus("open");
    // Fired for a dropped connection and for a failed attempt alike. The browser
    // is already retrying unless it closed the stream outright, which it only
    // does for an answer it cannot recover from — a 401, which the API client's
    // own middleware has by then already turned into a trip to the login screen.
    const onError = (): void => setStatus("reconnecting");
    source.addEventListener("open", onOpen);
    source.addEventListener("error", onError);

    const unsubscribes = EVENT_TYPES.map((type) => {
      const listener = (event: Event): void => {
        if (!(event instanceof MessageEvent)) return;
        const parsed = parseEventFrame(type, event);
        if (parsed === null) return;

        applyEvent(queryClient, parsed);
        notify(parsed);

        const key = ANNOUNCEMENT_KEYS[type];
        if (key !== undefined) {
          setAnnounced((previous) => ({ key, nonce: (previous?.nonce ?? 0) + 1 }));
        }
      };

      source.addEventListener(type, listener);
      return () => source.removeEventListener(type, listener);
    });

    return () => {
      for (const unsubscribe of unsubscribes) unsubscribe();
      source.removeEventListener("open", onOpen);
      source.removeEventListener("error", onError);
      source.close();
    };
  }, [queryClient]);

  if (announced === null) return { announcement: "", status };
  // A live region handed the same string twice says it once. Alternating a
  // trailing space makes every announcement a different string from the last.
  const sentence = t(announced.key);
  return {
    announcement: announced.nonce % 2 === 0 ? `${sentence} ` : sentence,
    status,
  };
}
