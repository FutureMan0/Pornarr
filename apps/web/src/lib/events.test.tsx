/**
 * The SSE hook.
 *
 * jsdom has no EventSource, so one is supplied here. The stub is deliberately
 * dumb — it records listeners and dispatches real `MessageEvent`s — because the
 * property under test is that the hook subscribes *per named type*. A stub that
 * also implemented `onmessage` would let the bug this guards against pass.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { createTestQueryClient } from "../test/harness";
import { EVENTS_URL, EVENT_TYPES, useEventStream } from "./events";

afterEach(cleanup);

class StubEventSource {
  static instances: StubEventSource[] = [];

  readonly url: string;
  readonly listeners = new Map<string, Set<(event: Event) => void>>();
  closed = false;

  constructor(url: string) {
    this.url = url;
    StubEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }

  removeEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {
    this.closed = true;
  }

  /** Deliver a named frame exactly as the browser would. */
  emit(type: string, data: string, lastEventId = "1699-0"): void {
    const event = new MessageEvent(type, { data, lastEventId });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

beforeEach(() => {
  StubEventSource.instances = [];
  Object.defineProperty(globalThis, "EventSource", {
    configurable: true,
    writable: true,
    value: StubEventSource,
  });
});

function mount() {
  const queryClient = createTestQueryClient();
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const view = renderHook(() => useEventStream(), { wrapper });
  const source = StubEventSource.instances[0];
  if (source === undefined) throw new Error("the hook did not open a stream");
  return { view, source, invalidate };
}

describe("useEventStream", () => {
  test("subscribes with addEventListener for every named type", () => {
    const { source } = mount();

    expect(source.url).toBe(EVENTS_URL);
    for (const type of EVENT_TYPES) {
      expect(source.listeners.get(type)?.size).toBe(1);
    }
  });

  test("invalidates the queries an event makes stale", async () => {
    const { source, invalidate } = mount();

    source.emit("download.completed", JSON.stringify({ download_id: "d-1" }));

    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["downloads"] });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["imports"] });
    });
  });

  test("announces politely, and only for frames worth announcing", async () => {
    const { view, source } = mount();

    source.emit("download.progress", JSON.stringify({ percent: 40 }));
    expect(view.result.current.announcement).toBe("");

    source.emit("media.available", JSON.stringify({ media_id: "m-1" }));
    await waitFor(() => expect(view.result.current.announcement).toBe("New media is available."));
  });

  test("drops a frame whose data is not a JSON object", () => {
    const { source, invalidate } = mount();

    source.emit("download.completed", "not json");
    source.emit("download.completed", '"a string"');

    expect(invalidate).not.toHaveBeenCalled();
  });

  test("closes the stream when the component unmounts", () => {
    const { view, source } = mount();

    view.unmount();

    expect(source.closed).toBe(true);
    expect(source.listeners.get("download.completed")?.size).toBe(0);
  });
});
