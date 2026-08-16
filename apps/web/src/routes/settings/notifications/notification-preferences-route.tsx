import type { paths } from "@pornarr/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { getApiClient } from "../../../lib/api";
import { apiFailure } from "../../../lib/api-error";

type Preference = paths["/api/notifications/preferences"]["get"]["responses"][200]["content"]["application/json"][number];
const KEY = ["notification-preferences"] as const;
export function NotificationPreferencesRoute() {
  const { t } = useTranslation(); const cache = useQueryClient();
  const preferences = useQuery({ queryKey: KEY, queryFn: async (): Promise<Preference[]> => { const { data, error, response } = await getApiClient().GET("/api/notifications/preferences"); if (!data || error) throw apiFailure(error, response); return data; } });
  const change = useMutation({ mutationFn: async ({ kind, enabled }: Preference) => { const { error, response } = await getApiClient().PUT("/api/notifications/preferences/{kind}", { params: { path: { kind } }, body: { enabled } }); if (error) throw apiFailure(error, response); }, onSuccess: () => void cache.invalidateQueries({ queryKey: KEY }) });
  if (preferences.isPending) return <p>{t("notifications.loading")}</p>; if (preferences.isError) return <p role="alert">{t("errors.generic")}</p>;
  return <section><h1>{t("notifications.preferences")}</h1><ul>{preferences.data.map((preference) => <li key={preference.kind}><label><input type="checkbox" checked={preference.enabled} onChange={() => change.mutate({ ...preference, enabled: !preference.enabled })} />{t(`notifications.kind.${preference.kind}`)}</label></li>)}</ul></section>;
}
