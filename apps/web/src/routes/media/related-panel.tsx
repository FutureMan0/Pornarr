/**
 * Titles like the one on screen.
 *
 * A horizontal row rather than a grid, because it is a sidebar to the thing you
 * are already looking at and not a second library. Each row carries the reason
 * it is there — same performer, same studio, shared tags — which is the whole
 * difference between a related row and six titles nobody chose.
 *
 * The reason arrives as a key, not a sentence. The server decides *which* link
 * is the strongest; the wording is translated here, where the rest of the
 * interface's language lives.
 */
import { Artwork, Stars } from "@pornarr/ui";
import { useQuery } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { tileBlur, useArtVisible } from "../../lib/art-visibility";
import { formatDuration, seedFrom } from "../../lib/format";

export interface RelatedPanelProps {
  readonly mediaId: string;
}

export function RelatedPanel({ mediaId }: RelatedPanelProps): JSX.Element | null {
  const { t } = useTranslation();
  const artVisible = useArtVisible();

  const related = useQuery({
    queryKey: ["related", mediaId],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/media/{media_id}/related", {
        params: { path: { media_id: mediaId } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  // Nothing related is a fact about the library, not a failure, and a heading
  // over an empty row is worse than no row: it promises something.
  if (related.data === undefined || related.data.length === 0) return null;

  return (
    <section aria-labelledby="related-heading" className="flex flex-col gap-3">
      <h2 id="related-heading" className="text-2xs uppercase tracking-[0.08em] text-ink-muted">
        {t("related.title")}
      </h2>

      <ul className="flex flex-col gap-2">
        {related.data.map((item) => (
          <li key={item.media_id}>
            <Link
              to={`/library/${item.media_id}`}
              className="flex items-center gap-3 rounded-md p-2 hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary)]"
            >
              <Artwork
                seed={seedFrom(item.media_id)}
                blur={tileBlur(artVisible)}
                className="w-32 flex-none"
              >
                {item.duration_seconds === null ? null : (
                  <span className="absolute bottom-1 right-1 rounded bg-[color-mix(in_oklch,var(--pa-bg-00)_82%,transparent)] px-1.5 py-px text-2xs tabular-nums text-ink">
                    {formatDuration(item.duration_seconds)}
                  </span>
                )}
              </Artwork>

              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-sm text-ink">{item.title}</span>
                <span className="truncate text-2xs text-ink-muted">{item.studio ?? "—"}</span>
                <span className="flex items-center gap-2">
                  <Stars
                    value={item.rating}
                    label={
                      item.rating === null
                        ? t("library.rating.none")
                        : t("library.rating.value", {
                            value: item.rating,
                            count: item.rating_count,
                          })
                    }
                  />
                  <span className="text-2xs tabular-nums text-ink-muted">
                    {item.rating === null ? "—" : item.rating.toFixed(1)}
                  </span>
                  {/* Why this title is here, in one chip. */}
                  <span className="truncate rounded-full border border-border px-2 py-px text-2xs text-ink-muted">
                    {reasonText(t, item)}
                  </span>
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

type Reason = {
  readonly reason: string;
  readonly shared_tags: number;
  readonly shared_performers: number;
};

/**
 * The chip's words. Shared tags carry their count because "3 shared tags" says
 * considerably more than "shared tags"; the other two links are singular facts
 * and a number would add nothing.
 */
function reasonText(t: TFunction, item: Reason): string {
  if (item.reason === "performer") return t("related.reason.performer");
  if (item.reason === "tag") return t("related.reason.tag", { count: item.shared_tags });
  return t("related.reason.studio");
}
