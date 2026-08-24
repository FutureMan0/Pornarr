import type { paths } from "@pornarr/api-client";
/**
 * The reader's own account: the name others see, what today has cost, and the
 * identities they have linked.
 *
 * Four routes with no caller at all - `GET/PATCH /api/account/profile`,
 * `GET /api/account/storage`, `GET/DELETE /api/account/oidc` - so a member
 * could not change their display name, could not see what their downloads had
 * used against the day's allowance, and could not unlink an identity once it
 * was linked. An operator removing a provider left those identities behind
 * with no way to reach them.
 *
 * Not administrator-only: every one of these routes is about the caller's own
 * account, which is why it sits beside API keys rather than in the
 * administration group.
 */
import { Button, Checkbox, Input, SkeletonRegion, SkeletonText } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FormEvent, JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ErrorScreen } from "../../../errors/error-screen";
import { useFormat } from "../../../i18n/format";
import { getApiClient } from "../../../lib/api";
import { apiFailure, messageForError, nextStepForError } from "../../../lib/api-error";

type Profile =
  paths["/api/account/profile"]["get"]["responses"][200]["content"]["application/json"];
type Storage =
  paths["/api/account/storage"]["get"]["responses"][200]["content"]["application/json"];
type Identity =
  paths["/api/account/oidc"]["get"]["responses"][200]["content"]["application/json"][number];
type Preference =
  paths["/api/notifications/preferences"]["get"]["responses"][200]["content"]["application/json"][number];

const PROFILE_KEY = ["account", "profile"] as const;
const IDENTITIES_KEY = ["account", "oidc"] as const;
const PREFERENCES_KEY = ["notifications", "preferences"] as const;

