/**
 * A4 — the tag list, and cleaning it up.
 *
 * SELECTION DRIVES THE ACTIONS. Merge needs two or more; rename and delete need
 * exactly one. Rather than disabling four buttons and leaving the reader to
 * work out why, the bar says what the current selection can do.
 *
 * MERGE NAMES ITS SURVIVOR. "Merge 3 tags" is the question nobody can answer
 * safely — into which one? The bar asks for the target explicitly, because a
 * merge cannot be undone and picking the first alphabetically would be a
 * decision made by an implementation detail.
 *
 * WHAT THE DESIGN SHOWS THAT IS NOT HERE. A group per tag and a guest-visible
 * eye: neither exists in the schema, and a control that governs nothing is
 * worse than a missing one on a screen about privacy.
 */
import { Button, Input } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "../../i18n/format";
import { getApiClient } from "../../lib/api";
import { apiFailure, messageForError } from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";

export function TagsRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const cache = useQueryClient();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const tags = useQuery({
    queryKey: ["admin", "tags", query],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/tags", {
        params: { query: query.trim() === "" ? {} : { q: query.trim() } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const done = (): void => {
    setSelected(new Set());
    setRenaming(null);
    void cache.invalidateQueries({ queryKey: ["admin", "tags"] });
  };

  const rename = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }) => {
      const { error, response } = await getApiClient().PATCH("/api/admin/tags/{tag_id}", {
        params: { path: { tag_id: id } },
        body: { name },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: done,
  });

  const merge = useMutation({
    mutationFn: async ({ into, sources }: { into: string; sources: string[] }) => {
      const { error, response } = await getApiClient().POST("/api/admin/tags/{tag_id}/merge", {
        params: { path: { tag_id: into } },
        body: { source_ids: sources },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: done,
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().DELETE("/api/admin/tags/{tag_id}", {
        params: { path: { tag_id: id } },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: done,
  });

  usePageTitle(
    t("tags.title"),
    tags.data === undefined ? undefined : t("tags.subtitle", { count: tags.data.length }),
  );

  const rows = tags.data ?? [];
  const chosen = rows.filter((tag) => selected.has(tag.id));
  const failure = rename.error ?? merge.error ?? remove.error;

  const toggle = (id: string): void =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <span className="flex min-w-[16rem] flex-1 flex-col gap-1">
          <label htmlFor="tag-search" className="text-xs text-ink-muted">
            {t("tags.search")}
          </label>
          <Input
            id="tag-search"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </span>
      </div>

      {failure === null || failure === undefined ? null : (
        <p role="alert" className="text-sm text-ink">
          {messageForError(failure)}
        </p>
      )}

      {chosen.length > 0 ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg bg-[var(--primary-weak)] p-3">
          <span className="text-sm text-ink">{t("tags.selected", { count: chosen.length })}</span>

          {chosen.length === 1 && chosen[0] !== undefined ? (
            renaming === chosen[0].id ? (
              <span className="flex items-center gap-2">
                <label htmlFor="tag-rename" className="visually-hidden">
                  {t("tags.newName")}
                </label>
                <Input
                  id="tag-rename"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                />
                <Button
                  disabled={rename.isPending || draft.trim().length === 0}
                  onClick={() => {
                    const target = chosen[0];
                    if (target !== undefined) {
                      rename.mutate({ id: target.id, name: draft.trim() });
                    }
                  }}
                >
                  {t("tags.save")}
                </Button>
                <Button variant="ghost" onClick={() => setRenaming(null)}>
                  {t("tags.cancel")}
                </Button>
              </span>
            ) : (
              <Button
                variant="secondary"
                onClick={() => {
                  const target = chosen[0];
                  if (target === undefined) return;
                  setRenaming(target.id);
                  setDraft(target.name);
                }}
              >
                {t("tags.rename")}
              </Button>
            )
          ) : null}

          {chosen.length > 1 ? (
            <span className="flex flex-wrap items-center gap-2">
              {/* Which one survives is the whole question. Choosing it for the
                  administrator would be an irreversible decision made by
                  whatever order the list happened to be in. */}
              <span className="text-xs text-ink-muted">{t("tags.mergeInto")}</span>
              {chosen.map((tag) => (
                <Button
                  key={tag.id}
                  variant="secondary"
                  disabled={merge.isPending}
                  onClick={() =>
                    merge.mutate({
                      into: tag.id,
                      sources: chosen.filter((other) => other.id !== tag.id).map((o) => o.id),
                    })
                  }
                >
                  {tag.name}
                </Button>
              ))}
            </span>
          ) : null}

          {chosen.length === 1 && chosen[0] !== undefined ? (
            <Button
              variant="ghost"
              disabled={remove.isPending}
              onClick={() => {
                const target = chosen[0];
                if (target !== undefined) remove.mutate(target.id);
              }}
            >
              {t("tags.delete")}
            </Button>
          ) : null}

          <button
            type="button"
            className="ml-auto text-xs text-ink-muted underline hover:text-ink"
            onClick={() => setSelected(new Set())}
          >
            {t("tags.clearSelection")}
          </button>
        </div>
      ) : null}

      {tags.isError ? (
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      ) : tags.isPending ? (
        <p className="text-sm text-ink-muted">{t("tags.loading")}</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-ink-muted">
          {query.trim() === "" ? t("tags.empty") : t("tags.noMatches")}
        </p>
      ) : (
        <div className="min-w-0 overflow-x-auto">
          {/* `min-w-0` on the wrapper, not just here: without it the wrapper
              stretches to the table and its own overflow never engages. */}
          <table className="w-full min-w-[36rem] text-sm">
            <thead className="text-left text-2xs uppercase tracking-[0.08em] text-ink-muted">
              <tr>
                <th className="w-8 py-2 font-normal">
                  <span className="visually-hidden">{t("tags.select")}</span>
                </th>
                <th className="py-2 font-normal">{t("tags.column.tag")}</th>
                <th className="py-2 font-normal">{t("tags.column.titles")}</th>
                <th className="py-2 font-normal">{t("tags.column.lastUsed")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((tag) => (
                <tr key={tag.id} className="border-t border-border">
                  <td className="py-2">
                    <input
                      type="checkbox"
                      checked={selected.has(tag.id)}
                      onChange={() => toggle(tag.id)}
                      aria-label={t("tags.selectOne", { name: tag.name })}
                    />
                  </td>
                  <td className="py-2">
                    <span className="rounded-md bg-surface-3 px-2 py-0.5 text-ink">{tag.name}</span>
                  </td>
                  <td className="py-2 tabular-nums text-ink-muted">
                    {/* Zero is shown, not hidden: an unused tag is the first
                        thing worth removing. */}
                    {tag.media_count}
                  </td>
                  <td className="py-2 text-ink-muted">
                    {tag.last_used_at === null
                      ? t("tags.neverUsed")
                      : format.relativeDate(new Date(tag.last_used_at))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
