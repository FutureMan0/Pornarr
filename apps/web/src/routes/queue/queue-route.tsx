/**
 * A3 — the download queue.
 *
 * THE CARDS COUNT THE QUEUE, NOT THE PAGE. `/api/queue/summary` answers over
 * every job; deriving the four figures from the loaded rows would make them
 * change as someone scrolls, which is the one thing a summary must not do.
 *
 * THE FILTER ASKS THE SERVER. "Failed" has to reach the failures that are not
 * on the first page, so each tab is a query rather than a sieve over what
 * happened to arrive.
 *
 * PROGRESS IS ONLY DRAWN WHEN IT IS KNOWN. A client that has not reported a
 * size gives no percentage, and a bar sitting at zero would read as "stuck"
 * rather than "not yet measured" — the row says so in words instead.
 *
 * The stages are the download client's own vocabulary. The design also shows
 * "Transcoding" and "Thumbnails"; those are worker jobs rather than
 * download-client states, and this queue does not know about them.
 */
import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "../../i18n/format";
import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";

const REFRESH_MILLISECONDS = 5_000;

type Tab = "active" | "completed" | "failed";
const TABS: readonly Tab[] = ["active", "completed", "failed"];

/** The status a tab asks for; "active" means everything still in flight. */
const TAB_STATUS: Record<Tab, string | undefined> = {
  active: undefined,
  completed: "completed",
  failed: "failed",
};

/** Terminal and no longer anyone's problem. */
const DONE = new Set(["completed", "removed"]);

/** Terminal, including the one that still needs attention. */
const FINISHED = new Set([...DONE, "failed"]);

