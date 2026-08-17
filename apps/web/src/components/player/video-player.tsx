import Hls from "hls.js";
import type { RefObject } from "react";
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

export function VideoPlayer({
  mediaId,
  title,
  clip,
  portrait,
  onEnded,
  handleRef,
}: VideoPlayerProps) {
  const { t } = useTranslation();
  const video = useRef<HTMLVideoElement>(null);
  const hls = useRef<Hls | null>(null);
  const session = useRef<string | null>(null);
  const lastProgress = useRef(0);
  const [error, setError] = useState(false);

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
      element.pause();
      onEnded?.();
    }
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
      className={portrait === true ? "h-full bg-black" : "bg-black"}
    >
      <video
        ref={video}
        className={portrait === true ? "h-full w-full object-contain" : "aspect-video w-full"}
        controls
        playsInline
        onLoadedMetadata={enterClip}
        onTimeUpdate={() => {
          stopAtClipEnd();
          reportProgress();
        }}
        onEnded={() => {
          reportProgress();
          onEnded?.();
        }}
      >
        <track kind="captions" />
      </video>
    </section>
  );
}
