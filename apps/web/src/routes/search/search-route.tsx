/** The URL-addressable search workspace: local library first, indexers progressively. */
import { Button, Input, Select, Sheet, SkeletonRegion } from "@pornarr/ui";
import type { JSX, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { Numeric, useFormat } from "../../i18n/format";
import { messageForError } from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";
import { usePhoneLayout } from "../../shell/sidebar";
import { FacetSidebar } from "./facet-sidebar";
import {
  type ExternalSearchItem,
  type SearchFilters,
  useGrabRelease,
  useIndexerSearch,
  useLocalSearch,
  useSearchFacets,
  useStartIndexerSearch,
} from "./search";

const DEBOUNCE_MILLISECONDS = 300;
const STATUS_KEYS = {
  pending: "search.status.pending",
  completed: "search.status.completed",
  cached: "search.status.cached",
  failed: "search.status.failed",
  timed_out: "search.status.timedOut",
  unhealthy: "search.status.unhealthy",
  cancelled: "search.status.cancelled",
} as const;

export function SearchRoute() {
  const { t } = useTranslation();
  usePageTitle(t("search.title"));
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const deferredQuery = useDebouncedValue(query.trim());
  const filters = useMemo(() => readFilters(params), [params]);
  const localSearch = useLocalSearch(deferredQuery, filters);
  const facets = useSearchFacets(deferredQuery, filters);
  const startIndexerSearch = useStartIndexerSearch();
  const start = startIndexerSearch.mutate;
  const [searchId, setSearchId] = useState<string | null>(null);
  const indexerSearch = useIndexerSearch(searchId, filters);

  useEffect(() => {
    setSearchId(null);
    if (deferredQuery === "") return;
    start(deferredQuery, { onSuccess: setSearchId });
  }, [deferredQuery, start]);

  /** Selecting the value already on clears it; see the note in the sidebar. */
  const toggleFacet = (key: string, value: string): void => {
    setValue(key, params.get(key) === value ? "" : value);
  };

  const clearFacets = (): void => {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        for (const key of ["studio", "quality", "duration", "rating_gte", "tag"]) {
          next.delete(key);
        }
        return next;
      },
      { replace: true },
    );
  };

  /** Every filter except the query itself — what the sheet holds. */
  const FILTER_KEYS = [
    "quality",
    "minimum_size",
    "maximum_size",
    "maximum_age_days",
    "indexer_id",
    "protocol",
    "minimum_seeders",
    "sort",
  ] as const;

  const phone = usePhoneLayout();
  const [filtersOpen, setFiltersOpen] = useState(false);
  // How many are actually doing something, for the count on the button. `sort`
  // has a default, so having a value is not the same as being set.
  const activeFilters = FILTER_KEYS.filter((key) => {
    const value = params.get(key);
    if (value === null || value === "") return false;
    return !(key === "sort" && value === "relevance");
  }).length;

  const clearAllFilters = (): void => {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        for (const key of FILTER_KEYS) next.delete(key);
        return next;
      },
      { replace: true },
    );
  };

  const setValue = (key: string, value: string): void => {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value === "") next.delete(key);
        else next.set(key, value);
        return next;
      },
      { replace: true },
    );
  };

  /** The eight controls that are a sheet on a phone and a row on a desktop. */
  const filterControls = (
    <>
      <FilterSelect
        id="search-quality"
        label={t("search.filters.quality")}
        value={filters.quality ?? ""}
        onChange={(value) => setValue("quality", value)}
        options={[
          ["", t("search.filters.anyQuality")],
          ["720p", "720p"],
          ["1080p", "1080p"],
          ["2160p", "2160p"],
        ]}
      />
      <FilterInput
        id="search-minimum-size"
        label={t("search.filters.minimumSize")}
        value={params.get("minimum_size") ?? ""}
        onChange={(value) => setValue("minimum_size", value)}
      />
      <FilterInput
        id="search-maximum-size"
        label={t("search.filters.maximumSize")}
        value={params.get("maximum_size") ?? ""}
        onChange={(value) => setValue("maximum_size", value)}
      />
      <FilterSelect
        id="search-age"
        label={t("search.filters.age")}
        value={params.get("maximum_age_days") ?? ""}
        onChange={(value) => setValue("maximum_age_days", value)}
        options={[
          ["", t("search.filters.anyAge")],
          ["1", t("search.filters.day")],
          ["7", t("search.filters.week")],
          ["30", t("search.filters.month")],
        ]}
      />
      <FilterText
        id="search-indexer"
        label={t("search.filters.indexer")}
        value={params.get("indexer_id") ?? ""}
        onChange={(value) => setValue("indexer_id", value)}
      />
      <FilterSelect
        id="search-protocol"
        label={t("search.filters.protocol")}
        value={filters.protocol ?? ""}
        onChange={(value) => setValue("protocol", value)}
        options={[
          ["", t("search.filters.anyProtocol")],
          ["torrent", t("search.filters.torrent")],
          ["usenet", t("search.filters.usenet")],
        ]}
      />
      <FilterInput
        id="search-minimum-seeders"
        label={t("search.filters.minimumSeeders")}
        value={params.get("minimum_seeders") ?? ""}
        onChange={(value) => setValue("minimum_seeders", value)}
      />
      <FilterSelect
        id="search-sort"
        label={t("search.filters.sort")}
        value={filters.sort}
        onChange={(value) => setValue("sort", value)}
        options={[
          ["relevance", t("search.sort.relevance")],
          ["age", t("search.sort.age")],
          ["size", t("search.sort.size")],
          ["quality", t("search.sort.quality")],
          ["seeders", t("search.sort.seeders")],
          ["estimated_time", t("search.sort.estimatedTime")],
        ]}
      />
    </>
  );

  return (
    <section className="flex flex-col gap-10" aria-label={t("search.title")}>
      <header className="flex flex-col gap-2">
        <p className="max-w-[70ch] text-sm text-ink-muted">{t("search.intro")}</p>
      </header>

      <form
        className={
          phone
            ? "flex flex-col gap-3 border-y border-border py-4"
            : "grid gap-4 border-y border-border py-4 md:grid-cols-[minmax(0,1fr)_repeat(4,minmax(8rem,0.45fr))]"
        }
        onSubmit={(event) => event.preventDefault()}
      >
        <div className="md:col-span-5">
          <label className="mb-1 block text-sm text-ink" htmlFor="search-query">
            {t("search.query")}
          </label>
          <Input
            id="search-query"
            type="search"
            value={query}
            placeholder={t("search.placeholder")}
            onChange={(event) => setValue("q", event.target.value)}
          />
        </div>

        {/* Eight controls above the first result is a form to fill in, not a
            search. On a phone they move into the sheet C3 draws, and the count
            on the button says how many are doing anything. */}
        {phone ? (
          <>
            <Button variant="secondary" onClick={() => setFiltersOpen(true)}>
              {activeFilters === 0
                ? t("search.openFilters")
                : `${t("search.openFilters")} (${activeFilters})`}
            </Button>
            <Sheet
              open={filtersOpen}
              onClose={() => setFiltersOpen(false)}
              title={t("search.filtersTitle")}
              action={
                <Button variant="ghost" onClick={clearAllFilters}>
                  {t("search.clearFilters")}
                </Button>
              }
              footer={
                <Button onClick={() => setFiltersOpen(false)}>{t("search.applyFilters")}</Button>
              }
            >
              {filterControls}
            </Sheet>
          </>
        ) : (
          filterControls
        )}
      </form>

      {/* The sidebar beside the results, as the design lays it out; stacked
          below the breakpoint, where a 15rem column would leave the table
          nothing to be wide in. */}
      {/* `min-w-0` on the children, not just `minmax(0,1fr)` on the track: a
          grid item defaults to `min-width: auto`, so the results table would
          widen the column past the viewport however the track is declared. */}
      <div className="grid gap-8 lg:grid-cols-[15rem_minmax(0,1fr)]">
        <FacetSidebar
          facets={facets.data}
          filters={filters}
          onToggle={toggleFacet}
          onClearAll={clearFacets}
        />
        <div className="min-w-0">
          <LocalResults query={deferredQuery} search={localSearch} facets={facets.data} />
        </div>
      </div>

      <ExternalResults
        query={deferredQuery}
        search={indexerSearch}
        starting={startIndexerSearch.isPending}
        startError={startIndexerSearch.error}
      />
    </section>
  );
}

