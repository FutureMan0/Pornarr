/**
 * The two addresses that are not a failure of the software: nothing here, and
 * not yours.
 *
 * Both are `EmptyState`, which refuses to be built without an action. That is
 * the right constraint for exactly these screens — a 404 that says "not found"
 * and stops is the canonical dead end — and it means the destination is part of
 * the component's type rather than something a reviewer has to notice is
 * missing.
 *
 * The library is the destination in both cases because it is the application's
 * index route and the one screen that is always reachable by any signed-in
 * account.
 */
import { EmptyState } from "@pornarr/ui";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { NAV_ITEMS } from "../shell/sidebar";

/**
 * Read from the nav table so the link cannot outlive the route it points at —
 * but looked up by identity, not by position. The table is ordered for the
 * sidebar, and that order has already changed once; the library is the index
 * route regardless of where it happens to sit in the list.
 */
const LIBRARY_PATH = NAV_ITEMS.find((item) => item.id === "library")?.path ?? "/library";

export function NotFoundRoute(): JSX.Element {
  const { t } = useTranslation();

  return (
    <EmptyState
      title={t("screen.notFoundTitle")}
      body={t("screen.notFoundBody")}
      action={{ label: t("screen.backToLibrary"), href: LIBRARY_PATH }}
    />
  );
}

/**
 * 403. Reachable as a route so that a screen which learns it may not show its
 * data has somewhere to send the reader, rather than rendering an empty table.
 */
export function ForbiddenRoute(): JSX.Element {
  const { t } = useTranslation();

  return (
    <EmptyState
      title={t("screen.forbiddenTitle")}
      body={t("screen.forbiddenBody")}
      action={{ label: t("screen.backToLibrary"), href: LIBRARY_PATH }}
    />
  );
}
