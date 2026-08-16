import type { paths } from "@pornarr/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getApiClient } from "../../../lib/api";
import { apiFailure } from "../../../lib/api-error";

type RootFolder = paths["/api/admin/library/root-folders"]["get"]["responses"][200]["content"]["application/json"][number];
type Scan = paths["/api/admin/library/root-folders/{folder_id}/scan"]["post"]["responses"][202]["content"]["application/json"];
const KEY = ["admin", "library"] as const;

export function LibrarySettingsRoute() {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const [path, setPath] = useState("");
  const [jobs, setJobs] = useState<Record<string, string>>({});
  const [progress, setProgress] = useState<Record<string, number>>({});
  const folders = useQuery({ queryKey: KEY, queryFn: async (): Promise<RootFolder[]> => { const { data, error, response } = await getApiClient().GET("/api/admin/library/root-folders"); if (!data || error) throw apiFailure(error, response); return data; } });
  const refresh = (): void => { void cache.invalidateQueries({ queryKey: KEY }); };
  const add = useMutation({ mutationFn: async () => { const { data, error, response } = await getApiClient().POST("/api/admin/library/root-folders", { body: { path, enabled: true } }); if (!data || error) throw apiFailure(error, response); }, onSuccess: () => { setPath(""); refresh(); } });
  const scan = useMutation({ mutationFn: async (id: string): Promise<{ id: string; scan: Scan }> => { const { data, error, response } = await getApiClient().POST("/api/admin/library/root-folders/{folder_id}/scan", { params: { path: { folder_id: id } } }); if (!data || error) throw apiFailure(error, response); return { id, scan: data }; }, onSuccess: ({ id, scan }) => setJobs((current) => ({ ...current, [id]: scan.job_id })) });
  const cancel = useMutation({ mutationFn: async ({ id, jobId }: { id: string; jobId: string }) => { const { error, response } = await getApiClient().DELETE("/api/admin/library/root-folders/{folder_id}/scan/{job_id}", { params: { path: { folder_id: id, job_id: jobId } } }); if (error) throw apiFailure(error, response); }, onSuccess: (_, { id }) => setJobs((current) => { const next = { ...current }; delete next[id]; return next; }) });
  const remove = useMutation({ mutationFn: async (id: string) => { const { error, response } = await getApiClient().DELETE("/api/admin/library/root-folders/{folder_id}", { params: { path: { folder_id: id } } }); if (error) throw apiFailure(error, response); }, onSuccess: refresh });

  useEffect(() => {
    const source = new EventSource("/api/events");
    const listener = (event: Event): void => { if (!(event instanceof MessageEvent) || typeof event.data !== "string") return; const data: unknown = JSON.parse(event.data); if (typeof data !== "object" || data === null || !("root_folder_id" in data) || !("files_scanned" in data) || typeof data.root_folder_id !== "string" || typeof data.files_scanned !== "number") return; const folderId = data.root_folder_id; const filesScanned = data.files_scanned; setProgress((current) => ({ ...current, [folderId]: filesScanned })); };
    source.addEventListener("scan.progress", listener);
    return () => { source.removeEventListener("scan.progress", listener); source.close(); };
  }, []);

  if (folders.isPending) return <p>{t("librarySettings.loading")}</p>;
  if (folders.isError) return <p role="alert">{t("errors.generic")}</p>;
  return <section><h1>{t("librarySettings.title")}</h1><p>{t("librarySettings.intro")}</p><form onSubmit={(event) => { event.preventDefault(); if (path.trim()) add.mutate(); }}><label htmlFor="library-root-path">{t("librarySettings.path")}</label><input id="library-root-path" value={path} onChange={(event) => setPath(event.target.value)} required /><button type="submit">{t("librarySettings.add")}</button></form><ul>{folders.data.map((folder) => { const jobId = jobs[folder.id]; return <li key={folder.id}><strong>{folder.path}</strong><p>{t("librarySettings.freeSpace", { value: folder.free_space_bytes })}</p>{folder.warning ? <p role="alert">{folder.warning}</p> : null}{progress[folder.id] === undefined ? null : <p>{t("librarySettings.progress", { value: progress[folder.id] })}</p>}{jobId ? <button onClick={() => cancel.mutate({ id: folder.id, jobId })}>{t("librarySettings.cancel")}</button> : <button onClick={() => scan.mutate(folder.id)} disabled={!folder.enabled}>{t("librarySettings.scan")}</button>}<button onClick={() => remove.mutate(folder.id)}>{t("librarySettings.remove")}</button></li>; })}</ul></section>;
}
