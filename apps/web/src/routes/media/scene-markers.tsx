/**
 * Where the scenes change, as a row you can jump from.
 *
 * `/api/media/{id}/scenes` has existed since scene detection landed and nothing
 * had ever read it. This is the whole reason the detector runs: a two-hour title
 * with five marks is navigable, and the same title without them is a scrub bar.
 *
 * The placeholder is `{{index}}` and not the obvious `{{ordinal}}`, because
 * `ordinal` is one of i18next's own option names — the boolean that selects
 * ordinal plural forms — so an interpolation called that fails to typecheck
 * with an error about `boolean` that names nothing you wrote.
 *
 * NUMBERED, NOT NAMED. The design labels them "Opening", "Scene 2", "Close".
 * The detector produces an ordinal and two timestamps and no label at all, so
 * "Opening" would be a word this application invented about somebody's film.
 * The ordinal is what is true.
 *
 * ABSENT RATHER THAN EMPTY. A title nothing has analysed gets no row. A heading
 * over nothing tells a reader the feature is broken rather than that the work
 * has not run.
 */
import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { formatDuration } from "../../lib/format";

export interface SceneMarkersProps {
  readonly mediaId: string;
  /** Called with a position in seconds when a marker is chosen. */
  readonly onSeek: (seconds: number) => void;
}

export function SceneMarkers({ mediaId, onSeek }: SceneMarkersProps): JSX.Element | null {
  const { t } = useTranslation();

  const scenes = useQuery({
    queryKey: ["scenes", mediaId],
    // A title nobody has analysed answers 404, and retrying will not analyse it.
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/media/{media_id}/scenes", {
        params: { path: { media_id: mediaId } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const markers = scenes.data?.scenes ?? [];
  if (markers.length === 0) return null;

  return (
    <section aria-labelledby="scenes-heading" className="flex flex-col gap-2">
      <h2 id="scenes-heading" className="text-2xs uppercase tracking-[0.08em] text-ink-muted">
        {t("media.scenes")}
      </h2>
      <ul className="flex flex-wrap gap-2">
        {markers.map((scene) => (
          <li key={scene.id}>
            <button
              type="button"
              onClick={() => onSeek(scene.start_seconds)}
              className="flex items-center gap-2 rounded-full bg-surface-2 px-3 py-1.5 text-xs text-ink-muted transition-colors duration-[var(--duration-fast)] ease-out hover:bg-surface-3 hover:text-ink"
            >
              <span className="tabular-nums text-[var(--pa-accent-300)]">
                {formatDuration(scene.start_seconds)}
              </span>
              {t("media.sceneLabel", { index: scene.ordinal })}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
