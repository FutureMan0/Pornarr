import type { paths } from "@pornarr/api-client";
import { Button, Checkbox, Input, Select, SkeletonRegion, SkeletonText, cx } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import type { JSX } from "react";
import { useEffect, useMemo, useState } from "react";
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

type QualityDefinition =
  paths["/api/admin/quality/definitions"]["get"]["responses"][200]["content"]["application/json"][number];
type QualityProfile =
  paths["/api/admin/quality/profiles"]["get"]["responses"][200]["content"]["application/json"][number];
type QualityProfileWrite =
  paths["/api/admin/quality/profiles"]["post"]["requestBody"]["content"]["application/json"];
type CustomFormat =
  paths["/api/admin/quality/custom-formats"]["get"]["responses"][200]["content"]["application/json"][number];
type CustomFormatWrite =
  paths["/api/admin/quality/custom-formats"]["post"]["requestBody"]["content"]["application/json"];
type CustomFormatCondition = CustomFormatWrite["conditions"][number];
type QualityPreview =
  paths["/api/admin/quality/preview"]["post"]["responses"][200]["content"]["application/json"];
type ConditionField = CustomFormatCondition["field"];
type ConditionOperator = CustomFormatCondition["operator"];

const DEFINITIONS_KEY = ["admin", "quality", "definitions"] as const;
const PROFILES_KEY = ["admin", "quality", "profiles"] as const;
const FORMATS_KEY = ["admin", "quality", "custom-formats"] as const;
const CONDITION_FIELDS: readonly ConditionField[] = [
  "title",
  "codec",
  "source",
  "size",
  "indexer",
  "protocol",
  "flags",
];
const CONDITION_OPERATORS: readonly ConditionOperator[] = [
  "equals",
  "contains",
  "greater_than",
  "less_than",
];

type ProfileDraft = QualityProfileWrite & { readonly id: string | null };
type ConditionDraft = {
  readonly field: ConditionField;
  readonly operator: ConditionOperator;
  readonly value: string;
  readonly negate: boolean;
  readonly required: boolean;
};
type FormatDraft = {
  readonly id: string | null;
  readonly name: string;
  readonly score: string;
  readonly conditions: readonly ConditionDraft[];
};

function profileDraft(profile: QualityProfile): ProfileDraft {
  return {
    id: profile.id,
    name: profile.name,
    quality_definition_ids: profile.qualities.map((quality) => quality.id),
    cutoff_quality_id: profile.cutoff_quality_id,
    minimum_custom_format_score: profile.minimum_custom_format_score,
    is_default: profile.is_default,
  };
}

function newProfileDraft(definitions: readonly QualityDefinition[]): ProfileDraft {
  const ids = definitions.map((definition) => definition.id);
  return {
    id: null,
    name: "",
    quality_definition_ids: ids,
    cutoff_quality_id: ids.at(-1) ?? "",
    minimum_custom_format_score: 0,
    is_default: false,
  };
}

function conditionValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (Array.isArray(value)) return value.join(", ");
  return "";
}

function formatDraft(format: CustomFormat): FormatDraft {
  return {
    id: format.id,
    name: format.name,
    score: String(format.score),
    conditions: format.conditions.map((condition) => ({
      field: condition.field,
      operator: condition.operator,
      value: conditionValue(condition.value),
      negate: condition.negate,
      required: condition.required,
    })),
  };
}

function newFormatDraft(): FormatDraft {
  return {
    id: null,
    name: "",
    score: "0",
    conditions: [
      { field: "title", operator: "contains", value: "", negate: false, required: true },
    ],
  };
}

function formatPayload(format: FormatDraft): CustomFormatWrite {
  return {
    name: format.name.trim(),
    score: Number.parseInt(format.score, 10) || 0,
    conditions: format.conditions.map((condition) => ({
      field: condition.field,
      operator: condition.operator,
      value: numericalOperator(condition.operator)
        ? Number(condition.value)
        : condition.value.trim(),
      negate: condition.negate,
      required: condition.required,
    })),
  };
}

