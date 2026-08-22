import type { paths } from "@pornarr/api-client";
import { EmptyState, MediaTile, Select } from "@pornarr/ui";
import type { InfiniteData } from "@tanstack/react-query";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { JSX } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { NAV_ITEMS } from "../../shell/sidebar";
import { usePeers } from "../settings/peers/peers";
import { useRootFolders } from "../settings/root-folders/root-folders";
import { ROOT_FOLDERS_PATH } from "../settings/settings-layout";

/** Read from the nav table so the link cannot outlive the route it points at. */
const REQUESTS_PATH = NAV_ITEMS.find((item) => item.id === "requests")?.path ?? "/requests";

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
type Facets =
  paths["/api/library/facets"]["get"]["responses"]["200"]["content"]["application/json"];
type Facet = Facets["studios"][number];

/** How many titles one page brings back. */
const PAGE_SIZE = 48;

const SORTS = ["added", "title", "release", "duration"] as const;
type Sort = (typeof SORTS)[number];
const FILTER_KEYS = ["studio", "performer", "tag"] as const;

/** Browsing your own library, or everyone's. Anything else is one peer's id. */
const LOCAL_SOURCE = "local";
const ALL_SOURCES = "all";

function sortOf(value: string | null): Sort {
  return SORTS.includes(value as Sort) ? (value as Sort) : "added";
}

/** The rating floors the design offers as chips. */
const RATING_FILTERS = [4, 3] as const;

/** A floor from the URL, or none. Anything the chips do not offer is none. */
function ratingOf(value: string | null): number | null {
  const floor = Number(value);
  return RATING_FILTERS.includes(floor as (typeof RATING_FILTERS)[number]) ? floor : null;
}

/**
 * How many half-watched titles the row shows before deferring to `/continue`.
 *
 * Five is the column count the design was drawn at, so the row is one row on a
 * wide screen and wraps to two at most on a narrow one.
 */
const RESUME_ROW = 5;

