/**
 * Single sign-on: the OIDC providers a household may sign in through.
 *
 * ADR 0016 ships OIDC in the same release as local accounts, and the API side
 * of it has existed since -- a provider could be created and `/login` on this
 * server would still have offered nothing, because nothing in the application
 * ever reached `/api/admin/oidc`. This screen is that reach: list, add, test
 * and remove a provider, and the sign-in button on `/login` follows from
 * `enabled` alone.
 *
 * Only the fields a provider needs to authenticate are exposed here --
 * `role_mapping`, `required_claim` and the two claim names stay on their
 * server defaults, the same way `metadata-providers-route.tsx` does not expose
 * every field a metadata provider has either. An operator who needs those can
 * still reach them through `/api/admin/oidc` directly.
 *
 * The client secret is entered once and never comes back: `ProviderResponse`
 * does not carry it, the same shape `metadata-providers-route.tsx` follows for
 * a provider's own API key.
 */
import { Button, Checkbox, Input, SkeletonRegion, SkeletonText } from "@pornarr/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { FormEvent, JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate } from "react-router-dom";
import { useSession } from "../../../auth/session";
import { ErrorScreen } from "../../../errors/error-screen";
import { useFormat } from "../../../i18n/format";
import { getApiClient } from "../../../lib/api";
import {
  type ApiRequestError,
  apiFailure,
  isRetryableError,
  messageForError,
  nextStepForError,
} from "../../../lib/api-error";
import {
  OIDC_ADMIN_PROVIDERS_KEY,
  type OidcProvider,
  type OidcProviderWrite,
  useOidcAdminProviders,
} from "./oidc-providers";

function OidcLoading(): JSX.Element {
  const { t } = useTranslation();
  return (
    <SkeletonRegion label={t("oidcProviders.loading")}>
      <div className="flex flex-col gap-4">
        <div className="border border-border bg-surface p-4">
          <SkeletonText lines={4} />
        </div>
        <div className="border border-border bg-surface p-6">
          <SkeletonText lines={3} />
        </div>
      </div>
    </SkeletonRegion>
  );
}

