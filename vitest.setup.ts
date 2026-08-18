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

// Only in a browser-like environment: the node-environment suites have no
// window, and inventing storage for them would hide a real mistake.
if (typeof window !== "undefined" && typeof document !== "undefined") {
  install("localStorage");
  install("sessionStorage");
}
