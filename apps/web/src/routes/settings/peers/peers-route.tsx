/**
 * Shared libraries: the servers other people run, listed alongside your own.
 *
 * A peer is another household's Pornarr, reachable over the network with a key
 * it issued. Adding one does not copy anything: its titles appear in the
 * library's source picker, streamed from that server on demand. The screen says
 * so in its own copy, because "peer" is the kind of word that means nothing to
 * the person who has to decide whether to add one.
 *
 * Built on the conventions of the root folders section beside it: a list, an
 * add form, and removal that confirms inline rather than in a modal, because
 * DESIGN.md says modal is never the first answer and a two-step control in the
 * row keeps the server being removed on screen while the reader decides.
 *
 * The key is write-only. It is typed once, sent once, and never comes back from
 * the API, so there is no field on this screen that could ever display one.
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
import {
  type ApiRequestError,
  isRetryableError,
  messageForError,
  nextStepForError,
} from "../../../lib/api-error";
import { PEERS_KEY, type Peer, createPeer, deletePeer, testPeer, usePeers } from "./peers";

/**
 * The health vocabulary the rest of the API uses (`routers/health.py`), plus
 * the state a peer nobody has tested yet is in. `health` is an open string on
 * purpose: a value this build has not heard of is reported rather than hidden,
 * the same way an unknown error code still reaches the screen.
 */
const HEALTH_KEYS = {
  healthy: "peers.healthHealthy",
  degraded: "peers.healthDegraded",
  unhealthy: "peers.healthUnhealthy",
  unknown: "peers.healthUnknown",
} as const;

