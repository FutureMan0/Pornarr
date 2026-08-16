import type { paths } from "@pornarr/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { getApiClient } from "../../../lib/api";
import { apiFailure } from "../../../lib/api-error";

type Profile = paths["/api/recommendations/profile"]["get"]["responses"][200]["content"]["application/json"];
const KEY = ["recommendations", "profile"] as const;
export function PreferencesRoute() {
  const { t } = useTranslation(); const cache = useQueryClient(); const [confirming, setConfirming] = useState(false);
  const profile = useQuery({ queryKey: KEY, queryFn: async (): Promise<Profile> => { const { data, error, response } = await getApiClient().GET("/api/recommendations/profile"); if (!data || error) throw apiFailure(error, response); return data; } });
  const reset = useMutation({ mutationFn: async () => { const { error, response } = await getApiClient().DELETE("/api/recommendations/profile"); if (error) throw apiFailure(error, response); }, onSuccess: () => { setConfirming(false); void cache.invalidateQueries({ queryKey: KEY }); } });
  const unhide = useMutation({ mutationFn: async ({ axis, subject }: { axis: "tag" | "performer"; subject: string }) => { const { error, response } = await getApiClient().DELETE("/api/recommendations/profile/hidden/{axis}/{subject}", { params: { path: { axis, subject } } }); if (error) throw apiFailure(error, response); }, onSuccess: () => void cache.invalidateQueries({ queryKey: KEY }) });
  if (profile.isPending) return <p>{t("preferences.loading")}</p>; if (profile.isError) return <p role="alert">{t("errors.generic")}</p>;
  const hidden = profile.data.preferences.filter((item) => item.hidden);
  return <section><h1>{t("preferences.title")}</h1><p>{t("preferences.retention", { days: profile.data.retention_days ?? t("preferences.forever") })}</p><h2>{t("preferences.interests")}</h2><ul>{profile.data.preferences.filter((item) => !item.hidden).map((item) => <li key={`${item.axis}-${item.subject}`}>{item.axis}: {item.label} ({Math.round(item.score * 100)}%)</li>)}</ul><h2>{t("preferences.hidden")}</h2>{hidden.length ? <ul>{hidden.map((item) => <li key={`${item.axis}-${item.subject}`}>{item.label}<button type="button" onClick={() => unhide.mutate({ axis: item.axis as "tag" | "performer", subject: item.subject })}>{t("preferences.unhide")}</button></li>)}</ul> : <p>{t("preferences.none")}</p>}<h2>{t("preferences.resetTitle")}</h2><p>{t("preferences.resetBody")}</p>{confirming ? <button type="button" onClick={() => reset.mutate()}>{t("preferences.confirmReset")}</button> : <button type="button" onClick={() => setConfirming(true)}>{t("preferences.reset")}</button>}</section>;
}
