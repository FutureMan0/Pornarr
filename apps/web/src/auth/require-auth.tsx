/**
 * The authenticated boundary.
 *
 * One place decides whether a route may render, and it decides from the session
 * query alone. That is what makes the 401 middleware in `lib/api.ts` sufficient:
 * emptying the session query is enough to move the whole application to the
 * login screen, with no imperative navigation and no `window.location`.
 */
import { Button, SkeletonRegion, SkeletonText } from "@pornarr/ui";
import type { JSX } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { messageForError } from "../lib/api-error";
import { useSession } from "./session";

/** Where `login-route` reads the destination to return to after signing in. */
export interface FromLocationState {
  readonly from?: string;
}

export function RequireAuth(): JSX.Element {
  const session = useSession();
  const location = useLocation();

  // The first `/api/auth/me` has not answered yet. Rendering the login screen
  // here would flash it at every already-signed-in user on every cold load,
  // which reads as being logged out.
  if (session.isPending) {
    return (
      <div className="p-6">
        <SkeletonRegion label="Checking your session">
          <SkeletonText lines={3} />
        </SkeletonRegion>
      </div>
    );
  }

  // An unavailable API is not evidence that the user signed out. Keep the
  // authenticated boundary closed, explain the failure, and let the visitor
  // retry without throwing away the route they were trying to reach.
  if (session.isError) {
    return (
      <main className="mx-auto flex max-w-[calc(var(--space-16)*6)] flex-col gap-6 p-6">
        <h1 className="text-lg text-ink">Could not check your session</h1>
        <p role="alert" className="text-sm text-ink-muted">
          {messageForError(session.error)}
        </p>
        <div>
          <Button onClick={() => void session.refetch()}>Try again</Button>
        </div>
      </main>
    );
  }

  // `null` is the one confirmed signed-out state. It alone reaches login.
  if (session.data === null) {
    const state: FromLocationState = { from: `${location.pathname}${location.search}` };
    return <Navigate to="/login" replace state={state} />;
  }

  return <Outlet />;
}
