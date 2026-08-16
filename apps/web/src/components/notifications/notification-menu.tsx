import type { paths } from "@pornarr/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";

type Notification = paths["/api/notifications"]["get"]["responses"][200]["content"]["application/json"][number];
const KEY = ["notifications"] as const;

export function NotificationMenu() {
  const { t } = useTranslation(); const cache = useQueryClient(); const [open, setOpen] = useState(false);
  const notifications = useQuery({ queryKey: KEY, queryFn: async (): Promise<Notification[]> => { const { data, error, response } = await getApiClient().GET("/api/notifications"); if (!data || error) throw apiFailure(error, response); return data; } });
  const markRead = useMutation({ mutationFn: async (id: string) => { const { error, response } = await getApiClient().POST("/api/notifications/{notification_id}/read", { params: { path: { notification_id: id } } }); if (error) throw apiFailure(error, response); }, onSuccess: () => void cache.invalidateQueries({ queryKey: KEY }) });
  const unread = notifications.data?.filter((item) => item.read_at === null).length ?? 0;
  return <div className="relative"><button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{t("notifications.open", { count: unread })}</button>{open ? <section className="absolute right-0 z-10 w-80 border border-border bg-surface p-3" aria-label={t("notifications.title")}><h2>{t("notifications.title")}</h2>{notifications.isPending ? <p>{t("notifications.loading")}</p> : notifications.data?.length ? <ul>{notifications.data.map((item) => <li key={item.id}><button type="button" onClick={() => item.read_at === null && markRead.mutate(item.id)}><strong>{t(`notifications.kind.${item.kind}`)}</strong><span>{item.read_at === null ? t("notifications.unread") : t("notifications.read")}</span></button></li>)}</ul> : <p>{t("notifications.empty")}</p>}</section> : null}</div>;
}
