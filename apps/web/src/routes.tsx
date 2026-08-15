/**
 * The route table.
 *
 * Two levels above every screen: `RequireAuth` decides whether anything renders
 * at all, and `AppShell` is the chrome everything renders inside. Nesting them
 * rather than repeating a guard per route is what makes "you cannot reach a
 * screen signed out" a property of the table instead of a convention.
 *
 * The destinations come from `NAV_ITEMS`, so a link in the sidebar and a route
 * that answers it cannot fall out of step. The screens behind them belong to
 * later issues; what stands there now says so plainly rather than 404ing.
 */
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import type { RouteObject } from "react-router-dom";
import { Navigate, createBrowserRouter } from "react-router-dom";
import { LoginRoute } from "./auth/login-route";
import { RequireAuth } from "./auth/require-auth";
import { ForbiddenRoute, NotFoundRoute } from "./errors/route-errors";
import { QuarantineReviewRoute } from "./routes/admin/quarantine/quarantine-review-route";
import { SearchRoute } from "./routes/search/search-route";
import { QualityProfilesRoute } from "./routes/settings/quality/quality-profiles-route";
import { SetupGate } from "./routes/setup/setup-gate";
import { SetupRoute } from "./routes/setup/setup-route";
import { AppShell } from "./shell/app-shell";
import { NAV_ITEMS, type NavId } from "./shell/sidebar";

/** Titled from the destination's own key, so it reads the same as its link. */
function Placeholder({ navId }: { readonly navId: NavId }): JSX.Element {
  const { t } = useTranslation();
  return (
    <section className="flex flex-col gap-2">
      <h1 className={"text-lg text-ink"}>{t(`nav.${navId}`)}</h1>
      <p className={"text-sm text-ink-muted"}>{t("screen.notBuilt")}</p>
    </section>
  );
}

/**
 * `/forbidden` is a real address rather than a component a screen renders in
 * place, because "you may not see this" has to survive a reload and be something
 * a link can point at — a state held only in a component's memory is neither.
 */
export const FORBIDDEN_PATH = "/forbidden";

export const appRoutes: RouteObject[] = [
  { path: "/setup", element: <SetupRoute /> },
  {
    path: "/",
    element: <SetupGate />,
    children: [
      { path: "login", element: <LoginRoute /> },
      {
        element: <RequireAuth />,
        children: [
          {
            element: <AppShell />,
            children: [
              { index: true, element: <Navigate to="/library" replace /> },
              { path: "admin/quarantine", element: <QuarantineReviewRoute /> },
              { path: "settings", element: <Navigate to="/settings/quality" replace /> },
              { path: "settings/quality", element: <QualityProfilesRoute /> },
              { path: "search", element: <SearchRoute /> },
              ...NAV_ITEMS.filter((item) => item.id !== "settings").map((item) => ({
                path: item.path.slice(1),
                element: <Placeholder navId={item.id} />,
              })),
              { path: FORBIDDEN_PATH.slice(1), element: <ForbiddenRoute /> },
              { path: "*", element: <NotFoundRoute /> },
            ],
          },
        ],
      },
    ],
  },
];

export function createAppRouter(): ReturnType<typeof createBrowserRouter> {
  return createBrowserRouter(appRoutes);
}
