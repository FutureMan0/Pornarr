/**
 * The search field in the top bar.
 *
 * WHAT IT WAS. A plain input that did exactly one thing: on Enter it navigated to
 * `/search?q=…`. Three problems, all of them silent. The ⌘K shortcut was already
 * implemented and invisible, so nobody used it. Typing produced no answer at all
 * until you committed to leaving the screen you were on — which for "what was
 * that title called" is the whole cost of asking. And once there was text in it
 * there was no way to get rid of it except selecting and deleting.
 *
 * WHAT IT IS. A combobox over `/api/search/local`. Six matches while you type,
 * arrow keys to walk them, Enter to open one or to fall through to the full
 * results page. The shortcut is printed in the field, and there is a clear
 * button.
 *
 * ONLY THE LOCAL ENDPOINT. `/api/search/indexers` reaches out to trackers, which
 * takes seconds and costs somebody else's bandwidth. Firing that per keystroke
 * would be rude at best. The suggestions are the library; the results page is
 * where a search leaves the house.
 *
 * THE LIST IS A LISTBOX, NOT A MENU. `role="menu"` moves focus onto items, which
 * takes it out of the field and stops typing. A combobox keeps focus in the input
 * and points at the active option with `aria-activedescendant`, which is what
 * lets you keep typing while a suggestion is highlighted.
 */
import { useQuery } from "@tanstack/react-query";
import type { FormEvent, JSX, KeyboardEvent } from "react";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { Input } from "@pornarr/ui";
import { getApiClient } from "../lib/api";
import { apiFailure } from "../lib/api-error";
import { formatDuration } from "../lib/format";

/** Enough to recognise a title, few enough to read without scrolling. */
const LIMIT = 6;

/**
 * How long a keystroke waits before it becomes a request.
 *
 * Long enough that typing a word is one query rather than six, short enough that
 * it still feels like an answer rather than a page load.
 */
const DEBOUNCE_MS = 180;

/** Below this a query matches everything and answers nothing. */
const MINIMUM_QUERY = 2;

