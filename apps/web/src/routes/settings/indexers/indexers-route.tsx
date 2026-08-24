/**
 * Indexers: the servers this one asks for releases.
 *
 * ADR 0002 L9 says indexers are "managed through the administration UI", and
 * until this screen existed they were not: the setup wizard created the first
 * one and nothing could change it afterwards. Editing meant deleting the row and
 * making a new one, which throws away its health, its statistics and - through
 * `ON DELETE CASCADE` on `release_cache.indexer_id` - every release cached from
 * it.
 *
 * Built on the conventions of the root folders and shared libraries sections
 * beside it: a list, an add form, and both editing and removal opening inside
 * the row rather than in a modal, because DESIGN.md says modal is never the
 * first answer and a two-step control keeps the indexer being changed on screen
 * while the reader decides.
 *
 * The field vocabulary is the setup wizard's, deliberately: "Indexer type",
 * "Base URL", "API key" are what the wizard asks for, and a second name for the
 * same field would make the two screens read like two products. The protocol is
 * not asked for at all - `INDEXER_PROTOCOLS` derives it from the implementation
 * the way `routers/setup.py` does.
 *
 * The key is write-only. It never comes back from the API, so the edit form
 * starts empty and an empty box means "keep the key you have" rather than
 * "clear it" - the one place this screen departs from a plain edit form, and the
 * reason it says so in its own copy.
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
  INDEXERS_KEY,
  INDEXER_PROTOCOLS,
  type Indexer,
  type IndexerImplementation,
  type IndexerUpdate,
  type IndexerWrite,
  useIndexers,
} from "./indexers";

/**
 * The health vocabulary `IndexerHealth` uses, plus the state an indexer nobody
 * has tested yet is in. `health` is an open string on purpose: a value this
 * build has not heard of is reported rather than hidden.
 */
const HEALTH_KEYS = {
  unknown: "indexers.healthUnknown",
  healthy: "indexers.healthHealthy",
  unhealthy: "indexers.healthUnhealthy",
  "half-open": "indexers.healthHalfOpen",
} as const;

interface IndexerForm {
  readonly name: string;
  readonly implementation: IndexerImplementation;
  readonly baseUrl: string;
  readonly apiKey: string;
  readonly priority: string;
  readonly enabled: boolean;
  readonly searchCategories: string;
}

const EMPTY_FORM: IndexerForm = {
  name: "",
  implementation: "torznab",
  baseUrl: "",
  apiKey: "",
  priority: "0",
  enabled: true,
  searchCategories: "",
};

function formFor(indexer: Indexer): IndexerForm {
  return {
    name: indexer.name,
    implementation: indexer.implementation === "newznab" ? "newznab" : "torznab",
    baseUrl: indexer.base_url,
    // Write-only: there is nothing to prefill, and an empty box keeps it.
    apiKey: "",
    priority: String(indexer.priority),
    enabled: indexer.enabled,
    searchCategories: indexer.search_categories.join(", "),
  };
}

/** "6000, 6010" and "6000 6010" both mean the same two categories. */
function categoriesFrom(value: string): string[] {
  return value.split(/[,\s]+/).filter((category) => category !== "");
}

function isComplete(form: IndexerForm, { keyRequired }: { keyRequired: boolean }): boolean {
  return (
    form.name.trim() !== "" &&
    form.baseUrl.trim() !== "" &&
    (!keyRequired || form.apiKey !== "") &&
    Number.isFinite(Number(form.priority))
  );
}

