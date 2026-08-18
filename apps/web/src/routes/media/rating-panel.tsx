/**
 * The rating control on the detail screen.
 *
 * Two things at once, and the design keeps them visually distinct: what the
 * household thinks — an average and a bar per star — and what *you* think, a
 * control you can set and take back.
 *
 * The stars are real buttons rather than a styled range input. A range slider
 * announces "50" and moves in steps nobody chose; five buttons announce
 * "3 stars" and are reachable one Tab apart.
 */
import { Stars } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";

const SCALE = [1, 2, 3, 4, 5] as const;

export interface RatingPanelProps {
  readonly mediaId: string;
}

export function RatingPanel({ mediaId }: RatingPanelProps): JSX.Element {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const key = ["rating", mediaId] as const;

  const rating = useQuery({
    queryKey: key,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/media/{media_id}/rating", {
        params: { path: { media_id: mediaId } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const set = useMutation({
    mutationFn: async (stars: number) => {
      const { error, response } = await getApiClient().PUT("/api/media/{media_id}/rating", {
        params: { path: { media_id: mediaId } },
        body: { stars },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: key }),
  });

  const clear = useMutation({
    mutationFn: async () => {
      const { error, response } = await getApiClient().DELETE("/api/media/{media_id}/rating", {
        params: { path: { media_id: mediaId } },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: key }),
  });

  if (rating.data === undefined) return <div className="h-24" aria-hidden="true" />;
  const summary = rating.data;
  const yours = summary.your_stars;

  return (
    <section aria-labelledby="rating-heading" className="flex flex-col gap-3">
      <h2 id="rating-heading" className="text-sm text-ink">
        {t("rating.title")}
      </h2>

      <div className="flex items-center gap-3">
        <Stars
          value={summary.average}
          label={
            summary.average === null
              ? t("library.rating.none")
              : t("library.rating.value", { value: summary.average, count: summary.count })
          }
        />
        <span className="text-sm tabular-nums text-ink">
          {summary.average === null ? "—" : summary.average.toFixed(1)}
        </span>
        <span className="text-xs text-ink-muted">
          {t("rating.count", { count: summary.count })}
        </span>
      </div>

      {/* One bar per star, widest first. Reading it is how you tell "everyone
          agrees it is a four" from "half loved it and half did not". */}
      <ul className="flex flex-col gap-1">
        {[5, 4, 3, 2, 1].map((star) => {
          const value = summary.breakdown[String(star)] ?? 0;
          const share = summary.count === 0 ? 0 : (value / summary.count) * 100;
          return (
            <li key={star} className="flex items-center gap-2 text-2xs text-ink-muted">
              <span className="w-6 tabular-nums">{star}★</span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
                <span className="block h-full bg-[var(--primary)]" style={{ width: `${share}%` }} />
              </span>
              <span className="w-8 text-right tabular-nums">{value}</span>
            </li>
          );
        })}
      </ul>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-ink-muted">{t("rating.yours")}</span>
        <span className="flex gap-1">
          {SCALE.map((star) => (
            <button
              key={star}
              type="button"
              aria-pressed={yours === star}
              aria-label={t("rating.set", { count: star })}
              disabled={set.isPending}
              onClick={() => set.mutate(star)}
              className={
                yours !== null && star <= yours
                  ? "text-[var(--primary)]"
                  : "text-[var(--border-strong)] hover:text-[var(--pa-accent-400)]"
              }
            >
              <svg viewBox="0 0 16 16" className="size-5" fill="currentColor" aria-hidden="true">
                <path d="M8 1.6l1.9 4 4.4.6-3.2 3 .8 4.3L8 11.5 4.1 13.5l.8-4.3-3.2-3 4.4-.6z" />
              </svg>
            </button>
          ))}
        </span>
        {yours === null ? null : (
          <button
            type="button"
            className="text-xs text-ink-muted underline"
            disabled={clear.isPending}
            onClick={() => clear.mutate()}
          >
            {t("rating.clear")}
          </button>
        )}
      </div>
    </section>
  );
}
