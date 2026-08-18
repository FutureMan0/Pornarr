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

export function VideoPlayer({
  mediaId,
  title,
}: { readonly mediaId: string; readonly title: string }) {
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
    <section aria-label={t("player.label", { title })} className="bg-black">
      <video
        ref={video}
        className="aspect-video w-full"
        controls
        playsInline
        onTimeUpdate={reportProgress}
        onEnded={reportProgress}
      >
        <track kind="captions" />
      </video>
    </section>
  );
}
