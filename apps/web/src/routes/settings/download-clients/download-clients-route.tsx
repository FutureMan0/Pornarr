/**
 * Download clients: where a grabbed release is actually sent.
 *
 * The one piece of configuration that decides whether anything is ever
 * fetched, and the only one with no screen: the setup wizard posted a client
 * once and the four routes that manage it - list, replace, test, remove - had
 * no caller anywhere in the application. A client that moved port, rotated its
 * password or was replaced by another could not be told so.
 *
 * Built on the conventions of the indexers section beside it: a list carrying
 * each client's health, an add form, and both editing and removal opening
 * inside the row rather than in a modal.
 *
 * The credential is write-only - the response carries everything about a client
 * except its secret - so the edit form starts empty and an empty box means
 * "keep the credential you have" rather than "clear it". qBittorrent takes a
 * username and a password; SABnzbd takes its API key. `credentials` is one
 * string either way, which is what `#79` stores encrypted, so the form asks for
 * the two halves and joins them the way the adapter expects.
 */
import { Button, Checkbox, Input, Select, SkeletonRegion, SkeletonText } from "@pornarr/ui";
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
  CLIENT_PROTOCOLS,
  DOWNLOAD_CLIENTS_KEY,
  type DownloadClient,
  type DownloadClientImplementation,
  type DownloadClientUpdate,
  type DownloadClientWrite,
  useDownloadClients,
} from "./download-clients";

const IMPLEMENTATIONS = ["qbittorrent", "sabnzbd"] as const;

/** The health vocabulary, plus the state a client nobody has tested yet is in. */
const HEALTH_KEYS = {
  unknown: "downloadClients.healthUnknown",
  healthy: "downloadClients.healthHealthy",
  unhealthy: "downloadClients.healthUnhealthy",
} as const;

type Draft = {
  readonly implementation: DownloadClientImplementation;
  readonly name: string;
  readonly host: string;
  readonly port: string;
  readonly urlBase: string;
  readonly category: string;
  readonly username: string;
  readonly password: string;
  readonly apiKey: string;
  readonly removeCompleted: boolean;
};

const EMPTY: Draft = {
  implementation: "qbittorrent",
  name: "",
  host: "",
  port: "8080",
  urlBase: "",
  category: "",
  username: "",
  password: "",
  apiKey: "",
  removeCompleted: false,
};

/**
 * The one string the adapters take.
 *
 * `qbittorrent.py` parses `{"username": …, "password": …}`; `sabnzbd.py` takes
 * the raw API key. Returning `null` means the operator left the secret alone,
 * which an edit has to be able to say.
 */
function credentialsFrom(draft: Draft): string | null {
  if (draft.implementation === "sabnzbd") {
    return draft.apiKey.trim() === "" ? null : draft.apiKey.trim();
  }
  if (draft.username.trim() === "" && draft.password === "") return null;
  return JSON.stringify({ username: draft.username.trim(), password: draft.password });
}

function bodyFrom(draft: Draft, credentials: string | null): DownloadClientUpdate {
  return {
    name: draft.name.trim(),
    protocol: CLIENT_PROTOCOLS[draft.implementation],
    implementation: draft.implementation,
    host: draft.host.trim(),
    port: Number(draft.port),
    url_base: draft.urlBase.trim(),
    category: draft.category.trim() === "" ? null : draft.category.trim(),
    credentials,
    priority: 0,
    remove_completed: draft.removeCompleted,
    enabled: true,
  };
}

function ClientsLoading(): JSX.Element {
  const { t } = useTranslation();
  return (
    <SkeletonRegion label={t("downloadClients.loading")}>
      <div className="border border-border bg-surface p-6">
        <SkeletonText lines={4} />
      </div>
    </SkeletonRegion>
  );
}