function numericalOperator(operator: ConditionOperator): boolean {
  return operator === "greater_than" || operator === "less_than";
}

function profilePayload(draft: ProfileDraft): QualityProfileWrite {
  return {
    name: draft.name.trim(),
    quality_definition_ids: [...draft.quality_definition_ids],
    cutoff_quality_id: draft.cutoff_quality_id,
    minimum_custom_format_score: draft.minimum_custom_format_score,
    is_default: draft.is_default,
  };
}

function moveItem(values: readonly string[], itemId: string, targetIndex: number): string[] {
  const sourceIndex = values.indexOf(itemId);
  if (sourceIndex < 0 || sourceIndex === targetIndex) return [...values];
  const next = [...values];
  next.splice(sourceIndex, 1);
  next.splice(targetIndex, 0, itemId);
  return next;
}

function verdictLabel(t: TFunction, verdict: string): string {
  switch (verdict) {
    case "grab":
      return t("quality.verdict.grab");
    case "upgrade":
      return t("quality.verdict.upgrade");
    default:
      return t("quality.verdict.reject");
  }
}

function reasonLabel(t: TFunction, reason: string): string {
  switch (reason) {
    case "not_in_profile":
      return t("quality.reason.not_in_profile");
    case "below_minimum_score":
      return t("quality.reason.below_minimum_score");
    case "no_existing_file":
      return t("quality.reason.no_existing_file");
    case "not_an_upgrade":
      return t("quality.reason.not_an_upgrade");
    case "cutoff_met":
      return t("quality.reason.cutoff_met");
    case "upgrade_available":
      return t("quality.reason.upgrade_available");
    default:
      return t("quality.reason.quality_not_detected");
  }
}

function fieldLabel(t: TFunction, field: string): string {
  switch (field) {
    case "title":
      return t("quality.fields.title");
    case "codec":
      return t("quality.fields.codec");
    case "source":
      return t("quality.fields.source");
    case "size":
      return t("quality.fields.size");
    case "indexer":
      return t("quality.fields.indexer");
    case "protocol":
      return t("quality.fields.protocol");
    case "flags":
      return t("quality.fields.flags");
    default:
      return t("quality.fields.resolution");
  }
}

function QualityLoading(): JSX.Element {
  const { t } = useTranslation();
  return (
    <SkeletonRegion label={t("quality.loading")}>
      <div className="grid gap-4 xl:grid-cols-[minmax(var(--layout-sidebar-width),0.7fr)_minmax(0,1.7fr)]">
        <div className="border border-border bg-surface p-4">
          <SkeletonText lines={5} />
        </div>
        <div className="border border-border bg-surface p-6">
          <SkeletonText lines={9} />
        </div>
      </div>
    </SkeletonRegion>
  );
}

