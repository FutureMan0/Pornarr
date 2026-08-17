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

import { useFormat } from "../../i18n/format";
import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";

export interface CommentsPanelProps {
  readonly mediaId: string;
  /**
   * `column` is the narrow treatment: the composer moves to the bottom, the list
   * scrolls inside its own box, and the heading is left to whatever wraps it.
   *
   * The shorts feed shows comments in a column beside the video, where the panel
   * has a fixed height and the reader is scrolling comments rather than the page.
   * Same data, same actions, different geometry — a second component would be a
   * second place to fix a bug in the report button.
   */
  readonly layout?: "panel" | "column" | undefined;
  /**
   * Drop the visible heading, for a container that already has a title.
   *
   * The accessible name stays — the heading becomes `visually-hidden` rather
   * than disappearing — because `aria-labelledby` still points at it and a
   * section with no name is a section a screen reader cannot announce.
   */
  readonly headingVisible?: boolean | undefined;
}

export function CommentsPanel({
  mediaId,
  layout = "panel",
  headingVisible = true,
}: CommentsPanelProps): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const cache = useQueryClient();
  const column = layout === "column";
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

  const composer = (
    <form onSubmit={submit} className={column ? "flex flex-none gap-2" : "flex flex-col gap-2"}>
      <label
        htmlFor="comment-draft"
        className={column ? "visually-hidden" : "text-xs text-ink-muted"}
      >
        {t("comments.add")}
      </label>
      <textarea
        id="comment-draft"
        value={draft}
        maxLength={4000}
        rows={column ? 1 : 3}
        placeholder={column ? t("comments.placeholder") : undefined}
        onChange={(event) => setDraft(event.target.value)}
        className={
          column
            ? // One line that grows with the text, so an empty composer is a
              // single row rather than a box waiting to be filled.
              "min-h-9 flex-1 resize-none rounded-lg bg-surface-3 px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--primary)] field-sizing-content"
            : "rounded-md border border-border bg-surface-2 p-2 text-sm text-ink"
        }
      />
      <Button
        type="submit"
        variant={column ? "ghost" : "primary"}
        disabled={post.isPending || draft.trim().length === 0}
      >
        {t("comments.post")}
      </Button>
    </form>
  );

  return (
    <section
      aria-labelledby="comments-heading"
      className={column ? "flex h-full min-h-0 flex-col gap-3" : "flex flex-col gap-4"}
    >
      <h2
        id="comments-heading"
        className={headingVisible ? "flex-none text-sm text-ink" : "visually-hidden"}
      >
        {t("comments.title", { count: comments.data?.length ?? 0 })}
      </h2>

      {/* In a column the composer is at the bottom, where a messaging surface
          puts it and where the thumb already is. In a panel it stays at the top,
          because the panel is read from the top and the list under it can be
          long. */}
      {column ? null : composer}

      {comments.isError ? (
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      ) : null}

      {comments.data !== undefined && comments.data.length === 0 ? (
        <p className="flex-none text-2xs text-ink-faint">{t("comments.empty")}</p>
      ) : null}

      <ul
        className={
          column
            ? "-mr-2 flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-2"
            : "flex flex-col gap-3"
        }
      >
        {(comments.data ?? []).map((comment) => {
          const who =
            comment.author ?? (comment.is_own ? t("comments.you") : t("comments.someone"));
          return (
            <li
              key={comment.id}
              className={
                column ? "flex gap-2.5" : "flex flex-col gap-2 rounded-md bg-surface-2 p-3"
              }
            >
              {/* An initial, not an avatar. There is no picture to show — the
                server sends no author at all under the default configuration —
                and a letter is enough to tell one column of remarks apart. */}
              {column ? (
                <span
                  aria-hidden="true"
                  className="grid size-8 flex-none place-items-center rounded-full bg-surface-3 text-2xs text-ink-muted"
                >
                  {who.slice(0, 1).toUpperCase()}
                </span>
              ) : null}

              <div className={column ? "flex min-w-0 flex-1 flex-col gap-1" : "contents"}>
                <div className="flex flex-wrap items-center gap-2">
                  {/* No name under the default configuration; the server simply
                  does not send one. `is_own` is what the buttons key off. */}
                  <span className="text-xs text-ink-muted">{who}</span>
                  {/* The comment carried a timestamp the panel never printed. In a
                  conversation, when something was said is half of reading it. */}
                  <span className="text-2xs text-ink-faint">
                    {format.relativeDate(new Date(comment.created_at))}
                  </span>
                  {comment.stars === null ? null : (
                    <Stars
                      value={comment.stars}
                      label={t("library.rating.value", { value: comment.stars, count: 1 })}
                    />
                  )}
                  {comment.state === "hidden" ? (
                    <span className="text-2xs text-[var(--pa-accent-300)]">
                      {t("comments.hidden")}
                    </span>
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

                <div
                  className={
                    column
                      ? "flex flex-wrap items-center gap-3 pt-0.5"
                      : "flex flex-wrap items-center gap-2"
                  }
                >
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
              </div>
            </li>
          );
        })}
      </ul>

      {column ? composer : null}
    </section>
  );
}