export function OidcProvidersRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const session = useSession();
  const queryClient = useQueryClient();
  const providersQuery = useOidcAdminProviders();
  const [name, setName] = useState("");
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [pendingRemoval, setPendingRemoval] = useState<string | null>(null);

  const addProvider = useMutation<OidcProvider, ApiRequestError, void>({
    mutationFn: async () => {
      // `default_role`, `role_claim`, `username_claim` and `enabled` carry a
      // server default but the contract still marks them required (see
      // `invites-route.tsx`'s comment on the same shape), so the defaults are
      // repeated here rather than left implicit.
      const body: OidcProviderWrite = {
        name: name.trim(),
        issuer: issuer.trim(),
        client_id: clientId.trim(),
        client_secret: clientSecret,
        default_role: "user",
        role_claim: "groups",
        username_claim: "preferred_username",
        enabled,
      };
      const { data, error, response } = await getApiClient().POST("/api/admin/oidc", { body });
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: () => {
      setName("");
      setIssuer("");
      setClientId("");
      setClientSecret("");
      setEnabled(true);
      void queryClient.invalidateQueries({ queryKey: OIDC_ADMIN_PROVIDERS_KEY });
    },
  });

  const testProvider = useMutation<OidcProvider, ApiRequestError, string>({
    mutationFn: async (providerId) => {
      const { data, error, response } = await getApiClient().POST(
        "/api/admin/oidc/{provider_id}/test",
        { params: { path: { provider_id: providerId } } },
      );
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: (provider) => {
      queryClient.setQueryData<OidcProvider[]>(
        OIDC_ADMIN_PROVIDERS_KEY,
        (current) =>
          current?.map((existing) => (existing.id === provider.id ? provider : existing)) ?? [],
      );
    },
  });

  const removeProvider = useMutation<void, ApiRequestError, string>({
    mutationFn: async (providerId) => {
      const { error, response } = await getApiClient().DELETE("/api/admin/oidc/{provider_id}", {
        params: { path: { provider_id: providerId } },
      });
      if (error !== undefined) throw apiFailure(error, response);
    },
    onSuccess: (_, providerId) => {
      setPendingRemoval(null);
      queryClient.setQueryData<OidcProvider[]>(
        OIDC_ADMIN_PROVIDERS_KEY,
        (current) => current?.filter((provider) => provider.id !== providerId) ?? [],
      );
    },
  });

  const providers = providersQuery.data ?? [];
  const mutationError = addProvider.error ?? testProvider.error ?? removeProvider.error ?? null;

  if (session.data?.role !== "admin" && session.data !== undefined)
    return <Navigate to="/forbidden" replace />;
  if (providersQuery.error !== null) {
    return (
      <ErrorScreen
        title={messageForError(providersQuery.error)}
        nextStep={nextStepForError(providersQuery.error)}
        onRetry={
          isRetryableError(providersQuery.error)
            ? () => void queryClient.invalidateQueries({ queryKey: OIDC_ADMIN_PROVIDERS_KEY })
            : undefined
        }
      />
    );
  }
  if (providersQuery.isPending) return <OidcLoading />;

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!name.trim() || !issuer.trim() || !clientId.trim() || !clientSecret) return;
    addProvider.mutate();
  };

  return (
    <section className="flex flex-col gap-6" aria-labelledby="oidc-providers-heading">
      <header className="max-w-[70ch]">
        <h1 id="oidc-providers-heading" className="text-xl text-ink">
          {t("oidcProviders.title")}
        </h1>
        <p className="mt-2 text-sm text-ink-muted">{t("oidcProviders.intro")}</p>
      </header>

      {mutationError === null ? null : (
        <ErrorScreen
          title={messageForError(mutationError)}
          nextStep={nextStepForError(mutationError)}
        />
      )}

      <section
        className="border border-border bg-surface p-6"
        aria-labelledby="oidc-providers-list"
      >
        <h2 id="oidc-providers-list" className="text-lg text-ink">
          {t("oidcProviders.configured")}
        </h2>

        {providers.length === 0 ? (
          <p className="mt-4 max-w-[70ch] text-sm text-ink-muted">{t("oidcProviders.none")}</p>
        ) : (
          <ul className="mt-4 flex flex-col gap-4">
            {providers.map((provider) => (
              <li key={provider.id} className="border border-border-control bg-surface-2 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="text-md text-ink">{provider.name}</h3>
                    <p className="break-all text-sm text-ink-muted">{provider.issuer}</p>
                  </div>
                  <div className="flex flex-wrap gap-3">
                    <Button
                      variant="secondary"
                      aria-label={t("oidcProviders.testProvider", { name: provider.name })}
                      loading={testProvider.isPending && testProvider.variables === provider.id}
                      onClick={() => {
                        testProvider.reset();
                        testProvider.mutate(provider.id);
                      }}
                    >
                      {t("oidcProviders.test")}
                    </Button>
                    {pendingRemoval === provider.id ? null : (
                      <Button
                        variant="ghost"
                        aria-label={t("oidcProviders.removeProvider", { name: provider.name })}
                        onClick={() => {
                          removeProvider.reset();
                          setPendingRemoval(provider.id);
                        }}
                      >
                        {t("oidcProviders.remove")}
                      </Button>
                    )}
                  </div>
                </div>

                <dl className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("oidcProviders.status")}</dt>
                    <dd className="mt-1 text-sm text-ink">
                      {provider.enabled ? t("oidcProviders.enabled") : t("oidcProviders.disabled")}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("oidcProviders.lastTested")}</dt>
                    <dd className="mt-1 text-sm text-ink">
                      {provider.discovery_fetched_at === null
                        ? t("oidcProviders.never")
                        : format.relativeDate(new Date(provider.discovery_fetched_at))}
                    </dd>
                  </div>
                </dl>

                {pendingRemoval === provider.id ? (
                  <div className="mt-4 border-t border-border pt-4">
                    <p className="max-w-[70ch] text-sm text-ink">
                      {t("oidcProviders.confirmRemoval", { name: provider.name })}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-3">
                      <Button
                        loading={removeProvider.isPending}
                        onClick={() => removeProvider.mutate(provider.id)}
                      >
                        {t("oidcProviders.confirmRemovalAction")}
                      </Button>
                      <Button variant="ghost" onClick={() => setPendingRemoval(null)}>
                        {t("oidcProviders.cancelRemoval")}
                      </Button>
                    </div>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <form
        className="border border-border bg-surface p-6"
        aria-labelledby="oidc-providers-add"
        onSubmit={submit}
      >
        <h2 id="oidc-providers-add" className="text-lg text-ink">
          {t("oidcProviders.addTitle")}
        </h2>
        <p className="mt-1 max-w-[70ch] text-sm text-ink-muted">{t("oidcProviders.addHint")}</p>

        <div className="mt-4 flex flex-col gap-4">
          <div className="flex flex-col gap-4 sm:flex-row">
            <label
              className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-ink"
              htmlFor="oidc-name"
            >
              {t("oidcProviders.name")}
              <Input
                id="oidc-name"
                value={name}
                placeholder={t("oidcProviders.namePlaceholder")}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label
              className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-ink"
              htmlFor="oidc-issuer"
            >
              {t("oidcProviders.issuer")}
              <Input
                id="oidc-issuer"
                type="url"
                value={issuer}
                placeholder={t("oidcProviders.issuerPlaceholder")}
                onChange={(event) => setIssuer(event.target.value)}
              />
            </label>
          </div>
          <label className="flex min-w-0 flex-col gap-2 text-sm text-ink" htmlFor="oidc-client-id">
            {t("oidcProviders.clientId")}
            <Input
              id="oidc-client-id"
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
            />
          </label>
          <label
            className="flex min-w-0 flex-col gap-2 text-sm text-ink"
            htmlFor="oidc-client-secret"
          >
            {t("oidcProviders.clientSecret")}
            <Input
              id="oidc-client-secret"
              type="password"
              autoComplete="off"
              value={clientSecret}
              onChange={(event) => setClientSecret(event.target.value)}
            />
          </label>
          <p className="max-w-[70ch] text-sm text-ink-muted">
            {t("oidcProviders.clientSecretHint")}
          </p>
          <div className="flex flex-wrap items-center gap-4">
            <Checkbox
              label={t("oidcProviders.enable")}
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
            />
            <Button
              type="submit"
              loading={addProvider.isPending}
              disabled={!name.trim() || !issuer.trim() || !clientId.trim() || !clientSecret}
            >
              {t("oidcProviders.add")}
            </Button>
          </div>
        </div>
      </form>
    </section>
  );
}
