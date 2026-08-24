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
import { messageForError, nextStepForError } from "../lib/api-error";
import { useOidcProviders } from "./oidc-providers";
import type { FromLocationState } from "./require-auth";
import { useLogin, useSession } from "./session";

export function LoginRoute(): JSX.Element {
  const { t } = useTranslation();
  const session = useSession();
  const login = useLogin();
  const location = useLocation();
  const providers = useOidcProviders();
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
            ever sends any, is never reachable from this component.

            Two sentences, not one. PRODUCT.md L59-60: "Errors state the cause
            and the next step, never just that something failed." The cause
            alone leaves a reader who typed the wrong password looking at a
            statement of fact with nothing to do about it, and the step for
            every code is already written and translated — this screen simply
            was not asking for it. */}
        {login.error !== null ? (
          <div
            className={"text-sm rounded-md bg-[var(--danger-weak)] px-3 py-2 text-ink"}
            role="alert"
          >
            <p>{messageForError(login.error)}</p>
            <p className="text-xs text-ink">{nextStepForError(login.error)}</p>
          </div>
        ) : null}

        <Button type="submit" loading={login.isPending}>
          {t("login.submit")}
        </Button>
      </form>

      {/* Absent until an administrator configures a provider (ADR 0016 ships
          this alongside local accounts), and absent again on a load that
          could not reach the server -- there is nothing an outage here would
          add to the local-login form above. */}
      {(providers.data ?? []).length === 0 ? null : (
        <section aria-labelledby="login-oidc-heading" className="flex flex-col gap-3">
          <h2 id="login-oidc-heading" className="text-sm text-ink-muted">
            {t("login.oidc.heading")}
          </h2>
          <ul className="flex flex-col gap-2">
            {(providers.data ?? []).map((provider) => (
              <li key={provider.id}>
                {/* A real navigation, not a client-side route: the answer is a
                    307 to the provider, which only the browser's own request
                    can follow. */}
                <a
                  className="flex items-center justify-center rounded-md border border-border-control bg-surface-2 px-3 py-2 text-sm text-ink hover:bg-surface-3"
                  href={`/api/auth/oidc/${provider.id}/login`}
                >
                  {t("login.oidc.signIn", { name: provider.name })}
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