function PeersLoading(): JSX.Element {
  const { t } = useTranslation();
  return (
    <SkeletonRegion label={t("peers.loading")}>
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

export function PeersRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const session = useSession();
  const queryClient = useQueryClient();
  const peersQuery = usePeers();
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [pendingRemoval, setPendingRemoval] = useState<string | null>(null);

  const healthLabel = (health: string): string => {
    const key = HEALTH_KEYS[health as keyof typeof HEALTH_KEYS];
    return key === undefined ? t("peers.healthOther", { health }) : t(key);
  };

  const addPeer = useMutation<Peer, ApiRequestError, void>({
    mutationFn: () =>
      createPeer({
        name: name.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey,
        enabled,
      }),
    onSuccess: (peer) => {
      queryClient.setQueryData<Peer[]>(PEERS_KEY, (current) =>
        [...(current ?? []), peer].sort((left, right) => left.name.localeCompare(right.name)),
      );
      setName("");
      setBaseUrl("");
      setApiKey("");
      setEnabled(true);
    },
  });

  const checkPeer = useMutation<Peer, ApiRequestError, string>({
    mutationFn: testPeer,
    onSuccess: (peer) => {
      queryClient.setQueryData<Peer[]>(
        PEERS_KEY,
        (current) => current?.map((existing) => (existing.id === peer.id ? peer : existing)) ?? [],
      );
    },
  });

  const removePeer = useMutation<void, ApiRequestError, string>({
    mutationFn: deletePeer,
    onSuccess: (_, peerId) => {
      queryClient.setQueryData<Peer[]>(
        PEERS_KEY,
        (current) => current?.filter((peer) => peer.id !== peerId) ?? [],
      );
      setPendingRemoval(null);
    },
  });

  const peers = peersQuery.data ?? [];
  const mutationError = addPeer.error ?? checkPeer.error ?? removePeer.error ?? null;
  if (session.data?.role !== "admin" && session.data !== undefined)
    return <Navigate to="/forbidden" replace />;
  if (peersQuery.error !== null) {
    return (
      <ErrorScreen
        title={messageForError(peersQuery.error)}
        nextStep={nextStepForError(peersQuery.error)}
        onRetry={
          isRetryableError(peersQuery.error)
            ? () => void queryClient.invalidateQueries({ queryKey: PEERS_KEY })
            : undefined
        }
      />
    );
  }
  if (peersQuery.isPending) return <PeersLoading />;

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!name.trim() || !baseUrl.trim() || !apiKey) return;
    addPeer.mutate();
  };

  return (
    <section className="flex flex-col gap-6" aria-labelledby="peers-heading">
      <header className="max-w-[70ch]">
        <h1 id="peers-heading" className="text-xl text-ink">
          {t("peers.title")}
        </h1>
        <p className="mt-2 text-sm text-ink-muted">{t("peers.intro")}</p>
      </header>

      {mutationError === null ? null : (
        <ErrorScreen
          title={messageForError(mutationError)}
          nextStep={nextStepForError(mutationError)}
        />
      )}

      <section className="border border-border bg-surface p-6" aria-labelledby="peers-list">
        <h2 id="peers-list" className="text-lg text-ink">
          {t("peers.configured")}
        </h2>

        {peers.length === 0 ? (
          <p className="mt-4 max-w-[70ch] text-sm text-ink-muted">{t("peers.none")}</p>
        ) : (
          <ul className="mt-4 flex flex-col gap-4">
            {peers.map((peer) => (
              <li key={peer.id} className="border border-border-control bg-surface-2 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="text-md text-ink">{peer.name}</h3>
                    <p className="break-all text-sm text-ink-muted">{peer.base_url}</p>
                  </div>
                  <div className="flex flex-wrap gap-3">
                    <Button
                      variant="secondary"
                      aria-label={t("peers.testPeer", { name: peer.name })}
                      loading={checkPeer.isPending && checkPeer.variables === peer.id}
                      onClick={() => {
                        checkPeer.reset();
                        checkPeer.mutate(peer.id);
                      }}
                    >
                      {t("peers.test")}
                    </Button>
                    {pendingRemoval === peer.id ? null : (
                      <Button
                        variant="ghost"
                        aria-label={t("peers.removePeer", { name: peer.name })}
                        onClick={() => {
                          removePeer.reset();
                          setPendingRemoval(peer.id);
                        }}
                      >
                        {t("peers.remove")}
                      </Button>
                    )}
                  </div>
                </div>

                <dl className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("peers.status")}</dt>
                    <dd className="mt-1 text-sm text-ink">
                      {peer.enabled ? t("peers.enabled") : t("peers.disabled")}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("peers.health")}</dt>
                    {/* Never colour alone: the state is the word. */}
                    <dd className="mt-1 text-sm text-ink">{healthLabel(peer.health)}</dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("peers.titles")}</dt>
                    <dd className="mt-1 text-sm text-ink tabular">
                      {format.number(peer.media_count)}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("peers.lastTested")}</dt>
                    <dd className="mt-1 text-sm text-ink">
                      {peer.last_tested_at === null
                        ? t("peers.never")
                        : format.relativeDate(new Date(peer.last_tested_at))}
                    </dd>
                  </div>
                </dl>

                {peer.health_reason === null ? null : (
                  // The reason comes from the peer, so it is shown as the
                  // detail it is, inside a sentence this side wrote.
                  <p className="mt-3 max-w-[70ch] break-all text-sm text-ink-muted">
                    {t("peers.healthReason", { reason: peer.health_reason })}
                  </p>
                )}

                {pendingRemoval === peer.id ? (
                  <div className="mt-4 border-t border-border pt-4">
                    <p className="max-w-[70ch] text-sm text-ink">
                      {t("peers.confirmRemoval", { name: peer.name })}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-3">
                      <Button
                        loading={removePeer.isPending}
                        onClick={() => removePeer.mutate(peer.id)}
                      >
                        {t("peers.confirmRemovalAction")}
                      </Button>
                      <Button variant="ghost" onClick={() => setPendingRemoval(null)}>
                        {t("peers.cancelRemoval")}
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
        aria-labelledby="peers-add"
        onSubmit={submit}
      >
        <h2 id="peers-add" className="text-lg text-ink">
          {t("peers.addTitle")}
        </h2>
        <p className="mt-1 max-w-[70ch] text-sm text-ink-muted">{t("peers.addHint")}</p>

        <div className="mt-4 flex flex-col gap-4">
          <div className="flex flex-col gap-4 sm:flex-row">
            <label
              className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-ink"
              htmlFor="peer-name"
            >
              {t("peers.name")}
              <Input
                id="peer-name"
                value={name}
                placeholder={t("peers.namePlaceholder")}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label
              className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-ink"
              htmlFor="peer-url"
            >
              {t("peers.baseUrl")}
              <Input
                id="peer-url"
                type="url"
                value={baseUrl}
                placeholder={t("peers.baseUrlPlaceholder")}
                onChange={(event) => setBaseUrl(event.target.value)}
              />
            </label>
          </div>
          <label className="flex min-w-0 flex-col gap-2 text-sm text-ink" htmlFor="peer-key">
            {t("peers.apiKey")}
            <Input
              id="peer-key"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </label>
          <p className="max-w-[70ch] text-sm text-ink-muted">{t("peers.apiKeyHint")}</p>
          <div className="flex flex-wrap items-center gap-4">
            <Checkbox
              label={t("peers.enable")}
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
            />
            <Button
              type="submit"
              loading={addPeer.isPending}
              disabled={!name.trim() || !baseUrl.trim() || !apiKey}
            >
              {t("peers.add")}
            </Button>
          </div>
        </div>
      </form>
    </section>
  );
}
