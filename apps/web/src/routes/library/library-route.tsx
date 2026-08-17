import { MediaTile } from "@pornarr/ui";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { tileBlur, useArtVisible } from "../../lib/art-visibility";
import { usePageTitle } from "../../shell/page-title";

type Item = {
  id: string;
  title: string;
  studio: string | null;
  release_date: string | null;
  duration_seconds: number | null;
  quality: string | null;
  resolution: string | null;
  position_seconds: number | null;
  progress_duration_seconds: number | null;
  poster_url: string;
  sprite_url: string | null;
  rating: number | null;
  rating_count: number;
  tag_count: number;
  comment_count: number;
};
type Page = { items: Item[]; next_offset: number | null };

/** The rating floors the design offers as chips. */
const RATING_FILTERS = [4, 3] as const;

export function LibraryRoute() {
  const { t } = useTranslation();
  const [ratingFloor, setRatingFloor] = useState<number | null>(null);

  const library = useInfiniteQuery<Page, Error>({
    queryKey: ["library", ratingFloor],
    initialPageParam: 0,
    getNextPageParam: (page) => page.next_offset ?? undefined,
    queryFn: async ({ pageParam }): Promise<Page> => {
      const filter = ratingFloor === null ? "" : `&rating_gte=${ratingFloor}`;
      const response = await fetch(`/api/library?limit=48&offset=${pageParam}${filter}`);
      if (!response.ok) throw new Error();
      return response.json() as Promise<Page>;
    },
  });

  // Resume position comes from the same rows, so "continue watching" is a
  // partition of the page rather than a second request.
  const resuming = (items: Item[]): Item[] =>
    items.filter((item) => item.position_seconds !== null && item.position_seconds > 0);
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

      {items.length === 0 ? (
        <p className="text-sm text-ink-muted">
          {ratingFloor === null ? t("library.empty") : t("library.noMatches")}
        </p>
      ) : (
        <>
          {resuming(items).length > 0 ? (
            <section aria-labelledby="continue-section" className="flex flex-col gap-3">
              <h2
                id="continue-section"
                className="text-2xs uppercase tracking-[0.08em] text-ink-muted"
              >
                {t("library.sections.continue")}
              </h2>
              <ul className="grid grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-4">
                {resuming(items).map((item) => (
                  <li key={item.id}>
                    <MediaCard item={item} />
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

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
  const [columns, setColumns] = useState(1);
  useEffect(() => {
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setColumns(Math.max(1, Math.floor(entry.contentRect.width / 216)));
    });
    if (parentRef.current) observer.observe(parentRef.current);
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
            className="absolute left-0 grid w-full grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-4"
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
