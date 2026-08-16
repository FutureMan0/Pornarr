import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";

type RequestStatus = "searching" | "results_found" | "queued" | "downloading" | "processing" | "available" | "failed" | "not_found" | "monitoring" | "cancelled";
type RequestItem = {
  id: string;
  query: string;
  status: RequestStatus;
  priority: number;
  selected_release_guid: string | null;
  history: { status: RequestStatus }[];
};

const TERMINAL = new Set(["available", "cancelled", "failed", "not_found"]);

export function RequestsRoute() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const requests = useQuery({
    queryKey: ["requests"],
    queryFn: async (): Promise<RequestItem[]> => {
      const { data, error, response } = await getApiClient().GET("/api/requests");
      if (data === undefined || error !== undefined) throw apiFailure(error, response);
      return data;
    },
    refetchInterval: 5_000,
  });
  const action = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: "cancel" | "retry" }) => {
      const result = action === "cancel"
        ? await getApiClient().POST("/api/requests/{request_id}/cancel", {
            params: { path: { request_id: id } },
          })
        : await getApiClient().POST("/api/requests/{request_id}/retry", {
        params: { path: { request_id: id } },
      });
      if (result.error !== undefined || result.data === undefined)
        throw apiFailure(result.error, result.response);
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["requests"] }),
  });

  if (requests.isPending) return <p>{t("requests.loading")}</p>;
  if (requests.isError) return <p role="alert">{t("errors.generic")}</p>;
  return (
    <section aria-labelledby="requests-heading" className="flex flex-col gap-4">
      <header><h1 id="requests-heading" className="text-xl text-ink">{t("requests.title")}</h1><p className="text-sm text-ink-muted">{t("requests.intro")}</p></header>
      {requests.data?.length === 0 ? <p>{t("requests.empty")}</p> : (
        <ul className="grid gap-3">
          {requests.data?.map((request) => (
            <li key={request.id} className="border border-border p-4">
              <div className="flex items-start justify-between gap-3"><div><h2 className="font-medium text-ink">{request.query}</h2><p className="text-sm text-ink-muted">{t(`requests.status.${request.status}`)} · {t("requests.priority", { value: request.priority })}</p></div>
                <div className="flex gap-2">
                  {request.status === "failed" || request.status === "not_found" ? <button type="button" className="button" onClick={() => action.mutate({ id: request.id, action: "retry" })}>{t("requests.retry")}</button> : null}
                  {!TERMINAL.has(request.status) ? <button type="button" className="button" onClick={() => action.mutate({ id: request.id, action: "cancel" })}>{t("requests.cancel")}</button> : null}
                </div>
              </div>
              <ol className="mt-3 flex flex-wrap gap-2 text-sm text-ink-muted" aria-label={t("requests.history")}>{request.history.map((item, index) => <li key={`${item.status}-${index}`}>{t(`requests.status.${item.status}`)}</li>)}</ol>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