export function GlobalSearch(): JSX.Element {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const optionId = (index: number): string => `${listId}-${index}`;

  const [query, setQuery] = useState("");
  const [committed, setCommitted] = useState("");
  const [open, setOpen] = useState(false);
  /** null means "the field, not a suggestion" — Enter then submits the query. */
  const [active, setActive] = useState<number | null>(null);

  // Debounce here rather than in the query's key: a key that changes per
  // keystroke starts a request per keystroke and only *displays* the last one.
  useEffect(() => {
    const timer = setTimeout(() => setCommitted(query.trim()), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  const suggestions = useQuery({
    queryKey: ["search", "suggest", committed],
    enabled: committed.length >= MINIMUM_QUERY,
    // A stale suggestion is worse than none, but re-asking for a query somebody
    // just typed is a wasted round trip on the way back up the same word.
    staleTime: 30_000,
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/search/local", {
        params: { query: { q: committed, limit: LIMIT } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data.items;
    },
  });

  const items = committed.length >= MINIMUM_QUERY ? (suggestions.data ?? []) : [];
  const showList = open && committed.length >= MINIMUM_QUERY;

  useEffect(() => {
    const focusSearch = (event: globalThis.KeyboardEvent): void => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "k") return;
      event.preventDefault();
      inputRef.current?.focus();
      inputRef.current?.select();
    };
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);

  const goToResults = (): void => {
    const trimmed = query.trim();
    setOpen(false);
    navigate(trimmed === "" ? "/search" : `/search?q=${encodeURIComponent(trimmed)}`);
  };

  const openTitle = (mediaId: string): void => {
    setOpen(false);
    setActive(null);
    navigate(`/library/${mediaId}`);
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const chosen = active === null ? undefined : items[active];
    if (chosen !== undefined) {
      openTitle(chosen.id);
      return;
    }
    goToResults();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "Escape") {
      // First press closes the list, second clears the field. Escape that wipes
      // a query you are still reading suggestions for is Escape as a trapdoor.
      if (showList) {
        setOpen(false);
        setActive(null);
        return;
      }
      setQuery("");
      return;
    }

    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (items.length === 0) return;
      event.preventDefault();
      setOpen(true);
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => {
        if (current === null) return step === 1 ? 0 : items.length - 1;
        const next = current + step;
        // Off either end returns to the field rather than wrapping: wrapping
        // makes "back to what I typed" unreachable.
        return next < 0 || next >= items.length ? null : next;
      });
    }
  };

  return (
    <search className="order-last w-full min-w-0 basis-full sm:order-none sm:w-auto sm:max-w-[26rem] sm:flex-1 sm:basis-auto">
      <form
        onSubmit={onSubmit}
        // Focus leaving the whole control closes the list. Without this, clicking
        // a suggestion races the blur that would unmount it.
        onBlur={(event) => {
          const next = event.relatedTarget;
          if (next instanceof Node && event.currentTarget.contains(next)) return;
          setOpen(false);
          setActive(null);
        }}
        className="relative"
      >
        <label className="visually-hidden" htmlFor="global-search">
          {t("search.label")}
        </label>
        <Input
          ref={inputRef}
          id="global-search"
          name="q"
          // `type="search"` brings the browser's own clear button on some
          // platforms, which would sit next to ours and do something subtly
          // different. One clear control, ours, at every width.
          type="text"
          role="combobox"
          autoComplete="off"
          // The property built for this, rather than leaving the shortcut to the
          // glyph in the corner that a screen reader has no reason to read.
          aria-keyshortcuts="Meta+K Control+K"
          aria-expanded={showList}
          // ARIA 1.2 requires a combobox to name what it controls, so the
          // listbox below renders whenever the field is expanded — empty, with a
          // status row inside it, rather than not at all. Dropping the attribute
          // for the empty case left the role incomplete.
          aria-controls={showList ? listId : undefined}
          aria-activedescendant={active === null ? undefined : optionId(active)}
          value={query}
          placeholder={t("search.placeholder")}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(null);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          leading={<SearchIcon />}
          trailing={
            query === "" ? (
              <ShortcutHint />
            ) : (
              <button
                type="button"
                aria-label={t("search.clear")}
                onClick={() => {
                  setQuery("");
                  setActive(null);
                  inputRef.current?.focus();
                }}
                className="grid size-5 place-items-center rounded-full text-ink-muted transition-colors duration-[var(--duration-fast)] ease-out hover:bg-surface-3 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--primary)]"
              >
                <CloseIcon />
              </button>
            )
          }
        />

        {showList ? (
          <div className="pa-pop absolute inset-x-0 top-[calc(100%+var(--space-1))] z-[var(--z-dropdown)] overflow-hidden rounded-md bg-surface-2 shadow-[var(--shadow-floating)]">
            {/* A div, not a ul. `role="listbox"` on a list element is an
                interactive role on a non-interactive element, and the options here
                are buttons rather than list items — the list semantics would be a
                second, contradictory description of the same thing. `tabIndex` is
                -1 because focus never comes here: it stays in the field, which is
                what `aria-activedescendant` is for. */}
            {/* biome-ignore lint/a11y/useSemanticElements: a native <select> cannot be a
                typeahead suggestion list — it does not stay open while the field is being
                typed into, and its options are not links to a screen. The ARIA combobox
                pattern is the correct one here and this rule cannot express it. */}
            <div id={listId} role="listbox" tabIndex={-1} aria-label={t("search.suggestions")}>
              {/* Empty and busy are different answers and the list says which. A
                  spinner would say neither. */}
              {items.length === 0 ? (
                <p className="m-0 px-3 py-2 text-2xs text-ink-muted">
                  {suggestions.isFetching ? t("search.suggestionsBusy") : t("search.noSuggestions")}
                </p>
              ) : (
                items.map((item, index) => (
                  <button
                    key={item.id}
                    type="button"
                    id={optionId(index)}
                    // biome-ignore lint/a11y/useSemanticElements: see the listbox above —
                    // an <option> cannot hold a title, a badge and a duration side by
                    // side, and cannot be clicked to navigate to a screen.
                    role="option"
                    aria-selected={active === index}
                    // Options are not in the tab order: focus stays in the field
                    // so typing never stops.
                    tabIndex={-1}
                    onPointerEnter={() => setActive(index)}
                    onClick={() => openTitle(item.id)}
                    className={
                      active === index
                        ? "flex w-full items-baseline gap-2 bg-surface-3 px-3 py-2 text-left"
                        : "flex w-full items-baseline gap-2 px-3 py-2 text-left"
                    }
                  >
                    <span className="min-w-0 flex-1 truncate text-sm text-ink">{item.title}</span>
                    {/* The studio, not an in-library marker. Every suggestion comes
                        from the local library, so a badge saying so was true on
                        every row and therefore said nothing; the studio is what
                        tells two similarly named titles apart. */}
                    {item.studio === null ? null : (
                      <span className="max-w-[8rem] flex-none truncate text-2xs text-ink-muted">
                        {item.studio}
                      </span>
                    )}
                    <span className="flex-none text-2xs tabular-nums text-ink-faint">
                      {item.duration_seconds === null
                        ? (item.quality ?? item.resolution ?? "")
                        : formatDuration(item.duration_seconds)}
                    </span>
                  </button>
                ))
              )}
            </div>

            {/* Always last, always available: the suggestions are the library and
                this is how a search reaches the indexers. */}
            <button
              type="button"
              tabIndex={-1}
              onClick={goToResults}
              className="flex w-full items-center gap-2 border-t border-border px-3 py-2 text-left text-2xs text-ink-muted transition-colors duration-[var(--duration-fast)] ease-out hover:bg-surface-3 hover:text-ink"
            >
              {t("search.seeAll", { query: committed })}
            </button>
          </div>
        ) : null}
      </form>
    </search>
  );
}

/**
 * The shortcut, printed.
 *
 * ⌘ on Apple platforms and Ctrl everywhere else, because a hint showing the wrong
 * key is worse than no hint. `navigator.platform` is deprecated and still the
 * only thing that answers this in every browser; the fallback is Ctrl, which is
 * the more common case.
 *
 * Hidden from assistive technology, because the field carries the same fact as
 * `aria-keyshortcuts` — the property built for it, which a screen reader can
 * announce in the user's own words instead of reading out a glyph.
 */
function ShortcutHint(): JSX.Element {
  const apple = /Mac|iPhone|iPad/i.test(globalThis.navigator?.platform ?? "");
  return (
    <span
      aria-hidden="true"
      className="pointer-events-none select-none rounded border border-border px-1.5 py-px font-sans text-2xs text-ink-faint"
    >
      {apple ? "⌘K" : "Ctrl K"}
    </span>
  );
}

function SearchIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 16 16"
      className="size-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="7" cy="7" r="4.25" />
      <path d="m10.5 10.5 3 3" />
    </svg>
  );
}

function CloseIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 16 16"
      className="size-3"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="m4 4 8 8M12 4l-8 8" />
    </svg>
  );
}
