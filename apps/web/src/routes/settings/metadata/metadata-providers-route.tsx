/**
 * Metadata providers: where an operator's StashDB or TPDB key goes.
 *
 * The import cascade has always taken provider adapters and there was nowhere
 * to store a key, so every import fell back to the file name. Stash calls this
 * screen Scrapers and Radarr calls it Metadata; either way it is the screen
 * that decides whether an imported file gets a real title, studio, performers
 * and tags, or the name whoever packed it happened to choose.
 *
 * The key is written and never read back: the response carries everything about
 * a provider except its secret.
 */
import { Button, Input, Select, SkeletonRegion, SkeletonText } from "@pornarr/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { FormEvent, JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate } from "react-router-dom";
import { useSession } from "../../../auth/session";
import { ErrorScreen } from "../../../errors/error-screen";
import { getApiClient } from "../../../lib/api";
import {
  type ApiRequestError,
  apiFailure,
  isRetryableError,
  messageForError,
  nextStepForError,
} from "../../../lib/api-error";
import {
  METADATA_PROVIDERS_KEY,
  type MetadataProvider,
  type MetadataProviderWrite,
  useMetadataProviders,
} from "./metadata-providers";

const IMPLEMENTATIONS = ["stashdb", "tpdb"] as const;
type Implementation = (typeof IMPLEMENTATIONS)[number];

function MetadataLoading(): JSX.Element {
  const { t } = useTranslation();
  return (
    <SkeletonRegion label={t("metadataProviders.loading")}>
      <div className="border border-border bg-surface p-6">
        <SkeletonText lines={4} />
      </div>
    </SkeletonRegion>
  );
}

export function MetadataProvidersRoute(): JSX.Element {
  const { t } = useTranslation();
  const session = useSession();
  const queryClient = useQueryClient();
  const providersQuery = useMetadataProviders();
  const [implementation, setImplementation] = useState<Implementation>("stashdb");
  const [endpoint, setEndpoint] = useState("");
  const [apiKey, setApiKey] = useState("");

  const configure = useMutation<MetadataProvider, ApiRequestError, MetadataProviderWrite>({
    mutationFn: async (body) => {
      const { data, error, response } = await getApiClient().POST("/api/admin/metadata-providers", {
        body,
      });
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: () => {
      setApiKey("");
      setEndpoint("");
      void queryClient.invalidateQueries({ queryKey: METADATA_PROVIDERS_KEY });
    },
  });

  const remove = useMutation<void, ApiRequestError, string>({
    mutationFn: async (providerId) => {
      const { error, response } = await getApiClient().DELETE(
        "/api/admin/metadata-providers/{provider_id}",
        { params: { path: { provider_id: providerId } } },
      );
      if (error !== undefined) throw apiFailure(error, response);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: METADATA_PROVIDERS_KEY });
    },
  });

  if (session.data?.role !== "admin" && session.data !== undefined)
    return <Navigate to="/forbidden" replace />;
  if (providersQuery.error !== null) {
    return (
      <ErrorScreen
        title={messageForError(providersQuery.error)}
        nextStep={nextStepForError(providersQuery.error)}
        onRetry={
          isRetryableError(providersQuery.error)
            ? () => void queryClient.invalidateQueries({ queryKey: METADATA_PROVIDERS_KEY })
            : undefined
        }
      />
    );
  }
  if (providersQuery.isPending) return <MetadataLoading />;

  const providers = providersQuery.data ?? [];
  const mutationError = configure.error ?? remove.error ?? null;
  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (apiKey.trim() === "") return;
    configure.mutate({
      implementation,
      api_key: apiKey.trim(),
      endpoint: endpoint.trim() === "" ? null : endpoint.trim(),
      priority: 0,
      enabled: true,
    });
  };

  return (
    <section className="flex flex-col gap-6" aria-labelledby="metadata-providers-heading">
      <header className="max-w-[70ch]">
        <h1 id="metadata-providers-heading" className="text-xl text-ink">
          {t("metadataProviders.title")}
        </h1>
        <p className="mt-2 text-sm text-ink-muted">{t("metadataProviders.intro")}</p>
      </header>

      {mutationError === null ? null : (
        <ErrorScreen
          title={messageForError(mutationError)}
          nextStep={nextStepForError(mutationError)}
        />
      )}

      <section
        className="border border-border bg-surface p-6"
        aria-labelledby="metadata-providers-list"
      >
        <h2 id="metadata-providers-list" className="text-lg text-ink">
          {t("metadataProviders.configured")}
        </h2>
        {providers.length === 0 ? (
          <p className="mt-4 max-w-[70ch] text-sm text-ink-muted">{t("metadataProviders.none")}</p>
        ) : (
          <ul className="mt-4 flex flex-col gap-3">
            {providers.map((provider) => (
              <li
                key={provider.id}
                className="flex flex-wrap items-center justify-between gap-3 border border-border-control bg-surface-2 p-4"
              >
                <div className="min-w-0">
                  <h3 className="text-md text-ink">
                    {t(`metadataProviders.implementations.${provider.implementation}`, {
                      defaultValue: provider.implementation,
                    })}
                  </h3>
                  <p className="break-all text-sm text-ink-muted">
                    {provider.endpoint ?? t("metadataProviders.defaultEndpoint")}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  aria-label={t("metadataProviders.removeProvider", {
                    name: provider.implementation,
                  })}
                  onClick={() => remove.mutate(provider.id)}
                >
                  {t("metadataProviders.remove")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <form
        className="flex flex-col gap-4 border border-border bg-surface p-6"
        aria-labelledby="metadata-providers-add"
        onSubmit={submit}
      >
        <h2 id="metadata-providers-add" className="text-lg text-ink">
          {t("metadataProviders.add")}
        </h2>
        <div className="flex flex-col gap-2">
          <label className="text-sm text-ink-muted" htmlFor="metadata-implementation">
            {t("metadataProviders.provider")}
          </label>
          <Select
            id="metadata-implementation"
            value={implementation}
            onChange={(event) => setImplementation(event.target.value as Implementation)}
          >
            {IMPLEMENTATIONS.map((value) => (
              <option key={value} value={value}>
                {t(`metadataProviders.implementations.${value}`)}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-col gap-2">
          <label className="text-sm text-ink-muted" htmlFor="metadata-api-key">
            {t("metadataProviders.apiKey")}
          </label>
          <Input
            id="metadata-api-key"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label className="text-sm text-ink-muted" htmlFor="metadata-endpoint">
            {t("metadataProviders.endpoint")}
          </label>
          <Input
            id="metadata-endpoint"
            value={endpoint}
            placeholder={t("metadataProviders.endpointPlaceholder")}
            onChange={(event) => setEndpoint(event.target.value)}
          />
        </div>
        <div>
          <Button type="submit" loading={configure.isPending} disabled={apiKey.trim() === ""}>
            {t("metadataProviders.save")}
          </Button>
        </div>
      </form>
    </section>
  );
}