export function DownloadClientsRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const session = useSession();
  const queryClient = useQueryClient();
  const clientsQuery = useDownloadClients();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: DOWNLOAD_CLIENTS_KEY });

  // `health` is an open string on purpose: a value this build has not heard of
  // is reported rather than hidden behind "unknown".
  const healthLabel = (health: string): string => {
    const key = HEALTH_KEYS[health as keyof typeof HEALTH_KEYS];
    return key === undefined ? t("downloadClients.healthOther", { health }) : t(key);
  };

  const add = useMutation<DownloadClient, ApiRequestError, DownloadClientWrite>({
    mutationFn: async (body) => {
      const { data, error, response } = await getApiClient().POST("/api/admin/download-clients", {
        body,
      });
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: () => {
      setDraft(EMPTY);
      invalidate();
    },
  });

  const replace = useMutation<
    DownloadClient,
    ApiRequestError,
    { id: string; body: DownloadClientUpdate }
  >({
    mutationFn: async ({ id, body }) => {
      const { data, error, response } = await getApiClient().PUT(
        "/api/admin/download-clients/{client_id}",
        { params: { path: { client_id: id } }, body },
      );
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: () => {
      setEditing(null);
      setDraft(EMPTY);
      invalidate();
    },
  });

  const test = useMutation<DownloadClient, ApiRequestError, string>({
    mutationFn: async (id) => {
      const { data, error, response } = await getApiClient().POST(
        "/api/admin/download-clients/{client_id}/test",
        { params: { path: { client_id: id } } },
      );
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: invalidate,
  });

  const remove = useMutation<void, ApiRequestError, string>({
    mutationFn: async (id) => {
      const { error, response } = await getApiClient().DELETE(
        "/api/admin/download-clients/{client_id}",
        { params: { path: { client_id: id } } },
      );
      if (error !== undefined) throw apiFailure(error, response);
    },
    onSuccess: () => {
      setConfirming(null);
      invalidate();
    },
  });

  if (session.data?.role !== "admin" && session.data !== undefined)
    return <Navigate to="/forbidden" replace />;
  if (clientsQuery.error !== null) {
    return (
      <ErrorScreen
        title={messageForError(clientsQuery.error)}
        nextStep={nextStepForError(clientsQuery.error)}
        onRetry={isRetryableError(clientsQuery.error) ? invalidate : undefined}
      />
    );
  }
  if (clientsQuery.isPending) return <ClientsLoading />;

  const clients = clientsQuery.data ?? [];
  const mutationError = add.error ?? replace.error ?? test.error ?? remove.error ?? null;

  const startEditing = (client: DownloadClient): void => {
    setEditing(client.id);
    setDraft({
      ...EMPTY,
      implementation: client.implementation as DownloadClientImplementation,
      name: client.name,
      host: client.host,
      port: String(client.port),
      urlBase: client.url_base,
      category: client.category ?? "",
      removeCompleted: client.remove_completed,
    });
  };

  const submitAdd = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const credentials = credentialsFrom(draft);
    if (credentials === null || draft.name.trim() === "" || draft.host.trim() === "") return;
    // A new client has to carry one; only an edit may leave it out.
    add.mutate({ ...bodyFrom(draft, credentials), credentials });
  };

  const submitEdit = (event: FormEvent<HTMLFormElement>, client: DownloadClient): void => {
    event.preventDefault();
    // `null`, not `""`. An empty box means "keep the credential you have" -
    // the value never comes back from the server, so there is nothing to put
    // in the box and no way for a form to resend it. An empty string would be
    // a credential, and writing one is how a working client stops answering.
    replace.mutate({ id: client.id, body: bodyFrom(draft, credentialsFrom(draft)) });
  };

  const secretFields = (idPrefix: string): JSX.Element =>
    draft.implementation === "sabnzbd" ? (
      <div className="flex flex-col gap-2">
        <label className="text-sm text-ink-muted" htmlFor={`${idPrefix}-api-key`}>
          {t("downloadClients.apiKey")}
        </label>
        <Input
          id={`${idPrefix}-api-key`}
          type="password"
          autoComplete="off"
          value={draft.apiKey}
          onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })}
        />
      </div>
    ) : (
      <>
        <div className="flex flex-col gap-2">
          <label className="text-sm text-ink-muted" htmlFor={`${idPrefix}-username`}>
            {t("downloadClients.username")}
          </label>
          <Input
            id={`${idPrefix}-username`}
            autoComplete="off"
            value={draft.username}
            onChange={(event) => setDraft({ ...draft, username: event.target.value })}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label className="text-sm text-ink-muted" htmlFor={`${idPrefix}-password`}>
            {t("downloadClients.password")}
          </label>
          <Input
            id={`${idPrefix}-password`}
            type="password"
            autoComplete="off"
            value={draft.password}
            onChange={(event) => setDraft({ ...draft, password: event.target.value })}
          />
        </div>
      </>
    );

  const commonFields = (idPrefix: string): JSX.Element => (
    <>
      <div className="flex flex-col gap-2">
        <label className="text-sm text-ink-muted" htmlFor={`${idPrefix}-implementation`}>
          {t("downloadClients.implementation")}
        </label>
        <Select
          id={`${idPrefix}-implementation`}
          value={draft.implementation}
          onChange={(event) =>
            setDraft({
              ...draft,
              implementation: event.target.value as DownloadClientImplementation,
            })
          }
        >
          {IMPLEMENTATIONS.map((value) => (
            <option key={value} value={value}>
              {t(`downloadClients.implementations.${value}`)}
            </option>
          ))}
        </Select>
      </div>
      <div className="flex flex-col gap-2">
        <label className="text-sm text-ink-muted" htmlFor={`${idPrefix}-name`}>
          {t("downloadClients.name")}
        </label>
        <Input
          id={`${idPrefix}-name`}
          value={draft.name}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
        />
      </div>
      <div className="flex flex-wrap gap-4">
        <div className="flex min-w-[16rem] flex-1 flex-col gap-2">
          <label className="text-sm text-ink-muted" htmlFor={`${idPrefix}-host`}>
            {t("downloadClients.host")}
          </label>
          <Input
            id={`${idPrefix}-host`}
            value={draft.host}
            onChange={(event) => setDraft({ ...draft, host: event.target.value })}
          />
        </div>
        <div className="flex w-[8rem] flex-col gap-2">
          <label className="text-sm text-ink-muted" htmlFor={`${idPrefix}-port`}>
            {t("downloadClients.port")}
          </label>
          <Input
            id={`${idPrefix}-port`}
            type="number"
            min={1}
            max={65535}
            className="tabular-nums"
            value={draft.port}
            onChange={(event) => setDraft({ ...draft, port: event.target.value })}
          />
        </div>
      </div>
      {secretFields(idPrefix)}
      <div className="flex flex-col gap-2">
        <label className="text-sm text-ink-muted" htmlFor={`${idPrefix}-category`}>
          {t("downloadClients.category")}
        </label>
        <Input
          id={`${idPrefix}-category`}
          value={draft.category}
          placeholder={t("downloadClients.categoryPlaceholder")}
          onChange={(event) => setDraft({ ...draft, category: event.target.value })}
        />
      </div>
      <Checkbox
        label={t("downloadClients.removeCompleted")}
        checked={draft.removeCompleted}
        onChange={(event) => setDraft({ ...draft, removeCompleted: event.target.checked })}
      />
    </>
  );

  return (
    <section className="flex flex-col gap-6" aria-labelledby="download-clients-heading">
      <header className="max-w-[70ch]">
        <h1 id="download-clients-heading" className="text-xl text-ink">
          {t("downloadClients.title")}
        </h1>
        <p className="mt-2 text-sm text-ink-muted">{t("downloadClients.intro")}</p>
      </header>

      {mutationError === null ? null : (
        <ErrorScreen
          title={messageForError(mutationError)}
          nextStep={nextStepForError(mutationError)}
        />
      )}

      <section
        className="border border-border bg-surface p-6"
        aria-labelledby="download-clients-list"
      >
        <h2 id="download-clients-list" className="text-lg text-ink">
          {t("downloadClients.configured")}
        </h2>
        {clients.length === 0 ? (
          <p className="mt-4 max-w-[70ch] text-sm text-ink-muted">{t("downloadClients.none")}</p>
        ) : (
          <ul className="mt-4 flex flex-col gap-3">
            {clients.map((client) => (
              <li key={client.id} className="border border-border-control bg-surface-2 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="text-md text-ink">{client.name}</h3>
                    <p className="break-all text-sm text-ink-muted">
                      {client.host}
                      {":"}
                      <span className="tabular-nums">{client.port}</span>
                      {client.url_base === "" ? "" : client.url_base}
                    </p>
                    <p className="text-2xs text-ink-faint">
                      {healthLabel(client.health)}
                      {" · "}
                      {client.last_tested_at === null
                        ? t("downloadClients.never")
                        : format.relativeDate(new Date(client.last_tested_at))}
                    </p>
                    {client.last_error === null ? null : (
                      <p role="alert" className="mt-1 text-2xs text-[var(--pa-accent-300)]">
                        {t("downloadClients.lastErrorDetail", { reason: client.last_error })}
                      </p>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      loading={test.isPending && test.variables === client.id}
                      aria-label={t("downloadClients.testClient", { name: client.name })}
                      onClick={() => test.mutate(client.id)}
                    >
                      {t("downloadClients.test")}
                    </Button>
                    <Button
                      variant="secondary"
                      aria-label={t("downloadClients.editClient", { name: client.name })}
                      onClick={() =>
                        editing === client.id ? setEditing(null) : startEditing(client)
                      }
                    >
                      {t("downloadClients.edit")}
                    </Button>
                    <Button
                      variant="ghost"
                      aria-label={t("downloadClients.removeClient", { name: client.name })}
                      onClick={() => setConfirming(confirming === client.id ? null : client.id)}
                    >
                      {t("downloadClients.remove")}
                    </Button>
                  </div>
                </div>

                {confirming === client.id ? (
                  <div className="mt-3 flex flex-wrap items-center gap-3 border-border-control border-t pt-3">
                    <p className="text-sm text-ink">{t("downloadClients.confirmRemove")}</p>
                    <Button
                      variant="secondary"
                      loading={remove.isPending}
                      onClick={() => remove.mutate(client.id)}
                    >
                      {t("downloadClients.confirmRemoveAction")}
                    </Button>
                    <Button variant="ghost" onClick={() => setConfirming(null)}>
                      {t("downloadClients.cancel")}
                    </Button>
                  </div>
                ) : null}

                {editing === client.id ? (
                  <form
                    className="mt-3 flex flex-col gap-4 border-border-control border-t pt-3"
                    onSubmit={(event) => submitEdit(event, client)}
                  >
                    {commonFields(`edit-${client.id}`)}
                    <p className="text-2xs text-ink-faint">{t("downloadClients.secretKept")}</p>
                    <div className="flex gap-2">
                      <Button type="submit" loading={replace.isPending}>
                        {t("downloadClients.save")}
                      </Button>
                      <Button variant="ghost" type="button" onClick={() => setEditing(null)}>
                        {t("downloadClients.cancel")}
                      </Button>
                    </div>
                  </form>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      {editing === null ? (
        <form
          className="flex flex-col gap-4 border border-border bg-surface p-6"
          aria-labelledby="download-clients-add"
          onSubmit={submitAdd}
        >
          <h2 id="download-clients-add" className="text-lg text-ink">
            {t("downloadClients.add")}
          </h2>
          {commonFields("add")}
          <div>
            <Button
              type="submit"
              loading={add.isPending}
              disabled={draft.name.trim() === "" || draft.host.trim() === ""}
            >
              {t("downloadClients.save")}
            </Button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
