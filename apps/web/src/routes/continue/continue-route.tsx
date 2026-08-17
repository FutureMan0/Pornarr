/**
 * Everything you started and did not finish.
 *
 * The endpoint already excludes completed titles, so this screen does no
 * filtering of its own — a title disappearing from here is the server's
 * decision about what "finished" means, not the client's.
 */
import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";

import { usePageTitle } from "../../shell/page-title";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { ResumeTile } from "./resume-tile";

export function ContinueRoute(): JSX.Element {
  const { t } = useTranslation();
  usePageTitle(t("continueWatching.title"));
  const resuming = useQuery({
    queryKey: ["continue-watching"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/playback/continue-watching");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  return (
    <section aria-label={t("continueWatching.title")} className="flex flex-col gap-6">
      {resuming.isPending ? (
        <p className="text-sm text-ink-muted">{t("continueWatching.loading")}</p>
      ) : null}
      {resuming.isError ? (
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      ) : null}

      {resuming.data !== undefined ? (
        resuming.data.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("continueWatching.empty")}</p>
        ) : (
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-4">
            {resuming.data.map((item) => (
              <li key={item.media_id}>
                <ResumeTile item={item} />
              </li>
            ))}
          </ul>
        )
      ) : null}
    </section>
  );
}
