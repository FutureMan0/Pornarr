/**
 * Invitations, from the side that issues them.
 *
 * THE LINK IS SHOWN ONCE, AND THE SCREEN SAYS SO. The server returns the token
 * exactly once and stores only its hash, so the moment this component drops the
 * value there is no way to recover it. A panel that quietly loses a link an
 * administrator has not copied yet is the whole failure mode here, which is why
 * the fresh link sits in its own block with a copy button rather than in a
 * toast.
 *
 * A USED INVITATION IS A RECORD, NOT RUBBISH. It stays in the list naming who
 * joined through it. That is usually the only answer to "how did this account
 * get here", and the server refuses to delete it for the same reason.
 */
import { Button, Input } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FormEvent, JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormat } from "../../i18n/format";
import { getApiClient } from "../../lib/api";
import { apiFailure, messageForError } from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";

/**
 * How long a fresh link lives. The same figure the server defaults to, sent
 * explicitly because the generated contract marks the field required — a
 * property with a default is not an optional one to `openapi-typescript`. The
 * server still caps it, so this is a preference rather than a policy.
 */
const VALID_DAYS = 7;

interface Fresh {
  readonly url: string;
  readonly note: string | null;
}

export function InvitesRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const cache = useQueryClient();
  const [note, setNote] = useState("");
  const [fresh, setFresh] = useState<Fresh | null>(null);
  const [copied, setCopied] = useState(false);

  const invites = useQuery({
    queryKey: ["admin", "invites"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/invites");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const create = useMutation({
    mutationFn: async (value: string) => {
      const { data, error, response } = await getApiClient().POST("/api/admin/invites", {
        body: {
          valid_days: VALID_DAYS,
          ...(value.trim() === "" ? {} : { note: value.trim() }),
        },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
    onSuccess: (data) => {
      setNote("");
      setCopied(false);
      // Built here rather than by the server: the server has no idea what
      // address this household is reached on, and guessing one would produce a
      // link that works from the machine it was generated on and nowhere else.
      setFresh({ url: `${window.location.origin}/join/${data.token}`, note: data.note });
      void cache.invalidateQueries({ queryKey: ["admin", "invites"] });
    },
  });

  const revoke = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().DELETE("/api/admin/invites/{invite_id}", {
        params: { path: { invite_id: id } },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: ["admin", "invites"] }),
  });

  usePageTitle(
    t("invites.title"),
    invites.data === undefined
      ? undefined
      : t("invites.subtitle", {
          count: invites.data.filter((invite) => invite.redeemed_at === null).length,
        }),
  );

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    create.mutate(note);
  };

  return (
    <div className="flex max-w-[46rem] flex-col gap-6">
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        <span className="flex min-w-[16rem] flex-1 flex-col gap-1">
          <label htmlFor="invite-note" className="text-xs text-ink-muted">
            {t("invites.note")}
          </label>
          <span className="text-2xs text-ink-faint">{t("invites.noteHint")}</span>
          <Input
            id="invite-note"
            value={note}
            maxLength={128}
            onChange={(event) => setNote(event.target.value)}
          />
        </span>
        <Button type="submit" disabled={create.isPending}>
          {t("invites.create")}
        </Button>
      </form>

      {create.error === null ? null : (
        <p role="alert" className="text-sm text-ink">
          {messageForError(create.error)}
        </p>
      )}

      {fresh === null ? null : (
        <section
          aria-labelledby="fresh-heading"
          className="flex flex-col gap-2 rounded-lg border border-[var(--primary)] bg-[color-mix(in_oklch,var(--primary)_8%,transparent)] p-4"
        >
          <h2 id="fresh-heading" className="text-sm text-ink">
            {t("invites.freshTitle")}
          </h2>
          {/* Stated plainly, because it is true and irreversible. */}
          <p className="text-2xs text-ink-muted">{t("invites.freshHint")}</p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-md bg-surface-2 px-2 py-1 font-mono text-2xs text-ink">
              {fresh.url}
            </code>
            <Button
              variant="secondary"
              onClick={() => {
                void navigator.clipboard?.writeText(fresh.url).then(() => setCopied(true));
              }}
            >
              {copied ? t("invites.copied") : t("invites.copy")}
            </Button>
          </div>
        </section>
      )}

      <section aria-labelledby="invites-heading" className="flex flex-col gap-3">
        <h2 id="invites-heading" className="text-sm text-ink">
          {t("invites.existing")}
        </h2>

        {invites.isError ? (
          <p role="alert" className="text-sm text-ink">
            {t("errors.generic")}
          </p>
        ) : invites.data === undefined ? (
          <p className="text-sm text-ink-muted">{t("invites.loading")}</p>
        ) : invites.data.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("invites.empty")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {invites.data.map((invite) => {
              const used = invite.redeemed_at !== null;
              const expired = !used && new Date(invite.expires_at) <= new Date();
              return (
                <li
                  key={invite.id}
                  className="flex flex-wrap items-center gap-3 rounded-md bg-surface-2 px-3 py-2"
                >
                  <span className="min-w-0 flex-1 text-sm text-ink">
                    {invite.note ?? t("invites.unnamed")}
                  </span>
                  <span className="text-2xs text-ink-muted">
                    {used
                      ? t("invites.usedBy", { name: invite.redeemed_username ?? "—" })
                      : expired
                        ? t("invites.expired")
                        : t("invites.expires", {
                            when: format.relativeDate(new Date(invite.expires_at)),
                          })}
                  </span>
                  {used ? null : (
                    <Button
                      variant="ghost"
                      disabled={revoke.isPending}
                      onClick={() => revoke.mutate(invite.id)}
                    >
                      {t("invites.revoke")}
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
