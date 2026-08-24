import { Button, Input, SkeletonRegion, SkeletonText } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { useState } from "react";
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
  // The form is behind a button rather than always open: this screen is read
  // far more often than it is added to, and a standing form pushes the list -
  // the thing the reader came for - below the fold.
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [minimumScore, setMinimumScore] = useState(0);
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
  const create = useMutation({
    mutationFn: async (query: string) => {
      const { error, response } = await getApiClient().POST("/api/monitors", {
        body: { kind: "query", query, minimum_score: minimumScore, enabled: true },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => {
      setQuery("");
      setAdding(false);
      void cache.invalidateQueries({ queryKey: ["monitors"] });
    },
  });
  const remove = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().DELETE("/api/monitors/{monitor_id}", {
        params: { path: { monitor_id: id } },
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
  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const term = query.trim();
    if (term === "") return;
    create.mutate(term);
  };
  return (
    // The heading is the top bar's, declared with `usePageTitle` above: the
    // design puts the screen's name there, and a second one here would announce
    // it twice to anyone navigating by heading.
    <section aria-label={t("monitors.title")} className="flex flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-[70ch] text-sm text-ink-muted">{t("monitors.intro")}</p>
        {adding ? null : <Button onClick={() => setAdding(true)}>{t("monitors.add")}</Button>}
      </header>

      {adding ? (
        <form
          className="flex flex-col gap-4 border border-border bg-surface p-4"
          aria-labelledby="monitor-add-heading"
          onSubmit={submit}
        >
          <h2 id="monitor-add-heading" className="text-md text-ink">
            {t("monitors.addTitle")}
          </h2>
          <div className="flex flex-col gap-2">
            <label className="text-sm text-ink-muted" htmlFor="monitor-query">
              {t("monitors.query")}
            </label>
            <Input
              id="monitor-query"
              value={query}
              autoComplete="off"
              onChange={(event) => setQuery(event.target.value)}
            />
            <p className="text-2xs text-ink-faint">{t("monitors.queryHint")}</p>
          </div>
          <div className="flex flex-col gap-2">
            <label className="text-sm text-ink-muted" htmlFor="monitor-score">
              {t("monitors.minimumScore")}
            </label>
            <Input
              id="monitor-score"
              type="number"
              min={0}
              className="max-w-[8rem] tabular-nums"
              value={String(minimumScore)}
              onChange={(event) => setMinimumScore(Number(event.target.value) || 0)}
            />
          </div>
          {create.isError ? (
            <p role="alert" className="text-sm text-ink">
              {t("monitors.duplicate")}
            </p>
          ) : null}
          <div className="flex gap-2">
            <Button type="submit" loading={create.isPending} disabled={query.trim() === ""}>
              {t("monitors.save")}
            </Button>
            <Button variant="ghost" type="button" onClick={() => setAdding(false)}>
              {t("monitors.cancel")}
            </Button>
          </div>
        </form>
      ) : null}
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
                  <Button
                    variant="ghost"
                    aria-label={t("monitors.removeMonitor", { name: m.query ?? m.kind })}
                    onClick={() => remove.mutate(m.id)}
                  >
                    {t("monitors.remove")}
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