export function AccountRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const cache = useQueryClient();
  const [displayName, setDisplayName] = useState<string | null>(null);

  const profile = useQuery({
    queryKey: PROFILE_KEY,
    queryFn: async (): Promise<Profile> => {
      const { data, error, response } = await getApiClient().GET("/api/account/profile");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const storage = useQuery({
    queryKey: ["account", "storage"],
    queryFn: async (): Promise<Storage> => {
      const { data, error, response } = await getApiClient().GET("/api/account/storage");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const identities = useQuery({
    queryKey: IDENTITIES_KEY,
    queryFn: async (): Promise<Identity[]> => {
      const { data, error, response } = await getApiClient().GET("/api/account/oidc");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const preferences = useQuery({
    queryKey: PREFERENCES_KEY,
    queryFn: async (): Promise<Preference[]> => {
      const { data, error, response } = await getApiClient().GET("/api/notifications/preferences");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const setPreference = useMutation({
    mutationFn: async ({ kind, enabled }: { kind: Preference["kind"]; enabled: boolean }) => {
      const { error, response } = await getApiClient().PUT(
        "/api/notifications/preferences/{kind}",
        { params: { path: { kind } }, body: { enabled } },
      );
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: PREFERENCES_KEY });
      void cache.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  const save = useMutation({
    mutationFn: async (name: string | null) => {
      const { error, response } = await getApiClient().PATCH("/api/account/profile", {
        body: { display_name: name },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: PROFILE_KEY }),
  });

  /**
   * Throw away what this instance has worked out about the reader.
   *
   * `DELETE /api/recommendations/profile` had no caller, so a profile built
   * from everything an account had watched could be added to and never
   * cleared - which is the one control a shared household actually asks for.
   */
  const forget = useMutation({
    mutationFn: async () => {
      const { error, response } = await getApiClient().DELETE("/api/recommendations/profile");
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["recommendations"] });
    },
  });

  const unlink = useMutation({
    mutationFn: async (identityId: string) => {
      const { error, response } = await getApiClient().DELETE("/api/account/oidc/{identity_id}", {
        params: { path: { identity_id: identityId } },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: IDENTITIES_KEY }),
  });

  if (profile.isPending) {
    return (
      <SkeletonRegion label={t("account.loading")}>
        <div className="border border-border bg-surface p-6">
          <SkeletonText lines={4} />
        </div>
      </SkeletonRegion>
    );
  }
  if (profile.isError) {
    return (
      <ErrorScreen
        title={messageForError(profile.error)}
        nextStep={nextStepForError(profile.error)}
      />
    );
  }

  const current = profile.data;
  // `null` until the reader types: an empty box means "no display name", and a
  // form that seeded itself with the username could not tell the two apart.
  const value = displayName ?? current.display_name ?? "";
  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    save.mutate(value.trim() === "" ? null : value.trim());
  };

  return (
    <section className="flex flex-col gap-6" aria-labelledby="account-heading">
      <header className="max-w-[70ch]">
        <h1 id="account-heading" className="text-xl text-ink">
          {t("account.title")}
        </h1>
        <p className="mt-2 text-sm text-ink-muted">{t("account.intro")}</p>
      </header>

      <form
        className="flex flex-col gap-4 border border-border bg-surface p-6"
        aria-labelledby="account-profile"
        onSubmit={submit}
      >
        <h2 id="account-profile" className="text-lg text-ink">
          {t("account.profile")}
        </h2>
        <dl className="flex flex-col gap-1 text-sm">
          <div className="flex gap-2">
            <dt className="text-ink-muted">{t("account.username")}</dt>
            <dd className="text-ink">{current.username}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-ink-muted">{t("account.role")}</dt>
            <dd className="text-ink">
              {t(`members.roles.${current.role}` as "members.roles.admin", {
                defaultValue: current.role,
              })}
            </dd>
          </div>
        </dl>
        <div className="flex flex-col gap-2">
          <label className="text-sm text-ink-muted" htmlFor="account-display-name">
            {t("account.displayName")}
          </label>
          <Input
            id="account-display-name"
            value={value}
            maxLength={64}
            onChange={(event) => setDisplayName(event.target.value)}
          />
          <p className="text-2xs text-ink-faint">{t("account.displayNameHint")}</p>
        </div>
        {save.isError ? (
          <p role="alert" className="text-sm text-ink">
            {t("errors.generic")}
          </p>
        ) : null}
        <div className="flex items-center gap-3">
          <Button type="submit" loading={save.isPending}>
            {t("account.save")}
          </Button>
          {save.isSuccess ? (
            <output className="block text-2xs text-ink-faint">{t("account.saved")}</output>
          ) : null}
        </div>
      </form>

      <section className="border border-border bg-surface p-6" aria-labelledby="account-storage">
        <h2 id="account-storage" className="text-lg text-ink">
          {t("account.storage")}
        </h2>
        {storage.isPending ? (
          <p className="mt-4 text-sm text-ink-muted">{t("account.loading")}</p>
        ) : storage.isError ? (
          <p role="alert" className="mt-4 text-sm text-ink">
            {t("errors.generic")}
          </p>
        ) : (
          <dl className="mt-4 flex flex-col gap-1 text-sm">
            <div className="flex gap-2">
              <dt className="text-ink-muted">{t("account.downloadedToday")}</dt>
              <dd className="text-ink tabular-nums">
                {format.bytes(storage.data.downloaded_bytes)}
                {" · "}
                {t("account.downloads", { count: storage.data.download_count })}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-ink-muted">{t("account.reserved")}</dt>
              <dd className="text-ink tabular-nums">
                {format.bytes(storage.data.reserved_bytes)}
                {" · "}
                {t("account.downloads", { count: storage.data.reserved_download_count })}
              </dd>
            </div>
          </dl>
        )}
      </section>

      <section
        className="border border-border bg-surface p-6"
        aria-labelledby="account-notifications"
      >
        <h2 id="account-notifications" className="text-lg text-ink">
          {t("notifications.preferences")}
        </h2>
        <p className="mt-2 max-w-[70ch] text-sm text-ink-muted">
          {t("notifications.preferencesHint")}
        </p>
        {preferences.isPending ? (
          <p className="mt-4 text-sm text-ink-muted">{t("account.loading")}</p>
        ) : preferences.isError ? (
          <p role="alert" className="mt-4 text-sm text-ink">
            {t("errors.generic")}
          </p>
        ) : (
          <ul className="mt-4 flex flex-col gap-3">
            {(preferences.data ?? []).map((preference) => (
              <li key={preference.kind}>
                <Checkbox
                  label={t(
                    `notifications.kinds.${preference.kind}` as "notifications.kinds.instance_notice",
                    { defaultValue: preference.kind },
                  )}
                  checked={preference.enabled}
                  disabled={setPreference.isPending}
                  onChange={(event) =>
                    setPreference.mutate({
                      kind: preference.kind,
                      enabled: event.target.checked,
                    })
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="border border-border bg-surface p-6" aria-labelledby="account-interest">
        <h2 id="account-interest" className="text-lg text-ink">
          {t("account.interest")}
        </h2>
        <p className="mt-2 max-w-[70ch] text-sm text-ink-muted">{t("account.interestHint")}</p>
        {forget.isSuccess ? (
          <output className="mt-2 block text-2xs text-ink-faint">
            {t("account.interestForgotten")}
          </output>
        ) : null}
        <div className="mt-4">
          <Button variant="ghost" loading={forget.isPending} onClick={() => forget.mutate()}>
            {t("account.forget")}
          </Button>
        </div>
      </section>

      <section className="border border-border bg-surface p-6" aria-labelledby="account-identities">
        <h2 id="account-identities" className="text-lg text-ink">
          {t("account.identities")}
        </h2>
        {identities.isPending ? (
          <p className="mt-4 text-sm text-ink-muted">{t("account.loading")}</p>
        ) : identities.isError ? (
          <p role="alert" className="mt-4 text-sm text-ink">
            {t("errors.generic")}
          </p>
        ) : (identities.data ?? []).length === 0 ? (
          <p className="mt-4 max-w-[70ch] text-sm text-ink-muted">{t("account.noIdentities")}</p>
        ) : (
          <ul className="mt-4 flex flex-col gap-3">
            {(identities.data ?? []).map((identity) => (
              <li
                key={identity.id}
                className="flex flex-wrap items-center justify-between gap-3 border border-border-control bg-surface-2 p-4"
              >
                <p className="text-sm text-ink">{identity.provider_name}</p>
                <Button
                  variant="ghost"
                  loading={unlink.isPending && unlink.variables === identity.id}
                  aria-label={t("account.unlinkIdentity", { name: identity.provider_name })}
                  onClick={() => unlink.mutate(identity.id)}
                >
                  {t("account.unlink")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
