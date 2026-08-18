import { cx } from "@pornarr/ui";
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

function releaseSession(sessionId: string): void {
  void fetch(`/api/transcode/sessions/${sessionId}`, {
    method: "DELETE",
    headers: csrfHeaders(),
    credentials: "same-origin",
    keepalive: true,
  });
}

export interface VideoPlayerProps {
  readonly mediaId: string;
  readonly title: string;
  /**
   * Play only a window of the title, as offsets in seconds.
   *
   * A short is an offset pair into an existing file rather than a file of its
   * own (`packages/db/pornarr_db/models/social.py`), so a clip is this player
   * pointed at the parent title and told where to begin and where to stop.
   * Absent, the whole title plays, which is what the media detail screen asks
   * for and what every existing caller gets.
   */
  readonly startSeconds?: number;
  readonly endSeconds?: number;
  /**
   * Start as soon as the source is ready. Implies muted, because that is the
   * only autoplay a browser permits without a gesture; the controls still let
   * the reader turn sound on.
   */
  readonly autoPlay?: boolean;
  /** Fill the parent instead of holding a 16:9 block of it. */
  readonly fill?: boolean;
}

export function VideoPlayer({
  mediaId,
  title,
  startSeconds,
  endSeconds,
  autoPlay = false,
  fill = false,
}: VideoPlayerProps) {
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
    async function load() {
      try {
        const info = await responseJson<PlaybackInfo>(`/api/media/${mediaId}/playback-info`);
        if (info.direct_play) {
          if (cancelled || video.current === null) return;
          video.current.src = `/api/media/${mediaId}/stream`;
          return;
        }
        // Remounting must not ask for a second session: the request the first
        // mount sent is still in flight, and whichever of the two answers last
        // leaves a transcode nobody is holding.
        starting.current ??= responseJson<TranscodeSession>(
          `/api/transcode/media/${mediaId}/sessions`,
          { method: "POST", headers: csrfHeaders() },
        );
        const source = await starting.current;
        if (cancelled || video.current === null) {
          // A session created after the player went away still holds a
          // transcode slot, and the cleanup below could not release it because
          // it did not exist yet. Release it here - but only when no later
          // mount has taken over, because the server hands that mount the very
          // same session and deleting it would break the player that is live.
          if (generation === mount.current) releaseSession(source.session_id);
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
          hls.current.loadSource(source.playlist_url);
          hls.current.attachMedia(video.current);
        } else if (video.current.canPlayType("application/vnd.apple.mpegurl")) {
          video.current.src = source.playlist_url;
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
        releaseSession(session.current);
        session.current = null;
      }
    };
  }, [mediaId]);

  // React runs no cleanup when the page itself goes away, so a reload, a hard
  // navigation or a closed tab left the transcode running until its heartbeat
  // expired - a slot the next viewer could not have.
  useEffect(() => {
    const release = (): void => {
      if (session.current === null) return;
      releaseSession(session.current);
      session.current = null;
    };
    window.addEventListener("pagehide", release);
    return () => window.removeEventListener("pagehide", release);
  }, []);

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

  // Two clips cut from the same title are the same source at two offsets, so
  // moving between them changes `startSeconds` without changing `mediaId`. The
  // effect above does not re-run then — deliberately, because tearing the
  // transcode session down and building it again for a seek is exactly what
  // this player exists to avoid — so the seek happens here instead. Before the
  // element has metadata there is nothing to seek; `startAtOffset` below runs
  // when it arrives.
  useEffect(() => {
    const element = video.current;
    if (element === null || startSeconds === undefined) return;
    if (element.readyState === 0) return;
    element.currentTime = startSeconds;
  }, [startSeconds]);

  function startAtOffset() {
    if (startSeconds === undefined || video.current === null) return;
    video.current.currentTime = startSeconds;
  }

  function stopAtOffset() {
    const element = video.current;
    if (element === null || endSeconds === undefined) return;
    if (element.currentTime >= endSeconds) element.pause();
  }

  // Pressing play on a clip that has run out means "again", not "carry on into
  // the rest of the title" — the rest of the title is what the media detail
  // screen is for.
  function replayFromOffset() {
    const element = video.current;
    if (element === null || startSeconds === undefined || endSeconds === undefined) return;
    if (element.currentTime >= endSeconds) element.currentTime = startSeconds;
  }

  function reportProgress() {
    const element = video.current;
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
    <section aria-label={t("player.label", { title })} className={cx("bg-black", fill && "h-full")}>
      <video
        ref={video}
        className={cx("w-full", fill ? "h-full object-contain" : "aspect-video")}
        controls
        playsInline
        autoPlay={autoPlay}
        muted={autoPlay}
        onLoadedMetadata={startAtOffset}
        onPlay={replayFromOffset}
        onTimeUpdate={() => {
          stopAtOffset();
          reportProgress();
        }}
        onEnded={reportProgress}
      >
        <track kind="captions" />
      </video>
    </section>
  );
}
