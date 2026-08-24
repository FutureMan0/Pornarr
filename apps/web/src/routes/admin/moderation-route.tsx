/**
 * A6 — ratings and comments, from the administrator's side.
 *
 * THIS SCREEN SEES NAMES THE REST OF THE APPLICATION DOES NOT. The moderation
 * queue attributes every remark, including under `anonymous_social`, and the
 * server does that on purpose: deciding on a report means knowing whether one
 * person wrote all six. Everywhere else — the detail screen, the shorts player
 * — the same setting hides the author completely.
 *
 * That asymmetry is worth stating out loud rather than leaving an administrator
 * to notice it. The screen says so, because someone who believes the household
 * is anonymous should not learn otherwise by accident.
 *
 * THE REPORT COUNT IS A NUMBER, NOT A NAME. The design shows "Reported by Tobi";
 * the API returns how many reports a comment has and never who filed them.
 * Naming the reporter turns moderation into a dispute between two people, and
 * the point of a report is that it is not one.
 *
 * THE RULES PANEL IS THE SETTINGS THAT EXIST. The design sketches five toggles;
 * three of them correspond to switches this server actually enforces. The other
 * two are not built here, because a toggle that changes nothing is worse than
 * an absent one — it is a promise about privacy that the software does not
 * keep.
 */
import { Stars } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { useFormat } from "../../i18n/format";
import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";

type Filter = "all" | "reported" | "answered";
const FILTERS: readonly Filter[] = ["all", "reported", "answered"];

/** The privacy switches this server enforces, in the order they matter. */
const RULES = [
  { key: "private_libraries", labelKey: "moderation.rules.privateLibraries" },
  { key: "pooled_search", labelKey: "moderation.rules.pooledSearch" },
  { key: "anonymous_social", labelKey: "moderation.rules.anonymousSocial" },
] as const;

