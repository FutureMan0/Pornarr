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

function NotFound(): JSX.Element {
  const { t } = useTranslation();
  return (
    <section className="flex flex-col gap-2">
      <h1 className={"text-lg text-ink"}>{t("screen.notFoundTitle")}</h1>
      <p className={"text-sm text-ink-muted"}>{t("screen.notFoundBody")}</p>
    </section>
  );
}

export const appRoutes: RouteObject[] = [
  { path: "/login", element: <LoginRoute /> },
  {
    path: "/",
    element: <RequireAuth />,
    children: [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <Navigate to="/library" replace /> },
          ...NAV_ITEMS.map((item) => ({
            path: item.path.slice(1),
            element: <Placeholder navId={item.id} />,
          })),
          { path: "*", element: <NotFound /> },
        ],
      },
    ],
  },
];

export function createAppRouter(): ReturnType<typeof createBrowserRouter> {
  return createBrowserRouter(appRoutes);
}
