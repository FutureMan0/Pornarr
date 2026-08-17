/**
 * Root folders: the screen that makes an empty instance fillable.
 *
 * `GET|POST /api/admin/library/root-folders` and `DELETE .../{folder_id}` have
 * been served since the first release with nothing in the client pointed at
 * them, so a fresh instance was told to configure a root folder by a screen
 * that offered no way to do it. Radarr answers the same moment under
 * Settings → Media Management and Stash under Settings → Library; this is that
 * screen, built on the conventions of the quality profiles route beside it.
 *
 * Removal confirms inline rather than in a modal. DESIGN.md says modal is never
 * the first answer, and a two-step control in the row keeps the path being
 * removed on screen while the reader decides.
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
  ROOT_FOLDERS_KEY,
  type RootFolder,
  type RootFolderWrite,
  useRootFolders,
} from "./root-folders";

function RootFoldersLoading(): JSX.Element {
  const { t } = useTranslation();
  return (
    <SkeletonRegion label={t("rootFolders.loading")}>
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

export function RootFoldersRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const session = useSession();
  const queryClient = useQueryClient();
  const foldersQuery = useRootFolders();
  const [path, setPath] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [pendingRemoval, setPendingRemoval] = useState<string | null>(null);

  const addFolder = useMutation<RootFolder, ApiRequestError, RootFolderWrite>({
    mutationFn: async (body) => {
      const { data, error, response } = await getApiClient().POST(
        "/api/admin/library/root-folders",
        { body },
      );
      if (error !== undefined || data === undefined) throw apiFailure(error, response);
      return data;
    },
    onSuccess: (folder) => {
      queryClient.setQueryData<RootFolder[]>(ROOT_FOLDERS_KEY, (current) =>
        [...(current ?? []), folder].sort((left, right) => left.path.localeCompare(right.path)),
      );
      setPath("");
      setEnabled(true);
    },
  });

  const removeFolder = useMutation<void, ApiRequestError, string>({
    mutationFn: async (folderId) => {
      const { error, response } = await getApiClient().DELETE(
        "/api/admin/library/root-folders/{folder_id}",
        { params: { path: { folder_id: folderId } } },
      );
      if (error !== undefined) throw apiFailure(error, response);
    },
    onSuccess: (_, folderId) => {
      queryClient.setQueryData<RootFolder[]>(
        ROOT_FOLDERS_KEY,
        (current) => current?.filter((folder) => folder.id !== folderId) ?? [],
      );
      setPendingRemoval(null);
    },
  });

  const folders = foldersQuery.data ?? [];
  const mutationError = addFolder.error ?? removeFolder.error ?? null;
  if (session.data?.role !== "admin" && session.data !== undefined)
    return <Navigate to="/forbidden" replace />;
  if (foldersQuery.error !== null) {
    return (
      <ErrorScreen
        title={messageForError(foldersQuery.error)}
        nextStep={nextStepForError(foldersQuery.error)}
        onRetry={
          isRetryableError(foldersQuery.error)
            ? () => void queryClient.invalidateQueries({ queryKey: ROOT_FOLDERS_KEY })
            : undefined
        }
      />
    );
  }
  if (foldersQuery.isPending) return <RootFoldersLoading />;

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!path.trim()) return;
    addFolder.mutate({ path: path.trim(), enabled });
  };

  return (
    <section className="flex flex-col gap-6" aria-labelledby="root-folders-heading">
      <header className="max-w-[70ch]">
        <h1 id="root-folders-heading" className="text-xl text-ink">
          {t("rootFolders.title")}
        </h1>
        <p className="mt-2 text-sm text-ink-muted">{t("rootFolders.intro")}</p>
      </header>

      {mutationError === null ? null : (
        <ErrorScreen
          title={messageForError(mutationError)}
          nextStep={nextStepForError(mutationError)}
        />
      )}

      <section className="border border-border bg-surface p-6" aria-labelledby="root-folders-list">
        <h2 id="root-folders-list" className="text-lg text-ink">
          {t("rootFolders.configured")}
        </h2>

        {folders.length === 0 ? (
          <p className="mt-4 max-w-[70ch] text-sm text-ink-muted">{t("rootFolders.none")}</p>
        ) : (
          <ul className="mt-4 flex flex-col gap-4">
            {folders.map((folder) => (
              <li key={folder.id} className="border border-border-control bg-surface-2 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <h3 className="min-w-0 break-all text-md text-ink">{folder.path}</h3>
                  {pendingRemoval === folder.id ? null : (
                    <Button
                      variant="ghost"
                      aria-label={t("rootFolders.removeFolder", { path: folder.path })}
                      onClick={() => {
                        removeFolder.reset();
                        setPendingRemoval(folder.id);
                      }}
                    >
                      {t("rootFolders.remove")}
                    </Button>
                  )}
                </div>

                <dl className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("rootFolders.status")}</dt>
                    <dd className="mt-1 text-sm text-ink">
                      {folder.enabled ? t("rootFolders.enabled") : t("rootFolders.disabled")}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("rootFolders.freeSpace")}</dt>
                    <dd className="mt-1 text-sm text-ink tabular">
                      {format.bytes(folder.free_space_bytes)}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("rootFolders.totalSpace")}</dt>
                    <dd className="mt-1 text-sm text-ink tabular">
                      {folder.total_space_bytes === null
                        ? t("format.unknown")
                        : format.bytes(folder.total_space_bytes)}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("rootFolders.lastScanned")}</dt>
                    <dd className="mt-1 text-sm text-ink">
                      {folder.last_scanned_at === null
                        ? t("rootFolders.never")
                        : format.relativeDate(new Date(folder.last_scanned_at))}
                    </dd>
                  </div>
                  <div className="border-b border-border pb-2">
                    <dt className="text-2xs text-ink-muted">{t("rootFolders.spaceChecked")}</dt>
                    <dd className="mt-1 text-sm text-ink">
                      {folder.last_space_checked_at === null
                        ? t("rootFolders.never")
                        : format.relativeDate(new Date(folder.last_space_checked_at))}
                    </dd>
                  </div>
                </dl>

                {folder.same_filesystem_as_downloads ? null : (
                  <p className="mt-3 max-w-[70ch] text-sm text-ink-muted">
                    {t("rootFolders.differentFilesystem")}
                  </p>
                )}
                {folder.low_space_warning_sent ? (
                  <p className="mt-1 max-w-[70ch] text-sm text-ink-muted">
                    {t("rootFolders.lowSpace")}
                  </p>
                ) : null}

                {pendingRemoval === folder.id ? (
                  <div className="mt-4 border-t border-border pt-4">
                    <p className="max-w-[70ch] text-sm text-ink">
                      {t("rootFolders.confirmRemoval", { path: folder.path })}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-3">
                      <Button
                        loading={removeFolder.isPending}
                        onClick={() => removeFolder.mutate(folder.id)}
                      >
                        {t("rootFolders.confirmRemovalAction")}
                      </Button>
                      <Button variant="ghost" onClick={() => setPendingRemoval(null)}>
                        {t("rootFolders.cancelRemoval")}
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
        aria-labelledby="root-folders-add"
        onSubmit={submit}
      >
        <h2 id="root-folders-add" className="text-lg text-ink">
          {t("rootFolders.addTitle")}
        </h2>
        <p className="mt-1 max-w-[70ch] text-sm text-ink-muted">{t("rootFolders.addHint")}</p>

        <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-end">
          <label
            className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-ink"
            htmlFor="root-folder-path"
          >
            {t("rootFolders.path")}
            <Input
              id="root-folder-path"
              value={path}
              placeholder={t("rootFolders.pathPlaceholder")}
              onChange={(event) => setPath(event.target.value)}
            />
          </label>
          <div className="flex items-end">
            <Checkbox
              label={t("rootFolders.enable")}
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
            />
          </div>
          <div className="flex items-end">
            <Button type="submit" loading={addFolder.isPending} disabled={!path.trim()}>
              {t("rootFolders.add")}
            </Button>
          </div>
        </div>
      </form>
    </section>
  );
}
