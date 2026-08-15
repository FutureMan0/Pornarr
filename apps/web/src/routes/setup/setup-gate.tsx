/** Redirect an unconfigured instance to the only route it may use. */
import { SkeletonRegion, SkeletonText } from "@pornarr/ui";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, Outlet } from "react-router-dom";
import { ErrorScreen } from "../../errors/error-screen";
import { isRetryableError, messageForError, nextStepForError } from "../../lib/api-error";
import { useSetupStatus } from "./setup";

export function SetupGate(): JSX.Element {
  const { t } = useTranslation();
  const setup = useSetupStatus();

  if (setup.isPending) {
    return (
      <main className="p-6">
        <SkeletonRegion label={t("setup.checking")}>
          <SkeletonText lines={3} />
        </SkeletonRegion>
      </main>
    );
  }

  if (setup.isError) {
    return (
      <main className="p-6">
        <ErrorScreen
          title={messageForError(setup.error)}
          nextStep={nextStepForError(setup.error)}
          onRetry={
            isRetryableError(setup.error)
              ? () => {
                  void setup.refetch();
                }
              : undefined
          }
        />
      </main>
    );
  }

  return setup.data.configured ? <Outlet /> : <Navigate to="/setup" replace />;
}
