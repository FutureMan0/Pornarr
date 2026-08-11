/**
 * The authenticated boundary.
 *
 * One place decides whether a route may render, and it decides from the session
 * query alone. That is what makes the 401 middleware in `lib/api.ts` sufficient:
 * emptying the session query is enough to move the whole application to the
 * login screen, with no imperative navigation and no `window.location`.
 */
import { SkeletonRegion, SkeletonText } from "@pornarr/ui";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { ErrorScreen } from "../errors/error-screen";
import { isRetryableError, messageForError, nextStepForError } from "../lib/api-error";
import { useSession } from "./session";

/** Where `login-route` reads the destination to return to after signing in. */
export interface FromLocationState {
  readonly from?: string;
}

export function RequireAuth(): JSX.Element {
  const { t } = useTranslation();
  const session = useSession();
  const location = useLocation();

  // The first `/api/auth/me` has not answered yet. Rendering the login screen
  // here would flash it at every already-signed-in user on every cold load,
  // which reads as being logged out.
  if (session.isPending) {
    return (
      <div className="p-6">
        <SkeletonRegion label={t("session.checking")}>
          <SkeletonText lines={3} />
        </SkeletonRegion>
      </div>
    );
  }

  // A 401 resolves to null rather than rejecting (see `session.ts`), so an error
  // here is never "signed out" — it is an API that could not answer. Sending
  // that to the login screen would tell the user to sign in again, which is a
  // lie about the cause and a next step that cannot work. State the cause and,
  // when repeating the request could help, offer to repeat it.
  if (session.isError) {
    return (
      <div className="p-6">
        <ErrorScreen
          title={messageForError(session.error)}
          nextStep={nextStepForError(session.error)}
          onRetry={
            isRetryableError(session.error)
              ? () => {
                  void session.refetch();
                }
              : undefined
          }
        />
      </div>
    );
  }

  // `null` is the one confirmed signed-out state. It alone reaches login.
  if (session.data === null) {
    const state: FromLocationState = { from: `${location.pathname}${location.search}` };
    return <Navigate to="/login" replace state={state} />;
  }

  return <Outlet />;
}
