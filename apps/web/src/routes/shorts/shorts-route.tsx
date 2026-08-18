/**
 * The shorts feed: one clip at a time, filling the stage.
 *
 * `GET /api/shorts` has been served since the first release with nothing in the
 * client pointed at it, so every clip the server cut was unreachable. This is
 * that screen.
 *
 * A short is an offset pair into a title rather than a file of its own, so the
 * feed is `VideoPlayer` told where to begin and where to stop — never a second
 * player, and never a second transcode session per clip. Only the clip on
 * screen is mounted, and the player is keyed by the title it comes from, so two
 * clips cut from the same file are a seek rather than a new session.
 *
 * Moving is a swipe, an arrow key or a button, and all three do the same thing.
 * The buttons are not decoration: a feed whose only affordance is a gesture is
 * a feed a keyboard cannot reach, and the key hint below the stage is how a
 * reader learns the gesture exists at all.
 */
import type { paths } from "@pornarr/api-client";
import { Button, EmptyState } from "@pornarr/ui";
import { useQuery } from "@tanstack/react-query";
import type { JSX, TouchEvent } from "react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { VideoPlayer } from "../../components/player/video-player";
import { ErrorScreen } from "../../errors/error-screen";
import { useFormat } from "../../i18n/format";
import { getApiClient } from "../../lib/api";
import {
  type ApiRequestError,
  apiFailure,
  isRetryableError,
  messageForError,
  nextStepForError,
} from "../../lib/api-error";
import { NAV_ITEMS } from "../../shell/sidebar";

export type Short =
  paths["/api/shorts"]["get"]["responses"][200]["content"]["application/json"][number];

export const SHORTS_KEY = ["shorts"] as const;

/** Read from the nav table so the link cannot outlive the route it points at. */
const LIBRARY_PATH = NAV_ITEMS[0].path;

/** How far a finger has to travel before it is a swipe rather than a tap. */
const SWIPE_THRESHOLD_PX = 48;

export function useShorts() {
  return useQuery<Short[], ApiRequestError>({
    queryKey: SHORTS_KEY,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/shorts");
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
}

export function ShortsRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const shorts = useShorts();
  const [index, setIndex] = useState(0);
  const count = shorts.data?.length ?? 0;

  // Wrapping rather than stopping: a feed that refuses the next swipe reads as
  // broken, and there is no further page to fetch behind the last clip.
  const move = useCallback(
    (delta: number): void => {
      if (count === 0) return;
      setIndex((current) => (current + delta + count) % count);
    },
    [count],
  );

  // Bound to the window rather than to the stage: the clip is the whole screen,
  // so arrowing through it must not depend on which part of it holds focus. A
  // field is the one place the same key means something else.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && target.closest("input, select, textarea")) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        move(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        move(-1);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [move]);

  const [touchStart, setTouchStart] = useState<number | null>(null);
  const onTouchStart = (event: TouchEvent<HTMLDivElement>): void => {
    setTouchStart(event.touches[0]?.clientY ?? null);
  };
  const onTouchEnd = (event: TouchEvent<HTMLDivElement>): void => {
    const end = event.changedTouches[0]?.clientY;
    if (touchStart === null || end === undefined) return;
    setTouchStart(null);
    const travelled = touchStart - end;
    if (Math.abs(travelled) < SWIPE_THRESHOLD_PX) return;
    // Up means forward, the way every vertical feed reads: the next clip comes
    // from below.
    move(travelled > 0 ? 1 : -1);
  };

  const heading = (
    <h1 id="shorts-heading" className="text-xl text-ink">
      {t("shorts.title")}
    </h1>
  );

  if (shorts.isPending)
    return (
      <section aria-labelledby="shorts-heading" className="flex flex-col gap-4">
        {heading}
        <p className="text-sm text-ink-muted">{t("shorts.loading")}</p>
      </section>
    );

  if (shorts.isError)
    return (
      <section aria-labelledby="shorts-heading" className="flex flex-col gap-4">
        {heading}
        <ErrorScreen
          title={messageForError(shorts.error)}
          nextStep={nextStepForError(shorts.error)}
          onRetry={isRetryableError(shorts.error) ? () => void shorts.refetch() : undefined}
        />
      </section>
    );

  const current = shorts.data[Math.min(index, count - 1)];
  if (current === undefined)
    return (
      <section aria-labelledby="shorts-heading" className="flex flex-col gap-4">
        {heading}
        <EmptyState
          title={t("shorts.emptyTitle")}
          body={t("shorts.empty")}
          action={{ label: t("shorts.emptyAction"), href: LIBRARY_PATH }}
        />
      </section>
    );

  return (
    <section aria-labelledby="shorts-heading" className="flex flex-col gap-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        {heading}
        <p className="text-sm text-ink-muted tabular">
          {t("shorts.position", { position: index + 1, count })}
        </p>
      </header>
      <p className="max-w-[70ch] text-sm text-ink-muted">{t("shorts.intro")}</p>

      {/* A tap-target region, not a control: the swipe is a shortcut for the
          buttons below, which are what carries the accessible name. */}
      <div
        className="h-[calc(100vh-22rem)] min-h-72 border border-border bg-black"
        onTouchStart={onTouchStart}
        onTouchEnd={onTouchEnd}
      >
        {/* A clip is vertical, so the column is capped rather than stretched
            across a desktop width the footage would be letterboxed into. */}
        <div className="mx-auto h-full w-full max-w-[28rem]">
          <VideoPlayer
            key={current.media_id}
            mediaId={current.media_id}
            title={current.title}
            startSeconds={current.start_seconds}
            endSeconds={current.end_seconds}
            autoPlay
            fill
          />
        </div>
      </div>

      <div
        aria-live="polite"
        className="flex flex-wrap items-start justify-between gap-4 border border-border bg-surface p-4"
      >
        <div className="min-w-0">
          <h2 className="text-lg text-ink">{current.title}</h2>
          <p className="text-sm text-ink-muted">
            {current.media_title} · {format.duration(current.duration_seconds)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" onClick={() => move(-1)}>
            {t("shorts.previous")}
          </Button>
          <Button variant="ghost" onClick={() => move(1)}>
            {t("shorts.next")}
          </Button>
          <Link className="text-sm text-primary underline" to={`/library/${current.media_id}`}>
            {t("shorts.openTitle")}
          </Link>
        </div>
      </div>

      <p className="text-xs text-ink-muted">{t("shorts.hint")}</p>
    </section>
  );
}
