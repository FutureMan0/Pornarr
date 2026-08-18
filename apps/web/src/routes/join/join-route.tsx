/**
 * B5 — joining a household you were invited to.
 *
 * OUTSIDE THE SHELL. Whoever opens this has no account, so there is no
 * sidebar, no top bar and nothing to navigate to. It is a page, not a screen.
 *
 * THE LINK IS CHECKED BEFORE ANYTHING IS TYPED. A form that accepts a password
 * and then says "this link expired" has taken a password for nothing — and on a
 * shared machine, into a field somebody may have autofilled.
 *
 * WHAT IT DOES NOT SAY. The design's heading is "You've been invited to Kai's
 * library". The server does not send that and deliberately so: whoever holds
 * the URL might have found it in a browser history rather than been given it,
 * and naming the household to them is the disclosure the token was supposed to
 * gate. The page says what it can — that the invitation is good — and no more.
 *
 * The design's language, subtitles and artwork choices are not here. Language
 * is already a control in the top bar, artwork visibility is per-device and
 * flips from there, and subtitle preferences have no endpoint at all. Asking
 * for three settings on the way in and then storing none of them would be a
 * form that pretends.
 */
import { Button, Input } from "@pornarr/ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { FormEvent, JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";

import { useFormat } from "../../i18n/format";
import { getApiClient } from "../../lib/api";
import { apiFailure, messageForError } from "../../lib/api-error";

/** The server refuses anything shorter; saying so first saves a round trip. */
const MINIMUM_PASSWORD = 12;

export function JoinRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const navigate = useNavigate();
  const { token = "" } = useParams();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");

  const invite = useQuery({
    queryKey: ["invite", token],
    // A dead link does not become alive on a retry, and each attempt is a
    // request from somebody who is not yet a guest.
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/invites/{token}", {
        params: { path: { token } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const join = useMutation({
    mutationFn: async () => {
      const { error, response } = await getApiClient().POST("/api/invites/{token}/redeem", {
        params: { path: { token } },
        body: {
          username: username.trim(),
          password,
          ...(displayName.trim() === "" ? {} : { display_name: displayName.trim() }),
        },
      });
      if (error) throw apiFailure(error, response);
    },
    // Straight to the login screen with the name filled in: the account exists
    // but no session does, and signing somebody in from a redemption would mean
    // a second way to mint a session.
    onSuccess: () => navigate(`/login?username=${encodeURIComponent(username.trim())}`),
  });

  const tooShort = password.length > 0 && password.length < MINIMUM_PASSWORD;
  const ready = username.trim().length > 0 && password.length >= MINIMUM_PASSWORD;

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    if (ready) join.mutate();
  };

  return (
    <main className="mx-auto flex min-h-full w-full max-w-[34rem] flex-col justify-center gap-6 p-6">
      <h1 className="text-xl text-ink">{t("join.title")}</h1>

      {invite.isPending ? (
        <p className="text-sm text-ink-muted">{t("join.checking")}</p>
      ) : invite.isError || invite.data?.valid !== true ? (
        <div className="flex flex-col gap-2 rounded-lg border border-border-control bg-surface-2 p-4">
          {/* Said before the form, not after it: a page that takes a password
              and then refuses it has taken a password for nothing. */}
          <p role="alert" className="text-sm text-ink">
            {t("join.unusable")}
          </p>
          <p className="text-xs text-ink-muted">{t("join.unusableHint")}</p>
        </div>
      ) : (
        <>
          <p className="text-sm text-ink-muted">
            {t("join.intro")}
            {invite.data.expires_at === null
              ? ""
              : ` ${t("join.expires", {
                  when: format.relativeDate(new Date(invite.data.expires_at)),
                })}`}
          </p>

          <form onSubmit={submit} className="flex flex-col gap-4">
            <span className="flex flex-col gap-1">
              <label htmlFor="join-username" className="text-sm text-ink">
                {t("join.username")}
              </label>
              <span className="text-2xs text-ink-muted">{t("join.usernameHint")}</span>
              <Input
                id="join-username"
                autoComplete="username"
                value={username}
                maxLength={64}
                onChange={(event) => setUsername(event.target.value)}
              />
            </span>

            <span className="flex flex-col gap-1">
              <label htmlFor="join-display-name" className="text-sm text-ink">
                {t("join.displayName")}
              </label>
              <span className="text-2xs text-ink-muted">{t("join.displayNameHint")}</span>
              <Input
                id="join-display-name"
                value={displayName}
                maxLength={64}
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </span>

            <span className="flex flex-col gap-1">
              <label htmlFor="join-password" className="text-sm text-ink">
                {t("join.password")}
              </label>
              <span className="text-2xs text-ink-muted">
                {t("join.passwordHint", { count: MINIMUM_PASSWORD })}
              </span>
              <Input
                id="join-password"
                type="password"
                autoComplete="new-password"
                value={password}
                error={tooShort ? t("join.tooShort", { count: MINIMUM_PASSWORD }) : false}
                onChange={(event) => setPassword(event.target.value)}
              />
            </span>

            {join.error === null ? null : (
              <p role="alert" className="text-sm text-ink">
                {messageForError(join.error)}
              </p>
            )}

            <Button type="submit" disabled={!ready || join.isPending}>
              {t("join.enter")}
            </Button>
          </form>
        </>
      )}
    </main>
  );
}
