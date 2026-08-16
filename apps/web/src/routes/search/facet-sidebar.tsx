/**
 * B2 — the filter sidebar, and the chips that say what is currently on.
 *
 * Every option carries its count, which is the whole point: a filter list
 * without numbers makes you click to find out that a choice leads nowhere. The
 * server computes them from the same pipeline as the results, so a number here
 * is a promise the list underneath will keep.
 *
 * SELECTING IS TOGGLING. Clicking the option you already chose clears it. The
 * alternative — a separate clear affordance per group — is four more controls
 * for something everyone tries by clicking anyway.
 *
 * THE CHIPS ARE NOT DECORATION. With five collapsible groups the sidebar can
 * scroll a selection out of sight, and a filter you cannot see is a filter you
 * will blame the library for. The chip row keeps every active choice on screen
 * with its own way off.
 */
import type { TFunction } from "i18next";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";

import type { SearchFacets, SearchFilters } from "./search";

/** Which URL parameter each group writes, and which facet feeds it. */
const GROUPS = [
  { id: "studio", param: "studio", facet: "studio" },
  { id: "resolution", param: "quality", facet: "resolution" },
  { id: "duration", param: "duration", facet: "duration" },
  { id: "rating", param: "rating_gte", facet: "rating" },
  { id: "tag", param: "tag", facet: "tag" },
] as const satisfies readonly {
  id: string;
  param: string;
  facet: keyof Omit<SearchFacets, "matched" | "total" | "capped">;
}[];

export interface FacetSidebarProps {
  readonly facets: SearchFacets | undefined;
  readonly filters: SearchFilters;
  readonly onToggle: (param: string, value: string) => void;
  readonly onClearAll: () => void;
}

export function FacetSidebar({
  facets,
  filters,
  onToggle,
  onClearAll,
}: FacetSidebarProps): JSX.Element | null {
  const { t } = useTranslation();
  if (facets === undefined) return null;

  const active = activeSelections(filters);

  return (
    <div className="flex flex-col gap-5">
      {active.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-2xs uppercase tracking-[0.08em] text-ink-muted">
            {t("search.facets.active")}
          </span>
          {active.map((selection) => (
            <button
              key={`${selection.param}-${selection.value}`}
              type="button"
              onClick={() => onToggle(selection.param, selection.value)}
              className="flex items-center gap-1.5 rounded-full bg-[color-mix(in_oklch,var(--primary)_16%,transparent)] px-2.5 py-1 text-2xs text-[var(--pa-accent-300)]"
            >
              {label(t, selection.group, selection.value)}
              {/* The glyph is decorative; the button's text already names what
                  removing it does. */}
              <span aria-hidden="true">×</span>
              <span className="visually-hidden">{t("search.facets.remove")}</span>
            </button>
          ))}
          <button
            type="button"
            onClick={onClearAll}
            className="text-2xs text-ink-muted underline hover:text-ink"
          >
            {t("search.facets.clearAll")}
          </button>
        </div>
      ) : null}

      {GROUPS.map((group) => {
        const values = facets[group.facet];
        if (values.length === 0) return null;
        const headingId = `facet-${group.id}`;
        return (
          <fieldset key={group.id} className="flex flex-col gap-1.5 border-0 p-0">
            <legend
              id={headingId}
              className="mb-1 text-2xs uppercase tracking-[0.08em] text-ink-muted"
            >
              {groupLabel(t, group.id)}
            </legend>
            {values.map((option) => {
              const selected = isSelected(filters, group.id, option.value);
              return (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => onToggle(group.param, option.value)}
                  className="flex items-center gap-2 rounded-md px-1 py-1 text-left text-sm hover:bg-surface-2"
                >
                  <span
                    aria-hidden="true"
                    className={
                      selected
                        ? "size-3.5 flex-none rounded-[3px] bg-[var(--primary)]"
                        : "size-3.5 flex-none rounded-[3px] border border-border-control"
                    }
                  />
                  <span className={selected ? "flex-1 text-ink" : "flex-1 text-ink-muted"}>
                    {label(t, group.id, option.value)}
                  </span>
                  <span className="tabular-nums text-2xs text-ink-faint">{option.count}</span>
                </button>
              );
            })}
          </fieldset>
        );
      })}
    </div>
  );
}

function isSelected(filters: SearchFilters, group: string, value: string): boolean {
  if (group === "studio") return filters.studio === value;
  if (group === "resolution") return filters.quality === value;
  if (group === "duration") return filters.duration === value;
  if (group === "tag") return filters.tag === value;
  if (group === "rating") return String(filters.ratingFloor ?? "") === value;
  return false;
}

function activeSelections(
  filters: SearchFilters,
): { param: string; group: string; value: string }[] {
  const out: { param: string; group: string; value: string }[] = [];
  if (filters.studio !== undefined) {
    out.push({ param: "studio", group: "studio", value: filters.studio });
  }
  if (filters.quality !== undefined) {
    out.push({ param: "quality", group: "resolution", value: filters.quality });
  }
  if (filters.duration !== undefined) {
    out.push({ param: "duration", group: "duration", value: filters.duration });
  }
  if (filters.ratingFloor !== undefined) {
    out.push({ param: "rating_gte", group: "rating", value: String(filters.ratingFloor) });
  }
  if (filters.tag !== undefined) {
    out.push({ param: "tag", group: "tag", value: filters.tag });
  }
  return out;
}

/**
 * How a facet value reads.
 *
 * Studios and tags are names the library already holds and are printed as they
 * are — translating a studio would be inventing one. Durations and ratings are
 * the server's own vocabulary and do get worded here.
 */
/**
 * The bands, spelled out rather than interpolated into a key — the translation
 * keys are typed, and a key built from a server string is not one of them. A
 * band the server grows later prints as itself instead of as a missing key.
 */
function durationLabel(t: TFunction, value: string): string {
  switch (value) {
    case "under_10":
      return t("search.facets.duration.under_10");
    case "10_20":
      return t("search.facets.duration.10_20");
    case "20_40":
      return t("search.facets.duration.20_40");
    case "over_40":
      return t("search.facets.duration.over_40");
    default:
      return value;
  }
}

function groupLabel(t: TFunction, group: string): string {
  switch (group) {
    case "studio":
      return t("search.facets.group.studio");
    case "resolution":
      return t("search.facets.group.resolution");
    case "duration":
      return t("search.facets.group.duration");
    case "rating":
      return t("search.facets.group.rating");
    default:
      return t("search.facets.group.tag");
  }
}

function label(t: TFunction, group: string, value: string): string {
  if (group === "duration") return durationLabel(t, value);
  if (group === "rating") {
    return value === "unrated"
      ? t("search.facets.rating.unrated")
      : t("search.facets.rating.floor", { count: Number(value) });
  }
  return value;
}