export function QueueRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const [tab, setTab] = useState<Tab>("active");

  const summary = useQuery({
    queryKey: ["queue", "summary"],
    refetchInterval: REFRESH_MILLISECONDS,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/queue/summary");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const queue = useQuery({
    queryKey: ["queue", tab],
    refetchInterval: REFRESH_MILLISECONDS,
    queryFn: async () => {
      const status = TAB_STATUS[tab];
      const { data, error, response } = await getApiClient().GET("/api/queue", {
        params: { query: status === undefined ? {} : { status } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  usePageTitle(
    t("queue.title"),
    summary.data === undefined
      ? undefined
      : t("queue.subtitle", {
          active: summary.data.active,
          speed: format.speed(summary.data.speed_bytes),
        }),
  );

  // The "active" tab is everything still in flight, which the API expresses as
  // the absence of a status filter rather than a status of its own. Failures
  // stay in it: a failed download is not finished business, and one you have to
  // switch tabs to discover is one nobody discovers.
  const rows = (queue.data?.items ?? []).filter((job) => tab !== "active" || !DONE.has(job.status));

  return (
    <div className="flex flex-col gap-6">
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label={t("queue.active")} value={summary.data?.active} />
        <Stat label={t("queue.queued")} value={summary.data?.queued} />
        <Stat
          label={t("queue.speed")}
          text={summary.data === undefined ? undefined : format.speed(summary.data.speed_bytes)}
        />
        <Stat label={t("queue.failed")} value={summary.data?.failed} />
      </ul>

      <fieldset className="flex gap-1 border-0 p-0">
        <legend className="sr-only">{t("queue.filter")}</legend>
        {TABS.map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={tab === option}
            onClick={() => setTab(option)}
            className={
              tab === option
                ? "rounded-md bg-[color-mix(in_oklch,var(--primary)_14%,transparent)] px-3 py-1.5 text-sm text-[var(--pa-accent-300)]"
                : "rounded-md px-3 py-1.5 text-sm text-ink-muted hover:bg-surface-3 hover:text-ink"
            }
          >
            {t(`queue.tab.${option}` as "queue.tab.active")}
          </button>
        ))}
      </fieldset>

      {queue.isError ? (
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      ) : queue.isPending ? (
        <p className="text-sm text-ink-muted">{t("queue.loading")}</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-ink-muted">{t(`queue.empty.${tab}` as "queue.empty.active")}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[46rem] text-sm">
            <thead className="text-left text-2xs uppercase tracking-[0.08em] text-ink-muted">
              <tr>
                <th className="py-2 font-normal">{t("queue.column.item")}</th>
                <th className="py-2 font-normal">{t("queue.column.progress")}</th>
                <th className="py-2 font-normal">{t("queue.column.stage")}</th>
                <th className="py-2 font-normal">{t("queue.column.speed")}</th>
                <th className="py-2 font-normal">{t("queue.column.eta")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((job) => (
                <tr key={job.id} className="border-t border-border align-top">
                  <td className="py-3 pr-4">
                    <span className="block text-ink">
                      {/* The GUID is what there is when no request explains the
                          job. Better an identifier than a made-up name. */}
                      {job.title ?? job.release_guid}
                    </span>
                    <span className="block text-2xs text-ink-muted">
                      {job.client_name} · {job.protocol}
                    </span>
                    {job.error === null ? null : (
                      <span role="alert" className="block text-2xs text-[var(--pa-accent-300)]">
                        {job.error}
                      </span>
                    )}
                  </td>
                  <td className="py-3 pr-4">
                    <Progress
                      remaining={job.remaining_bytes}
                      size={job.size_bytes}
                      unknownLabel={t("queue.progressUnknown")}
                    />
                  </td>
                  <td className="py-3 pr-4">
                    <Stage status={job.status} />
                  </td>
                  <td className="py-3 pr-4 tabular-nums text-ink-muted">
                    {job.download_speed_bytes === null
                      ? "—"
                      : format.speed(job.download_speed_bytes)}
                  </td>
                  <td className="py-3 tabular-nums text-ink-muted">
                    {format.estimate(
                      job.queue_estimate.low_seconds,
                      job.queue_estimate.high_seconds,
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  text,
}: {
  readonly label: string;
  readonly value?: number | undefined;
  readonly text?: string | undefined;
}): JSX.Element {
  return (
    <li className="flex flex-col gap-1 rounded-lg border border-border bg-surface-2 p-4">
      <span className="text-2xs uppercase tracking-[0.08em] text-ink-muted">{label}</span>
      <span className="text-2xl tabular-nums text-ink">
        {text ?? (value === undefined ? "—" : value)}
      </span>
    </li>
  );
}

/**
 * How far along, when that is knowable.
 *
 * A bar at zero for a job whose size nobody has reported reads as a stalled
 * download. The words say which it is.
 */
function Progress({
  remaining,
  size,
  unknownLabel,
}: {
  readonly remaining: number | null;
  readonly size: number | null;
  readonly unknownLabel: string;
}): JSX.Element {
  if (size === null || size <= 0 || remaining === null) {
    return <span className="text-2xs text-ink-faint">{unknownLabel}</span>;
  }
  const percent = Math.max(0, Math.min(100, Math.round(((size - remaining) / size) * 100)));
  return (
    <span className="flex items-center gap-2">
      <span
        className="h-1.5 w-32 overflow-hidden rounded-full bg-surface-3"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span className="block h-full bg-[var(--primary)]" style={{ width: `${percent}%` }} />
      </span>
      <span className="tabular-nums text-2xs text-ink-muted">{percent}%</span>
    </span>
  );
}

/**
 * The client's own word for what it is doing.
 *
 * Printed as it comes rather than mapped through a table: a client that grows a
 * state this application has not heard of should show that state, not vanish
 * into an "unknown" bucket.
 */
function Stage({ status }: { readonly status: string }): JSX.Element {
  const finished = FINISHED.has(status);
  return (
    <span
      className={
        finished
          ? "rounded-md border border-border-control px-2 py-0.5 text-2xs text-ink-muted"
          : "rounded-md bg-[color-mix(in_oklch,var(--primary)_16%,transparent)] px-2 py-0.5 text-2xs text-[var(--pa-accent-300)]"
      }
    >
      {status}
    </span>
  );
}
