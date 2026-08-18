/**
 * B6 + B7 — shorts, as one thing instead of two.
 *
 * WHY THERE IS NO GRID IN FRONT OF THIS. The point of a short-form feed is that
 * the decision of what to watch is made for you. A grid hands that decision back
 * to the viewer and does it on the worst possible evidence: a blurred still and
 * a title. Nobody can judge a forty-second clip from a thumbnail — which is why
 * the grid survives at `/shorts/browse`, where you already know what you are
 * looking for, and is not the way in.
 *
 * SCROLL SNAP, NOT A CAROUSEL. One pane per viewport height with
 * `scroll-snap-type: y mandatory`. That gets a swipe on a phone, a wheel on a
 * desktop and a scrollbar for anyone who wants one, all from the browser. A
 * hand-written pager would have to reimplement momentum, rubber-banding and the
 * scrollbar, and would still be worse on a trackpad.
 *
 * THE ADDRESS FOLLOWS, IT DOES NOT ACCUMULATE. Scrolling replaces the URL rather
 * than pushing it. A feed that pushes a history entry per clip turns the back
 * button into a re-run of everything you scrolled past, which is a trap rather
 * than a navigation.
 *
 * ONE CLIP PLAYS. An IntersectionObserver names the pane in view; every other
 * pane is a still. Ten videos decoding at once is how a feed becomes a fan.
 */
import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";
import { ShortPane } from "./short-pane";

/**
 * Below this there is no room for a comment column beside a 9/16 frame.
 *
 * A pane is roughly 700px tall, so its frame is roughly 394px wide; the column
 * is 22rem; the sidebar takes another 240 in its full layout. Under about 1100
 * the column would squeeze the video rather than sit beside it, so the comments
 * move into a dialog instead.
 *
 * `max-width` rather than `min-width` because that is the form the whole shell
 * uses — and the only form the test harness's matchMedia understands.
 */
const NARROW_QUERY = "(max-width: 1099.98px)";

/** Whether the window has room for the comment column. */
function useWideEnoughForComments(): boolean {
  const read = (): boolean =>
    typeof window === "undefined" || typeof window.matchMedia !== "function"
      ? true
      : !window.matchMedia(NARROW_QUERY).matches;

  const [wide, setWide] = useState(read);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const list = window.matchMedia(NARROW_QUERY);
    const update = (): void => setWide(!list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, []);

  return wide;
}

/** How many clips one request brings back. */
const PAGE = 20;

/** Fetch the next page this many panes before the end. */
const PREFETCH_WITHIN = 4;

