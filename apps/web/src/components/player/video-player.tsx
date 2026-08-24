import Hls from "hls.js";
import type { JSX, RefObject } from "react";
import { useEffect, useImperativeHandle, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CSRF_HEADER, readCsrfToken } from "../../lib/api";
import { apiFailure, messageForError, nextStepForError } from "../../lib/api-error";

type PlaybackInfo = { direct_play: boolean };
type TranscodeSession = { session_id: string; playlist_url: string };

function csrfHeaders(): HeadersInit {
  const token = readCsrfToken();
  return token === null ? {} : { [CSRF_HEADER]: token };
}

/**
 * The body of a failed response is the `{code, status, context}` the API
 * contract promises (docs/api-contract.md), so it is parsed here rather than
 * discarded — `apiFailure` turns it into the same `ApiRequestError` every
 * other surface throws, which is what lets the render below map a refusal to
 * a real sentence instead of one string for every possible failure.
 */
async function responseJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: "same-origin", ...init });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw apiFailure(body, response);
  }
  return response.json() as Promise<T>;
}

/**
 * Where this player's requests go.
 *
 * A title on somebody else's instance is reached through the proxy on this
 * one, because the browser has no key for a peer and must never be given one.
 * Everything below is written against this prefix so the two cases differ in
 * one string rather than in every call.
 */
function apiBase(peerId: string | undefined): string {
  return peerId === undefined ? "/api" : `/api/peers/${peerId}/proxy`;
}

/** A peer answers with its own address for the playlist; the proxy owns it here. */
function playlistUrl(base: string, playlistUrl: string): string {
  return base === "/api" ? playlistUrl : `${base}${playlistUrl.replace(/^\/api/, "")}`;
}

/**
 * Raw `fetch`, deliberately, and the same for the heartbeat and the progress
 * report below. `keepalive` is the whole point: these fire as the player
 * unmounts or the tab closes, and a request the browser is free to cancel at
 * that moment is a transcode session left running on the server. The generated
 * client does not pass the flag through, so routing these through it would
 * silently drop the guarantee.
 */
function releaseSession(base: string, sessionId: string): void {
  void fetch(`${base}/transcode/sessions/${sessionId}`, {
    method: "DELETE",
    headers: csrfHeaders(),
    credentials: "same-origin",
    keepalive: true,
  });
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
  /** The peer this title lives on. Absent for this instance's own library. */
  readonly peerId?: string | undefined;
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
  peerId,
}: VideoPlayerProps) {
  const { t } = useTranslation();
  const video = useRef<HTMLVideoElement>(null);
  const hls = useRef<Hls | null>(null);
  const session = useRef<string | null>(null);
  const starting = useRef<Promise<TranscodeSession> | null>(null);
  const mount = useRef(0);
  const lastProgress = useRef(0);
  // The thrown value itself, not a boolean: rendering below maps it to a
  // cause and a next step the way every other surface does, and a viewer
  // refused a transcode slot needs to be told that, not just that something
  // failed.
  const [error, setError] = useState<unknown>(null);
  const minimal = chrome === "minimal";
  const [paused, setPaused] = useState(false);
  const [muted, setMuted] = useState(!soundWanted);
  /** Position inside the clip, 0–1. Drives the hairline, nothing else. */
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const generation = ++mount.current;
    const base = apiBase(peerId);
    async function load() {
      try {
        const info = await responseJson<PlaybackInfo>(`${base}/media/${mediaId}/playback-info`);
        if (info.direct_play) {
          if (cancelled || video.current === null) return;
          video.current.src = `${base}/media/${mediaId}/stream`;
          return;
        }
        // Remounting must not ask for a second session: the request the first
        // mount sent is still in flight, and whichever of the two answers last
        // leaves a transcode nobody is holding.
        starting.current ??= responseJson<TranscodeSession>(
          `${base}/transcode/media/${mediaId}/sessions`,
          { method: "POST", headers: csrfHeaders() },
        );
        const source = await starting.current;
        if (cancelled || video.current === null) {
          // A session created after the player went away still holds a
          // transcode slot, and the cleanup below could not release it because
          // it did not exist yet. Release it here — but only when no later
          // mount has taken over, because the server hands that mount the very
          // same session and deleting it would break the player that is live.
          if (generation === mount.current) releaseSession(base, source.session_id);
          return;
        }
        session.current = source.session_id;
        // hls.js first, the element's own HLS support only after it.
        // `canPlayType("application/vnd.apple.mpegurl")` answers "maybe" in
        // Chromium, which has no HLS demuxer at all: handing the playlist
        // straight to the element there ended every transcoded playback in
        // DEMUXER_ERROR_COULD_NOT_PARSE. `Hls.isSupported()` is false exactly
        // where Media Source Extensions are missing, which is where the native
        // path is the real one (iOS Safari).
        if (Hls.isSupported()) {
          hls.current = new Hls();
          hls.current.loadSource(playlistUrl(base, source.playlist_url));
          hls.current.attachMedia(video.current);
        } else if (video.current.canPlayType("application/vnd.apple.mpegurl")) {
          video.current.src = playlistUrl(base, source.playlist_url);
        } else {
          setError(new Error("This browser supports neither hls.js nor native HLS playback."));
        }
      } catch (thrown) {
        if (!cancelled) setError(thrown);
      }
    }
    void load();
    return () => {
      cancelled = true;
      hls.current?.destroy();
      hls.current = null;
      if (session.current !== null) {
        releaseSession(base, session.current);
        session.current = null;
      }
    };
  }, [mediaId, peerId]);

  // React runs no cleanup when the page itself goes away, so a reload, a hard
  // navigation or a closed tab left the transcode running until its heartbeat
  // expired — a slot the next viewer could not have.
  useEffect(() => {
    const release = (): void => {
      if (session.current === null) return;
      releaseSession(apiBase(peerId), session.current);
      session.current = null;
    };
    window.addEventListener("pagehide", release);
    return () => window.removeEventListener("pagehide", release);
  }, [peerId]);

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
        void fetch(`${apiBase(peerId)}/transcode/sessions/${session.current}/heartbeat`, {
          method: "POST",
          headers: csrfHeaders(),
          credentials: "same-origin",
        });
      }
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [peerId]);

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
    // Nor is a borrowed title this instance's to record. The id belongs to the
    // peer, and posting it here would file the position against whatever
    // happens to carry that id locally.
    if (peerId !== undefined) return;
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
  //
  // The code is mapped to a sentence here, the same way login-route.tsx maps
  // one for a failed sign-in: never the server's own prose, and never one
  // string for every possible failure. Two sentences, not one — a viewer
  // refused a transcode slot needs to be told that they were refused, not
  // just to be told that something failed, and the next step for every code
  // is already written and translated.
  if (error !== null)
    return (
      <div
        role="alert"
        className={
          portrait === true
            ? "grid h-full w-full place-items-center rounded-lg bg-surface-2 p-4 text-center text-sm text-ink-muted [aspect-ratio:9/16]"
            : "grid aspect-video w-full place-items-center rounded-lg bg-surface-2 p-4 text-center text-sm text-ink-muted"
        }
      >
        <div>
          <p>{messageForError(error)}</p>
          <p className="text-xs">{nextStepForError(error)}</p>
        </div>
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
