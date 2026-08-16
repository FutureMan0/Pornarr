import type { paths } from "@pornarr/api-client";
import {
  Button,
  Checkbox,
  EmptyState,
  Input,
  SkeletonPoster,
  SkeletonRegion,
  SkeletonText,
  cx,
} from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import type { JSX } from "react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate } from "react-router-dom";
import { useSession } from "../../../auth/session";
import { ErrorScreen } from "../../../errors/error-screen";
import { Numeric, useFormat } from "../../../i18n/format";
import { getApiClient } from "../../../lib/api";
import {
  type ApiRequestError,
  apiFailure,
  isRetryableError,
  messageForError,
  nextStepForError,
} from "../../../lib/api-error";

type QuarantineItem =
  paths["/api/admin/quarantine"]["get"]["responses"][200]["content"]["application/json"][number];
type QuarantineApproval =
  paths["/api/admin/quarantine/items/{item_id}/approve"]["post"]["requestBody"]["content"]["application/json"];
type BulkPreview =
  paths["/api/admin/quarantine/bulk/preview"]["post"]["responses"][200]["content"]["application/json"];

const QUARANTINE_QUERY_KEY = ["admin", "quarantine"] as const;

type CorrectionFields = {
  readonly title: string;
  readonly studio: string;
  readonly releaseDate: string;
  readonly quality: string;
};

type ReasonGroup = {
  readonly code: string;
  readonly items: readonly QuarantineItem[];
};

function stringValue(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value : "";
}

function itemTitle(item: QuarantineItem): string {
  return (
    stringValue(item.extracted_metadata, "title") || item.original_path.split("/").at(-1) || item.id
  );
}

function fieldsFor(item: QuarantineItem): CorrectionFields {
  return {
    title: itemTitle(item),
    studio: stringValue(item.extracted_metadata, "studio"),
    releaseDate: stringValue(item.extracted_metadata, "release_date"),
    quality: stringValue(item.extracted_metadata, "quality"),
  };
}

function correctionFor(item: QuarantineItem, fields: CorrectionFields): QuarantineApproval {
  const original = fieldsFor(item);
  const correction: QuarantineApproval = {};
  if (fields.title !== original.title) correction.title = fields.title.trim() || null;
  if (fields.studio !== original.studio) correction.studio = fields.studio.trim() || null;
  if (fields.releaseDate !== original.releaseDate)
    correction.release_date = fields.releaseDate || null;
  if (fields.quality !== original.quality) correction.quality = fields.quality.trim() || null;
  return correction;
}

function groupByReason(items: readonly QuarantineItem[]): readonly ReasonGroup[] {
  const groups = new Map<string, QuarantineItem[]>();
  for (const item of items) {
    for (const reason of item.reasons) {
      const code = typeof reason.code === "string" ? reason.code : "unknown";
      const group = groups.get(code) ?? [];
      group.push(item);
      groups.set(code, group);
    }
  }
  return [...groups.entries()].map(([code, groupedItems]) => ({ code, items: groupedItems }));
}

function previewFrames(item: QuarantineItem): readonly string[] {
  const candidates = [
    item.technical_details.preview_frames,
    item.extracted_metadata.preview_frames,
  ];
  for (const candidate of candidates) {
    if (Array.isArray(candidate))
      return candidate.filter((frame): frame is string => typeof frame === "string");
  }
  return [];
}

function valueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value === null || value === undefined) return "—";
  return JSON.stringify(value) ?? "—";
}

function reasonEvidence(reason: Record<string, unknown>): Record<string, unknown> {
  const evidence = reason.evidence;
  return evidence !== null && typeof evidence === "object" && !Array.isArray(evidence)
    ? (evidence as Record<string, unknown>)
    : {};
}

function detailText(reason: Record<string, unknown>): string | null {
  return typeof reason.detail === "string" && reason.detail.trim() ? reason.detail : null;
}

