import Hls from "hls.js";
import { useEffect, useRef, useState } from "react";
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
}

export function VideoPlayer({ mediaId, title, clip, portrait, onEnded }: VideoPlayerProps) {
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

  if (error) return <p role="alert">{t("player.unavailable")}</p>;
  return (
    <section aria-label={t("player.label", { title })} className="bg-black">
      <video
        ref={video}
        className={portrait === true ? "aspect-[9/16] h-full w-full" : "aspect-video w-full"}
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
