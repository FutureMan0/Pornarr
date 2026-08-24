/**
 * Every clip at once — the browse view, not the way in.
 *
 * `/shorts` is the feed. This is where you come when you are looking for a
 * particular clip rather than for something to watch, which is the only job a
 * grid of forty-second excerpts does well: nobody can judge one from a still,
 * but you can find one you already know about.
 *
 * The tiles are 9/16. A clip shaped like a film is a clip that gets opened by
 * somebody expecting a film, and the shape says "forty seconds" faster than the
 * duration badge does.
 */
import { MediaTile } from "@pornarr/ui";
import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { tileBlur, useArtVisible } from "../../lib/art-visibility";
import { formatDuration, seedFrom } from "../../lib/format";
import { usePageTitle } from "../../shell/page-title";

const SORTS = ["trending", "newest", "top", "duration"] as const;
type Sort = (typeof SORTS)[number];

export function ShortsRoute(): JSX.Element {
  const { t } = useTranslation();
  const artVisible = useArtVisible();
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

  usePageTitle(
    t("shorts.title"),
    shorts.data === undefined ? undefined : t("shorts.count", { count: shorts.data.length }),
  );

  return (
    <section aria-label={t("shorts.title")} className="flex flex-col gap-6">
      {/* A fieldset rather than a div carrying role="group": the grouping is
          then in the markup itself, and the legend names it for a screen
          reader without a parallel aria-label to keep in step. */}
      <div className="flex flex-wrap items-center justify-between gap-3">
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
                  ? "rounded-md bg-[var(--primary-weak)] px-3 py-1.5 text-sm text-[var(--pa-accent-300)]"
                  : "rounded-md px-3 py-1.5 text-sm text-ink-muted hover:bg-surface-3 hover:text-ink"
              }
            >
              {t(`shorts.sort.${option}`)}
            </button>
          ))}
        </fieldset>

        <Link
          to="/shorts"
          className="rounded-full border border-border-control px-3 py-1 text-2xs text-ink-muted transition-colors hover:bg-surface-3 hover:text-ink"
        >
          {t("shorts.backToFeed")}
        </Link>
      </div>

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
                  blur={tileBlur(artVisible)}
                  // A clip shaped like a film gets opened by somebody
                  // expecting a film.
                  ratio="9 / 16"
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
                    // Into the feed, anchored on this clip. The way back to the
                    // full title is there, where the timestamp has somewhere to
                    // land — a grid tile has no room to say "at 15:11".
                    //
                    // `article`, the tile's root, does not contribute to an
                    // accessible name from content, so the link needs one of
                    // its own or a screen reader hears nothing but "link".
                    <Link
                      to={`/shorts/${short.id}`}
                      className="block rounded-lg"
                      aria-label={short.title}
                    >
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
