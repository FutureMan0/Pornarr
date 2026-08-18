/**
 * Where signing in puts you, which depends on why you signed in.
 *
 * Everybody landed on the library. That is the right answer for nobody: a
 * household member opens this to watch something, and an administrator opens it
 * because something needs attention.
 *
 * A GUEST LANDS ON THE FEED. What is new, what somebody sent them, what they
 * were part-way through — the screen that answers "what should I watch" without
 * being asked. The library is a place to look something up, which is a different
 * question and one tap away.
 *
 * AN ADMINISTRATOR LANDS ON THE DASHBOARD. Disk, queue, failures, what the
 * scanner is doing. Maintenance is why the account exists; if they came to watch
 * instead, the library is one tap away for them too.
 *
 * This is safe to read the role here because `RequireAuth` above does not render
 * its children until the session query has answered — so there is no first frame
 * where the role is unknown and no double redirect.
 */
import type { JSX } from "react";
import { Navigate } from "react-router-dom";

import { useSession } from "../auth/session";

export function HomeRedirect(): JSX.Element {
  const session = useSession();
  const isAdmin = session.data?.role === "admin";
  return <Navigate to={isAdmin ? "/admin" : "/feed"} replace />;
}
