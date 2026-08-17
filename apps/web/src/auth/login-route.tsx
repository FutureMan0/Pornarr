/**
 * The login screen.
 *
 * There is no navigation call on success. The mutation writes the session query,
 * the redirect below sees a user, and the router moves — the same path a cold
 * load with a live cookie takes. One rule for "you are signed in, go inside" is
 * easier to keep true than two.
 */
import { Button, Input } from "@pornarr/ui";
import type { FormEvent, JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation, useSearchParams } from "react-router-dom";
import { messageForError } from "../lib/api-error";
import type { FromLocationState } from "./require-auth";
import { useLogin, useSession } from "./session";

export function LoginRoute(): JSX.Element {
  const { t } = useTranslation();
  const session = useSession();
  const login = useLogin();
  const location = useLocation();
  // Prefilled after joining: `/join` sends the name that was just chosen, so
  // the first thing a new guest sees is not an empty field asking them to
  // remember what they typed thirty seconds ago. Read once as the initial
  // value — after that the field is theirs.
  const [params] = useSearchParams();
  const [username, setUsername] = useState(() => params.get("username") ?? "");
  const [password, setPassword] = useState("");

  const state = location.state as FromLocationState | null;
  const destination = state?.from ?? "/";

  if (session.data != null) return <Navigate to={destination} replace />;

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    login.mutate({ username, password });
  };

  return (
    <main className="mx-auto flex min-h-full w-full max-w-[calc(var(--space-16)*6)] flex-col justify-center gap-6 p-6">
      <div className="flex flex-col gap-2">
        <h1 className={"text-lg text-ink"}>{t("app.name")}</h1>
        <p className={"text-sm text-ink-muted"}>{t("login.subtitle")}</p>
      </div>

      <form className="flex flex-col gap-4" onSubmit={onSubmit}>
        <div className="flex flex-col gap-2">
          <label className={"text-sm text-ink-muted"} htmlFor="login-username">
            {t("login.username")}
          </label>
          <Input
            id="login-username"
            name="username"
            autoComplete="username"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </div>

        <div className="flex flex-col gap-2">
          <label className={"text-sm text-ink-muted"} htmlFor="login-password">
            {t("login.password")}
          </label>
          <Input
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>

        {/* The code is mapped to a sentence here; the server's own prose, if it
            ever sends any, is never reachable from this component. */}
        {login.error !== null ? (
          <p
            className={"text-sm rounded-md bg-[var(--danger-weak)] px-3 py-2 text-ink"}
            role="alert"
          >
            {messageForError(login.error)}
          </p>
        ) : null}

        <Button type="submit" loading={login.isPending}>
          {t("login.submit")}
        </Button>
      </form>
    </main>
  );
}
