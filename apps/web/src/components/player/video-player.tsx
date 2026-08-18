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
  /** The peer this title lives on. Absent for this instance's own library. */
  readonly peerId?: string | undefined;
}

export function VideoPlayer({ mediaId, title, clip, portrait, onEnded, peerId }: VideoPlayerProps) {
  const { t } = useTranslation();
  const video = useRef<HTMLVideoElement>(null);
  const hls = useRef<Hls | null>(null);
  const session = useRef<string | null>(null);
  const starting = useRef<Promise<TranscodeSession> | null>(null);
  const mount = useRef(0);
  const lastProgress = useRef(0);
  const [error, setError] = useState(false);

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
          // it did not exist yet. Release it here - but only when no later
          // mount has taken over, because the server hands that mount the very
          // same session and deleting it would break the player that is live.
          if (generation === mount.current) releaseSession(base, source.session_id);
          return;
        }
        session.current = source.session_id;
        // Media Source first. Chromium answers "maybe" to the HLS mime type
        // and then never loads a segment, so asking the element what it can
        // play picked the one path that cannot work outside Safari.
        if (Hls.isSupported()) {
          hls.current = new Hls();
          hls.current.on(Hls.Events.ERROR, (_event, data) => {
            if (data.fatal) setError(true);
          });
          hls.current.loadSource(playlistUrl(base, source.playlist_url));
          hls.current.attachMedia(video.current);
        } else if (video.current.canPlayType("application/vnd.apple.mpegurl")) {
          video.current.src = playlistUrl(base, source.playlist_url);
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
        releaseSession(base, session.current);
        session.current = null;
      }
    };
  }, [mediaId, peerId]);

  // React runs no cleanup when the page itself goes away, so a reload, a hard
  // navigation or a closed tab left the transcode running until its heartbeat
  // expired - a slot the next viewer could not have.
  useEffect(() => {
    const release = (): void => {
      if (session.current === null) return;
      releaseSession(apiBase(peerId), session.current);
      session.current = null;
    };
    window.addEventListener("pagehide", release);
    return () => window.removeEventListener("pagehide", release);
  }, [peerId]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (session.current !== null) {
        void fetch(`${apiBase(peerId)}/transcode/sessions/${session.current}/heartbeat`, {
          method: "POST",
          headers: csrfHeaders(),
          credentials: "same-origin",
        });
      }
    }, 25_000);
    return () => window.clearInterval(timer);
  }, [peerId]);

  // Two clips cut from the same title are the same source at two offsets, so
  // moving between them changes `clip` without changing `mediaId`. The effect
  // above does not re-run then — deliberately, because tearing the transcode
  // session down and building it again for a seek is exactly what this player
  // exists to avoid — so the seek happens here instead. Before the element has
  // metadata there is nothing to seek; `enterClip` runs when it arrives.
  useEffect(() => {
    const element = video.current;
    if (element === null || clip === undefined) return;
    if (element.readyState === 0) return;
    element.currentTime = clip.startSeconds;
  }, [clip]);

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
