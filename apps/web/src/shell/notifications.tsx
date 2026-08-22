import type { paths } from "@pornarr/api-client";
/**
 * What happened while nobody was looking.
 *
 * Five routes and not one caller: the server has been recording that a request
 * became available, that a download failed, that something is waiting in
 * quarantine - and nothing could read any of it. A household ran on the
 * operator noticing.
 *
 * In the top bar rather than on a screen of its own, because a notification is
 * something you are told, not somewhere you go. The count is the whole point:
 * it has to be legible without opening anything, and it is a number rather
 * than a dot so that "one thing" and "eleven things" are different states.
 *
 * The payload is the server's and its shape varies by kind, so the line is
 * built from the kind - a translated sentence - plus whatever title the
 * payload happens to carry. Rendering the payload raw would put an internal
 * shape on a household's screen.
 */
import { Button } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "../i18n/format";
import { getApiClient } from "../lib/api";
import { apiFailure } from "../lib/api-error";

type Notification =
  paths["/api/notifications"]["get"]["responses"][200]["content"]["application/json"][number];

export const NOTIFICATIONS_KEY = ["notifications"] as const;

/** The one field every kind's payload is likely to carry, and nothing else. */
function subjectOf(payload: unknown): string | null {
  if (typeof payload !== "object" || payload === null) return null;
  const record = payload as Record<string, unknown>;
  for (const key of ["title", "media_title", "query"]) {
    const value = record[key];
    if (typeof value === "string" && value !== "") return value;
  }
  return null;
}

export function Notifications(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);

  const notifications = useQuery({
    queryKey: NOTIFICATIONS_KEY,
    // Every thirty seconds while the tab is open. The event stream carries
    // live work; this is the record of what was missed, and a minute-old count
    // is not a wrong count.
    refetchInterval: 30_000,
    queryFn: async (): Promise<Notification[]> => {
      const { data, error, response } = await getApiClient().GET("/api/notifications");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const markRead = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().POST(
        "/api/notifications/{notification_id}/read",
        { params: { path: { notification_id: id } } },
      );
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: NOTIFICATIONS_KEY }),
  });

  const markAllRead = useMutation({
    mutationFn: async () => {
      const { error, response } = await getApiClient().POST("/api/notifications/read-all");
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: NOTIFICATIONS_KEY }),
  });

  const items = notifications.data ?? [];
  const unread = items.filter((item) => item.read_at === null);

  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-md border border-border-control px-2 py-1 text-xs text-ink-muted transition-colors duration-[var(--duration-fast)] ease-out hover:bg-surface-3 hover:text-ink"
      >
        <BellIcon />
        {/* One element, not two, the way the artwork toggle does it: the words
            are hidden until there is room for them and the accessible name is
            the same at every width. A second span toggled by breakpoint would
            put the label in the accessibility tree twice wherever CSS has not
            loaded - and the words at every width made the bar wrap onto a
            second row at 1280, which moved every control in it. */}
        <span className="sr-only 2xl:not-sr-only">{t("notifications.title")}</span>
        {unread.length === 0 ? null : (
          <span className="rounded-full bg-[var(--primary-weak)] px-1.5 tabular-nums text-2xs text-[var(--pa-accent-300)]">
            {unread.length}
          </span>
        )}
      </button>

      {open ? (
        <div className="absolute right-0 z-20 mt-1 max-h-[24rem] w-[22rem] overflow-y-auto rounded-lg border border-border bg-surface p-3 shadow-lg">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm text-ink">{t("notifications.title")}</h2>
            {unread.length === 0 ? null : (
              <Button
                variant="ghost"
                loading={markAllRead.isPending}
                onClick={() => markAllRead.mutate()}
              >
                {t("notifications.readAll")}
              </Button>
            )}
          </div>

          {notifications.isPending ? (
            <p className="mt-2 text-sm text-ink-muted">{t("notifications.loading")}</p>
          ) : items.length === 0 ? (
            <p className="mt-2 text-sm text-ink-muted">{t("notifications.empty")}</p>
          ) : (
            <ul className="mt-2 flex flex-col gap-2">
              {items.map((item) => {
                const subject = subjectOf(item.payload);
                return (
                  <li
                    key={item.id}
                    className={
                      item.read_at === null
                        ? "rounded-md bg-surface-2 p-2"
                        : "rounded-md p-2 opacity-70"
                    }
                  >
                    <p className="text-sm text-ink">
                      {t(
                        `notifications.kinds.${item.kind}` as "notifications.kinds.instance_notice",
                        {
                          defaultValue: item.kind,
                        },
                      )}
                    </p>
                    {subject === null ? null : (
                      <p className="truncate text-2xs text-ink-muted">{subject}</p>
                    )}
                    <p className="text-2xs text-ink-faint">
                      {format.relativeDate(new Date(item.created_at))}
                    </p>
                    {item.read_at === null ? (
                      <Button variant="ghost" onClick={() => markRead.mutate(item.id)}>
                        {t("notifications.markRead")}
                      </Button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}

/**
 * A bell, because the word is hidden below `2xl`. Hidden from assistive
 * technology: the button's own label already says what it opens.
 */
function BellIcon(): JSX.Element {
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
      className="size-4 flex-none"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 6.8a4 4 0 0 1 8 0c0 3 .8 4.2 1.4 4.7H2.6C3.2 11 4 9.8 4 6.8Z" />
      <path d="M6.6 13.3a1.6 1.6 0 0 0 2.8 0" />
    </svg>
  );
}
