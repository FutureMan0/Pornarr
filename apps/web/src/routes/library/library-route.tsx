import type { paths } from "@pornarr/api-client";
import { MediaTile } from "@pornarr/ui";
import type { InfiniteData } from "@tanstack/react-query";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { tileBlur, useArtVisible } from "../../lib/art-visibility";
import { usePageTitle } from "../../shell/page-title";
import { ResumeTile } from "../continue/resume-tile";

/**
 * Derived from the contract, not transcribed from it.
 *
 * This screen carried a hand-written copy of the server's response — a copy
 * nothing checked, which had already drifted: it was missing `completed`. Naming
 * the types through `paths` makes a server change a compile error here, which is
 * the whole point of generating the client.
 */
type Page = paths["/api/library"]["get"]["responses"]["200"]["content"]["application/json"];
type Item = Page["items"][number];

/** How many titles one page brings back. */
const PAGE_SIZE = 48;

/** The rating floors the design offers as chips. */
const RATING_FILTERS = [4, 3] as const;

/**
 * How many half-watched titles the row shows before deferring to `/continue`.
 *
 * Five is the column count the design was drawn at, so the row is one row on a
 * wide screen and wraps to two at most on a narrow one.
 */
const RESUME_ROW = 5;

export function LibraryRoute() {
  const { t } = useTranslation();
  const [ratingFloor, setRatingFloor] = useState<number | null>(null);

  // The last generic is the page param. Without it `pageParam` arrives as
  // `unknown` and the offset has to be cast, which is the cast this file was
  // created to avoid.
  const library = useInfiniteQuery<Page, Error, InfiniteData<Page>, readonly unknown[], number>({
    queryKey: ["library", ratingFloor],
    initialPageParam: 0,
    getNextPageParam: (page) => page.next_offset ?? undefined,
    queryFn: async ({ pageParam }): Promise<Page> => {
      const { data, error, response } = await getApiClient().GET("/api/library", {
        params: {
          query: {
            limit: PAGE_SIZE,
            offset: pageParam,
            // Omitted rather than sent as null: the endpoint validates the
            // floor, and `rating_gte=null` is a 422 on every unfiltered load.
            ...(ratingFloor === null ? {} : { rating_gte: ratingFloor }),
          },
        },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  /**
   * What you are part-way through, from the endpoint that knows.
   *
   * This used to be a filter over the loaded library pages, which was wrong in
   * both directions: the row *grew* as you scrolled, because each new page
   * contributed more half-watched titles to a row above the one you were
   * reading; and a title you were half-way through on page five was missing
   * until you scrolled that far. `/api/playback/continue-watching` answers the
   * question directly, in the order the server considers most recent, and the
   * answer does not change while you scroll.
   */
  const resuming = useQuery({
    queryKey: ["continue-watching"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/playback/continue-watching");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
    // A failing row must not take the library with it. The grid below is the
    // screen; this is a shortcut across the top of it.
    retry: false,
  });
  const items = library.data?.pages.flatMap((page) => page.items) ?? [];

  // The count is what has loaded, not what exists: the library pages as you
  // scroll, and claiming a total the server never sent would be a number made
  // up in the browser.
  usePageTitle(
    t("library.title"),
    library.isPending ? undefined : t("library.results", { count: items.length }),
  );

  if (library.isPending)
    return (
      <section aria-label={t("library.title")}>
        <p className="text-sm text-ink-muted">{t("library.loading")}</p>
      </section>
    );
  if (library.isError)
    return (
      <section aria-label={t("library.title")}>
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      </section>
    );
  return (
    <section aria-label={t("library.title")} className="flex flex-col gap-6">
      {/* The controls are a fieldset; the result count is not one of them, so
          it sits beside the group rather than inside it. A screen reader
          reaching the filters is told what they filter, and the count is not
          announced as though it were another button. */}
      <div className="flex flex-wrap items-center gap-2">
        <fieldset className="flex flex-wrap items-center gap-2 border-0 p-0">
          <legend className="sr-only">{t("library.filters")}</legend>
          {RATING_FILTERS.map((floor) => (
            <button
              key={floor}
              type="button"
              aria-pressed={ratingFloor === floor}
              onClick={() => setRatingFloor(ratingFloor === floor ? null : floor)}
              className={
                ratingFloor === floor
                  ? "rounded-full bg-[var(--primary-weak)] px-3 py-1 text-xs text-[var(--pa-accent-300)]"
                  : "rounded-full border border-border px-3 py-1 text-xs text-ink-muted hover:bg-surface-3 hover:text-ink"
              }
            >
              {t("library.ratingFloor", { count: floor })}
            </button>
          ))}
        </fieldset>
      </div>

      {/* Outside the empty check below, because the two answer different
          questions. A rating filter that matches nothing says so about the
          grid; it says nothing about what you were half-way through, and
          hiding the row behind an empty grid meant a filter could make your
          own unfinished titles disappear. */}
      {resuming.data !== undefined && resuming.data.length > 0 ? (
        <section aria-labelledby="continue-section" className="flex flex-col gap-3">
          <div className="flex items-baseline justify-between gap-4">
            <h2
              id="continue-section"
              className="text-2xs uppercase tracking-[0.08em] text-ink-muted"
            >
              {t("library.sections.continue")}
            </h2>
            {/* The row is a shortcut, not the list. Without this link the
                Continue destination in the navigation and the row at the top
                of the library have no relationship to each other. */}
            {resuming.data.length > RESUME_ROW ? (
              <Link to="/continue" className="text-2xs text-ink-muted hover:text-ink">
                {t("media.seeAll")}
              </Link>
            ) : null}
          </div>
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] sm:grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-4">
            {resuming.data.slice(0, RESUME_ROW).map((item) => (
              <li key={item.media_id}>
                <ResumeTile item={item} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {items.length === 0 ? (
        <p className="text-sm text-ink-muted">
          {ratingFloor === null ? t("library.empty") : t("library.noMatches")}
        </p>
      ) : (
        <>
          <section aria-labelledby="everything-section" className="flex flex-col gap-3">
            <h2
              id="everything-section"
              className="text-2xs uppercase tracking-[0.08em] text-ink-muted"
            >
              {t("library.sections.everything")}
            </h2>
            <VirtualGrid
              items={items}
              onEnd={() =>
                library.hasNextPage && !library.isFetchingNextPage && void library.fetchNextPage()
              }
            />
          </section>
        </>
      )}
    </section>
  );
}

function VirtualGrid({ items, onEnd }: { readonly items: Item[]; readonly onEnd: () => void }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(1);

  /**
   * How many tiles fit, counted rather than calculated.
   *
   * This divided the width by a hardcoded 216 — the 12.5rem minimum plus the
   * gap. The moment a phone got a narrower minimum so that two tiles fit, the
   * arithmetic said one and the CSS drew two: the virtualiser would have sliced
   * one item per row and left every second tile out of the list entirely.
   *
   * `grid-template-columns` computes to the *used* track sizes, and `auto-fill`
   * creates the empty tracks too — so one rendered row answers the question for
   * the whole grid, whatever the breakpoint decides. One source of truth, and it
   * is the stylesheet.
   */
  useEffect(() => {
    const measure = (): void => {
      const row = rowRef.current;
      if (row === null) return;
      const tracks = window.getComputedStyle(row).gridTemplateColumns.split(" ").length;
      setColumns(Math.max(1, tracks));
    };

    const observer = new ResizeObserver(measure);
    if (parentRef.current) observer.observe(parentRef.current);
    measure();
    return () => observer.disconnect();
  }, []);
  const rows = Math.ceil(items.length / columns);
  const virtualizer = useVirtualizer({
    count: rows,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 260,
    overscan: 3,
  });
  return (
    <div
      ref={parentRef}
      className="h-[calc(100vh-12rem)] overflow-auto"
      onScroll={(event) => {
        const node = event.currentTarget;
        if (node.scrollTop + node.clientHeight >= node.scrollHeight - 800) onEnd();
      }}
    >
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((row) => (
          <div
            key={row.key}
            // Only the first row is measured; every row lays out identically.
            ref={row.index === 0 ? rowRef : undefined}
            className="absolute left-0 grid w-full grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] gap-4 sm:grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))]"
            style={{ transform: `translateY(${row.start}px)` }}
          >
            {items.slice(row.index * columns, (row.index + 1) * columns).map((item) => (
              <MediaCard key={item.id} item={item} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function MediaCard({ item }: { readonly item: Item }) {
  const { t } = useTranslation();
  const artVisible = useArtVisible();
  const [preview, setPreview] = useState(false);
  // A poster is generated by a background job, so a freshly scanned title has
  // none yet. Falling back to the placeholder keeps the grid readable instead
  // of filling it with black rectangles that look like a rendering fault.
  const [posterFailed, setPosterFailed] = useState(false);
  // The sprite is a strip of frames; swapping the source on hover is the
  // cheapest possible preview and it predates the design. Keeping it.
  const source = preview && item.sprite_url !== null ? item.sprite_url : item.poster_url;
  const progress =
    item.position_seconds !== null && item.progress_duration_seconds
      ? item.position_seconds / item.progress_duration_seconds
      : 0;

  return (
    <MediaTile
      title={item.title}
      meta={item.studio ?? undefined}
      resolution={item.quality ?? item.resolution ?? undefined}
      duration={item.duration_seconds === null ? undefined : formatDuration(item.duration_seconds)}
      progress={progress}
      rating={item.rating}
      ratingLabel={
        item.rating === null
          ? t("library.rating.none")
          : t("library.rating.value", { value: item.rating, count: item.rating_count })
      }
      tagCount={item.tag_count}
      commentCount={item.comment_count}
      blur={tileBlur(artVisible)}
      // The id is already a stable per-title number; hashing it again buys
      // nothing. Only the digits, so a UUID's letters do not skew the band.
      seed={seedFrom(item.id)}
      poster={
        posterFailed ? undefined : (
          <img src={source} alt="" loading="lazy" onError={() => setPosterFailed(true)} />
        )
      }
      action={(content) => (
        <Link
          to={`/library/${item.id}`}
          className="block rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary)]"
          onPointerEnter={() => setPreview(true)}
          onPointerLeave={() => setPreview(false)}
        >
          {content}
        </Link>
      )}
    />
  );
}

/** Digits of the id, folded into a number the artwork can key off. */
function seedFrom(id: string): number {
  let total = 0;
  for (const character of id) total = (total * 31 + character.charCodeAt(0)) % 100_000;
  return total;
}

function formatDuration(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = whole % 60;
  const pad = (value: number): string => String(value).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(rest)}` : `${minutes}:${pad(rest)}`;
}
