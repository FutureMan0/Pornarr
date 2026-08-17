/**
 * Comments on a title.
 *
 * The server sends no author under the default configuration, so this screen
 * has no name to render and cannot accidentally invent one. What it does get is
 * `is_own`, which is exactly enough to offer an edit and a delete on your own
 * remark and nothing else.
 *
 * Reporting says only that the report was accepted. Telling a reader how many
 * others reported the same comment would turn a moderation tool into a score.
 */
import { Button, Stars } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FormEvent, JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";

export interface CommentsPanelProps {
  readonly mediaId: string;
}

export function CommentsPanel({ mediaId }: CommentsPanelProps): JSX.Element {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const key = ["comments", mediaId] as const;
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [reported, setReported] = useState<ReadonlySet<string>>(new Set());

  const invalidate = (): void => void cache.invalidateQueries({ queryKey: key });

  const comments = useQuery({
    queryKey: key,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/media/{media_id}/comments", {
        params: { path: { media_id: mediaId } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const post = useMutation({
    mutationFn: async (body: string) => {
      const { error, response } = await getApiClient().POST("/api/media/{media_id}/comments", {
        params: { path: { media_id: mediaId } },
        body: { body },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => {
      setDraft("");
      invalidate();
    },
  });

  const edit = useMutation({
    mutationFn: async ({ id, body }: { id: string; body: string }) => {
      const { error, response } = await getApiClient().PATCH("/api/comments/{comment_id}", {
        params: { path: { comment_id: id } },
        body: { body },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => {
      setEditing(null);
      invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().DELETE("/api/comments/{comment_id}", {
        params: { path: { comment_id: id } },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: invalidate,
  });

  const like = useMutation({
    mutationFn: async ({ id, on }: { id: string; on: boolean }) => {
      const client = getApiClient();
      const request = on
        ? client.PUT("/api/comments/{comment_id}/like", {
            params: { path: { comment_id: id } },
          })
        : client.DELETE("/api/comments/{comment_id}/like", {
            params: { path: { comment_id: id } },
          });
      const { error, response } = await request;
      if (error) throw apiFailure(error, response);
    },
    onSuccess: invalidate,
  });

  const report = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().POST("/api/comments/{comment_id}/report", {
        params: { path: { comment_id: id } },
        body: {},
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: (_data, id) => setReported((current) => new Set(current).add(id)),
  });

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    if (draft.trim().length > 0) post.mutate(draft.trim());
  };

  return (
    <section aria-labelledby="comments-heading" className="flex flex-col gap-4">
      <h2 id="comments-heading" className="text-sm text-ink">
        {t("comments.title", { count: comments.data?.length ?? 0 })}
      </h2>

      <form onSubmit={submit} className="flex flex-col gap-2">
        <label htmlFor="comment-draft" className="text-xs text-ink-muted">
          {t("comments.add")}
        </label>
        <textarea
          id="comment-draft"
          value={draft}
          maxLength={4000}
          rows={3}
          onChange={(event) => setDraft(event.target.value)}
          className="rounded-md border border-border bg-surface-2 p-2 text-sm text-ink"
        />
        <Button type="submit" disabled={post.isPending || draft.trim().length === 0}>
          {t("comments.post")}
        </Button>
      </form>

      {comments.isError ? (
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      ) : null}

      <ul className="flex flex-col gap-3">
        {(comments.data ?? []).map((comment) => (
          <li key={comment.id} className="flex flex-col gap-2 rounded-md bg-surface-2 p-3">
            <div className="flex flex-wrap items-center gap-2">
              {/* No name under the default configuration; the server simply
                  does not send one. `is_own` is what the buttons key off. */}
              <span className="text-xs text-ink-muted">
                {comment.author ?? (comment.is_own ? t("comments.you") : t("comments.someone"))}
              </span>
              {comment.stars === null ? null : (
                <Stars
                  value={comment.stars}
                  label={t("library.rating.value", { value: comment.stars, count: 1 })}
                />
              )}
              {comment.state === "hidden" ? (
                <span className="text-2xs text-[var(--pa-accent-300)]">{t("comments.hidden")}</span>
              ) : null}
              {comment.edited_at === null ? null : (
                <span className="text-2xs text-ink-muted">{t("comments.edited")}</span>
              )}
            </div>

            {editing === comment.id ? (
              <div className="flex flex-col gap-2">
                <textarea
                  value={editDraft}
                  rows={3}
                  maxLength={4000}
                  onChange={(event) => setEditDraft(event.target.value)}
                  className="rounded-md border border-border bg-surface p-2 text-sm text-ink"
                />
                <span className="flex gap-2">
                  <Button
                    onClick={() => edit.mutate({ id: comment.id, body: editDraft.trim() })}
                    disabled={edit.isPending || editDraft.trim().length === 0}
                  >
                    {t("comments.save")}
                  </Button>
                  <Button variant="ghost" onClick={() => setEditing(null)}>
                    {t("comments.cancel")}
                  </Button>
                </span>
              </div>
            ) : (
              <p className="whitespace-pre-wrap text-sm text-ink">{comment.body}</p>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                aria-pressed={comment.you_liked}
                onClick={() => like.mutate({ id: comment.id, on: !comment.you_liked })}
                className={
                  comment.you_liked
                    ? "text-xs text-[var(--pa-accent-300)]"
                    : "text-xs text-ink-muted hover:text-ink"
                }
              >
                {t("comments.like", { count: comment.likes })}
              </button>

              {comment.is_own ? (
                <>
                  <button
                    type="button"
                    className="text-xs text-ink-muted hover:text-ink"
                    onClick={() => {
                      setEditing(comment.id);
                      setEditDraft(comment.body);
                    }}
                  >
                    {t("comments.edit")}
                  </button>
                  <button
                    type="button"
                    className="text-xs text-ink-muted hover:text-ink"
                    onClick={() => remove.mutate(comment.id)}
                  >
                    {t("comments.delete")}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="text-xs text-ink-muted hover:text-ink"
                  disabled={reported.has(comment.id)}
                  onClick={() => report.mutate(comment.id)}
                >
                  {/* Acknowledged, never counted. How many others reported the
                      same remark is an administrator's business. */}
                  {reported.has(comment.id) ? t("comments.reported") : t("comments.report")}
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
