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
import { Navigate, Outlet, useLocation } from "react-router-dom";
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

  // `== null` on purpose: null is "asked, not signed in", and undefined here
  // means the request itself failed. Neither is a session, and an unreachable
  // API must not leave a skeleton on screen forever.
  if (session.data == null) {
    const state: FromLocationState = { from: `${location.pathname}${location.search}` };
    return <Navigate to="/login" replace state={state} />;
  }

  return <Outlet />;
}