function evidenceNumber(reason: Record<string, unknown>, key: "actual" | "minimum"): number | null {
  const value = reasonEvidence(reason)[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function DefinitionList({
  entries,
  emptyLabel,
}: {
  readonly entries: readonly (readonly [string, unknown])[];
  readonly emptyLabel: string;
}): JSX.Element {
  if (entries.length === 0) return <p className="text-sm text-ink-muted">{emptyLabel}</p>;
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="min-w-0 border-b border-border pb-2">
          <dt className="text-2xs text-ink-muted">{key}</dt>
          <dd className="mt-1 break-words text-sm text-ink">{valueText(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function LoadingReview(): JSX.Element {
  const { t } = useTranslation();
  return (
    <SkeletonRegion label={t("quarantine.loading")}>
      <div className="grid gap-6 xl:grid-cols-[minmax(var(--layout-sidebar-width),0.8fr)_minmax(0,1.7fr)]">
        <div className="border border-border bg-surface p-4">
          <SkeletonText lines={7} />
        </div>
        <div className="border border-border bg-surface p-6">
          <SkeletonText lines={4} />
          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            <SkeletonPoster />
            <SkeletonPoster />
          </div>
        </div>
      </div>
    </SkeletonRegion>
  );
}

export function QuarantineReviewRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const session = useSession();
  const queryClient = useQueryClient();
  const [activeReason, setActiveReason] = useState<string | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<readonly string[]>([]);
  const [fields, setFields] = useState<CorrectionFields | null>(null);
  const [bulkPreview, setBulkPreview] = useState<BulkPreview | null>(null);

  const itemsQuery = useQuery<QuarantineItem[], ApiRequestError>({
    queryKey: QUARANTINE_QUERY_KEY,
    enabled: session.data?.role === "admin",
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/quarantine");
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
  const items = itemsQuery.data ?? [];
  const groups = useMemo(() => groupByReason(items), [items]);
  const currentGroup = groups.find((group) => group.code === activeReason) ?? groups[0] ?? null;
  const selectedItem =
    currentGroup?.items.find((item) => item.id === selectedItemId) ??
    currentGroup?.items[0] ??
    null;
  const currentFields = selectedItem === null ? null : (fields ?? fieldsFor(selectedItem));
  const correction =
    selectedItem === null || currentFields === null
      ? {}
      : correctionFor(selectedItem, currentFields);
  const hasCorrection = Object.keys(correction).length > 0;
  const selectedForGroup =
    currentGroup?.items.filter((item) => selectedIds.includes(item.id)) ?? [];

  useEffect(() => {
    if (currentGroup === null) {
      setActiveReason(null);
      setSelectedItemId(null);
      setSelectedIds([]);
      setFields(null);
      setBulkPreview(null);
      return;
    }
    if (activeReason !== currentGroup.code) setActiveReason(currentGroup.code);
    if (selectedItem === null || selectedItem.id !== selectedItemId) {
      setSelectedItemId(currentGroup.items[0]?.id ?? null);
      setFields(currentGroup.items[0] === undefined ? null : fieldsFor(currentGroup.items[0]));
    }
    setSelectedIds((current) =>
      current.filter((id) => currentGroup.items.some((item) => item.id === id)),
    );
  }, [activeReason, currentGroup, selectedItem, selectedItemId]);

  const removeCachedItems = (itemIds: readonly string[]): void => {
    queryClient.setQueryData<QuarantineItem[]>(QUARANTINE_QUERY_KEY, (current) =>
      current?.filter((item) => !itemIds.includes(item.id)),
    );
  };

  const approveMutation = useMutation({
    mutationFn: async ({
      itemId,
      approval,
    }: { readonly itemId: string; readonly approval: QuarantineApproval }) => {
      const { error, response } = await getApiClient().POST(
        "/api/admin/quarantine/items/{item_id}/approve",
        {
          params: { path: { item_id: itemId } },
          body: approval,
        },
      );
      if (error !== undefined) throw apiFailure(error, response);
    },
    onSuccess: (_, variables) => removeCachedItems([variables.itemId]),
  });

  const rejectMutation = useMutation({
    mutationFn: async (itemId: string) => {
      const { error, response } = await getApiClient().POST(
        "/api/admin/quarantine/items/{item_id}/reject",
        {
          params: { path: { item_id: itemId } },
        },
      );
      if (error !== undefined) throw apiFailure(error, response);
    },
    onSuccess: (_, itemId) => removeCachedItems([itemId]),
  });

  const previewMutation = useMutation({
    mutationFn: async ({
      itemIds,
      reasonCode,
    }: { readonly itemIds: readonly string[]; readonly reasonCode: string }) => {
      const { data, error, response } = await getApiClient().POST(
        "/api/admin/quarantine/bulk/preview",
        {
          body: { item_ids: [...itemIds], reason_code: reasonCode },
        },
      );
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: (preview) => setBulkPreview(preview),
  });

  const bulkApproveMutation = useMutation({
    mutationFn: async (preview: BulkPreview) => {
      const { error, response } = await getApiClient().POST("/api/admin/quarantine/bulk/approve", {
        body: {
          item_ids: preview.items.map((item) => item.id),
          reason_code: preview.reason_code,
          token: preview.token,
        },
      });
      if (error !== undefined) throw apiFailure(error, response);
    },
    onSuccess: (_, preview) => {
      removeCachedItems(preview.items.map((item) => item.id));
      setBulkPreview(null);
    },
  });

  if (session.data?.role !== "admin") return <Navigate to="/forbidden" replace />;

  if (itemsQuery.isPending) return <LoadingReview />;
  if (itemsQuery.isError) {
    return (
      <ErrorScreen
        title={messageForError(itemsQuery.error)}
        nextStep={nextStepForError(itemsQuery.error)}
        onRetry={isRetryableError(itemsQuery.error) ? () => void itemsQuery.refetch() : undefined}
      />
    );
  }

  const actionError =
    approveMutation.error ??
    rejectMutation.error ??
    previewMutation.error ??
    bulkApproveMutation.error;
  const actionPending =
    approveMutation.isPending ||
    rejectMutation.isPending ||
    previewMutation.isPending ||
    bulkApproveMutation.isPending;

  const selectGroup = (group: ReasonGroup): void => {
    setActiveReason(group.code);
    setSelectedItemId(group.items[0]?.id ?? null);
    setSelectedIds([]);
    setFields(group.items[0] === undefined ? null : fieldsFor(group.items[0]));
    setBulkPreview(null);
  };

  const selectItem = (item: QuarantineItem): void => {
    setSelectedItemId(item.id);
    setFields(fieldsFor(item));
  };

  const updateField = (key: keyof CorrectionFields, value: string): void => {
    if (currentFields === null) return;
    setFields({ ...currentFields, [key]: value });
  };

  const toggleItem = (itemId: string, checked: boolean): void => {
    setBulkPreview(null);
    setSelectedIds((current) =>
      checked ? [...new Set([...current, itemId])] : current.filter((id) => id !== itemId),
    );
  };

  const toggleGroup = (checked: boolean): void => {
    setBulkPreview(null);
    setSelectedIds(checked ? (currentGroup?.items.map((item) => item.id) ?? []) : []);
  };

  const submitApproval = (): void => {
    if (selectedItem === null) return;
    approveMutation.mutate({ itemId: selectedItem.id, approval: correction });
  };

  return (
    <section className="flex flex-col gap-6" aria-labelledby="quarantine-heading">
      <header className="max-w-[70ch]">
        <h1 id="quarantine-heading" className="text-xl text-ink">
          {t("quarantine.title")}
        </h1>
        <p className="mt-2 text-sm text-ink-muted">{t("quarantine.intro")}</p>
      </header>

      {items.length === 0 ? (
        <EmptyState
          title={t("quarantine.emptyTitle")}
          body={t("quarantine.emptyBody")}
          action={{ label: t("quarantine.emptyAction"), href: "/library" }}
        />
      ) : (
        <div className="grid gap-6 xl:grid-cols-[minmax(var(--layout-sidebar-width),0.8fr)_minmax(0,1.7fr)]">
          <aside
            className="border border-border bg-surface"
            aria-labelledby="quarantine-reasons-heading"
          >
            <div className="border-b border-border px-4 py-3">
              <h2 id="quarantine-reasons-heading" className="text-md text-ink">
                {t("quarantine.reasons")}
              </h2>
              <p className="mt-1 text-xs text-ink-muted">{t("quarantine.reasonHint")}</p>
            </div>

            <div className="p-2">
              {groups.map((group) => (
                <Button
                  key={group.code}
                  variant="ghost"
                  aria-pressed={currentGroup?.code === group.code}
                  aria-label={reasonWithCountLabel(t, group.code, group.items.length)}
                  className={cx(
                    "mb-1 flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm",
                    "!justify-between !text-left",
                    currentGroup?.code === group.code
                      ? "bg-[var(--primary-weak)] text-ink"
                      : "text-ink-muted hover:bg-surface-2 hover:text-ink",
                  )}
                  onClick={() => selectGroup(group)}
                >
                  <span>{reasonLabel(t, group.code)}</span>
                  <Numeric className="text-xs">{format.number(group.items.length)}</Numeric>
                </Button>
              ))}
            </div>

            {currentGroup !== null ? (
              <div className="border-t border-border p-4">
                <Checkbox
                  label={t("quarantine.selectReasonWithCount", {
                    count: currentGroup.items.length,
                  })}
                  checked={selectedForGroup.length === currentGroup.items.length}
                  onChange={(event) => toggleGroup(event.currentTarget.checked)}
                />
                <Button
                  variant="secondary"
                  className="mt-3 w-full"
                  disabled={selectedForGroup.length === 0 || actionPending}
                  loading={previewMutation.isPending}
                  onClick={() =>
                    previewMutation.mutate({
                      itemIds: selectedForGroup.map((item) => item.id),
                      reasonCode: currentGroup.code,
                    })
                  }
                >
                  {t("quarantine.previewBulkWithCount", { count: selectedForGroup.length })}
                </Button>
              </div>
            ) : null}
          </aside>

          <div className="min-w-0 border border-border bg-surface">
            {selectedItem !== null && currentFields !== null ? (
              <>
                <div className="border-b border-border px-4 py-3 sm:px-6">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-2xs text-ink-muted">{t("quarantine.reviewing")}</p>
                      <h2 className="mt-1 text-lg text-ink">{itemTitle(selectedItem)}</h2>
                    </div>
                    <p className="text-xs text-ink-muted">
                      {t("quarantine.createdAt", {
                        date: format.dateTime(new Date(selectedItem.created_at)),
                      })}
                    </p>
                  </div>
                </div>

                <div className="border-b border-border px-4 py-3 sm:px-6">
                  <ul className="flex flex-wrap gap-2" aria-label={t("quarantine.itemsInReason")}>
                    {currentGroup?.items.map((item) => (
                      <li key={item.id} className="flex items-center gap-2">
                        <Checkbox
                          label={t("quarantine.selectItem", { title: itemTitle(item) })}
                          className="shrink-0"
                          checked={selectedIds.includes(item.id)}
                          onChange={(event) => toggleItem(item.id, event.currentTarget.checked)}
                        />
                        <Button
                          variant="ghost"
                          aria-pressed={item.id === selectedItem.id}
                          className={cx(
                            "rounded-md px-2 py-1 text-sm",
                            item.id === selectedItem.id
                              ? "bg-[var(--primary-weak)] text-ink"
                              : "text-ink-muted hover:bg-surface-2 hover:text-ink",
                          )}
                          onClick={() => selectItem(item)}
                        >
                          {itemTitle(item)}
                        </Button>
                      </li>
                    ))}
                  </ul>
                </div>

                {actionError !== null ? (
                  <p
                    className="border-b border-border bg-[var(--danger-weak)] px-4 py-3 text-sm text-ink"
                    role="alert"
                  >
                    {messageForError(actionError)}
                  </p>
                ) : null}

                <div className="flex flex-col gap-6 px-4 py-5 sm:px-6">
                  <section aria-labelledby="quarantine-evidence-heading">
                    <h3 id="quarantine-evidence-heading" className="text-md text-ink">
                      {t("quarantine.matchedRules")}
                    </h3>
                    <div className="mt-3 divide-y divide-border border-y border-border">
                      {selectedItem.reasons.map((reason, index) => {
                        const actual = evidenceNumber(reason, "actual");
                        const minimum = evidenceNumber(reason, "minimum");
                        const evidence = reasonEvidence(reason);
                        return (
                          <article key={`${String(reason.code)}-${index}`} className="py-3">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <h4 className="text-sm text-ink">
                                {reasonLabel(t, String(reason.code))}
                              </h4>
                              {actual !== null && minimum !== null ? (
                                <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
                                  <div>
                                    <dt className="inline">{t("quarantine.confidenceActual")}: </dt>
                                    <dd className="inline text-ink">
                                      <Numeric>{format.number(actual, 2)}</Numeric>
                                    </dd>
                                  </div>
                                  <div>
                                    <dt className="inline">
                                      {t("quarantine.confidenceMinimum")}:{" "}
                                    </dt>
                                    <dd className="inline text-ink">
                                      <Numeric>{format.number(minimum, 2)}</Numeric>
                                    </dd>
                                  </div>
                                </dl>
                              ) : null}
                            </div>
                            {detailText(reason) !== null ? (
                              <p className="mt-2 text-sm text-ink-muted">{detailText(reason)}</p>
                            ) : null}
                            <div className="mt-3">
                              <DefinitionList
                                entries={Object.entries(evidence)}
                                emptyLabel={t("quarantine.noEvidence")}
                              />
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  </section>

                  <section aria-labelledby="quarantine-preview-heading">
                    <h3 id="quarantine-preview-heading" className="text-md text-ink">
                      {t("quarantine.previewFrames")}
                    </h3>
                    {previewFrames(selectedItem).length === 0 ? (
                      <p className="mt-2 text-sm text-ink-muted">
                        {t("quarantine.previewUnavailable")}
                      </p>
                    ) : (
                      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3">
                        {previewFrames(selectedItem).map((frame, index) => (
                          <img
                            key={frame}
                            src={frame}
                            alt={t("quarantine.previewFrame", { number: index + 1 })}
                            className="aspect-video w-full rounded-md border border-border object-cover"
                            loading="lazy"
                          />
                        ))}
                      </div>
                    )}
                  </section>

                  <div className="grid gap-6 lg:grid-cols-2">
                    <section aria-labelledby="quarantine-metadata-heading">
                      <h3 id="quarantine-metadata-heading" className="text-md text-ink">
                        {t("quarantine.extractedMetadata")}
                      </h3>
                      <div className="mt-3">
                        <DefinitionList
                          entries={Object.entries(selectedItem.extracted_metadata)}
                          emptyLabel={t("quarantine.noMetadata")}
                        />
                      </div>
                    </section>
                    <section aria-labelledby="quarantine-technical-heading">
                      <h3 id="quarantine-technical-heading" className="text-md text-ink">
                        {t("quarantine.technicalDetails")}
                      </h3>
                      <div className="mt-3">
                        <DefinitionList
                          entries={Object.entries(selectedItem.technical_details).filter(
                            ([key]) => key !== "preview_frames",
                          )}
                          emptyLabel={t("quarantine.noTechnicalDetails")}
                        />
                      </div>
                    </section>
                  </div>

                  <section
                    className="grid gap-x-4 gap-y-3 border-t border-border pt-5 sm:grid-cols-2"
                    aria-labelledby="quarantine-correction-heading"
                  >
                    <div className="sm:col-span-2">
                      <h3 id="quarantine-correction-heading" className="text-md text-ink">
                        {t("quarantine.correction")}
                      </h3>
                      <p className="mt-1 text-xs text-ink-muted">
                        {t("quarantine.correctionHint")}
                      </p>
                    </div>
                    <label htmlFor="quarantine-title" className="text-sm text-ink">
                      {t("quarantine.fields.title")}
                      <Input
                        id="quarantine-title"
                        className="mt-1"
                        value={currentFields.title}
                        onChange={(event) => updateField("title", event.currentTarget.value)}
                      />
                    </label>
                    <label htmlFor="quarantine-studio" className="text-sm text-ink">
                      {t("quarantine.fields.studio")}
                      <Input
                        id="quarantine-studio"
                        className="mt-1"
                        value={currentFields.studio}
                        onChange={(event) => updateField("studio", event.currentTarget.value)}
                      />
                    </label>
                    <label htmlFor="quarantine-release-date" className="text-sm text-ink">
                      {t("quarantine.fields.releaseDate")}
                      <Input
                        id="quarantine-release-date"
                        className="mt-1"
                        type="date"
                        value={currentFields.releaseDate}
                        onChange={(event) => updateField("releaseDate", event.currentTarget.value)}
                      />
                    </label>
                    <label htmlFor="quarantine-quality" className="text-sm text-ink">
                      {t("quarantine.fields.quality")}
                      <Input
                        id="quarantine-quality"
                        className="mt-1"
                        value={currentFields.quality}
                        onChange={(event) => updateField("quality", event.currentTarget.value)}
                      />
                    </label>
                  </section>

                  <section
                    className="border-t border-border pt-5"
                    aria-labelledby="quarantine-paths-heading"
                  >
                    <h3 id="quarantine-paths-heading" className="text-md text-ink">
                      {t("quarantine.paths")}
                    </h3>
                    <dl className="mt-3 grid gap-3">
                      <div>
                        <dt className="text-2xs text-ink-muted">{t("quarantine.originalPath")}</dt>
                        <dd className="mt-1 break-all font-mono text-xs text-ink">
                          {selectedItem.original_path}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-2xs text-ink-muted">
                          {t("quarantine.quarantinePath")}
                        </dt>
                        <dd className="mt-1 break-all font-mono text-xs text-ink">
                          {selectedItem.quarantine_path}
                        </dd>
                      </div>
                    </dl>
                  </section>

                  <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-5">
                    <Button
                      variant="ghost"
                      disabled={actionPending}
                      loading={rejectMutation.isPending}
                      error={rejectMutation.isError}
                      onClick={() => rejectMutation.mutate(selectedItem.id)}
                    >
                      {t("quarantine.reject")}
                    </Button>
                    <Button
                      disabled={
                        actionPending || (hasCorrection && currentFields.title.trim().length === 0)
                      }
                      loading={approveMutation.isPending}
                      error={approveMutation.isError}
                      onClick={submitApproval}
                    >
                      {hasCorrection ? t("quarantine.correctAndApprove") : t("quarantine.approve")}
                    </Button>
                  </div>
                </div>
              </>
            ) : null}
          </div>
        </div>
      )}

      {bulkPreview !== null ? (
        <section
          className="border border-border bg-surface"
          aria-labelledby="quarantine-bulk-heading"
        >
          <div className="border-b border-border px-4 py-3 sm:px-6">
            <h2 id="quarantine-bulk-heading" className="text-md text-ink">
              {t("quarantine.bulkPreview")}
            </h2>
            <p className="mt-1 text-sm text-ink-muted">
              {t("quarantine.bulkPreviewHint", { count: bulkPreview.items.length })}
            </p>
          </div>
          <ul className="divide-y divide-border" aria-label={t("quarantine.bulkItems")}>
            {bulkPreview.items.map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 sm:px-6"
              >
                <span className="text-sm text-ink">{itemTitle(item)}</span>
                <span className="font-mono text-xs text-ink-muted">{item.original_path}</span>
              </li>
            ))}
          </ul>
          <div className="flex justify-end gap-2 border-t border-border px-4 py-3 sm:px-6">
            <Button
              variant="ghost"
              disabled={bulkApproveMutation.isPending}
              onClick={() => setBulkPreview(null)}
            >
              {t("quarantine.cancelBulk")}
            </Button>
            <Button
              loading={bulkApproveMutation.isPending}
              error={bulkApproveMutation.isError}
              onClick={() => bulkApproveMutation.mutate(bulkPreview)}
            >
              {t("quarantine.confirmBulkWithCount", { count: bulkPreview.items.length })}
            </Button>
          </div>
        </section>
      ) : null}
    </section>
  );
}

function reasonLabel(t: TFunction, code: string): string {
  switch (code) {
    case "low_confidence":
      return t("quarantine.reason.low_confidence");
    case "filter_rule":
      return t("quarantine.reason.filter_rule");
    case "unexpected_file_type":
      return t("quarantine.reason.unexpected_file_type");
    case "contradictory_duplicate":
      return t("quarantine.reason.contradictory_duplicate");
    default:
      return t("quarantine.reason.unknown");
  }
}

function reasonWithCountLabel(t: TFunction, code: string, count: number): string {
  switch (code) {
    case "low_confidence":
      return t("quarantine.reasonWithCount.low_confidence", { count });
    case "filter_rule":
      return t("quarantine.reasonWithCount.filter_rule", { count });
    case "unexpected_file_type":
      return t("quarantine.reasonWithCount.unexpected_file_type", { count });
    case "contradictory_duplicate":
      return t("quarantine.reasonWithCount.contradictory_duplicate", { count });
    default:
      return t("quarantine.reasonWithCount.unknown", { count });
  }
}
