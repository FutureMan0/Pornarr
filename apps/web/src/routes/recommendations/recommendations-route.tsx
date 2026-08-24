import { Button, SkeletonRegion, SkeletonText } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";
type Item = {
  media_id: string;
  title: string;
  reason: { matched_tags?: string[]; matched_performers?: string[] };
};
export function RecommendationsRoute() {
  const { t } = useTranslation();
  usePageTitle(t("recommendations.title"));
  const client = useQueryClient();
  const recommendations = useQuery({
    queryKey: ["recommendations"],
    queryFn: async (): Promise<Item[]> => {
      const { data, error, response } = await getApiClient().GET("/api/recommendations");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });
  const feedback = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().POST(
        "/api/recommendations/{media_id}/feedback",
        {
          params: { path: { media_id: id } },
          body: { event_type: "not_interested", subject_id: null },
        },
      );
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ["recommendations"] }),
  });
  const items = recommendations.data ?? [];
  return (
    // The heading is the top bar's, declared with `usePageTitle` above.
    <section aria-label={t("recommendations.title")} className="flex flex-col gap-4">
      <header>
        <p className="text-sm text-ink-muted">{t("recommendations.intro")}</p>
      </header>
      {recommendations.isPending ? (
        <SkeletonRegion label={t("recommendations.loading")}>
          <SkeletonText lines={3} />
        </SkeletonRegion>
      ) : recommendations.isError ? (
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      ) : items.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("recommendations.empty")}</p>
      ) : (
        <ul className="grid gap-3">
          {items.map((item) => (
            <li key={item.media_id} className="border border-border p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="font-medium text-ink">{item.title}</h2>
                  <p className="text-sm text-ink-muted">
                    {t("recommendations.reason", {
                      values: [
                        ...(item.reason.matched_tags ?? []),
                        ...(item.reason.matched_performers ?? []),
                      ].join(", "),
                    })}
                  </p>
                </div>
                <Button variant="secondary" onClick={() => feedback.mutate(item.media_id)}>
                  {t("recommendations.notInterested")}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
