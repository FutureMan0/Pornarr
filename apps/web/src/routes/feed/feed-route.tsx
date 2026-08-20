/**
 * B8 — the feed: what the server thinks you'll like, and what people sent you.
 *
 * Two sections that look similar and mean different things. A recommendation is
 * the server guessing; a send is a person choosing. The design keeps them apart
 * and names the sender on one and not the other, which is the only place in the
 * product where another person is named at all.
 */
import { MediaTile } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { usePageTitle } from "../../shell/page-title";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { tileBlur, useArtVisible } from "../../lib/art-visibility";
import { seedFrom } from "../../lib/format";

const SENT_KEY = ["recommendations", "sent-to-me"] as const;
const OUTBOX_KEY = ["sends", "sent"] as const;

export function FeedRoute(): JSX.Element {
  const { t } = useTranslation();
  const artVisible = useArtVisible();
  usePageTitle(t("feed.sent.title"));
  const cache = useQueryClient();

  const forYou = useQuery({
    queryKey: ["recommendations"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/recommendations");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const outbox = useQuery({
    queryKey: OUTBOX_KEY,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/sends/sent");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const sent = useQuery({
    queryKey: SENT_KEY,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/recommendations/sent-to-me");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const seen = useMutation({
    mutationFn: async (sendId: string) => {
      const { error, response } = await getApiClient().POST("/api/sends/{send_id}/seen", {
        params: { path: { send_id: sendId } },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: SENT_KEY }),
  });

  return (
    <div className="flex flex-col gap-10">
      <section aria-label={t("feed.sent.title")} className="flex flex-col gap-4">
        <div>
          <p className="text-xs text-ink-muted">{t("feed.sent.intro")}</p>
        </div>

        {sent.data === undefined || sent.data.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("feed.sent.empty")}</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {sent.data.map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-center gap-3 rounded-md bg-surface-2 p-3"
              >
                <span className="text-sm text-ink">{item.media_title}</span>
                {/* The one attributed thing in the product. A hand-picked
                    recommendation is worthless without knowing whose taste it was. */}
                <span className="text-xs text-[var(--pa-accent-300)]">
                  {t("feed.sent.from", { name: item.sender ?? "" })}
                </span>
                {item.note === null ? null : (
                  <span className="w-full text-xs text-ink-muted">{item.note}</span>
                )}
                <span className="ml-auto flex items-center gap-2">
                  {item.seen_at === null ? (
                    <button
                      type="button"
                      className="rounded-md px-2 py-1 text-xs text-ink-muted hover:bg-surface-3 hover:text-ink"
                      onClick={() => seen.mutate(item.id)}
                    >
                      {t("feed.sent.markSeen")}
                    </button>
                  ) : null}
                  <Link
                    to={`/library/${item.media_id}`}
                    className="rounded-md px-2 py-1 text-xs text-[var(--pa-accent-300)]"
                  >
                    {t("feed.open")}
                  </Link>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* What you handed to somebody else, and whether they have looked at it.
          `GET /api/sends/sent` had no reader: a send disappeared the moment it
          was made, so there was no way to tell a title you had passed on from
          one you meant to. Absent rather than empty - a shelf headed "Sent by
          you" over nothing tells a reader the feature is broken. */}
      {outbox.data === undefined || outbox.data.length === 0 ? null : (
        <section aria-labelledby="outbox-heading" className="flex flex-col gap-4">
          <h2 id="outbox-heading" className="text-lg text-ink">
            {t("feed.outbox.title")}
          </h2>
          <ul className="flex flex-col gap-2">
            {outbox.data.map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-baseline justify-between gap-2 border border-border bg-surface p-3"
              >
                <Link to={`/library/${item.media_id}`} className="text-sm text-ink underline">
                  {item.media_title}
                </Link>
                <span className="text-2xs text-ink-faint">
                  {t("feed.outbox.to", { name: item.recipient ?? "" })}
                  {" · "}
                  {item.seen_at === null ? t("feed.outbox.unseen") : t("feed.outbox.seen")}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="for-you-heading" className="flex flex-col gap-4">
        <h2 id="for-you-heading" className="text-lg text-ink">
          {t("feed.forYou.title")}
        </h2>

        {forYou.isPending ? <p className="text-sm text-ink-muted">{t("feed.loading")}</p> : null}
        {forYou.isError ? (
          <p role="alert" className="text-sm text-ink">
            {t("errors.generic")}
          </p>
        ) : null}

        {forYou.data !== undefined ? (
          forYou.data.length === 0 ? (
            // Not a failure. A feed needs watching behind it, and saying so is
            // more useful than an empty grid.
            <p className="text-sm text-ink-muted">{t("feed.forYou.empty")}</p>
          ) : (
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-4">
              {forYou.data.map((item) => (
                <li key={item.media_id} className="flex flex-col gap-2">
                  <MediaTile
                    blur={tileBlur(artVisible)}
                    title={item.title}
                    seed={seedFrom(item.media_id)}
                    rating={null}
                    ratingLabel={t("library.rating.none")}
                    poster={
                      <img src={`/api/media/${item.media_id}/poster`} alt="" loading="lazy" />
                    }
                    action={(content) => (
                      // `article`, the tile's root, does not contribute to an
                      // accessible name from content, so the link needs one of
                      // its own or a screen reader hears nothing but "link".
                      <Link
                        to={`/library/${item.media_id}`}
                        className="block rounded-md"
                        aria-label={item.title}
                      >
                        {content}
                      </Link>
                    )}
                  />
                  <p className="text-xs text-[var(--pa-accent-300)]">
                    {t("feed.match", { value: item.match_score })}
                  </p>
                  {/* The reasons the recommender actually used, strongest first
                      — not a generic "because you watched" line. */}
                  <ul className="flex flex-col gap-0.5">
                    {item.reasons.map((reason) => (
                      <li key={reason} className="text-2xs text-ink-muted">
                        {reason}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )
        ) : null}
      </section>
    </div>
  );
}
