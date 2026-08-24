/**
 * Whether artwork is shown or held back.
 *
 * Every tile in the application defaults to blurred, which is the right default
 * for this library and the wrong permanent state: without a way to lift it, the
 * grid is a wall of frosted rectangles and the artwork the scanner produced is
 * unreachable. The top bar's control is the way to lift it, and this is the
 * state behind that control.
 *
 * ONE STORE, NOT A CONTEXT. The value is read by tiles on every screen and
 * written from exactly one button. A context would mean a provider wrapped
 * around the shell and a re-render of the tree on each toggle; a module-level
 * store with `useSyncExternalStore` gives the same guarantee — every reader
 * sees the same value in the same paint — without either.
 *
 * PERSISTED, AND DELIBERATELY NOT ON THE SERVER. This is a property of the room
 * you are in, not of the account: the same person wants art hidden on the
 * laptop in a shared office and shown on the machine at home. Sending it to the
 * server would make the safer setting follow you to the place you did not want
 * it, and — worse — make the unsafe one follow you to the place you did.
 *
 * Storage can throw rather than merely be empty (Safari in private mode, any
 * browser with storage blocked for the origin). Artwork visibility is not worth
 * failing a page load over, so an unreadable store reads as hidden — the
 * cautious end, which is also the default.
 */
import { useSyncExternalStore } from "react";

const STORAGE_KEY = "pornarr-art-visible";

type Listener = () => void;

const listeners = new Set<Listener>();

function readStored(): boolean {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

let visible = readStored();

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Whether artwork is currently shown. */
export function artIsVisible(): boolean {
  return visible;
}

export function setArtVisible(next: boolean): void {
  if (next === visible) return;
  visible = next;
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, String(next));
  } catch {
    // The choice still applies to this page; it just will not survive a reload.
  }
  for (const listener of listeners) listener();
}

/**
 * Reset to the stored value. Only for tests, which share a module registry
 * across cases and would otherwise inherit whichever value ran last.
 */
export function resetArtVisibility(): void {
  visible = readStored();
  for (const listener of listeners) listener();
}

/** The server snapshot is `false`: nothing is revealed before hydration. */
export function useArtVisible(): boolean {
  return useSyncExternalStore(subscribe, artIsVisible, () => false);
}

/**
 * What a tile passes to `MediaTile`. Named for the question a screen is asking
 * so no screen has to remember which blur level means "shown".
 */
export function tileBlur(visible: boolean): "hidden" | "none" {
  return visible ? "none" : "hidden";
}
