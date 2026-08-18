import { EmptyState, Select } from "@pornarr/ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { JSX } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { NAV_ITEMS } from "../../shell/sidebar";
import { useRootFolders } from "../settings/root-folders/root-folders";
import { ROOT_FOLDERS_PATH } from "../settings/settings-layout";

/** Read from the nav table so the link cannot outlive the route it points at. */
const REQUESTS_PATH = NAV_ITEMS[1].path;

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
};
type Page = { items: Item[]; next_offset: number | null };
type Facet = { value: string; count: number };
type Facets = { studios: Facet[]; performers: Facet[]; tags: Facet[] };

const SORTS = ["added", "title", "release", "duration"] as const;
type Sort = (typeof SORTS)[number];
const FILTER_KEYS = ["studio", "performer", "tag"] as const;

function sortOf(value: string | null): Sort {
  return SORTS.includes(value as Sort) ? (value as Sort) : "added";
}

export function LibraryRoute() {
  const { t } = useTranslation();
  const rootFolders = useRootFolders();
  const [params, setParams] = useSearchParams();
  // The filters live in the URL, so a filtered library is a link somebody can
  // send, and reloading the page does not throw the selection away.
  const filters = FILTER_KEYS.map((key) => [key, params.get(key) ?? ""] as const);
  const sort = sortOf(params.get("sort"));
  const facets = useQuery<Facets, Error>({
    queryKey: ["library", "facets"],
    queryFn: async (): Promise<Facets> => {
      const response = await fetch("/api/library/facets");
      if (!response.ok) throw new Error();
      return response.json() as Promise<Facets>;
    },
  });
  const library = useInfiniteQuery<Page, Error>({
    queryKey: ["library", Object.fromEntries(filters), sort],
    initialPageParam: 0,
    getNextPageParam: (page) => page.next_offset ?? undefined,
    queryFn: async ({ pageParam }): Promise<Page> => {
      const query = new URLSearchParams({ limit: "48", offset: String(pageParam), sort });
      for (const [key, value] of filters) if (value !== "") query.set(key, value);
      const response = await fetch(`/api/library?${query.toString()}`);
      if (!response.ok) throw new Error();
      return response.json() as Promise<Page>;
    },
  });
  const filtered = filters.some(([, value]) => value !== "");
  const setFilter = (key: string, value: string): void => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value === "") next.delete(key);
      else next.set(key, value);
      return next;
    });
  };
  const items = library.data?.pages.flatMap((page) => page.items) ?? [];
  if (library.isPending)
    return (
      <section aria-labelledby="library-heading">
        <h1 id="library-heading" className="text-xl text-ink">
          {t("library.title")}
        </h1>
        <p className="text-sm text-ink-muted">{t("library.loading")}</p>
      </section>
    );
  if (library.isError)
    return (
      <section aria-labelledby="library-heading">
        <h1 id="library-heading" className="text-xl text-ink">
          {t("library.title")}
        </h1>
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      </section>
    );
  return (
    <section aria-labelledby="library-heading" className="flex flex-col gap-6">
      <h1 id="library-heading" className="text-xl text-ink">
        {t("library.title")}
      </h1>
      <LibraryFilters
        facets={facets.data}
        loading={facets.isPending}
        studio={params.get("studio") ?? ""}
        performer={params.get("performer") ?? ""}
        tag={params.get("tag") ?? ""}
        sort={sort}
        onChange={setFilter}
      />
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
        <VirtualGrid
          items={items}
          onEnd={() =>
            library.hasNextPage && !library.isFetchingNextPage && void library.fetchNextPage()
          }
        />
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
  onChange,
}: {
  readonly facets: Facets | undefined;
  readonly loading: boolean;
  readonly studio: string;
  readonly performer: string;
  readonly tag: string;
  readonly sort: Sort;
  readonly onChange: (key: string, value: string) => void;
}): JSX.Element {
  const { t } = useTranslation();
  const label = (facet: Facet): string =>
    t("library.facetCount", { value: facet.value, count: facet.count });

  return (
    <div className="flex flex-wrap gap-4" aria-label={t("library.filters")}>
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
  const [preview, setPreview] = useState(false);
  const source = preview && item.sprite_url ? item.sprite_url : item.poster_url;
  return (
    <Link
      to={`/library/${item.id}`}
      className="overflow-hidden border border-border bg-surface"
      onPointerEnter={() => setPreview(true)}
      onPointerLeave={() => setPreview(false)}
    >
      <div className="relative aspect-video bg-surface-2">
        <img src={source} alt="" className="h-full w-full object-cover" loading="lazy" />
        {item.position_seconds !== null && item.progress_duration_seconds ? (
          <span
            className="absolute bottom-0 left-0 h-1 bg-ink"
            style={{
              width: `${Math.min(100, (item.position_seconds * 100) / item.progress_duration_seconds)}%`,
            }}
          />
        ) : null}
      </div>
      <div className="min-h-24 p-3">
        <h2 className="truncate text-sm font-medium text-ink">{item.title}</h2>
        <p className="truncate text-xs text-ink-muted">
          {item.studio ?? "—"}
          {item.release_date ? ` · ${item.release_date}` : ""}
        </p>
        <p className="text-xs text-ink-muted">{item.quality ?? item.resolution ?? "—"}</p>
      </div>
    </Link>
  );
}
