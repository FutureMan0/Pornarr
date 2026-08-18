/**
 * The route table.
 *
 * Two levels above every screen: `RequireAuth` decides whether anything renders
 * at all, and `AppShell` is the chrome everything renders inside. Nesting them
 * rather than repeating a guard per route is what makes "you cannot reach a
 * screen signed out" a property of the table instead of a convention.
 *
 * Every destination in `NAV_ITEMS` is answered by one of the routes below. The
 * table used to end with a `NAV_ITEMS`-derived placeholder for screens that had
 * not been written yet; now that all of them have, that spread would only
 * shadow the real routes with "not built", so it is gone.
 */
import type { RouteObject } from "react-router-dom";
import { Navigate, createBrowserRouter } from "react-router-dom";
import { LoginRoute } from "./auth/login-route";
import { RequireAuth } from "./auth/require-auth";
import { ForbiddenRoute, NotFoundRoute } from "./errors/route-errors";
import { DashboardRoute } from "./routes/admin/dashboard-route";
import { InvitesRoute } from "./routes/admin/invites-route";
import { ModerationRoute } from "./routes/admin/moderation-route";
import { QuarantineReviewRoute } from "./routes/admin/quarantine/quarantine-review-route";
import { ScanRoute } from "./routes/admin/scan-route";
import { TagsRoute } from "./routes/admin/tags-route";
import { CollectionDetailRoute, CollectionsRoute } from "./routes/collections/collections-route";
import { ContinueRoute } from "./routes/continue/continue-route";
import { FeedRoute } from "./routes/feed/feed-route";
import { JoinRoute } from "./routes/join/join-route";
import { LibraryRoute } from "./routes/library/library-route";
import { MediaDetailRoute } from "./routes/media/media-detail-route";
import { MonitorsRoute } from "./routes/monitors/monitors-route";
import { QueueRoute } from "./routes/queue/queue-route";
import { RecommendationsRoute } from "./routes/recommendations/recommendations-route";
import { RequestsRoute } from "./routes/requests/requests-route";
import { SearchRoute } from "./routes/search/search-route";
import { MetadataProvidersRoute } from "./routes/settings/metadata/metadata-providers-route";
import { PeersRoute } from "./routes/settings/peers/peers-route";
import { QualityProfilesRoute } from "./routes/settings/quality/quality-profiles-route";
import { RootFoldersRoute } from "./routes/settings/root-folders/root-folders-route";
import { SettingsLayout } from "./routes/settings/settings-layout";
import { SettingsRoute } from "./routes/settings/settings-route";
import { SetupGate } from "./routes/setup/setup-gate";
import { SetupRoute } from "./routes/setup/setup-route";
import { ShortsPlayerRoute } from "./routes/shorts/shorts-player-route";
import { ShortsRoute } from "./routes/shorts/shorts-route";
import { WatchlistRoute } from "./routes/watchlist/watchlist-route";
import { AppShell } from "./shell/app-shell";

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
      { path: "join/:token", element: <JoinRoute /> },
      {
        element: <RequireAuth />,
        children: [
          {
            element: <AppShell />,
            children: [
              { index: true, element: <Navigate to="/library" replace /> },
              { path: "admin", element: <DashboardRoute /> },
              { path: "admin/quarantine", element: <QuarantineReviewRoute /> },
              { path: "admin/moderation", element: <ModerationRoute /> },
              { path: "admin/scan", element: <ScanRoute /> },
              { path: "admin/tags", element: <TagsRoute /> },
              { path: "admin/invites", element: <InvitesRoute /> },
              // The server's own settings are the index of the section, not a
              // sibling of it: `/settings` has to answer with something, and
              // bouncing it to root folders is what made every other section
              // unreachable by navigation in the first place.
              {
                path: "settings",
                element: <SettingsLayout />,
                children: [
                  { index: true, element: <SettingsRoute /> },
                  { path: "root-folders", element: <RootFoldersRoute /> },
                  { path: "quality", element: <QualityProfilesRoute /> },
                  { path: "metadata", element: <MetadataProvidersRoute /> },
                  { path: "peers", element: <PeersRoute /> },
                ],
              },
              { path: "search", element: <SearchRoute /> },
              { path: "monitors", element: <MonitorsRoute /> },
              { path: "requests", element: <RequestsRoute /> },
              { path: "recommendations", element: <RecommendationsRoute /> },
              { path: "downloads", element: <QueueRoute /> },
              { path: "feed", element: <FeedRoute /> },
              { path: "continue", element: <ContinueRoute /> },
              { path: "shorts", element: <ShortsRoute /> },
              { path: "shorts/:shortId", element: <ShortsPlayerRoute /> },
              { path: "collections", element: <CollectionsRoute /> },
              { path: "collections/:collectionId", element: <CollectionDetailRoute /> },
              { path: "watchlist", element: <WatchlistRoute /> },
              { path: "library", element: <LibraryRoute /> },
              { path: "library/:mediaId", element: <MediaDetailRoute /> },
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
