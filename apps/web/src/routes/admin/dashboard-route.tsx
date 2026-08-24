/**
 * A1 — the administrator's first screen.
 *
 * Four cards, a recently-added row, the library's health and what has been
 * happening. Everything here is a number the server computed; nothing is
 * derived from a page of results, because a dashboard that reads its figures
 * off whatever happened to load is a dashboard that changes when you scroll.
 *
 * SHARES ARE DRAWN, NOT SENT. The server returns counts and the total they are
 * out of; the percentage is worked out here. That keeps the rounding in one
 * place and means a reader who wants the raw numbers can be shown them.
 *
 * THE ACTIVITY FEED IS THE AUDIT LOG. There is no second stream of "things that
 * happened" — inventing one would give the household two accounts of its own
 * history, and only one of them would be the one an administrator is
 * accountable for.
 */
import type { paths } from "@pornarr/api-client";
import { Button, MediaTile } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Numeric, useFormat } from "../../i18n/format";
import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { tileBlur, useArtVisible } from "../../lib/art-visibility";
import { formatDuration, seedFrom } from "../../lib/format";
import { usePageTitle } from "../../shell/page-title";

/** How many of the newest titles the row shows. */
const RECENT_LIMIT = 5;

export function DashboardRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();

  const overview = useQuery({
    queryKey: ["admin", "overview"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/overview");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const recent = useQuery({
    queryKey: ["library", "recent"],
    queryFn: async () => {
      // The library endpoint already orders by most recently touched, so the
      // first page is the answer; a separate "recent" endpoint would be a
      // second ordering to keep in step with this one.
      const { data, error, response } = await getApiClient().GET("/api/library", {
        params: { query: { limit: RECENT_LIMIT } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const audit = useQuery({
    queryKey: ["admin", "audit"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/audit");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  // transcode.md L38-40: what the machine actually supports "is shown in the
  // administration area, because 'why is it transcoding on CPU' is otherwise
  // unanswerable". The endpoint already reports it; this is the one screen
  // that reads it.
  const capabilities = useQuery({
    queryKey: ["admin", "transcode", "capabilities"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET(
        "/api/admin/transcode/capabilities",
      );
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const cache = useQueryClient();

  const transcodeLimits = useQuery({
    queryKey: ["admin", "transcode", "limits"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/transcode/limits");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  /**
   * Who is transcoding right now, what failed recently, and how fast this
   * machine actually is.
   *
   * Four routes with no reader at all. "Why is it transcoding on CPU" is
   * answered by the panel above; "who is holding the slots", "why did that
   * stream stop" and "is this machine keeping up" were not answerable from
   * anywhere - an operator had to read the worker's log.
   *
   * Sessions refetch on a cadence because they are live; failures and
   * performance are history and are read once per visit.
   */
  const sessions = useQuery({
    queryKey: ["admin", "transcode", "sessions"],
    refetchInterval: 10_000,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/transcode/sessions");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const failures = useQuery({
    queryKey: ["admin", "transcode", "failures"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/transcode/failures");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const performance = useQuery({
    queryKey: ["admin", "performance"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/performance");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const endSession = useMutation({
    mutationFn: async (sessionId: string) => {
      const { error, response } = await getApiClient().DELETE(
        "/api/admin/transcode/sessions/{session_id}",
        { params: { path: { session_id: sessionId } } },
      );
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["admin", "transcode", "sessions"] });
      void cache.invalidateQueries({ queryKey: ["admin", "transcode", "limits"] });
    },
  });

  usePageTitle(
    t("dashboard.title"),
    overview.data === undefined
      ? undefined
      : overview.data.last_scan_at === null
        ? t("dashboard.neverScanned")
        : t("dashboard.lastScan", {
            when: format.relativeDate(new Date(overview.data.last_scan_at)),
          }),
  );

  if (overview.isError)
    return (
      <p role="alert" className="text-sm text-ink">
        {t("errors.generic")}
      </p>
    );
  if (overview.data === undefined)
    return <p className="text-sm text-ink-muted">{t("dashboard.loading")}</p>;

  const data = overview.data;
  const storage = data.storage;

  return (
    <div className="flex flex-col gap-6">
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card label={t("dashboard.titles")} value={format.number(data.titles)}>
          {data.titles_added_this_week > 0
            ? t("dashboard.addedThisWeek", { count: data.titles_added_this_week })
            : t("dashboard.nothingNew")}
        </Card>

        <Card
          label={t("dashboard.storage")}
          value={storage.used_bytes === null ? "—" : format.bytes(storage.used_bytes)}
        >
          {/* Three different answers, because they mean three different
              things: no library folder set up at all, one that exists but has
              never been measured, and a real figure. "0 B of 0 B" would read
              as an empty disk in the first two cases. */}
          {storage.volumes === 0
            ? t("dashboard.storageNone")
            : storage.used_bytes === null || storage.total_bytes === null
              ? t("dashboard.storageUnmeasured", { count: storage.volumes })
              : t("dashboard.storageOf", {
                  percent: Math.round((storage.used_bytes / storage.total_bytes) * 100),
                  total: format.bytes(storage.total_bytes),
                })}
        </Card>

        <Card label={t("dashboard.untagged")} value={format.number(data.untagged)}>
          {t("dashboard.ofTitles", { count: data.titles })}
        </Card>

        <Card label={t("dashboard.guests")} value={format.number(data.guests)}>
          {t("dashboard.guestsHint")}
        </Card>
      </ul>

      {recent.data === undefined || recent.data.items.length === 0 ? null : (
        <section aria-labelledby="recent-heading" className="flex flex-col gap-3">
          <div className="flex items-baseline justify-between gap-4">
            <h2 id="recent-heading" className="text-sm text-ink">
              {t("dashboard.recentlyAdded")}
            </h2>
            <Link to="/library" className="text-2xs text-ink-muted underline hover:text-ink">
              {t("dashboard.openLibrary")}
            </Link>
          </div>
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-3">
            {recent.data.items.map((item) => (
              <li key={item.id}>
                <RecentTile item={item} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <section aria-labelledby="health-heading" className="flex flex-col gap-3">
          <h2 id="health-heading" className="text-sm text-ink">
            {t("dashboard.health")}
          </h2>
          {data.health.total === 0 ? (
            <p className="text-sm text-ink-muted">{t("dashboard.healthEmpty")}</p>
          ) : (
            <ul className="flex flex-col gap-3">
              <Bar
                label={t("dashboard.metadataMatched")}
                value={data.health.metadata_matched}
                total={data.health.total}
              />
              <Bar
                label={t("dashboard.artworkPresent")}
                value={data.health.artwork_present}
                total={data.health.total}
              />
              <Bar
                label={t("dashboard.tagged")}
                value={data.health.tagged}
                total={data.health.total}
              />
              <li className="flex items-baseline justify-between gap-4 text-sm">
                <span className="text-ink-muted">{t("dashboard.duplicates")}</span>
                {/* A count, not a share: a duplicate is a thing to deal with,
                    not a proportion to feel good about. */}
                <Link to="/admin/quarantine" className="text-ink underline">
                  {t("dashboard.duplicateFiles", { count: data.health.duplicates_flagged })}
                </Link>
              </li>
            </ul>
          )}
        </section>

        <section aria-labelledby="activity-heading" className="flex flex-col gap-3">
          <div className="flex items-baseline justify-between gap-4">
            <h2 id="activity-heading" className="text-sm text-ink">
              {t("dashboard.activity")}
            </h2>
            <Link to="/downloads" className="text-2xs text-ink-muted underline hover:text-ink">
              {t("dashboard.openQueue")}
            </Link>
          </div>
          {audit.data === undefined || audit.data.length === 0 ? (
            <p className="text-sm text-ink-muted">{t("dashboard.activityEmpty")}</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {audit.data.slice(0, 12).map((entry) => (
                <li key={entry.id} className="flex items-baseline justify-between gap-3 text-xs">
                  {/* The action verbatim. Mapping every audit action to a
                      sentence would mean this screen silently omits any action
                      the mapping has not caught up with. */}
                  <span className="truncate text-ink">{entry.action}</span>
                  <span className="flex-none tabular-nums text-ink-faint">
                    {format.relativeDate(new Date(entry.created_at))}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* Absent while the request is still in flight, the same way "recently
          added" is: a placeholder heading with nothing under it answers
          nothing, and the panel below is supplementary to the four cards
          above rather than something the page depends on. */}
      {capabilities.data === undefined ? null : (
        <section aria-labelledby="transcoding-heading" className="flex flex-col gap-3">
          <h2 id="transcoding-heading" className="text-sm text-ink">
            {t("dashboard.transcoding")}
          </h2>
          {capabilities.data.methods.length === 0 && capabilities.data.rejections.length === 0 ? (
            <p className="text-sm text-ink-muted">{t("dashboard.transcodingEmpty")}</p>
          ) : (
            <>
              {capabilities.data.methods.length === 0 ? (
                <p className="text-sm text-ink-muted">{t("dashboard.transcodingSoftwareOnly")}</p>
              ) : null}
              <ul className="flex flex-col gap-1.5 text-sm">
                {capabilities.data.methods.map((method) => (
                  <li key={method.acceleration} className="text-ink">
                    {t("dashboard.transcodingAvailable", {
                      acceleration: method.acceleration.toUpperCase(),
                      codecs: method.codecs.map((codec) => codec.codec.toUpperCase()).join(", "),
                    })}
                  </li>
                ))}
                {/* The reason is rendered exactly as the endpoint gave it,
                    never reworded here: a client-side rewrite is a second
                    place that reason could go stale against what FFmpeg
                    actually said. */}
                {capabilities.data.rejections.map((rejection, index) => (
                  <li key={rejection.acceleration ?? `general-${index}`} className="text-ink-muted">
                    {rejection.acceleration === null
                      ? t("dashboard.transcodingRejectedGeneral", { reason: rejection.reason })
                      : t("dashboard.transcodingRejected", {
                          acceleration: rejection.acceleration.toUpperCase(),
                          reason: rejection.reason,
                        })}
                  </li>
                ))}
              </ul>
            </>
          )}
          {transcodeLimits.data === undefined ? null : (
            <p className="text-2xs text-ink-faint">
              {t("dashboard.transcodingSlots", {
                hardwareInUse: transcodeLimits.data.hardware_in_use,
                hardwareLimit: transcodeLimits.data.effective_hardware,
                softwareInUse: transcodeLimits.data.software_in_use,
                softwareLimit: transcodeLimits.data.effective_software,
              })}
            </p>
          )}
        </section>
      )}

      {/* Absent rather than empty, like the panel above: an operator reading a
          quiet instance should see a quiet page, not three headings over
          nothing. */}
      {(sessions.data ?? []).length === 0 ? null : (
        <section aria-labelledby="sessions-heading" className="flex flex-col gap-3">
          <h2 id="sessions-heading" className="text-sm text-ink">
            {t("dashboard.sessions")}
          </h2>
          <ul className="flex flex-col gap-2">
            {(sessions.data ?? []).map((session) => (
              <li
                key={session.id}
                className="flex flex-wrap items-center justify-between gap-3 border border-border bg-surface p-3"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-ink">{session.media_title}</p>
                  <p className="text-2xs text-ink-faint">
                    {t("dashboard.sessionDetail", {
                      username: session.username,
                      mode: session.mode,
                      seconds: Math.round(session.elapsed_seconds),
                    })}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  loading={endSession.isPending && endSession.variables === session.id}
                  aria-label={t("dashboard.endSessionOf", { title: session.media_title })}
                  onClick={() => endSession.mutate(session.id)}
                >
                  {t("dashboard.endSession")}
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {(failures.data ?? []).length === 0 ? null : (
        <section aria-labelledby="failures-heading" className="flex flex-col gap-3">
          <h2 id="failures-heading" className="text-sm text-ink">
            {t("dashboard.transcodeFailures")}
          </h2>
          <ul className="flex flex-col gap-1.5 text-sm">
            {(failures.data ?? []).slice(0, 10).map((failure) => (
              <li key={failure.session_id} className="text-ink-muted">
                {/* The reason as the server gave it. FFmpeg's own output never
                    reaches here - `transcode.md` is explicit that a viewer and
                    an operator get a reason, not a command line. */}
                {t("dashboard.transcodeFailure", {
                  reason: failure.reason,
                  code: failure.exit_code ?? "—",
                })}
              </li>
            ))}
          </ul>
        </section>
      )}

      {(performance.data ?? []).length === 0 ? null : (
        <section aria-labelledby="performance-heading" className="flex flex-col gap-3">
          <h2 id="performance-heading" className="text-sm text-ink">
            {t("dashboard.performance")}
          </h2>
          <dl className="flex flex-col gap-1 text-sm">
            {(performance.data ?? []).map((input) => (
              <div key={`${input.scope}-${input.metric}`} className="flex flex-wrap gap-2">
                <dt className="text-ink-muted">
                  {t(`dashboard.metrics.${input.metric}` as "dashboard.metrics.download_speed", {
                    defaultValue: input.metric,
                  })}
                  {input.scope === "" ? "" : ` (${input.scope})`}
                </dt>
                <dd className="tabular-nums text-ink">
                  {/* Null is what a metric with no measurement yet answers,
                      and an em dash says that rather than "0.0", which would
                      read as a machine that measured zero. */}
                  {input.value === null ? "—" : input.value.toFixed(1)}
                  {" · "}
                  {t("dashboard.samples", { count: input.sample_count })}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </div>
  );
}

function Card({
  label,
  value,
  children,
}: {
  readonly label: string;
  readonly value: string;
  readonly children: React.ReactNode;
}): JSX.Element {
  return (
    <li className="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 p-4">
      <span className="text-2xs uppercase tracking-[0.08em] text-ink-muted">{label}</span>
      <span className="text-2xl tabular-nums text-ink">{value}</span>
      <span className="text-2xs text-[var(--pa-accent-300)]">{children}</span>
    </li>
  );
}

function Bar({
  label,
  value,
  total,
}: {
  readonly label: string;
  readonly value: number;
  readonly total: number;
}): JSX.Element {
  const percent = total === 0 ? 0 : Math.round((value / total) * 100);
  return (
    <li className="flex flex-col gap-1">
      <span className="flex items-baseline justify-between gap-4 text-sm">
        <span className="text-ink-muted">{label}</span>
        <span className="tabular-nums text-ink">
          <Numeric>{percent}</Numeric>%
        </span>
      </span>
      {/* Decorative: the row above already reads "Metadata matched 94%".
          A `progressbar` role here would say it a second time. */}
      <span
        aria-hidden="true"
        className="h-1.5 overflow-hidden rounded-full bg-surface-3"
        data-percent={percent}
      >
        <span className="block h-full bg-[var(--primary)]" style={{ width: `${percent}%` }} />
      </span>
    </li>
  );
}

/**
 * Derived from the contract rather than transcribed from it. The hand-written
 * version was a copy of the server's response that nothing checked, so a field
 * added or renamed on the server changed nothing here until something rendered
 * `undefined`.
 */
type RecentItem =
  paths["/api/library"]["get"]["responses"]["200"]["content"]["application/json"]["items"][number];

/**
 * One newly added title. The same tile the library uses, and the same artwork
 * control — a dashboard that ignores "art hidden" would reveal on the one
 * screen most likely to be open when somebody walks past.
 */
function RecentTile({ item }: { readonly item: RecentItem }): JSX.Element {
  const { t } = useTranslation();
  const artVisible = useArtVisible();

  return (
    <MediaTile
      title={item.title}
      meta={item.studio ?? undefined}
      resolution={item.quality ?? item.resolution ?? undefined}
      duration={item.duration_seconds === null ? undefined : formatDuration(item.duration_seconds)}
      rating={item.rating}
      ratingLabel={
        item.rating === null
          ? t("library.rating.none")
          : t("library.rating.value", { value: item.rating, count: item.rating_count })
      }
      tagCount={item.tag_count}
      commentCount={item.comment_count}
      seed={seedFrom(item.id)}
      blur={tileBlur(artVisible)}
      action={(content) => (
        // `article`, the tile's root, does not contribute to an accessible
        // name from content, so the link needs one of its own or a screen
        // reader hears nothing but "link".
        <Link to={`/library/${item.id}`} className="block rounded-md" aria-label={item.title}>
          {content}
        </Link>
      )}
    />
  );
}
