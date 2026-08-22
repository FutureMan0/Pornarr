import type { paths } from "@pornarr/api-client";
/**
 * Hand this title to somebody in the household.
 *
 * The feed's "Sent to you" shelf reads `GET /api/recommendations/sent-to-me`
 * and can mark one seen, and nothing anywhere could create a send: `POST
 * /api/sends` had no caller, so the shelf was a permanent empty state. This is
 * the other end of it.
 *
 * A disclosure beside the watchlist and collection controls rather than a
 * modal, and the note is optional - the point of handing somebody a title by
 * hand is usually the title, occasionally a sentence about why.
 */
import { Button, Input, Select } from "@pornarr/ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { FormEvent, JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useSession } from "../../auth/session";
import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";

type Member =
  paths["/api/admin/users"]["get"]["responses"][200]["content"]["application/json"][number];

export function SendToMember({ mediaId }: { readonly mediaId: string }): JSX.Element | null {
  const { t } = useTranslation();
  const session = useSession();
  const [open, setOpen] = useState(false);
  const [recipient, setRecipient] = useState("");
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);

  // The only list of people this server publishes is the administrator's.
  // A member can still be sent to - the send route takes any recipient id -
  // but until there is a household directory for everybody, the control is
  // offered to whoever can see who is here.
  const isAdmin = session.data?.role === "admin";

  const members = useQuery({
    queryKey: ["admin", "users"],
    enabled: open && isAdmin,
    queryFn: async (): Promise<Member[]> => {
      const { data, error, response } = await getApiClient().GET("/api/admin/users");
      if (!data || error) throw apiFailure(error, response);
      return data.filter((member) => member.is_active && member.id !== session.data?.id);
    },
  });

  const send = useMutation({
    mutationFn: async () => {
      const { error, response } = await getApiClient().POST("/api/sends", {
        body: {
          media_id: mediaId,
          recipient_id: recipient,
          note: note.trim() === "" ? null : note.trim(),
        },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => {
      setSent(true);
      setOpen(false);
      setNote("");
    },
  });

  if (!isAdmin) return null;

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (recipient === "") return;
    send.mutate();
  };

  const people = members.data ?? [];

  return (
    <div className="flex flex-col items-start gap-2">
      <Button variant="secondary" aria-expanded={open} onClick={() => setOpen(!open)}>
        {t("sends.send")}
      </Button>
      {/* Said once, where the action was, and not as a banner that outlives
          the moment: the confirmation is about the click that just happened. */}
      {sent && !open ? (
        <output className="block text-2xs text-ink-faint">{t("sends.sentConfirmation")}</output>
      ) : null}

      {open ? (
        <form
          className="flex min-w-[16rem] flex-col gap-3 rounded-lg border border-border bg-surface p-3"
          onSubmit={submit}
        >
          <div className="flex flex-col gap-2">
            <label className="text-sm text-ink-muted" htmlFor="send-recipient">
              {t("sends.recipient")}
            </label>
            {members.isPending ? (
              <p className="text-sm text-ink-muted">{t("sends.loadingMembers")}</p>
            ) : people.length === 0 ? (
              <p className="text-sm text-ink-muted">{t("sends.nobodyElse")}</p>
            ) : (
              <Select
                id="send-recipient"
                value={recipient}
                onChange={(event) => setRecipient(event.target.value)}
              >
                <option value="">{t("sends.choose")}</option>
                {people.map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.display_name ?? member.username}
                  </option>
                ))}
              </Select>
            )}
          </div>
          <div className="flex flex-col gap-2">
            <label className="text-sm text-ink-muted" htmlFor="send-note">
              {t("sends.note")}
            </label>
            <Input
              id="send-note"
              value={note}
              placeholder={t("sends.notePlaceholder")}
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
          {send.isError ? (
            <p role="alert" className="text-sm text-ink">
              {t("errors.generic")}
            </p>
          ) : null}
          <div className="flex gap-2">
            <Button type="submit" loading={send.isPending} disabled={recipient === ""}>
              {t("sends.confirm")}
            </Button>
            <Button variant="ghost" type="button" onClick={() => setOpen(false)}>
              {t("sends.cancel")}
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}