function IndexersLoading(): JSX.Element {
  const { t } = useTranslation();
  return (
    <SkeletonRegion label={t("indexers.loading")}>
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

/**
 * The fields, once, for the add form and for every row's edit form.
 *
 * `idPrefix` is what keeps a label pointing at its own control when the add form
 * and an open edit form are on screen together: duplicate ids would send both
 * labels to whichever field the document happens to reach first.
 */
function IndexerFields({
  idPrefix,
  value,
  onChange,
  keyHint,
}: {
  readonly idPrefix: string;
  readonly value: IndexerForm;
  readonly onChange: (next: IndexerForm) => void;
  readonly keyHint: string;
}): JSX.Element {
  const { t } = useTranslation();

  return (
    <div className="mt-4 flex flex-col gap-4">
      <div className="flex flex-col gap-4 sm:flex-row">
        <label
          className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-ink"
          htmlFor={`${idPrefix}-name`}
        >
          {t("indexers.name")}
          <Input
            id={`${idPrefix}-name`}
            value={value.name}
            placeholder={t("indexers.namePlaceholder")}
            onChange={(event) => onChange({ ...value, name: event.target.value })}
          />
        </label>
        <label
          className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-ink"
          htmlFor={`${idPrefix}-implementation`}
        >
          {t("indexers.implementation")}
          <Select
            id={`${idPrefix}-implementation`}
            value={value.implementation}
            onChange={(event) =>
              onChange({ ...value, implementation: event.target.value as IndexerImplementation })
            }
          >
            <option value="torznab">{t("indexers.implementations.torznab")}</option>
            <option value="newznab">{t("indexers.implementations.newznab")}</option>
          </Select>
        </label>
      </div>
      <label className="flex min-w-0 flex-col gap-2 text-sm text-ink" htmlFor={`${idPrefix}-url`}>
        {t("indexers.baseUrl")}
        <Input
          id={`${idPrefix}-url`}
          type="url"
          value={value.baseUrl}
          placeholder={t("indexers.baseUrlPlaceholder")}
          onChange={(event) => onChange({ ...value, baseUrl: event.target.value })}
        />
      </label>
      <label className="flex min-w-0 flex-col gap-2 text-sm text-ink" htmlFor={`${idPrefix}-key`}>
        {t("indexers.apiKey")}
        <Input
          id={`${idPrefix}-key`}
          type="password"
          autoComplete="off"
          value={value.apiKey}
          onChange={(event) => onChange({ ...value, apiKey: event.target.value })}
        />
      </label>
      <p className="max-w-[70ch] text-sm text-ink-muted">{keyHint}</p>
      <div className="flex flex-col gap-4 sm:flex-row">
        <label
          className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-ink"
          htmlFor={`${idPrefix}-priority`}
        >
          {t("indexers.priority")}
          <Input
            id={`${idPrefix}-priority`}
            type="number"
            value={value.priority}
            onChange={(event) => onChange({ ...value, priority: event.target.value })}
          />
        </label>
        <label
          className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-ink"
          htmlFor={`${idPrefix}-categories`}
        >
          {t("indexers.searchCategories")}
          <Input
            id={`${idPrefix}-categories`}
            value={value.searchCategories}
            placeholder={t("indexers.searchCategoriesPlaceholder")}
            onChange={(event) => onChange({ ...value, searchCategories: event.target.value })}
          />
        </label>
      </div>
      <p className="max-w-[70ch] text-sm text-ink-muted">{t("indexers.searchCategoriesHint")}</p>
      <Checkbox
        label={t("indexers.enable")}
        checked={value.enabled}
        onChange={(event) => onChange({ ...value, enabled: event.target.checked })}
      />
    </div>
  );
}

export function IndexersRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const session = useSession();
  const queryClient = useQueryClient();
  const indexersQuery = useIndexers();
  const [addForm, setAddForm] = useState<IndexerForm>(EMPTY_FORM);
  const [editing, setEditing] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<IndexerForm>(EMPTY_FORM);
  const [pendingRemoval, setPendingRemoval] = useState<string | null>(null);

  const healthLabel = (health: string): string => {
    const key = HEALTH_KEYS[health as keyof typeof HEALTH_KEYS];
    return key === undefined ? t("indexers.healthOther", { health }) : t(key);
  };

  const replaceInList = (indexer: Indexer): void => {
    queryClient.setQueryData<Indexer[]>(
      INDEXERS_KEY,
      (current) =>
        current?.map((existing) => (existing.id === indexer.id ? indexer : existing)) ?? [],
    );
  };

  const addIndexer = useMutation<Indexer, ApiRequestError, IndexerWrite>({
    mutationFn: async (body) => {
      const { data, error, response } = await getApiClient().POST("/api/admin/indexers", { body });
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: (indexer) => {
      queryClient.setQueryData<Indexer[]>(INDEXERS_KEY, (current) =>
        [...(current ?? []), indexer].sort(
          (left, right) => left.priority - right.priority || left.name.localeCompare(right.name),
        ),
      );
      setAddForm(EMPTY_FORM);
    },
  });

  const saveIndexer = useMutation<Indexer, ApiRequestError, { id: string; body: IndexerUpdate }>({
    mutationFn: async ({ id, body }) => {
      const { data, error, response } = await getApiClient().PUT(
        "/api/admin/indexers/{indexer_id}",
        { params: { path: { indexer_id: id } }, body },
      );
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: (indexer) => {
      replaceInList(indexer);
      setEditing(null);
    },
  });

  const checkIndexer = useMutation<Indexer, ApiRequestError, string>({
    mutationFn: async (indexerId) => {
      const { data, error, response } = await getApiClient().POST(
        "/api/admin/indexers/{indexer_id}/test",
        { params: { path: { indexer_id: indexerId } } },
      );
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: replaceInList,
  });

  const removeIndexer = useMutation<void, ApiRequestError, string>({
    mutationFn: async (indexerId) => {
      const { error, response } = await getApiClient().DELETE("/api/admin/indexers/{indexer_id}", {
        params: { path: { indexer_id: indexerId } },
      });
      if (error !== undefined) throw apiFailure(error, response);
    },
    onSuccess: (_, indexerId) => {
      queryClient.setQueryData<Indexer[]>(
        INDEXERS_KEY,
        (current) => current?.filter((indexer) => indexer.id !== indexerId) ?? [],
      );
      setPendingRemoval(null);
    },
  });

  const indexers = indexersQuery.data ?? [];
  const mutationError =
    addIndexer.error ?? saveIndexer.error ?? checkIndexer.error ?? removeIndexer.error ?? null;
  if (session.data?.role !== "admin" && session.data !== undefined)
    return <Navigate to="/forbidden" replace />;
  if (indexersQuery.error !== null) {
    return (
      <ErrorScreen
        title={messageForError(indexersQuery.error)}
        nextStep={nextStepForError(indexersQuery.error)}
        onRetry={
          isRetryableError(indexersQuery.error)
            ? () => void queryClient.invalidateQueries({ queryKey: INDEXERS_KEY })
            : undefined
        }
      />
    );
  }
  if (indexersQuery.isPending) return <IndexersLoading />;

  const submitAdd = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!isComplete(addForm, { keyRequired: true })) return;
    const categories = categoriesFrom(addForm.searchCategories);
    addIndexer.reset();
    addIndexer.mutate({
      name: addForm.name.trim(),
      protocol: INDEXER_PROTOCOLS[addForm.implementation],
      implementation: addForm.implementation,
      base_url: addForm.baseUrl.trim(),
      api_key: addForm.apiKey,
      priority: Number(addForm.priority),
      enabled: addForm.enabled,
      // Left blank means "whatever the server configures by default", which is
      // not the same request as an empty selection.
      ...(categories.length === 0 ? {} : { search_categories: categories }),
    });
  };

  const submitEdit = (event: FormEvent<HTMLFormElement>, indexerId: string): void => {
    event.preventDefault();
    if (!isComplete(editForm, { keyRequired: false })) return;
    saveIndexer.reset();
    saveIndexer.mutate({
      id: indexerId,
      body: {
        name: editForm.name.trim(),
        protocol: INDEXER_PROTOCOLS[editForm.implementation],
        implementation: editForm.implementation,
        base_url: editForm.baseUrl.trim(),
        // An empty box keeps the stored key rather than clearing it.
        ...(editForm.apiKey === "" ? {} : { api_key: editForm.apiKey }),
        priority: Number(editForm.priority),
        enabled: editForm.enabled,
        search_categories: categoriesFrom(editForm.searchCategories),
      },
    });
  };

  return (
    <section className="flex flex-col gap-6" aria-labelledby="indexers-heading">
      <header className="max-w-[70ch]">
        <h1 id="indexers-heading" className="text-xl text-ink">
          {t("indexers.title")}
        </h1>
        <p className="mt-2 text-sm text-ink-muted">{t("indexers.intro")}</p>
      </header>

      {mutationError === null ? null : (
        <ErrorScreen
          title={messageForError(mutationError)}
          nextStep={nextStepForError(mutationError)}
        />
      )}

      <section className="border border-border bg-surface p-6" aria-labelledby="indexers-list">
        <h2 id="indexers-list" className="text-lg text-ink">
          {t("indexers.configured")}
        </h2>

        {indexers.length === 0 ? (
          <p className="mt-4 max-w-[70ch] text-sm text-ink-muted">{t("indexers.none")}</p>
        ) : (
          <ul className="mt-4 flex flex-col gap-4">
            {indexers.map((indexer) => (
              <li key={indexer.id} className="border border-border-control bg-surface-2 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="text-md text-ink">{indexer.name}</h3>
                    <p className="break-all text-sm text-ink-muted">{indexer.base_url}</p>
                  </div>
                  <div className="flex flex-wrap gap-3">
                    <Button
                      variant="secondary"
                      aria-label={t("indexers.testIndexer", { name: indexer.name })}
                      loading={checkIndexer.isPending && checkIndexer.variables === indexer.id}
                      onClick={() => {
                        checkIndexer.reset();
                        checkIndexer.mutate(indexer.id);
                      }}
                    >
                      {t("indexers.test")}
                    </Button>
                    {editing === indexer.id ? null : (
                      <Button
                        variant="secondary"
                        aria-label={t("indexers.editIndexer", { name: indexer.name })}
                        onClick={() => {
                          saveIndexer.reset();
                          setEditForm(formFor(indexer));
                          setEditing(indexer.id);
                        }}
                      >
                        {t("indexers.edit")}
                      </Button>
                    )}
                    {pendingRemoval === indexer.id ? null : (
                      <Button
                        variant="ghost"
                        aria-label={t("indexers.removeIndexer", { name: indexer.name })}
                        onClick={() => {
                          removeIndexer.reset();
                          setPendingRemoval(indexer.id);
                        }}
                      >
                        {t("indexers.remove")}
                      </Button>
                    )}
                  </div>
                </div>

                <dl className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("indexers.status")}</dt>
                    <dd className="mt-1 text-sm text-ink">
                      {indexer.enabled ? t("indexers.enabled") : t("indexers.disabled")}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("indexers.health")}</dt>
                    {/* Never colour alone: the state is the word. */}
                    <dd className="mt-1 text-sm text-ink">{healthLabel(indexer.health)}</dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("indexers.implementation")}</dt>
                    <dd className="mt-1 text-sm text-ink">
                      {indexer.implementation === "newznab" || indexer.implementation === "torznab"
                        ? t(`indexers.implementations.${indexer.implementation}`)
                        : indexer.implementation}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("indexers.priority")}</dt>
                    <dd className="mt-1 text-sm text-ink tabular">
                      {format.number(indexer.priority)}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("indexers.searchCategories")}</dt>
                    <dd className="mt-1 text-sm text-ink tabular">
                      {indexer.search_categories.length === 0
                        ? t("indexers.allCategories")
                        : indexer.search_categories.join(", ")}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("indexers.queries")}</dt>
                    <dd className="mt-1 text-sm text-ink tabular">
                      {format.number(indexer.stats.queries)}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("indexers.failures")}</dt>
                    <dd className="mt-1 text-sm text-ink tabular">
                      {format.number(indexer.stats.failures)}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("indexers.lastTested")}</dt>
                    <dd className="mt-1 text-sm text-ink">
                      {indexer.last_tested_at === null
                        ? t("indexers.never")
                        : format.relativeDate(new Date(indexer.last_tested_at))}
                    </dd>
                  </div>
                </dl>

                {indexer.last_error === null ? null : (
                  // The reason comes from the indexer, so it is shown as the
                  // detail it is, inside a sentence this side wrote.
                  <p className="mt-3 max-w-[70ch] break-all text-sm text-ink-muted">
                    {t("indexers.lastErrorDetail", { reason: indexer.last_error })}
                  </p>
                )}

                {editing === indexer.id ? (
                  <form
                    className="mt-4 border-t border-border pt-4"
                    aria-label={t("indexers.editIndexer", { name: indexer.name })}
                    onSubmit={(event) => submitEdit(event, indexer.id)}
                  >
                    <IndexerFields
                      idPrefix={`indexer-${indexer.id}`}
                      value={editForm}
                      onChange={setEditForm}
                      keyHint={t("indexers.apiKeyKeepHint")}
                    />
                    <div className="mt-4 flex flex-wrap gap-3">
                      <Button
                        type="submit"
                        loading={saveIndexer.isPending}
                        disabled={!isComplete(editForm, { keyRequired: false })}
                      >
                        {t("indexers.save")}
                      </Button>
                      <Button variant="ghost" onClick={() => setEditing(null)}>
                        {t("indexers.cancelEdit")}
                      </Button>
                    </div>
                  </form>
                ) : null}

                {pendingRemoval === indexer.id ? (
                  <div className="mt-4 border-t border-border pt-4">
                    <p className="max-w-[70ch] text-sm text-ink">
                      {t("indexers.confirmRemoval", { name: indexer.name })}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-3">
                      <Button
                        loading={removeIndexer.isPending}
                        onClick={() => removeIndexer.mutate(indexer.id)}
                      >
                        {t("indexers.confirmRemovalAction")}
                      </Button>
                      <Button variant="ghost" onClick={() => setPendingRemoval(null)}>
                        {t("indexers.cancelRemoval")}
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
        aria-labelledby="indexers-add"
        onSubmit={submitAdd}
      >
        <h2 id="indexers-add" className="text-lg text-ink">
          {t("indexers.addTitle")}
        </h2>
        <p className="mt-1 max-w-[70ch] text-sm text-ink-muted">{t("indexers.addHint")}</p>

        <IndexerFields
          idPrefix="indexer-new"
          value={addForm}
          onChange={setAddForm}
          keyHint={t("indexers.apiKeyHint")}
        />

        <div className="mt-4">
          <Button
            type="submit"
            loading={addIndexer.isPending}
            disabled={!isComplete(addForm, { keyRequired: true })}
          >
            {t("indexers.add")}
          </Button>
        </div>
      </form>
    </section>
  );
}
