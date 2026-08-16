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

export function VideoPlayer({ mediaId, title }: { readonly mediaId: string; readonly title: string }) {
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
          : (await responseJson<TranscodeSession>(`/api/transcode/media/${mediaId}/sessions`, {
              method: "POST",
              headers: csrfHeaders(),
            }));
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

  function reportProgress() {
    const element = video.current;
    if (element === null || !Number.isFinite(element.duration) || element.duration <= 0) return;
    if (element.currentTime - lastProgress.current < 10 && !element.ended) return;
    lastProgress.current = element.currentTime;
    void fetch(`/api/playback/${mediaId}/progress`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      credentials: "same-origin",
      body: JSON.stringify({ position_seconds: element.currentTime, duration_seconds: element.duration }),
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
        <track kind="subtitles" />
      </video>
    </section>
  );
}
