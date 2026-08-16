import type { paths } from "@pornarr/api-client";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";

type Home = paths["/api/home"]["get"]["responses"][200]["content"]["application/json"];
type Item = Home["recently_added"][number];

function Section({ title, items, empty }: { readonly title: string; readonly items: Item[]; readonly empty: string }) {
  return <section><h2>{title}</h2>{items.length === 0 ? <p>{empty}</p> : <ul>{items.map((item) => <li key={item.id}><Link to={`/library/${item.id}`}>{item.title}</Link>{item.position_seconds !== null && item.progress_duration_seconds ? <progress value={item.position_seconds} max={item.progress_duration_seconds} /> : null}</li>)}</ul>}</section>;
}

export function HomeRoute() {
  const { t } = useTranslation();
  const home = useQuery({ queryKey: ["home"], queryFn: async (): Promise<Home> => { const { data, error, response } = await getApiClient().GET("/api/home"); if (!data || error) throw apiFailure(error, response); return data; } });
  if (home.isPending) return <p>{t("home.loading")}</p>;
  if (home.isError) return <p role="alert">{t("errors.generic")}</p>;
  return <main><h1>{t("home.title")}</h1><Section title={t("home.continueWatching")} items={home.data.continue_watching} empty={t("home.continueEmpty")} /><Section title={t("home.recentlyAdded")} items={home.data.recently_added} empty={t("home.recentEmpty")} /><section><h2>{t("home.nextTitle")}</h2><p>{t("home.nextBody")}</p></section></main>;
}