/**
 * One field of a result, for the card a phone gets instead of a table row.
 *
 * Label on the left, value on the right, which is the same reading order a table
 * header gives — the column name is just beside the value rather than above a
 * stack of them.
 */
function Field({
  label,
  children,
}: {
  readonly label: string;
  readonly children: ReactNode;
}): JSX.Element {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="flex-none text-2xs text-ink-muted">{label}</dt>
      <dd className="m-0 min-w-0 truncate text-right text-xs text-ink">{children}</dd>
    </div>
  );
}

function LocalResults({
  query,
  search,
  facets,
}: {
  readonly query: string;
  readonly search: ReturnType<typeof useLocalSearch>;
  readonly facets: ReturnType<typeof useSearchFacets>["data"];
}) {
  const { t } = useTranslation();
  const format = useFormat();
  const phone = usePhoneLayout();
  return (
    <section className="flex flex-col gap-4" aria-labelledby="local-results-heading">
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="local-results-heading" className="text-lg text-ink">
          {t("search.local.title")}
        </h2>
        {/* "86 of 3,268" — what the filters kept, out of what the search
            reached. `capped` says the second number is a floor, not a total,
            and the sentence changes rather than the number quietly lying. */}
        {facets === undefined ? null : (
          <span className="text-xs text-ink-muted">
            {facets.capped
              ? t("search.local.countCapped", { matched: facets.matched, total: facets.total })
              : t("search.local.count", { matched: facets.matched, total: facets.total })}
          </span>
        )}
        {search.isFetching ? (
          <span className="text-xs text-ink-muted">{t("search.searching")}</span>
        ) : null}
      </div>
      {query === "" ? (
        <p className="text-sm text-ink-muted">{t("search.local.notSearched")}</p>
      ) : search.isPending ? (
        <SkeletonRegion label={t("search.local.loading")}>
          <div className="h-24" />
        </SkeletonRegion>
      ) : search.isError ? (
        <p className="text-sm text-ink" role="alert">
          {messageForError(search.error)}
        </p>
      ) : search.data?.items.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("search.local.empty")}</p>
      ) : phone ? (
        /* A table you drag sideways is not a phone layout — it scrolls the whole
           page with it, and a keyboard cannot reach the scroller at all. The
           design draws these as cards; so does this. */
        <ul className="flex flex-col gap-2">
          {search.data?.items.map((item) => (
            <li key={item.id} className="card gap-1.5">
              <p className="text-sm text-ink">{item.title}</p>
              <dl className="flex flex-col gap-1">
                <Field label={t("search.columns.studio")}>{item.studio ?? "—"}</Field>
                <Field label={t("search.columns.age")}>
                  {item.release_date === null
                    ? "—"
                    : format.relativeDate(new Date(item.release_date))}
                </Field>
                <Field label={t("search.columns.quality")}>
                  {item.quality ?? item.resolution ?? "—"}
                </Field>
                <Field label={t("search.columns.size")}>
                  <Numeric>{format.bytes(item.size)}</Numeric>
                </Field>
              </dl>
            </li>
          ))}
        </ul>
      ) : (
        // `tabIndex`: a region that scrolls has to be reachable by a keyboard,
        // and a div with `overflow-x: auto` is not focusable on its own.
        <div className="min-w-0 overflow-x-auto border border-border">
          <table className="w-full min-w-[44rem] text-sm">
            <thead className="bg-surface-2 text-left text-xs text-ink-muted">
              <tr>
                <th className="px-3 py-2 font-medium">{t("search.columns.title")}</th>
                <th className="px-3 py-2 font-medium">{t("search.columns.studio")}</th>
                <th className="px-3 py-2 text-right font-medium">{t("search.columns.age")}</th>
                <th className="px-3 py-2 font-medium">{t("search.columns.quality")}</th>
                <th className="px-3 py-2 text-right font-medium">{t("search.columns.size")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {search.data?.items.map((item) => (
                <tr key={item.id} className="bg-surface text-ink hover:bg-surface-2">
                  <td className="max-w-[22rem] truncate px-3 py-2 font-medium" title={item.title}>
                    {item.title}
                  </td>
                  <td className="px-3 py-2 text-ink-muted">{item.studio ?? "—"}</td>
                  <td className="px-3 py-2 text-right text-ink-muted">
                    {item.release_date === null
                      ? "—"
                      : format.relativeDate(new Date(item.release_date))}
                  </td>
                  <td className="px-3 py-2">{item.quality ?? item.resolution ?? "—"}</td>
                  <td className="px-3 py-2 text-right">
                    <Numeric>{format.bytes(item.size)}</Numeric>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ExternalResults({
  query,
  search,
  starting,
  startError,
}: {
  readonly query: string;
  readonly search: ReturnType<typeof useIndexerSearch>;
  readonly starting: boolean;
  readonly startError: unknown;
}) {
  const { t } = useTranslation();
  const format = useFormat();
  const phone = usePhoneLayout();
  const grab = useGrabRelease();
  const items = useProgressiveItems(search.data?.id ?? null, search.data?.items ?? []);
  const names = new Map(items.map((item) => [item.indexer_id, item.indexer_name]));
  return (
    <section
      className="flex min-h-[20rem] flex-col gap-4"
      aria-labelledby="external-results-heading"
    >
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="external-results-heading" className="text-lg text-ink">
          {t("search.external.title")}
        </h2>
        {starting || search.isFetching ? (
          <span className="text-xs text-ink-muted">{t("search.searching")}</span>
        ) : null}
      </div>
      {query === "" ? (
        <p className="text-sm text-ink-muted">{t("search.external.notSearched")}</p>
      ) : startError !== null ? (
        <p className="text-sm text-ink" role="alert">
          {messageForUnknownError(startError)}
        </p>
      ) : search.isError ? (
        <p className="text-sm text-ink" role="alert">
          {messageForError(search.error)}
        </p>
      ) : (
        <>
          <IndexerStatus statuses={search.data?.statuses ?? {}} names={names} />
          {phone ? (
            /* Nine columns is 68rem, and 68rem on a 390px screen used to scroll
               the whole page sideways — measured at 673px. A card per release
               instead: the name, the four facts worth comparing, and the one
               control. */
            <ul className="flex flex-col gap-2">
              {items.map((item) => (
                <li key={item.id} className="card gap-2">
                  <p className="break-words font-mono text-2xs text-ink">{item.title}</p>
                  <dl className="flex flex-col gap-1">
                    <Field label={t("search.columns.indexer")}>{item.indexer_name}</Field>
                    <Field label={t("search.columns.quality")}>{item.quality ?? "—"}</Field>
                    <Field label={t("search.columns.size")}>
                      <Numeric>{item.size === null ? "—" : format.bytes(item.size)}</Numeric>
                    </Field>
                    <Field label={t("search.columns.seeders")}>
                      <Numeric>{item.seeders ?? "—"}</Numeric>
                    </Field>
                    <Field label={t("search.columns.age")}>
                      {item.published_at === null
                        ? "—"
                        : format.relativeDate(new Date(item.published_at))}
                    </Field>
                    <Field label={t("search.columns.time")}>
                      <Numeric>
                        {format.estimate(item.estimate.low_seconds, item.estimate.high_seconds)}
                      </Numeric>
                    </Field>
                  </dl>
                  <Button
                    variant="secondary"
                    loading={grab.isPending && grab.variables?.item.id === item.id}
                    onClick={() => grab.mutate({ item, releaseId: item.id })}
                  >
                    {t("search.grab")}
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            /* `min-w-0`: without it the wrapper stretches to the table and its
              own overflow never engages. `role="region"` with a name is what
              makes a scrolling box reachable by keyboard — a bare div with
              `tabIndex` is a focus stop with nothing to announce. */
            <div className="min-w-0 overflow-x-auto border border-border">
              <table className="w-full min-w-[68rem] text-sm">
                <thead className="bg-surface-2 text-left text-xs text-ink-muted">
                  <tr>
                    <th className="px-3 py-2 font-medium">{t("search.columns.title")}</th>
                    <th className="px-3 py-2 font-medium">{t("search.columns.indexer")}</th>
                    <th className="px-3 py-2 font-medium">{t("search.columns.quality")}</th>
                    <th className="px-3 py-2 text-right font-medium">{t("search.columns.size")}</th>
                    <th className="px-3 py-2 text-right font-medium">{t("search.columns.age")}</th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t("search.columns.seeders")}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t("search.columns.score")}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">{t("search.columns.time")}</th>
                    <th className="px-3 py-2">
                      <span className="visually-hidden">{t("search.columns.action")}</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {items.map((item) => (
                    <ExternalRow
                      key={item.id}
                      item={item}
                      onGrab={(releaseId = item.id) => grab.mutate({ item, releaseId })}
                      grabbing={grab.isPending && grab.variables?.item.id === item.id}
                      format={format}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {items.length === 0 && !search.isFetching ? (
            <p className="text-sm text-ink-muted">{t("search.external.empty")}</p>
          ) : null}
          {grab.isError ? (
            <p className="text-sm text-ink" role="alert">
              {messageForError(grab.error)}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

/** Keep the reader's established rows in place; newly arrived releases append. */
function useProgressiveItems(
  searchId: string | null,
  items: readonly ExternalSearchItem[],
): readonly ExternalSearchItem[] {
  const state = useRef({ searchId: null as string | null, ids: [] as string[] });
  if (state.current.searchId !== searchId) state.current = { searchId, ids: [] };
  for (const item of items) {
    if (!state.current.ids.includes(item.id)) state.current.ids.push(item.id);
  }
  return [...items].sort(
    (left, right) => state.current.ids.indexOf(left.id) - state.current.ids.indexOf(right.id),
  );
}

function ExternalRow({
  item,
  onGrab,
  grabbing,
  format,
}: {
  readonly item: ExternalSearchItem;
  readonly onGrab: (releaseId?: string) => void;
  readonly grabbing: boolean;
  readonly format: ReturnType<typeof useFormat>;
}) {
  const { t } = useTranslation();
  return (
    <tr className="bg-surface text-ink hover:bg-surface-2">
      <td className="max-w-[24rem] truncate px-3 py-2 font-mono text-xs" title={item.title}>
        {item.title}
        {(item.alternates?.length ?? 0) === 0 ? null : (
          <span className="ml-2 font-sans text-xs text-ink-muted">
            {(item.alternates ?? []).map((alternate) => (
              <button
                key={alternate.id}
                type="button"
                className="ml-1 underline decoration-border-control underline-offset-2"
                onClick={() => onGrab(alternate.id)}
              >
                {alternate.indexer_name}
              </button>
            ))}
          </span>
        )}
        {item.match.kind === "new" ? null : (
          <a
            className="ml-2 font-sans text-ink-muted underline decoration-border-control underline-offset-2"
            href={`/library?media=${item.match.media_id}`}
          >
            {t(`search.match.${item.match.kind}`)}
          </a>
        )}
      </td>
      <td className="px-3 py-2">{item.indexer_name}</td>
      <td className="px-3 py-2">{item.quality ?? "—"}</td>
      <td className="px-3 py-2 text-right">
        <Numeric>{item.size === null ? "—" : format.bytes(item.size)}</Numeric>
      </td>
      <td
        className="px-3 py-2 text-right"
        title={
          item.published_at === null ? undefined : format.dateTime(new Date(item.published_at))
        }
      >
        {item.published_at === null ? "—" : format.relativeDate(new Date(item.published_at))}
      </td>
      <td className="px-3 py-2 text-right">
        <Numeric>{item.seeders ?? "—"}</Numeric>
      </td>
      <td
        className="px-3 py-2 text-right"
        title={
          item.match.score === null
            ? undefined
            : t("search.match.scoreBreakdown", {
                title: format.number(item.match.breakdown.title ?? 0, 2),
                attributes: format.number(item.match.breakdown.attributes ?? 0, 2),
                reliability: format.number(item.match.breakdown.reliability ?? 0, 2),
              })
        }
      >
        <Numeric>{item.match.score === null ? "—" : format.number(item.match.score, 2)}</Numeric>
      </td>
      <td className="px-3 py-2 text-right">
        <Numeric>{format.estimate(item.estimate.low_seconds, item.estimate.high_seconds)}</Numeric>
      </td>
      <td className="px-3 py-2 text-right">
        <Button variant="secondary" loading={grabbing} onClick={() => onGrab()}>
          {t("search.grab")}
        </Button>
      </td>
    </tr>
  );
}

function IndexerStatus({
  statuses,
  names,
}: { readonly statuses: Record<string, string>; readonly names: ReadonlyMap<string, string> }) {
  const { t } = useTranslation();
  const entries = Object.entries(statuses);
  if (entries.length === 0) return null;
  return (
    <ul
      className="flex flex-wrap gap-2 text-xs text-ink-muted"
      aria-label={t("search.external.indexerStatus")}
    >
      {entries.map(([id, status]) => (
        <li key={id} className="border border-border bg-surface-2 px-2 py-1">
          <span className="text-ink">{names.get(id) ?? id.slice(0, 8)}</span>
          {" · "}
          {t(STATUS_KEYS[status as keyof typeof STATUS_KEYS] ?? "search.status.pending")}
        </li>
      ))}
    </ul>
  );
}

function FilterSelect({
  id,
  label,
  value,
  onChange,
  options,
}: {
  readonly id: string;
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly options: readonly (readonly [string, string])[];
}) {
  return (
    <label className="text-xs text-ink-muted" htmlFor={id}>
      {label}
      <Select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>
            {optionLabel}
          </option>
        ))}
      </Select>
    </label>
  );
}

function FilterInput({
  id,
  label,
  value,
  onChange,
}: {
  readonly id: string;
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
}) {
  return (
    <label className="text-xs text-ink-muted" htmlFor={id}>
      {label}
      <Input
        id={id}
        type="number"
        min="0"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function FilterText({
  id,
  label,
  value,
  onChange,
}: {
  readonly id: string;
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
}) {
  return (
    <label className="text-xs text-ink-muted" htmlFor={id}>
      {label}
      <Input id={id} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function readFilters(params: URLSearchParams): SearchFilters {
  const sort = params.get("sort");
  return {
    quality: value(params, "quality"),
    studio: value(params, "studio"),
    tag: value(params, "tag"),
    duration: value(params, "duration"),
    ratingFloor: numberValue(params, "rating_gte"),
    minimumSize: numberValue(params, "minimum_size"),
    maximumSize: numberValue(params, "maximum_size"),
    maximumAgeDays: numberValue(params, "maximum_age_days"),
    indexerId: value(params, "indexer_id"),
    protocol: value(params, "protocol"),
    minimumSeeders: numberValue(params, "minimum_seeders"),
    sort:
      sort === "age" ||
      sort === "size" ||
      sort === "quality" ||
      sort === "seeders" ||
      sort === "estimated_time"
        ? sort
        : "relevance",
  };
}

function value(params: URLSearchParams, key: string): string | undefined {
  return params.get(key) || undefined;
}
function numberValue(params: URLSearchParams, key: string): number | undefined {
  // A parameter that is not there is not zero. `Number(null)` is 0, which is
  // finite and non-negative, so the obvious version turns every absent numeric
  // filter into an explicit zero — harmless for a size floor, and a rejected
  // request for a rating floor, which the API requires to be at least 1.
  const raw = params.get(key);
  if (raw === null || raw.trim() === "") return undefined;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
}
function useDebouncedValue(value: string): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebounced(value), DEBOUNCE_MILLISECONDS);
    return () => window.clearTimeout(timeout);
  }, [value]);
  return debounced;
}
function messageForUnknownError(error: unknown): string {
  return error instanceof Error ? error.message : "";
}
