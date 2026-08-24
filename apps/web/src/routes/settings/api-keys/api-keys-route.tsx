/**
 * API keys: the credential `docs/federation.md` L74 tells a user is "shown
 * once, at creation" and ADR 0016 makes the machine path alongside local
 * accounts and OIDC. `GET/POST /api/account/api-keys` and
 * `DELETE /api/account/api-keys/{key_id}` have existed since, unreachable from
 * the application -- the only way to obtain one was to call the API by hand.
 *
 * THE KEY IS SHOWN ONCE, AND THE SCREEN SAYS SO. The server returns the
 * plaintext exactly once and stores only its hash, so the moment this
 * component drops the value there is no way to recover it -- the same shape
 * `invites-route.tsx` follows for a fresh invitation link.
 */
import { Button, Input, SkeletonRegion, SkeletonText } from "@pornarr/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { FormEvent, JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
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
import { API_KEYS_KEY, type ApiKeyCreated, useApiKeys } from "./api-keys";

function ApiKeysLoading(): JSX.Element {
  const { t } = useTranslation();
  return (
    <SkeletonRegion label={t("apiKeys.loading")}>
      <div className="border border-border bg-surface p-6">
        <SkeletonText lines={4} />
      </div>
    </SkeletonRegion>
  );
}

interface Fresh {
  readonly key: string;
}

export function ApiKeysRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const queryClient = useQueryClient();
  const keysQuery = useApiKeys();
  const [label, setLabel] = useState("");
  const [fresh, setFresh] = useState<Fresh | null>(null);
  const [copied, setCopied] = useState(false);

  const create = useMutation<ApiKeyCreated, ApiRequestError, string>({
    mutationFn: async (value) => {
      const { data, error, response } = await getApiClient().POST("/api/account/api-keys", {
        body: { label: value },
      });
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: (created) => {
      setLabel("");
      setCopied(false);
      setFresh({ key: created.key });
      void queryClient.invalidateQueries({ queryKey: API_KEYS_KEY });
    },
  });

  const revoke = useMutation<void, ApiRequestError, string>({
    mutationFn: async (keyId) => {
      const { error, response } = await getApiClient().DELETE("/api/account/api-keys/{key_id}", {
        params: { path: { key_id: keyId } },
      });
      if (error !== undefined) throw apiFailure(error, response);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: API_KEYS_KEY });
    },
  });

  if (keysQuery.error !== null) {
    return (
      <ErrorScreen
        title={messageForError(keysQuery.error)}
        nextStep={nextStepForError(keysQuery.error)}
        onRetry={
          isRetryableError(keysQuery.error)
            ? () => void queryClient.invalidateQueries({ queryKey: API_KEYS_KEY })
            : undefined
        }
      />
    );
  }
  if (keysQuery.isPending) return <ApiKeysLoading />;

  const keys = keysQuery.data ?? [];
  const mutationError = create.error ?? revoke.error ?? null;
  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (label.trim() === "") return;
    create.mutate(label.trim());
  };

  return (
    <section className="flex flex-col gap-6" aria-labelledby="api-keys-heading">
      <header className="max-w-[70ch]">
        <h1 id="api-keys-heading" className="text-xl text-ink">
          {t("apiKeys.title")}
        </h1>
        <p className="mt-2 text-sm text-ink-muted">{t("apiKeys.intro")}</p>
      </header>

      {mutationError === null ? null : (
        <ErrorScreen
          title={messageForError(mutationError)}
          nextStep={nextStepForError(mutationError)}
        />
      )}

      <form
        className="flex flex-col gap-4 border border-border bg-surface p-6"
        aria-labelledby="api-keys-add"
        onSubmit={submit}
      >
        <h2 id="api-keys-add" className="text-lg text-ink">
          {t("apiKeys.addTitle")}
        </h2>
        <div className="flex flex-col gap-2">
          <label className="text-sm text-ink-muted" htmlFor="api-key-label">
            {t("apiKeys.name")}
          </label>
          <Input
            id="api-key-label"
            value={label}
            placeholder={t("apiKeys.namePlaceholder")}
            onChange={(event) => setLabel(event.target.value)}
          />
        </div>
        <div>
          <Button type="submit" loading={create.isPending} disabled={label.trim() === ""}>
            {t("apiKeys.create")}
          </Button>
        </div>
      </form>

      {fresh === null ? null : (
        <section
          aria-labelledby="api-keys-fresh-heading"
          className="flex flex-col gap-2 rounded-lg border border-[var(--primary)] bg-[var(--primary-weak)] p-4"
        >
          <h2 id="api-keys-fresh-heading" className="text-sm text-ink">
            {t("apiKeys.freshTitle")}
          </h2>
          {/* Stated plainly, because it is true and irreversible. */}
          <p className="text-2xs text-ink-muted">{t("apiKeys.freshHint")}</p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-md bg-surface-2 px-2 py-1 font-mono text-2xs text-ink">
              {fresh.key}
            </code>
            <Button
              variant="secondary"
              onClick={() => {
                void navigator.clipboard?.writeText(fresh.key).then(() => setCopied(true));
              }}
            >
              {copied ? t("apiKeys.copied") : t("apiKeys.copy")}
            </Button>
          </div>
        </section>
      )}

      <section aria-labelledby="api-keys-list" className="border border-border bg-surface p-6">
        <h2 id="api-keys-list" className="text-lg text-ink">
          {t("apiKeys.configured")}
        </h2>
        {keys.length === 0 ? (
          <p className="mt-4 max-w-[70ch] text-sm text-ink-muted">{t("apiKeys.none")}</p>
        ) : (
          <ul className="mt-4 flex flex-col gap-3">
            {keys.map((key) => (
              <li
                key={key.id}
                className="flex flex-wrap items-center justify-between gap-3 border border-border-control bg-surface-2 p-4"
              >
                <div className="min-w-0">
                  <h3 className="text-md text-ink">{key.label}</h3>
                  <p className="font-mono text-sm text-ink-muted">{key.prefix}…</p>
                  <p className="text-2xs text-ink-muted">
                    {t("apiKeys.created", { when: format.relativeDate(new Date(key.created_at)) })}
                    {" · "}
                    {key.last_used_at === null
                      ? t("apiKeys.neverUsed")
                      : t("apiKeys.lastUsed", {
                          when: format.relativeDate(new Date(key.last_used_at)),
                        })}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  aria-label={t("apiKeys.revokeKey", { name: key.label })}
                  onClick={() => revoke.mutate(key.id)}
                >
                  {t("apiKeys.revoke")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
