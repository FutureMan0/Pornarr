import Hls from "hls.js";
import type { JSX, RefObject } from "react";
import { useEffect, useImperativeHandle, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CSRF_HEADER, readCsrfToken } from "../../lib/api";

type PlaybackInfo = { direct_play: boolean };
type TranscodeSession = { session_id: string; playlist_url: string };

function csrfHeaders(): HeadersInit {
  const token = readCsrfToken();
  return token === null ? {} : { [CSRF_HEADER]: token };
}

async function responseJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: "same-origin", ...init });
  if (!response.ok) throw new Error(`Playback request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export interface Clip {
  readonly startSeconds: number;
  readonly endSeconds: number;
}

/** What a caller outside the player can ask it to do. */
export interface PlayerHandle {
  /** Move to a position, in seconds from the start of the file. */
  readonly seek: (seconds: number) => void;
  /** Play if paused, pause if playing. */
  readonly toggle: () => void;
}

export interface VideoPlayerProps {
  readonly mediaId: string;
  readonly title: string;
  /**
   * Play only part of the title. The stream is the whole file — a short is a
   * pair of timestamps into it, not a second copy — so the player seeks in and
   * stops at the end rather than asking the server for a cut.
   */
  readonly clip?: Clip | undefined;
  /** Portrait for shorts, which are shot that way and letterbox otherwise. */
  readonly portrait?: boolean | undefined;
  /**
   * `native` is the browser's own control bar — right for a film, where scrubbing,
   * volume, captions and full screen all matter and the browser's version is
   * better than one we would write.
   *
   * `minimal` is the short-form treatment: no control bar, muted autoplay, click
   * to pause, a hairline of progress. A forty-second clip in a feed has no use
   * for a scrub bar, and the control bar is the single most obvious thing
   * standing between this and what it is imitating.
   */
  readonly chrome?: "native" | "minimal" | undefined;
  /** Run the clip again at its end rather than stopping on the last frame. */
  readonly loop?: boolean | undefined;
  readonly onEnded?: (() => void) | undefined;
  /**
   * A handle for seeking from outside — the scene markers use it.
   *
   * A ref rather than a `position` prop: seeking is an event, and expressing an
   * event as state means inventing a nonce so that asking for the same second
   * twice still does something.
   */
  readonly handleRef?: RefObject<PlayerHandle | null> | undefined;
}

/**
 * Whether sound is wanted, remembered across clips.
 *
 * Module scope rather than component state, and deliberately not persisted to
 * storage. A feed mounts a new player per clip, so component state would mute
 * again on every scroll and make the control useless; storage would carry the
 * choice into a session where the person is somewhere else entirely, possibly
 * with somebody in the room. It lasts as long as the tab, which is how long the
 * decision is good for.
 */
let soundWanted = false;

export function VideoPlayer({
  mediaId,
  title,
  clip,
  portrait,
  chrome = "native",
  loop,
  onEnded,
  handleRef,
}: VideoPlayerProps) {
  const { t } = useTranslation();
  const video = useRef<HTMLVideoElement>(null);
  const hls = useRef<Hls | null>(null);
  const session = useRef<string | null>(null);
  const lastProgress = useRef(0);
  const [error, setError] = useState(false);
  const minimal = chrome === "minimal";
  const [paused, setPaused] = useState(false);
  const [muted, setMuted] = useState(!soundWanted);
  /** Position inside the clip, 0–1. Drives the hairline, nothing else. */
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const info = await responseJson<PlaybackInfo>(`/api/media/${mediaId}/playback-info`);
        const source = info.direct_play
          ? `/api/media/${mediaId}/stream`
          : await responseJson<TranscodeSession>(`/api/transcode/media/${mediaId}/sessions`, {
              method: "POST",
              headers: csrfHeaders(),
            });
        if (cancelled || video.current === null) return;
        if (typeof source === "string") {
          video.current.src = source;
          return;
        }
        session.current = source.session_id;
        if (video.current.canPlayType("application/vnd.apple.mpegurl")) {
          video.current.src = source.playlist_url;
        } else if (Hls.isSupported()) {
          hls.current = new Hls();
          hls.current.loadSource(source.playlist_url);
          hls.current.attachMedia(video.current);
        } else {
          setError(true);
        }
      } catch {
        if (!cancelled) setError(true);
      }
    }
    void load();
    return () => {
      cancelled = true;
      hls.current?.destroy();
      hls.current = null;
      if (session.current !== null) {
        // Raw `fetch`, deliberately, and the same for the heartbeat and the
        // progress report below. `keepalive` is the whole point: these three fire
        // as the player unmounts or the tab closes, and a request the browser is
        // free to cancel at that moment is a transcode session left running on
        // the server. The generated client does not pass the flag through, so
        // routing these through it would silently drop the guarantee.
        void fetch(`/api/transcode/sessions/${session.current}`, {
          method: "DELETE",
          headers: csrfHeaders(),
          credentials: "same-origin",
          keepalive: true,
        });
        session.current = null;
      }
    };
  }, [mediaId]);

  useImperativeHandle(
    handleRef,
    () => ({
      seek: (seconds: number) => {
        const element = video.current;
        if (element === null) return;
        element.currentTime = seconds;
        // A seek from a marker is a request to watch from there, so it plays.
        // `catch` because a browser that has not seen a gesture yet refuses,
        // and a rejected promise here is not an error worth surfacing.
        void element.play().catch(() => {});
      },
      toggle: togglePlayback,
    }),
    [],
  );

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (session.current !== null) {
        void fetch(`/api/transcode/sessions/${session.current}/heartbeat`, {
          method: "POST",
          headers: csrfHeaders(),
          credentials: "same-origin",
        });
      }
    }, 25_000);
    return () => window.clearInterval(timer);
  }, []);

  /**
   * Keep a clip inside its bounds.
   *
   * Seeking on `loadedmetadata` rather than immediately: a seek before the
   * media knows its duration is silently dropped, and the clip then plays from
   * the top of the film.
   */
  function enterClip() {
    const element = video.current;
    if (element === null || clip === undefined) return;
    if (element.currentTime < clip.startSeconds) element.currentTime = clip.startSeconds;
  }

  function stopAtClipEnd() {
    const element = video.current;
    if (element === null || clip === undefined) return;
    if (element.currentTime >= clip.endSeconds) {
      if (loop === true) {
        // Round again rather than stopping on the last frame. A short-form clip
        // that ends leaves a still image and a decision; one that loops leaves
        // the reader free to keep watching or scroll on, which is the whole
        // shape of the format.
        element.currentTime = clip.startSeconds;
        return;
      }
      element.pause();
      onEnded?.();
    }
  }

  /** How far into the clip, for the hairline under a minimal player. */
  function trackProgress() {
    const element = video.current;
    if (element === null || !minimal) return;
    const start = clip?.startSeconds ?? 0;
    const end = clip?.endSeconds ?? element.duration;
    if (!Number.isFinite(end) || end <= start) return;
    setProgress(Math.min(1, Math.max(0, (element.currentTime - start) / (end - start))));
  }

  function togglePlayback() {
    const element = video.current;
    if (element === null) return;
    if (element.paused) {
      void element.play().catch(() => {});
      return;
    }
    element.pause();
  }

  function reportProgress() {
    const element = video.current;
    // A clip is not the film. Reporting its offset would put a two-hour title
    // in "continue watching" at the fifteen-minute mark because someone
    // watched forty seconds of it in the shorts feed.
    if (clip !== undefined) return;
    if (element === null || !Number.isFinite(element.duration) || element.duration <= 0) return;
    if (element.currentTime - lastProgress.current < 10 && !element.ended) return;
    lastProgress.current = element.currentTime;
    void fetch(`/api/playback/${mediaId}/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      credentials: "same-origin",
      body: JSON.stringify({
        position_seconds: element.currentTime,
        duration_seconds: element.duration,
      }),
      keepalive: true,
    });
  }

  // A failure keeps the video's footprint. As a bare paragraph this collapsed
  // the player to one line of text and pulled the whole screen up around it,
  // which reads as a page that was never meant to have a video on it rather
  // than as a video that did not load.
  if (error)
    return (
      <div
        role="alert"
        className={
          portrait === true
            ? "grid h-full w-full place-items-center rounded-lg bg-surface-2 p-4 text-center text-sm text-ink-muted [aspect-ratio:9/16]"
            : "grid aspect-video w-full place-items-center rounded-lg bg-surface-2 p-4 text-center text-sm text-ink-muted"
        }
      >
        {t("player.unavailable")}
      </div>
    );

  // Portrait fills whatever box it is given rather than declaring its own ratio.
  // `aspect-[9/16] h-full` looks equivalent but is not: `h-full` against an
  // auto-height parent resolves to nothing, the ratio wins, and the video grows
  // past the bottom of the box it was meant to sit in — which is how the shorts
  // caption ended up clipped away. The shorts pane owns the 9/16 now, and
  // `object-contain` letterboxes a mis-sized source rather than stretching it.
  return (
    <section
      aria-label={t("player.label", { title })}
      className={portrait === true ? "relative h-full w-full bg-black" : "relative bg-black"}
    >
      <video
        ref={video}
        className={
          portrait === true
            ? minimal
              ? // A short is shot for this frame, so it fills it. `contain` on a
                // correctly shot clip leaves black bars down both sides and makes
                // the feed look like a video player rather than a feed.
                "h-full w-full object-cover"
              : "h-full w-full object-contain"
            : "aspect-video w-full"
        }
        controls={!minimal}
        autoPlay={minimal}
        muted={minimal ? muted : undefined}
        playsInline
        onLoadedMetadata={enterClip}
        onPlay={() => setPaused(false)}
        onPause={() => setPaused(true)}
        onTimeUpdate={() => {
          stopAtClipEnd();
          trackProgress();
          reportProgress();
        }}
        onEnded={() => {
          reportProgress();
          onEnded?.();
        }}
      >
        <track kind="captions" />
      </video>

      {minimal ? (
        <>
          {/* The whole frame is the pause control, which is how the format
              works. A button rather than a click handler on the section: it has
              to be reachable by keyboard and named, and Space then does what the
              hint in the feed has always claimed it does. */}
          <button
            type="button"
            onClick={togglePlayback}
            aria-label={paused ? t("player.play") : t("player.pause")}
            className="absolute inset-0 grid place-items-center focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--primary)]"
          >
            {/* Only while paused. A play glyph over a running video is a control
                that says the opposite of what is happening. */}
            {paused ? (
              <span className="grid size-14 place-items-center rounded-full bg-[color-mix(in_oklch,var(--pa-bg-00)_55%,transparent)] text-ink">
                <PlayIcon />
              </span>
            ) : null}
          </button>

          <button
            type="button"
            onClick={() => {
              soundWanted = muted;
              setMuted(!muted);
            }}
            aria-pressed={!muted}
            aria-label={muted ? t("player.unmute") : t("player.mute")}
            className="absolute right-3 top-3 grid size-9 place-items-center rounded-full bg-[color-mix(in_oklch,var(--pa-bg-00)_55%,transparent)] text-ink transition-colors duration-[var(--duration-fast)] ease-out hover:bg-[color-mix(in_oklch,var(--pa-bg-00)_75%,transparent)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary)]"
          >
            <SoundIcon muted={muted} />
          </button>

          {/* A hairline, not a scrub bar. It says how much is left; it is not
              something to grab, because forty seconds has nothing worth seeking
              to. `scaleX` rather than width so it is a composited property and
              not a layout pass on every timeupdate. */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 h-0.5 origin-left bg-[var(--pa-accent-400)]"
            style={{ transform: `scaleX(${progress})` }}
          />
        </>
      ) : null}
    </section>
  );
}

function PlayIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 24 24" className="size-7" fill="currentColor" aria-hidden="true">
      <path d="M8 5.5v13l11-6.5z" />
    </svg>
  );
}

function SoundIcon({ muted }: { readonly muted: boolean }): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z" />
      {muted ? (
        <path d="m16 9.5 4 5m0-5-4 5" />
      ) : (
        <>
          <path d="M15.5 9.5a3.5 3.5 0 0 1 0 5" />
          <path d="M18 7a7 7 0 0 1 0 10" />
        </>
      )}
    </svg>
  );
}