export function ShortsFeedRoute(): JSX.Element {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { shortId } = useParams();
  const scroller = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);
  const [pages, setPages] = useState(1);
  const wide = useWideEnoughForComments();
  /**
   * The clip this screen opened on, captured once.
   *
   * Deliberately not re-read from the address. The feed *writes* the address as
   * it scrolls, so an anchor that watches `shortId` is an anchor watching its
   * own output — the first scroll updates the URL, the anchor sees a "new"
   * clip, and drags the reader back to where they started.
   */
  const openedOn = useRef(shortId);
  const anchored = useRef(false);

  const feed = useQuery({
    queryKey: ["shorts", "feed", pages],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/shorts", {
        params: { query: { sort: "trending", limit: PAGE * pages } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const clips = useMemo(() => feed.data ?? [], [feed.data]);
  const current = clips[active];

  usePageTitle(
    t("shorts.title"),
    clips.length === 0
      ? undefined
      : t("shorts.player.position", { index: active + 1, total: clips.length }),
  );

  // Land on the clip the opening address named, once the page holding it
  // arrives. A clip further down the feed is not in the first page, which is
  // why this waits on `clips` rather than running once on mount.
  useEffect(() => {
    const wanted = openedOn.current;
    if (anchored.current || wanted === undefined || clips.length === 0) return;
    const index = clips.findIndex((clip) => clip.id === wanted);
    if (index < 0) return;
    anchored.current = true;
    scroller.current?.children[index]?.scrollIntoView({ block: "start" });
    setActive(index);
  }, [clips]);

  // Which pane is on screen. `threshold: 0.6` rather than a scroll-position
  // calculation: the snap point is the browser's business, and reading it back
  // from `scrollTop` breaks the moment a pane's height is not exact.
  useEffect(() => {
    const container = scroller.current;
    if (container === null || clips.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const index = Number((entry.target as HTMLElement).dataset.index ?? "0");
          setActive(index);
        }
      },
      { root: container, threshold: 0.6 },
    );
    for (const pane of container.children) observer.observe(pane);
    return () => observer.disconnect();
  }, [clips.length]);

  // The address names the clip on screen, so a link out of here is a link to
  // this clip rather than to the top of the feed.
  useEffect(() => {
    const clip = clips[active];
    if (clip === undefined) return;
    navigate(`/shorts/${clip.id}`, { replace: true });
  }, [active, clips, navigate]);

  useEffect(() => {
    if (clips.length === 0) return;
    if (active >= clips.length - PREFETCH_WITHIN && clips.length >= PAGE * pages) {
      setPages((previous) => previous + 1);
    }
  }, [active, clips.length, pages]);

  const move = useCallback(
    (delta: number) => {
      const container = scroller.current;
      const target = container?.children[active + delta];
      if (target !== undefined) target.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    [active],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      const target = event.target as HTMLElement | null;
      // Never steal a keystroke from something being typed into: the comment
      // box lives inside a pane.
      if (target !== null && (target.tagName === "TEXTAREA" || target.tagName === "INPUT")) return;
      if (event.key === "ArrowDown" || event.key === "PageDown") {
        event.preventDefault();
        move(1);
      }
      if (event.key === "ArrowUp" || event.key === "PageUp") {
        event.preventDefault();
        move(-1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move]);

  if (feed.isError)
    return (
      <p role="alert" className="text-sm text-ink">
        {t("errors.generic")}
      </p>
    );
  if (feed.isPending) return <p className="text-sm text-ink-muted">{t("shorts.loading")}</p>;
  if (clips.length === 0)
    return (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-ink-muted">{t("shorts.empty")}</p>
        <p className="text-2xs text-ink-faint">{t("shorts.emptyHint")}</p>
      </div>
    );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-4">
        {/* Nothing to say to a thumb. The hint names Space and the arrow keys,
            which is advice for a keyboard and clutter for everyone else — two
            lines of it at 390px, above a feed that is scrolled by swiping. */}
        <p className="hidden text-2xs text-ink-muted sm:block">{t("shorts.player.keys")}</p>
        <Link
          to="/shorts/browse"
          className="rounded-full border border-border-control px-3 py-1 text-2xs text-ink-muted transition-colors hover:bg-surface-3 hover:text-ink"
        >
          {t("shorts.browseAll")}
        </Link>
      </div>

      <div
        ref={scroller}
        // `overscroll-contain` keeps a swipe past the last clip from scrolling
        // the page behind the feed.
        className="h-[calc(100dvh-var(--shell-chrome,11rem))] snap-y snap-mandatory overflow-y-auto overscroll-contain rounded-lg"
        tabIndex={-1}
      >
        {clips.map((clip, index) => (
          <div
            key={clip.id}
            data-index={index}
            className="flex h-full snap-start snap-always items-center justify-center"
          >
            <ShortPane clip={clip} active={index === active} wide={wide} />
          </div>
        ))}
      </div>

      {/* Announced rather than only drawn: a scroll-snap feed gives a screen
          reader no sense of where it is. */}
      <p aria-live="polite" className="visually-hidden">
        {current === undefined
          ? ""
          : t("shorts.player.announce", {
              index: active + 1,
              total: clips.length,
              title: current.title,
            })}
      </p>
    </div>
  );
}
