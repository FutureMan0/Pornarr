import { Button, SkeletonRegion, SkeletonText } from "@pornarr/ui";
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
  const items = monitors.data ?? [];
  return (
    // The heading is the top bar's, declared with `usePageTitle` above: the
    // design puts the screen's name there, and a second one here would announce
    // it twice to anyone navigating by heading.
    <section aria-label={t("monitors.title")} className="flex flex-col gap-4">
      <header>
        <p className="text-sm text-ink-muted">{t("monitors.intro")}</p>
      </header>
      {monitors.isPending ? (
        <SkeletonRegion label={t("monitors.loading")}>
          <SkeletonText lines={3} />
        </SkeletonRegion>
      ) : monitors.isError ? (
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      ) : items.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("monitors.empty")}</p>
      ) : (
        <ul className="grid gap-3">
          {items.map((m) => (
            <li key={m.id} className="border border-border p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="font-medium text-ink">{m.query ?? m.kind}</h2>
                  <p className="text-sm text-ink-muted">
                    {m.enabled ? t("monitors.enabled") : t("monitors.disabled")}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="secondary"
                    onClick={() => update.mutate({ id: m.id, enabled: !m.enabled })}
                  >
                    {m.enabled ? t("monitors.disable") : t("monitors.enable")}
                  </Button>
                  <Button variant="secondary" onClick={() => backlog.mutate(m.id)}>
                    {t("monitors.search")}
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