export function QualityProfilesRoute(): JSX.Element {
  const { t } = useTranslation();
  const session = useSession();
  const queryClient = useQueryClient();
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [formatDrafts, setFormatDrafts] = useState<readonly FormatDraft[]>([]);
  const [removedFormatIds, setRemovedFormatIds] = useState<readonly string[]>([]);
  const [releaseName, setReleaseName] = useState("");
  const [preview, setPreview] = useState<QualityPreview | null>(null);
  const [draggedQualityId, setDraggedQualityId] = useState<string | null>(null);

  const definitionsQuery = useQuery<QualityDefinition[], ApiRequestError>({
    queryKey: DEFINITIONS_KEY,
    enabled: session.data?.role === "admin",
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/quality/definitions");
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
  const profilesQuery = useQuery<QualityProfile[], ApiRequestError>({
    queryKey: PROFILES_KEY,
    enabled: session.data?.role === "admin",
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/quality/profiles");
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });
  const formatsQuery = useQuery<CustomFormat[], ApiRequestError>({
    queryKey: FORMATS_KEY,
    enabled: session.data?.role === "admin",
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET(
        "/api/admin/quality/custom-formats",
      );
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
  });

  const definitions = definitionsQuery.data ?? [];
  const profiles = profilesQuery.data ?? [];
  const selectedQualities = useMemo(
    () =>
      draft === null
        ? []
        : draft.quality_definition_ids.flatMap((id) => {
            const definition = definitions.find((candidate) => candidate.id === id);
            return definition === undefined ? [] : [definition];
          }),
    [definitions, draft],
  );
  const availableQualities = definitions.filter(
    (definition) => !draft?.quality_definition_ids.includes(definition.id),
  );

  useEffect(() => {
    if (profilesQuery.data === undefined || definitionsQuery.data === undefined) return;
    if (selectedProfileId !== null) return;
    const first = profilesQuery.data[0];
    if (first !== undefined) {
      setSelectedProfileId(first.id);
      setDraft(profileDraft(first));
    } else {
      setDraft(newProfileDraft(definitionsQuery.data));
    }
  }, [definitionsQuery.data, profilesQuery.data, selectedProfileId]);

  useEffect(() => {
    if (formatsQuery.data === undefined) return;
    setFormatDrafts(formatsQuery.data.map(formatDraft));
    setRemovedFormatIds([]);
  }, [formatsQuery.data]);

  const saveProfile = useMutation<QualityProfile, ApiRequestError, ProfileDraft>({
    mutationFn: async (nextDraft) => {
      const body = profilePayload(nextDraft);
      if (nextDraft.id === null) {
        const { data, error, response } = await getApiClient().POST("/api/admin/quality/profiles", {
          body,
        });
        if (error !== undefined || data === undefined) throw apiFailure(error, response);
        return data;
      }
      const { data, error, response } = await getApiClient().PUT(
        "/api/admin/quality/profiles/{profile_id}",
        { params: { path: { profile_id: nextDraft.id } }, body },
      );
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: (profile) => {
      queryClient.setQueryData<QualityProfile[]>(PROFILES_KEY, (current) => {
        const without = current?.filter((candidate) => candidate.id !== profile.id) ?? [];
        return [...without, profile].sort((left, right) => left.name.localeCompare(right.name));
      });
      setSelectedProfileId(profile.id);
      setDraft(profileDraft(profile));
    },
  });

  const deleteProfile = useMutation<void, ApiRequestError, string>({
    mutationFn: async (profileId) => {
      const { error, response } = await getApiClient().DELETE(
        "/api/admin/quality/profiles/{profile_id}",
        { params: { path: { profile_id: profileId } } },
      );
      if (error !== undefined) throw apiFailure(error, response);
    },
    onSuccess: (_, profileId) => {
      const next = profiles.filter((profile) => profile.id !== profileId);
      queryClient.setQueryData(PROFILES_KEY, next);
      const first = next[0];
      setSelectedProfileId(first?.id ?? null);
      setDraft(first === undefined ? newProfileDraft(definitions) : profileDraft(first));
    },
  });

  const saveFormats = useMutation<void, ApiRequestError>({
    mutationFn: async () => {
      await Promise.all(
        removedFormatIds.map(async (formatId) => {
          const { error, response } = await getApiClient().DELETE(
            "/api/admin/quality/custom-formats/{format_id}",
            { params: { path: { format_id: formatId } } },
          );
          if (error !== undefined) throw apiFailure(error, response);
        }),
      );
      await Promise.all(
        formatDrafts.map(async (format) => {
          const body = formatPayload(format);
          if (format.id === null) {
            const { error, response } = await getApiClient().POST(
              "/api/admin/quality/custom-formats",
              {
                body,
              },
            );
            if (error !== undefined) throw apiFailure(error, response);
            return;
          }
          const { error, response } = await getApiClient().PUT(
            "/api/admin/quality/custom-formats/{format_id}",
            { params: { path: { format_id: format.id } }, body },
          );
          if (error !== undefined) throw apiFailure(error, response);
        }),
      );
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: FORMATS_KEY }),
  });

  const previewDecision = useMutation<QualityPreview, ApiRequestError>({
    mutationFn: async () => {
      if (draft === null) throw new Error("quality profile is not ready");
      const { data, error, response } = await getApiClient().POST("/api/admin/quality/preview", {
        body: {
          release_name: releaseName,
          profile: {
            quality_definition_ids: [...draft.quality_definition_ids],
            cutoff_quality_id: draft.cutoff_quality_id,
            minimum_custom_format_score: draft.minimum_custom_format_score,
          },
          custom_formats: formatDrafts.map(formatPayload),
        },
      });
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: setPreview,
  });

  const loading =
    definitionsQuery.isPending ||
    profilesQuery.isPending ||
    formatsQuery.isPending ||
    draft === null;
  const queryError = definitionsQuery.error ?? profilesQuery.error ?? formatsQuery.error;
  const mutationError =
    saveProfile.error ?? deleteProfile.error ?? saveFormats.error ?? previewDecision.error ?? null;
  if (session.data?.role !== "admin" && session.data !== undefined)
    return <Navigate to="/forbidden" replace />;
  if (queryError !== null) {
    return (
      <ErrorScreen
        title={messageForError(queryError)}
        nextStep={nextStepForError(queryError)}
        onRetry={
          isRetryableError(queryError) ? () => void queryClient.invalidateQueries() : undefined
        }
      />
    );
  }
  if (loading) return <QualityLoading />;

  const updateDraft = (changes: Partial<ProfileDraft>): void => {
    setDraft((current) => (current === null ? current : { ...current, ...changes }));
    setPreview(null);
  };
  const selectProfile = (profile: QualityProfile): void => {
    setSelectedProfileId(profile.id);
    setDraft(profileDraft(profile));
    setPreview(null);
  };
  const createProfile = (): void => {
    setSelectedProfileId(null);
    setDraft(newProfileDraft(definitions));
    setPreview(null);
  };
  const reorderQuality = (qualityId: string, index: number): void =>
    updateDraft({
      quality_definition_ids: moveItem(draft.quality_definition_ids, qualityId, index),
    });
  const removeQuality = (qualityId: string): void => {
    const ids = draft.quality_definition_ids.filter((id) => id !== qualityId);
    updateDraft({
      quality_definition_ids: ids,
      cutoff_quality_id:
        draft.cutoff_quality_id === qualityId ? (ids.at(-1) ?? "") : draft.cutoff_quality_id,
    });
  };
  const updateFormat = (index: number, changes: Partial<FormatDraft>): void => {
    setFormatDrafts((current) =>
      current.map((format, item) => (item === index ? { ...format, ...changes } : format)),
    );
    setPreview(null);
  };
  const updateCondition = (
    formatIndex: number,
    conditionIndex: number,
    changes: Partial<ConditionDraft>,
  ): void => {
    setFormatDrafts((current) =>
      current.map((format, item) =>
        item !== formatIndex
          ? format
          : {
              ...format,
              conditions: format.conditions.map((condition, position) =>
                position === conditionIndex ? { ...condition, ...changes } : condition,
              ),
            },
      ),
    );
    setPreview(null);
  };
  const removeFormat = (index: number): void => {
    const current = formatDrafts[index];
    if (current?.id !== null && current !== undefined)
      setRemovedFormatIds((ids) => [...ids, current.id as string]);
    setFormatDrafts((formats) => formats.filter((_, item) => item !== index));
    setPreview(null);
  };

  return (
    <section className="mx-auto flex w-full max-w-[var(--layout-content-max)] flex-col gap-6">
      <header className="max-w-[70ch]">
        <h1 className="text-xl text-ink">{t("quality.title")}</h1>
        <p className="mt-2 text-sm text-ink-muted">{t("quality.intro")}</p>
      </header>

      {mutationError === null ? null : (
        <ErrorScreen
          title={messageForError(mutationError)}
          nextStep={nextStepForError(mutationError)}
        />
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(var(--layout-sidebar-width),0.7fr)_minmax(0,1.7fr)]">
        <aside className="border border-border bg-surface p-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-md text-ink">{t("quality.profiles")}</h2>
            <Button variant="secondary" onClick={createProfile}>
              {t("quality.newProfile")}
            </Button>
          </div>
          <p className="mt-2 text-sm text-ink-muted">{t("quality.profileHint")}</p>
          <ul className="mt-4 flex flex-col gap-1">
            {profiles.map((profile) => (
              <li key={profile.id}>
                <button
                  type="button"
                  className={cx(
                    "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm",
                    "transition-colors duration-[var(--duration-fast)] ease-out hover:bg-surface-3 hover:text-ink",
                    // Exclusive rather than layered: the two ink classes have
                    // equal specificity, and the muted one that won measured
                    // 3.76:1 on --primary-weak, which axe fails.
                    selectedProfileId === profile.id
                      ? "bg-[var(--primary-weak)] text-ink"
                      : "text-ink-muted",
                  )}
                  onClick={() => selectProfile(profile)}
                >
                  <span>{profile.name}</span>
                  {profile.is_default ? (
                    <span className="text-2xs">{t("quality.default")}</span>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <div className="flex flex-col gap-6">
          <section className="border border-border bg-surface p-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="text-lg text-ink">{t("quality.profileEditor")}</h2>
                <p className="mt-1 text-sm text-ink-muted">{t("quality.profileEditorHint")}</p>
              </div>
              {draft.id !== null && !draft.is_default ? (
                <Button
                  variant="ghost"
                  loading={deleteProfile.isPending}
                  onClick={() => deleteProfile.mutate(draft.id as string)}
                >
                  {t("quality.deleteProfile")}
                </Button>
              ) : null}
            </div>

            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <label
                className="flex flex-col gap-2 text-sm text-ink"
                htmlFor="quality-profile-name"
              >
                {t("quality.profileName")}
                <Input
                  id="quality-profile-name"
                  value={draft.name}
                  onChange={(event) => updateDraft({ name: event.target.value })}
                />
              </label>
              <div className="flex items-end">
                <Checkbox
                  label={t("quality.defaultProfile")}
                  checked={draft.is_default}
                  disabled={draft.is_default}
                  onChange={(event) => updateDraft({ is_default: event.target.checked })}
                />
              </div>
            </div>

            <div className="mt-6 border-t border-border pt-6">
              <div>
                <h3 className="text-md text-ink">{t("quality.allowedQualities")}</h3>
                <p className="mt-1 text-sm text-ink-muted">{t("quality.orderHint")}</p>
              </div>
              <ol className="mt-4 flex flex-col gap-2">
                {selectedQualities.map((quality, index) => (
                  <li
                    key={quality.id}
                    draggable
                    className="flex flex-wrap items-center gap-3 border border-border-control bg-surface-2 p-3"
                    onDragStart={() => setDraggedQualityId(quality.id)}
                    onDragOver={(event) => {
                      event.preventDefault();
                      if (draggedQualityId !== null) reorderQuality(draggedQualityId, index);
                    }}
                    onDrop={() => setDraggedQualityId(null)}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm text-ink">{quality.name}</span>
                      <span className="block text-2xs text-ink-muted">
                        {t("quality.qualityDetails", {
                          resolution: quality.resolution,
                          source: quality.source,
                          weight: quality.weight,
                        })}
                      </span>
                    </span>
                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        disabled={index === 0}
                        aria-label={t("quality.moveUp", { name: quality.name })}
                        onClick={() => reorderQuality(quality.id, index - 1)}
                      >
                        {t("quality.up")}
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={index === selectedQualities.length - 1}
                        aria-label={t("quality.moveDown", { name: quality.name })}
                        onClick={() => reorderQuality(quality.id, index + 1)}
                      >
                        {t("quality.down")}
                      </Button>
                      <Button
                        variant="ghost"
                        aria-label={t("quality.removeQuality", { name: quality.name })}
                        onClick={() => removeQuality(quality.id)}
                      >
                        {t("quality.remove")}
                      </Button>
                    </div>
                  </li>
                ))}
              </ol>
              {availableQualities.length === 0 ? null : (
                <label className="mt-4 flex flex-col gap-2 text-sm text-ink" htmlFor="quality-add">
                  {t("quality.addQuality")}
                  <Select
                    id="quality-add"
                    value=""
                    onChange={(event) => {
                      if (!event.target.value) return;
                      updateDraft({
                        quality_definition_ids: [
                          ...draft.quality_definition_ids,
                          event.target.value,
                        ],
                      });
                    }}
                  >
                    <option value="">{t("quality.chooseQuality")}</option>
                    {availableQualities.map((quality) => (
                      <option key={quality.id} value={quality.id}>
                        {quality.name}
                      </option>
                    ))}
                  </Select>
                </label>
              )}
            </div>

            <div className="mt-6 grid gap-4 border-t border-border pt-6 md:grid-cols-2">
              <label className="flex flex-col gap-2 text-sm text-ink" htmlFor="quality-cutoff">
                {t("quality.cutoff")}
                <Select
                  id="quality-cutoff"
                  value={draft.cutoff_quality_id}
                  onChange={(event) => updateDraft({ cutoff_quality_id: event.target.value })}
                >
                  {selectedQualities.map((quality) => (
                    <option key={quality.id} value={quality.id}>
                      {quality.name}
                    </option>
                  ))}
                </Select>
              </label>
              <label
                className="flex flex-col gap-2 text-sm text-ink"
                htmlFor="quality-minimum-score"
              >
                {t("quality.minimumScore")}
                <Input
                  id="quality-minimum-score"
                  type="number"
                  value={draft.minimum_custom_format_score}
                  onChange={(event) =>
                    updateDraft({
                      minimum_custom_format_score: Number.parseInt(event.target.value, 10) || 0,
                    })
                  }
                />
              </label>
              <p className="text-sm text-ink-muted md:col-span-2">
                {t("quality.cutoffHint", {
                  quality:
                    selectedQualities.find((quality) => quality.id === draft.cutoff_quality_id)
                      ?.name ?? t("format.unknown"),
                })}
              </p>
            </div>

            <div className="mt-6 flex flex-wrap gap-3">
              <Button
                loading={saveProfile.isPending}
                disabled={
                  !draft.name.trim() ||
                  draft.quality_definition_ids.length === 0 ||
                  !draft.cutoff_quality_id
                }
                onClick={() => saveProfile.mutate(draft)}
              >
                {t("quality.saveProfile")}
              </Button>
            </div>
          </section>

          <section className="border border-border bg-surface p-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="text-lg text-ink">{t("quality.customFormats")}</h2>
                <p className="mt-1 text-sm text-ink-muted">{t("quality.customFormatsHint")}</p>
              </div>
              <Button
                variant="secondary"
                onClick={() => setFormatDrafts((current) => [...current, newFormatDraft()])}
              >
                {t("quality.addFormat")}
              </Button>
            </div>

            {formatDrafts.length === 0 ? (
              <div className="mt-6 border border-border-control bg-surface-2 p-4">
                <h3 className="text-md text-ink">{t("quality.noFormatsTitle")}</h3>
                <p className="mt-1 text-sm text-ink-muted">{t("quality.noFormatsBody")}</p>
              </div>
            ) : (
              <div className="mt-6 flex flex-col gap-4">
                {formatDrafts.map((format, formatIndex) => (
                  <article
                    key={format.id ?? `new-${formatIndex}`}
                    className="border border-border-control bg-surface-2 p-4"
                  >
                    <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(9rem,0.35fr)_auto]">
                      <label
                        className="flex flex-col gap-2 text-sm text-ink"
                        htmlFor={`format-name-${formatIndex}`}
                      >
                        {t("quality.formatName")}
                        <Input
                          id={`format-name-${formatIndex}`}
                          value={format.name}
                          onChange={(event) =>
                            updateFormat(formatIndex, { name: event.target.value })
                          }
                        />
                      </label>
                      <label
                        className="flex flex-col gap-2 text-sm text-ink"
                        htmlFor={`format-score-${formatIndex}`}
                      >
                        {t("quality.score")}
                        <Input
                          id={`format-score-${formatIndex}`}
                          type="number"
                          aria-label={t("quality.scoreFor", {
                            name: format.name || t("quality.newFormat"),
                          })}
                          value={format.score}
                          onChange={(event) =>
                            updateFormat(formatIndex, { score: event.target.value })
                          }
                        />
                      </label>
                      <div className="flex items-end">
                        <Button variant="ghost" onClick={() => removeFormat(formatIndex)}>
                          {t("quality.remove")}
                        </Button>
                      </div>
                    </div>
                    <div className="mt-4 border-t border-border pt-4">
                      <h3 className="text-sm text-ink">{t("quality.conditions")}</h3>
                      <p className="mt-1 text-2xs text-ink-muted">{t("quality.conditionsHint")}</p>
                      <div className="mt-3 flex flex-col gap-3">
                        {format.conditions.map((condition, conditionIndex) => (
                          <div
                            key={`${condition.field}-${conditionIndex}`}
                            className="grid gap-3 border border-border p-3 md:grid-cols-2 2xl:grid-cols-[minmax(8rem,0.8fr)_minmax(8rem,0.8fr)_minmax(0,1fr)_auto]"
                          >
                            <label
                              className="flex flex-col gap-1 text-2xs text-ink-muted"
                              htmlFor={`condition-field-${formatIndex}-${conditionIndex}`}
                            >
                              {t("quality.conditionField")}
                              <Select
                                id={`condition-field-${formatIndex}-${conditionIndex}`}
                                value={condition.field}
                                onChange={(event) =>
                                  updateCondition(formatIndex, conditionIndex, {
                                    field: event.target.value as ConditionField,
                                  })
                                }
                              >
                                {CONDITION_FIELDS.map((field) => (
                                  <option key={field} value={field}>
                                    {t(`quality.fields.${field}`)}
                                  </option>
                                ))}
                              </Select>
                            </label>
                            <label
                              className="flex flex-col gap-1 text-2xs text-ink-muted"
                              htmlFor={`condition-operator-${formatIndex}-${conditionIndex}`}
                            >
                              {t("quality.conditionOperator")}
                              <Select
                                id={`condition-operator-${formatIndex}-${conditionIndex}`}
                                value={condition.operator}
                                onChange={(event) =>
                                  updateCondition(formatIndex, conditionIndex, {
                                    operator: event.target.value as ConditionOperator,
                                  })
                                }
                              >
                                {CONDITION_OPERATORS.map((operator) => (
                                  <option key={operator} value={operator}>
                                    {t(`quality.operators.${operator}`)}
                                  </option>
                                ))}
                              </Select>
                            </label>
                            <label
                              className="flex flex-col gap-1 text-2xs text-ink-muted"
                              htmlFor={`condition-value-${formatIndex}-${conditionIndex}`}
                            >
                              {t("quality.conditionValue")}
                              <Input
                                id={`condition-value-${formatIndex}-${conditionIndex}`}
                                value={condition.value}
                                onChange={(event) =>
                                  updateCondition(formatIndex, conditionIndex, {
                                    value: event.target.value,
                                  })
                                }
                              />
                            </label>
                            <div className="flex flex-wrap items-end gap-2 md:col-span-2 2xl:col-span-1">
                              <Checkbox
                                label={t("quality.conditionRequired")}
                                checked={condition.required}
                                onChange={(event) =>
                                  updateCondition(formatIndex, conditionIndex, {
                                    required: event.target.checked,
                                  })
                                }
                              />
                              <Checkbox
                                label={t("quality.conditionNegate")}
                                checked={condition.negate}
                                onChange={(event) =>
                                  updateCondition(formatIndex, conditionIndex, {
                                    negate: event.target.checked,
                                  })
                                }
                              />
                              <Button
                                variant="ghost"
                                aria-label={t("quality.removeCondition")}
                                disabled={format.conditions.length === 1}
                                onClick={() =>
                                  updateFormat(formatIndex, {
                                    conditions: format.conditions.filter(
                                      (_, item) => item !== conditionIndex,
                                    ),
                                  })
                                }
                              >
                                {t("quality.remove")}
                              </Button>
                            </div>
                          </div>
                        ))}
                      </div>
                      <Button
                        className="mt-3"
                        variant="ghost"
                        onClick={() =>
                          updateFormat(formatIndex, {
                            conditions: [
                              ...format.conditions,
                              {
                                field: "title",
                                operator: "contains",
                                value: "",
                                negate: false,
                                required: true,
                              },
                            ],
                          })
                        }
                      >
                        {t("quality.addCondition")}
                      </Button>
                    </div>
                  </article>
                ))}
              </div>
            )}
            <div className="mt-6">
              <Button loading={saveFormats.isPending} onClick={() => saveFormats.mutate()}>
                {t("quality.saveFormats")}
              </Button>
            </div>
          </section>

          <section className="border border-border bg-surface p-6">
            <h2 className="text-lg text-ink">{t("quality.preview")}</h2>
            <p className="mt-1 text-sm text-ink-muted">{t("quality.previewHint")}</p>
            <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
              <label
                className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-ink"
                htmlFor="quality-release-name"
              >
                {t("quality.releaseName")}
                <Input
                  id="quality-release-name"
                  value={releaseName}
                  placeholder={t("quality.releaseNamePlaceholder")}
                  onChange={(event) => setReleaseName(event.target.value)}
                />
              </label>
              <Button
                loading={previewDecision.isPending}
                disabled={
                  !releaseName.trim() ||
                  draft.quality_definition_ids.length === 0 ||
                  !draft.cutoff_quality_id
                }
                onClick={() => previewDecision.mutate()}
              >
                {t("quality.previewDecision")}
              </Button>
            </div>
            {preview === null ? null : (
              <div className="mt-6 border-t border-border pt-6" aria-live="polite">
                <h3 className="text-md text-ink">{verdictLabel(t, preview.verdict)}</h3>
                <p className="mt-1 text-sm text-ink-muted">{reasonLabel(t, preview.reason)}</p>
                <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("quality.detectedQuality")}</dt>
                    <dd className="mt-1 text-sm text-ink">
                      {preview.quality?.name ?? t("format.unknown")}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("quality.previewScore")}</dt>
                    <dd className="mt-1 text-sm text-ink">{preview.score}</dd>
                  </div>
                  {Object.entries(preview.fields).map(([field, value]) => (
                    <div key={field} className="border-b border-border pb-2">
                      <dt className="text-2xs text-ink-muted">{fieldLabel(t, field)}</dt>
                      <dd className="mt-1 text-sm text-ink">
                        {Array.isArray(value) ? value.join(", ") : String(value)}
                      </dd>
                    </div>
                  ))}
                </dl>
                <div className="mt-4">
                  <h4 className="text-sm text-ink">{t("quality.matchedFormats")}</h4>
                  {preview.matched_custom_formats.length === 0 ? (
                    <p className="mt-1 text-sm text-ink-muted">{t("quality.noMatchedFormats")}</p>
                  ) : (
                    <ul className="mt-2 flex flex-col gap-1 text-sm text-ink">
                      {preview.matched_custom_formats.map((format) => (
                        <li key={format.name}>{t("quality.matchedFormat", format)}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </section>
  );
}
