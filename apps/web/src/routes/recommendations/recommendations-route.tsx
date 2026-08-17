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
  if (recommendations.isPending) return <p>{t("recommendations.loading")}</p>;
  if (recommendations.isError) return <p role="alert">{t("errors.generic")}</p>;
  if (!recommendations.data.length)
    return (
      <section>
        <p>{t("recommendations.empty")}</p>
      </section>
    );
  return (
    <section aria-label={t("recommendations.title")}>
      <ul>
        {recommendations.data.map((item) => (
          <li key={item.media_id}>
            <h2>{item.title}</h2>
            <p>
              {t("recommendations.reason", {
                values: [
                  ...(item.reason.matched_tags ?? []),
                  ...(item.reason.matched_performers ?? []),
                ].join(", "),
              })}
            </p>
            <button type="button" onClick={() => feedback.mutate(item.media_id)}>
              {t("recommendations.notInterested")}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
