import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";
type Monitor = {
  id: string;
  kind: string;
  query: string | null;
  enabled: boolean;
  minimum_score: number;
  last_match_at: string | null;
};
export function MonitorsRoute() {
  const { t } = useTranslation();
  usePageTitle(t("monitors.title"));
  const cache = useQueryClient();
  const monitors = useQuery({
    queryKey: ["monitors"],
    queryFn: async (): Promise<Monitor[]> => {
      const { data, error, response } = await getApiClient().GET("/api/monitors");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });
  const update = useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) => {
      const { error, response } = await getApiClient().PATCH("/api/monitors/{monitor_id}", {
        params: { path: { monitor_id: id } },
        body: { enabled },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: ["monitors"] }),
  });
  const backlog = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().POST(
        "/api/monitors/{monitor_id}/backlog-search",
        { params: { path: { monitor_id: id } } },
      );
      if (error) throw apiFailure(error, response);
    },
  });
  if (monitors.isPending) return <p>{t("monitors.loading")}</p>;
  if (monitors.isError) return <p role="alert">{t("errors.generic")}</p>;
  return (
    <section>
      <ul>
        {monitors.data.map((m) => (
          <li key={m.id}>
            <strong>{m.query ?? m.kind}</strong>
            <p>{m.enabled ? t("monitors.enabled") : t("monitors.disabled")}</p>
            <button type="button" onClick={() => update.mutate({ id: m.id, enabled: !m.enabled })}>
              {m.enabled ? t("monitors.disable") : t("monitors.enable")}
            </button>
            <button type="button" onClick={() => backlog.mutate(m.id)}>
              {t("monitors.search")}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
