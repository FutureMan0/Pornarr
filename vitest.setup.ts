/**
 * Give jsdom back its web storage on newer Node.
 *
 * Node 22 has no `localStorage` global. Node 24 and later ship one, but it
 * evaluates to `undefined` unless the process was started with
 * `--localstorage-file`. Vitest's jsdom environment copies jsdom's globals onto
 * the Node global only where nothing is there already, so on those versions
 * Node's inert stub wins and every `window.localStorage` in a test reads
 * `undefined` — a failure with nothing to do with the code under test.
 *
 * CI pins Node 22 (`.nvmrc`, `NODE_VERSION` in the workflow) and never sees
 * this, which is exactly why it is worth handling here: the person who hits it
 * is a contributor on a newer runtime, alone, with a suite that fails for
 * reasons the diff cannot explain.
 *
 * The replacement is a plain in-memory `Storage`. Tests need persistence across
 * a single test, not across a page load, and this keeps the whole thing inside
 * one file instead of a launch flag every contributor has to know about.
 */

class MemoryStorage implements Storage {
  #entries = new Map<string, string>();

  get length(): number {
    return this.#entries.size;
  }

  key(index: number): string | null {
    return [...this.#entries.keys()][index] ?? null;
  }

  getItem(key: string): string | null {
    return this.#entries.get(String(key)) ?? null;
  }

  setItem(key: string, value: string): void {
    // The real thing stringifies both, and tests rely on reading back "null"
    // rather than null when something wrote a null.
    this.#entries.set(String(key), String(value));
  }

  removeItem(key: string): void {
    this.#entries.delete(String(key));
  }

  clear(): void {
    this.#entries.clear();
  }
}

function install(name: "localStorage" | "sessionStorage"): void {
  const existing = Reflect.get(globalThis, name) as Storage | undefined;
  if (existing !== undefined) return;

  Object.defineProperty(globalThis, name, {
    value: new MemoryStorage(),
    configurable: true,
    writable: true,
  });
}

/**
 * And its ResizeObserver.
 *
 * jsdom does not implement it, and `@tanstack/react-virtual` constructs one on
 * mount — so the virtualised library grid threw `ResizeObserver is not defined`
 * straight to the error boundary, and every test that rendered the library with
 * titles in it silently measured an error screen instead. The grid had never
 * been under test with content.
 *
 * A no-op is the honest stub: jsdom lays nothing out, so a real implementation
 * would only ever report zero. Tests that care about virtualisation have to
 * drive the sizes themselves.
 */
class NoopResizeObserver implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

// Only in a browser-like environment: the node-environment suites have no
// window, and inventing browser globals for them would hide a real mistake.
if (typeof window !== "undefined" && typeof document !== "undefined") {
  install("localStorage");
  install("sessionStorage");

  if (!("ResizeObserver" in globalThis)) {
    Object.defineProperty(globalThis, "ResizeObserver", {
      value: NoopResizeObserver,
      configurable: true,
      writable: true,
    });
  }
}

/**
 * And the methods of `<dialog>`.
 *
 * jsdom constructs `HTMLDialogElement` but, before 26, implements neither
 * `showModal()` nor `close()`. Any component built on the platform element then
 * throws on open — which is a failure of the environment, not of the component,
 * and one that two separate suites were about to work around separately.
 *
 * Installed only where the methods are genuinely missing, so this disappears on
 * its own the day the runtime supplies them. The mechanics are the minimum:
 * toggle `open`, move focus inward the way `showModal()` does, honour Escape via
 * a cancellable `cancel` event, fire `close`.
 *
 * It deliberately does NOT restore focus on close. That is behaviour the dialog
 * component implements itself, and a test harness that supplies it would be a
 * harness passing its own test.
 */
function installDialogMethods(): void {
  if (typeof HTMLDialogElement !== "function") return;
  const proto = HTMLDialogElement.prototype;
  if (typeof proto.showModal === "function" && typeof proto.close === "function") return;

  const escapeHandlers = new WeakMap<HTMLDialogElement, (event: Event) => void>();

  if (typeof proto.showModal !== "function") {
    proto.showModal = function showModal(this: HTMLDialogElement): void {
      this.open = true;
      const onKeyDown = (event: Event): void => {
        if (!(event instanceof KeyboardEvent) || event.key !== "Escape") return;
        event.preventDefault();
        const notCancelled = this.dispatchEvent(new Event("cancel", { cancelable: true }));
        if (notCancelled) this.close();
      };
      this.addEventListener("keydown", onKeyDown);
      escapeHandlers.set(this, onKeyDown);
      const first = this.querySelector<HTMLElement>("button, [href], input, select, textarea");
      first?.focus();
    };
  }

  if (typeof proto.show !== "function") {
    proto.show = function show(this: HTMLDialogElement): void {
      this.open = true;
    };
  }

  if (typeof proto.close !== "function") {
    proto.close = function close(this: HTMLDialogElement): void {
      if (!this.open) return;
      this.open = false;
      const handler = escapeHandlers.get(this);
      if (handler !== undefined) {
        this.removeEventListener("keydown", handler);
        escapeHandlers.delete(this);
      }
      this.dispatchEvent(new Event("close"));
    };
  }
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  installDialogMethods();
}
