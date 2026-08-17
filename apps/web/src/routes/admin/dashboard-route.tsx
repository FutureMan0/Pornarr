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
import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Numeric, useFormat } from "../../i18n/format";
import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";

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

  const audit = useQuery({
    queryKey: ["admin", "audit"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/audit");
      if (!data || error) throw apiFailure(error, response);
      return data;
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