export function ModerationRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const cache = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");

  const key = ["admin", "comments", filter] as const;

  const comments = useQuery({
    queryKey: key,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/comments", {
        params: {
          query: {
            reported_only: filter === "reported",
            ...(filter === "answered" ? { state: "answered" as const } : {}),
          },
        },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const ratings = useQuery({
    queryKey: ["admin", "ratings"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/ratings/overview");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const settings = useQuery({
    queryKey: ["admin", "settings"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/settings");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const invalidate = (): void => {
    void cache.invalidateQueries({ queryKey: ["admin", "comments"] });
  };

  const hide = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().POST(
        "/api/admin/comments/{comment_id}/hide",
        {
          params: { path: { comment_id: id } },
        },
      );
      if (error) throw apiFailure(error, response);
    },
    onSuccess: invalidate,
  });

  const resolve = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().POST(
        "/api/admin/comments/{comment_id}/resolve",
        { params: { path: { comment_id: id } } },
      );
      if (error) throw apiFailure(error, response);
    },
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().DELETE("/api/comments/{comment_id}", {
        params: { path: { comment_id: id } },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: invalidate,
  });

  const toggleRule = useMutation({
    mutationFn: async ({ key: name, value }: { key: string; value: boolean }) => {
      const { error, response } = await getApiClient().PATCH("/api/admin/settings", {
        body: { [name]: value },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: ["admin", "settings"] }),
  });

  const reported = (comments.data ?? []).filter((item) => item.reports > 0).length;

  usePageTitle(
    t("moderation.title"),
    ratings.data === undefined
      ? undefined
      : t("moderation.subtitle", {
          ratings: ratings.data.count,
          comments: comments.data?.length ?? 0,
          reported,
        }),
  );

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <section aria-label={t("moderation.title")} className="flex flex-col gap-4">
        <fieldset className="flex gap-1 border-0 p-0">
          <legend className="sr-only">{t("moderation.filter")}</legend>
          {FILTERS.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={filter === option}
              onClick={() => setFilter(option)}
              className={
                filter === option
                  ? "rounded-md bg-[var(--primary-weak)] px-3 py-1.5 text-sm text-[var(--pa-accent-300)]"
                  : "rounded-md px-3 py-1.5 text-sm text-ink-muted hover:bg-surface-3 hover:text-ink"
              }
            >
              {t(`moderation.tab.${option}` as "moderation.tab.all")}
            </button>
          ))}
        </fieldset>

        {/* Said once, above the list, rather than beside every remark. An
            administrator who believes the household is anonymous should not
            discover otherwise by reading a name. */}
        {settings.data?.anonymous_social === true ? (
          <p className="text-2xs text-ink-faint">{t("moderation.attributionNotice")}</p>
        ) : null}

        {comments.isError ? (
          <p role="alert" className="text-sm text-ink">
            {t("errors.generic")}
          </p>
        ) : null}

        {comments.data !== undefined && comments.data.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("moderation.empty")}</p>
        ) : null}

        <ul className="flex flex-col gap-3">
          {(comments.data ?? []).map((comment) => (
            <li key={comment.id} className="flex flex-col gap-2 rounded-lg bg-surface-2 p-4">
              <div className="flex flex-wrap items-center gap-2">
                {/* Present even under an anonymous household; see the note at
                    the top of this file, and the disclosure above the list. */}
                <span className="text-sm text-ink">{comment.author ?? t("comments.someone")}</span>
                <span className="text-2xs text-ink-muted">{t("moderation.on")}</span>
                <Link
                  to={`/library/${comment.media_id}`}
                  className="text-sm text-ink underline decoration-border"
                >
                  {comment.media_title}
                </Link>
                {comment.stars === null ? null : (
                  <Stars
                    value={comment.stars}
                    label={t("library.rating.value", { value: comment.stars, count: 1 })}
                  />
                )}
                <span className="ml-auto flex items-center gap-2">
                  {comment.reports > 0 ? (
                    <span className="rounded-full bg-[var(--primary-weak)] px-2 py-0.5 text-2xs text-[var(--pa-accent-300)]">
                      {/* How many, never by whom. */}
                      {t("moderation.reported", { count: comment.reports })}
                    </span>
                  ) : null}
                  <span className="text-2xs text-ink-faint">
                    {format.relativeDate(new Date(comment.created_at))}
                  </span>
                </span>
              </div>

              <p className="whitespace-pre-wrap text-sm text-ink">{comment.body}</p>

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={comment.state === "answered" || resolve.isPending}
                  onClick={() => resolve.mutate(comment.id)}
                  className="rounded-md border border-border-control px-2 py-1 text-xs text-ink-muted disabled:opacity-40 enabled:hover:bg-surface-3 enabled:hover:text-ink"
                >
                  {comment.state === "answered"
                    ? t("moderation.answered")
                    : t("moderation.markAnswered")}
                </button>
                <button
                  type="button"
                  disabled={comment.state === "hidden" || hide.isPending}
                  onClick={() => hide.mutate(comment.id)}
                  className="rounded-md border border-border-control px-2 py-1 text-xs text-ink-muted disabled:opacity-40 enabled:hover:bg-surface-3 enabled:hover:text-ink"
                >
                  {comment.state === "hidden" ? t("moderation.hidden") : t("moderation.hide")}
                </button>
                <button
                  type="button"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(comment.id)}
                  className="rounded-md border border-border-control px-2 py-1 text-xs text-ink-muted hover:bg-surface-3 hover:text-ink"
                >
                  {t("moderation.delete")}
                </button>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <aside className="flex flex-col gap-6">
        <section aria-labelledby="average-heading" className="flex flex-col gap-2">
          <h2 id="average-heading" className="text-sm text-ink">
            {t("moderation.libraryAverage")}
          </h2>
          {ratings.data === undefined ? null : ratings.data.average === null ? (
            <p className="text-sm text-ink-muted">{t("moderation.noRatings")}</p>
          ) : (
            <>
              <div className="flex items-center gap-3">
                <Stars
                  value={ratings.data.average}
                  label={t("library.rating.value", {
                    value: ratings.data.average,
                    count: ratings.data.count,
                  })}
                />
                <span className="text-xl tabular-nums text-ink">
                  {ratings.data.average.toFixed(1)}
                </span>
                <span className="ml-auto text-2xs text-ink-muted">
                  {t("rating.count", { count: ratings.data.count })}
                </span>
              </div>
              <ul className="flex flex-col gap-1">
                {[5, 4, 3, 2, 1].map((star) => {
                  const value = ratings.data.breakdown[String(star)] ?? 0;
                  const share = ratings.data.count === 0 ? 0 : (value / ratings.data.count) * 100;
                  return (
                    <li key={star} className="flex items-center gap-2 text-2xs text-ink-muted">
                      <span className="w-6 tabular-nums">{star}★</span>
                      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
                        <span
                          className="block h-full bg-[var(--primary)]"
                          style={{ width: `${share}%` }}
                        />
                      </span>
                      <span className="w-8 text-right tabular-nums">{value}</span>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </section>

        {ratings.data !== undefined && ratings.data.top.length > 0 ? (
          <section aria-labelledby="top-heading" className="flex flex-col gap-2">
            <h2 id="top-heading" className="text-sm text-ink">
              {t("moderation.topRated")}
            </h2>
            <ul className="flex flex-col gap-1">
              {ratings.data.top.map((item) => (
                <li key={item.media_id} className="flex items-center gap-2 text-sm">
                  <Link to={`/library/${item.media_id}`} className="flex-1 truncate text-ink">
                    {item.title}
                  </Link>
                  <Stars
                    value={item.average}
                    label={t("library.rating.value", { value: item.average, count: item.count })}
                  />
                  <span className="tabular-nums text-2xs text-ink-muted">
                    {item.average.toFixed(1)}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <section aria-labelledby="rules-heading" className="flex flex-col gap-3">
          <h2 id="rules-heading" className="text-sm text-ink">
            {t("moderation.rules.title")}
          </h2>
          {settings.data === undefined ? null : (
            <ul className="flex flex-col gap-3">
              {RULES.map((rule) => {
                const on = Boolean(settings.data[rule.key]);
                // The label is beside the control, not inside it, so the
                // association has to be explicit — a switch that announces
                // itself as "switch, on" tells a screen reader nothing about
                // what it governs.
                const labelId = `rule-${rule.key}`;
                return (
                  <li key={rule.key} className="flex items-start gap-3">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={on}
                      aria-labelledby={labelId}
                      disabled={toggleRule.isPending}
                      onClick={() => toggleRule.mutate({ key: rule.key, value: !on })}
                      className={
                        on
                          ? "mt-0.5 h-5 w-9 flex-none rounded-full bg-[var(--primary)] p-0.5 text-left"
                          : "mt-0.5 h-5 w-9 flex-none rounded-full bg-surface-3 p-0.5 text-left"
                      }
                    >
                      <span
                        aria-hidden="true"
                        className={
                          on
                            ? "block size-4 translate-x-4 rounded-full bg-[var(--pa-bg-00)] transition-transform"
                            : "block size-4 rounded-full bg-[var(--pa-text-faint)] transition-transform"
                        }
                      />
                    </button>
                    <span className="flex flex-col gap-0.5">
                      <span id={labelId} className="text-sm text-ink">
                        {t(rule.labelKey)}
                      </span>
                      <span className="text-2xs text-ink-muted">
                        {t(`${rule.labelKey}Hint` as "moderation.rules.privateLibrariesHint")}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </aside>
    </div>
  );
}