export function LibraryRoute() {
  const { t } = useTranslation();
  const rootFolders = useRootFolders();
  const [params, setParams] = useSearchParams();
  // The filters live in the URL, so a filtered library is a link somebody can
  // send, and reloading the page does not throw the selection away.
  const filters = FILTER_KEYS.map((key) => [key, params.get(key) ?? ""] as const);
  const sort = sortOf(params.get("sort"));
  const source = params.get("source") ?? LOCAL_SOURCE;
  const ratingFloor = ratingOf(params.get("rating"));
  // The names of other people's servers are only needed once the reader has
  // left their own library. Asking an administrator-only endpoint on every
  // library load would be a request nobody asked for, on the one screen the
  // application opens on.
  const peers = usePeers(source !== LOCAL_SOURCE);
  const facets = useQuery<Facets, Error>({
    queryKey: ["library", "facets"],
    queryFn: async (): Promise<Facets> => {
      const { data, error, response } = await getApiClient().GET("/api/library/facets");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });
  // The last generic is the page param. Without it `pageParam` arrives as
  // `unknown` and would have to be cast, which is the cast the derived types
  // exist to avoid. A page is addressed by an offset or by a cursor depending
  // on which library is being read, so it is whichever of the two the previous
  // page handed back.
  const library = useInfiniteQuery<
    Page,
    Error,
    InfiniteData<Page>,
    readonly unknown[],
    number | string
  >({
    queryKey: ["library", Object.fromEntries(filters), sort, source, ratingFloor],
    initialPageParam: 0,
    getNextPageParam: (page) => page.next_cursor ?? page.next_offset ?? undefined,
    queryFn: async ({ pageParam }): Promise<Page> => {
      const { data, error, response } = await getApiClient().GET("/api/library", {
        params: {
          query: {
            limit: PAGE_SIZE,
            ...(typeof pageParam === "string" ? { cursor: pageParam } : { offset: pageParam }),
            sort,
            source,
            ...Object.fromEntries(filters.filter(([, value]) => value !== "")),
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
  const filtered = filters.some(([, value]) => value !== "") || ratingFloor !== null;
  const setFilter = (key: string, value: string): void => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value === "") next.delete(key);
      else next.set(key, value);
      return next;
    });
  };

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
      <LibraryFilters
        facets={facets.data}
        loading={facets.isPending}
        studio={params.get("studio") ?? ""}
        performer={params.get("performer") ?? ""}
        tag={params.get("tag") ?? ""}
        sort={sort}
        source={source}
        ratingFloor={ratingFloor}
        peers={(peers.data ?? []).map((peer) => [peer.id, peer.name] as const)}
        onChange={setFilter}
      />

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
        // A filtered library that comes back empty is not an empty library, and
        // telling the reader to add a root folder they already have is worse
        // than saying nothing.
        filtered ? (
          <EmptyState
            title={t("library.noMatchTitle")}
            body={t("library.noMatchBody")}
            action={{ label: t("library.noMatchAction"), href: "/library" }}
          />
        ) : (
          // `undefined` is "not asked" — a non-administrator never asks — so only
          // a loaded, empty list makes the root folder the thing that is missing.
          <LibraryEmpty needsRootFolder={rootFolders.data?.length === 0} />
        )
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
              showSource={source === ALL_SOURCES}
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

/**
 * The values worth filtering by come from the library itself, so the screen
 * never offers a filter that matches nothing, and never hides one that exists.
 */
function LibraryFilters({
  facets,
  loading,
  studio,
  performer,
  tag,
  sort,
  source,
  ratingFloor,
  peers,
  onChange,
}: {
  readonly facets: Facets | undefined;
  readonly loading: boolean;
  readonly studio: string;
  readonly performer: string;
  readonly tag: string;
  readonly sort: Sort;
  readonly source: string;
  readonly ratingFloor: number | null;
  readonly peers: readonly (readonly [string, string])[];
  readonly onChange: (key: string, value: string) => void;
}): JSX.Element {
  const { t } = useTranslation();
  const label = (facet: Facet): string =>
    t("library.facetCount", { value: facet.value, count: facet.count });

  return (
    <div className="flex flex-wrap gap-4" aria-label={t("library.filters")}>
      {/* First, because it decides what the other three are filtering. The
          peers appear once they are known; until then the choice is between
          this library and every library, which is what the picker is for. */}
      <FacetSelect
        id="library-source"
        label={t("library.filterSource")}
        value={source}
        loading={false}
        options={[
          [LOCAL_SOURCE, t("library.sourceLocal")],
          [ALL_SOURCES, t("library.sourceAll")],
          ...peers,
        ]}
        onChange={(value) => onChange("source", value)}
      />
      <FacetSelect
        id="library-studio"
        label={t("library.filterStudio")}
        placeholder={t("library.anyStudio")}
        value={studio}
        loading={loading}
        options={(facets?.studios ?? []).map((facet) => [facet.value, label(facet)])}
        onChange={(value) => onChange("studio", value)}
      />
      <FacetSelect
        id="library-performer"
        label={t("library.filterPerformer")}
        placeholder={t("library.anyPerformer")}
        value={performer}
        loading={loading}
        options={(facets?.performers ?? []).map((facet) => [facet.value, label(facet)])}
        onChange={(value) => onChange("performer", value)}
      />
      <FacetSelect
        id="library-tag"
        label={t("library.filterTag")}
        placeholder={t("library.anyTag")}
        value={tag}
        loading={loading}
        options={(facets?.tags ?? []).map((facet) => [facet.value, label(facet)])}
        onChange={(value) => onChange("tag", value)}
      />
      <FacetSelect
        id="library-sort"
        label={t("library.filterSort")}
        value={sort}
        loading={false}
        options={[
          ["added", t("library.sortAdded")],
          ["title", t("library.sortTitle")],
          ["release", t("library.sortRelease")],
          ["duration", t("library.sortDuration")],
        ]}
        onChange={(value) => onChange("sort", value)}
      />
      {/* A fieldset rather than a div carrying role="group": the grouping is
          then in the markup itself, and the legend names it for a screen
          reader without a parallel aria-label to keep in step. */}
      <fieldset className="flex flex-wrap items-end gap-2 border-0 p-0">
        <legend className="sr-only">{t("library.filters")}</legend>
        {RATING_FILTERS.map((floor) => (
          <button
            key={floor}
            type="button"
            aria-pressed={ratingFloor === floor}
            onClick={() => onChange("rating", ratingFloor === floor ? "" : String(floor))}
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
  );
}

function FacetSelect({
  id,
  label,
  placeholder,
  value,
  loading,
  options,
  onChange,
}: {
  readonly id: string;
  readonly label: string;
  readonly placeholder?: string;
  readonly value: string;
  readonly loading: boolean;
  readonly options: readonly (readonly [string, string])[];
  readonly onChange: (value: string) => void;
}): JSX.Element {
  return (
    <div className="flex min-w-40 flex-col gap-1">
      <label className="text-xs text-ink-muted" htmlFor={id}>
        {label}
      </label>
      <Select
        id={id}
        value={value}
        loading={loading}
        onChange={(event) => onChange(event.target.value)}
      >
        {placeholder === undefined ? null : <option value="">{placeholder}</option>}
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </Select>
    </div>
  );
}

/**
 * DESIGN.md: "an empty library says how to add a root folder and links to it".
 * It used to name the fix and stop, and the screen it named was not in the
 * client at all — which made the sentence advice nobody could follow.
 *
 * Nothing here mentions scanning: the API exposes no way to start one, and an
 * empty state that asks for an action the client cannot perform is the defect
 * this replaces rather than a smaller version of it.
 */
function LibraryEmpty({ needsRootFolder }: { readonly needsRootFolder: boolean }): JSX.Element {
  const { t } = useTranslation();

  return needsRootFolder ? (
    <EmptyState
      title={t("library.emptyTitle")}
      body={t("library.empty")}
      action={{ label: t("library.emptyAction"), href: ROOT_FOLDERS_PATH }}
    />
  ) : (
    <EmptyState
      title={t("library.emptyTitle")}
      body={t("library.emptyRequestBody")}
      action={{ label: t("library.emptyRequestAction"), href: REQUESTS_PATH }}
    />
  );
}

function VirtualGrid({
  items,
  showSource,
  onEnd,
}: {
  readonly items: Item[];
  readonly showSource: boolean;
  readonly onEnd: () => void;
}) {
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
              // Ids are per-server: two libraries can hand back the same one,
              // and browsing both at once made React see one card twice.
              <MediaCard
                key={`${item.peer_id ?? ""}:${item.id}`}
                item={item}
                showSource={showSource}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function MediaCard({ item, showSource }: { readonly item: Item; readonly showSource: boolean }) {
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
  const from = item.peer_name ?? null;
  // A remote title's id means nothing on this instance, so the detail screen is
  // told which library to ask; without it the card led to the not-found screen.
  const address =
    item.peer_id === null || item.peer_id === undefined
      ? `/library/${item.id}`
      : `/library/${item.id}?peer=${item.peer_id}`;

  return (
    <MediaTile
      title={item.title}
      // Only while every library is on screen at once: on one library the
      // answer is the same for every card and says nothing.
      meta={
        showSource
          ? from === null
            ? t("library.fromLocal")
            : t("library.fromPeer", { name: from })
          : (item.studio ?? undefined)
      }
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
        // `article` does not contribute to an accessible name from content, so
        // without this the link around every tile would announce as nameless.
        <Link
          to={address}
          className="block rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary)]"
          onPointerEnter={() => setPreview(true)}
          onPointerLeave={() => setPreview(false)}
          aria-label={item.title}
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
