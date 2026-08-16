/**
 * B6 — the Shorts grid.
 *
 * Vertical clips, so the tiles are 9/16 rather than 16/10 and the grid is
 * denser. Each clip links back to the title it was cut from, which is the one
 * navigation the design insists on: a clip is an excerpt, and an excerpt with
 * no way back to the source is a dead end.
 */
import { MediaTile } from "@pornarr/ui";
import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { formatDuration, seedFrom } from "../../lib/format";

const SORTS = ["trending", "newest", "top", "duration"] as const;
type Sort = (typeof SORTS)[number];

export function ShortsRoute(): JSX.Element {
  const { t } = useTranslation();
  const [sort, setSort] = useState<Sort>("trending");

  const shorts = useQuery({
    queryKey: ["shorts", sort],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/shorts", {
        params: { query: { sort, limit: 60 } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  return (
    <section aria-labelledby="shorts-heading" className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 id="shorts-heading" className="text-xl text-ink">
          {t("shorts.title")}
        </h1>
        {shorts.data === undefined ? null : (
          <span className="text-xs text-ink-muted">
            {t("shorts.count", { count: shorts.data.length })}
          </span>
        )}
      </div>

      {/* A fieldset rather than a div carrying role="group": the grouping is
          then in the markup itself, and the legend names it for a screen
          reader without a parallel aria-label to keep in step. */}
      <fieldset className="flex flex-wrap gap-1 border-0 p-0">
        <legend className="sr-only">{t("shorts.sort.label")}</legend>
        {SORTS.map((option) => (
          <button
            key={option}
            type="button"
            // aria-pressed rather than a radio group: these are filters on one
            // list, not a choice that is submitted.
            aria-pressed={sort === option}
            onClick={() => setSort(option)}
            className={
              sort === option
                ? "rounded-md bg-[color-mix(in_oklch,var(--primary)_14%,transparent)] px-3 py-1.5 text-sm text-[var(--pa-accent-300)]"
                : "rounded-md px-3 py-1.5 text-sm text-ink-muted hover:bg-surface-3 hover:text-ink"
            }
          >
            {t(`shorts.sort.${option}`)}
          </button>
        ))}
      </fieldset>

      {shorts.isPending ? <p className="text-sm text-ink-muted">{t("shorts.loading")}</p> : null}
      {shorts.isError ? (
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      ) : null}

      {shorts.data !== undefined ? (
        shorts.data.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("shorts.empty")}</p>
        ) : (
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-3">
            {shorts.data.map((short) => (
              <li key={short.id}>
                <MediaTile
                  title={short.title}
                  meta={t("shorts.from", { title: short.media_title })}
                  duration={formatDuration(short.duration_seconds)}
                  rating={short.average_stars}
                  ratingLabel={
                    short.average_stars === null
                      ? t("library.rating.none")
                      : t("library.rating.value", {
                          value: short.average_stars,
                          count: short.comment_count,
                        })
                  }
                  commentCount={short.comment_count}
                  seed={seedFrom(short.id)}
                  action={(content) => (
                    // Back to the full title, which is what `parent_media_id`
                    // is carried for.
                    <Link to={`/library/${short.parent_media_id}`} className="block rounded-md">
                      {content}
                    </Link>
                  )}
                />
              </li>
            ))}
          </ul>
        )
      ) : null}
    </section>
  );
}
