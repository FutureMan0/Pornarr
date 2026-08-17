/**
 * A2 — watched folders, and what a scan is doing right now.
 *
 * PROGRESS COMES OVER THE STREAM, NOT FROM POLLING. A walk of ten thousand
 * files cannot be reported by holding a request open, and polling a count every
 * second would ask the server a question it has to compute each time. The
 * worker already publishes `scan.progress` per file; this screen listens.
 *
 * THE LOG IS THE FRAMES, NOT A NARRATIVE. Each line is one event the worker
 * actually sent. Composing a prettier story out of them would mean inventing
 * steps nobody performed, and the value of a scan log is precisely that it is
 * the machine's own account.
 *
 * WHAT THE DESIGN SHOWS AND THIS DOES NOT. Pause and cancel: the scan is one
 * transactional job and there is nothing to pause it with. A per-folder file
 * count: nothing counts files except a scan, so the number would be a guess
 * until the first one finishes. Both are absent rather than drawn as controls
 * that do nothing.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FormEvent, JSX } from "react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Button, Input } from "@pornarr/ui";
import { useFormat } from "../../i18n/format";
import { getApiClient } from "../../lib/api";
import { apiFailure, messageForError } from "../../lib/api-error";
import { subscribeToEvent } from "../../lib/events";
import { usePageTitle } from "../../shell/page-title";

/** How many log lines to keep. Enough to see a pattern, not a memory leak. */
const LOG_LIMIT = 200;

interface LogLine {
  readonly id: number;
  readonly text: string;
  readonly warning: boolean;
}

interface Live {
  readonly folderId: string;
  readonly filesScanned: number;
  readonly currentPath: string;
}

export function ScanRoute(): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();
  const cache = useQueryClient();
  const [path, setPath] = useState("");
  const [live, setLive] = useState<Live | null>(null);
  const [log, setLog] = useState<readonly LogLine[]>([]);

  const folders = useQuery({
    queryKey: ["root-folders"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/library/root-folders");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const add = useMutation({
    mutationFn: async (value: string) => {
      const { error, response } = await getApiClient().POST("/api/admin/library/root-folders", {
        body: { path: value, enabled: true },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => {
      setPath("");
      void cache.invalidateQueries({ queryKey: ["root-folders"] });
    },
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().DELETE(
        "/api/admin/library/root-folders/{folder_id}",
        { params: { path: { folder_id: id } } },
      );
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: ["root-folders"] }),
  });

  const scan = useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await getApiClient().POST(
        "/api/admin/library/root-folders/{folder_id}/scan",
        { params: { path: { folder_id: id } } },
      );
      if (error) throw apiFailure(error, response);
    },
  });

  useEffect(() => {
    let sequence = 0;
    const append = (text: string, warning = false): void => {
      sequence += 1;
      const line = { id: sequence, text, warning };
      setLog((previous) => [...previous, line].slice(-LOG_LIMIT));
    };

    const onProgress = subscribeToEvent("scan.progress", (event) => {
      const folderId = String(event.payload.root_folder_id ?? "");
      const filesScanned = Number(event.payload.files_scanned ?? 0);
      const currentPath = String(event.payload.current_path ?? "");
      setLive({ folderId, filesScanned, currentPath });
      append(`scan ${currentPath}`);
    });

    const onCompleted = subscribeToEvent("scan.completed", (event) => {
      setLive(null);
      const {
        imported = 0,
        changed = 0,
        missing = 0,
        scanned = 0,
      } = event.payload as Record<string, number>;
      append(`ok ${scanned} scanned · ${imported} new · ${changed} changed · ${missing} missing`);
      // A file that has gone is worth noticing, not just counting.
      if (Number(missing) > 0) append(`warn ${missing} files are no longer where they were`, true);
    });

    return () => {
      onProgress();
      onCompleted();
    };
  }, []);

  usePageTitle(
    t("scan.title"),
    folders.data === undefined ? undefined : t("scan.subtitle", { count: folders.data.length }),
  );

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    if (path.trim().length > 0) add.mutate(path.trim());
  };

  return (
    <div className="grid gap-6 xl:grid-cols-[24rem_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-6">
        <section aria-labelledby="folders-heading" className="flex flex-col gap-3">
          <h2 id="folders-heading" className="text-sm text-ink">
            {t("scan.folders")}
          </h2>

          {folders.isError ? (
            <p role="alert" className="text-sm text-ink">
              {t("errors.generic")}
            </p>
          ) : null}

          {folders.data !== undefined && folders.data.length === 0 ? (
            <p className="text-sm text-ink-muted">{t("scan.noFolders")}</p>
          ) : null}

          <ul className="flex flex-col gap-2">
            {(folders.data ?? []).map((folder) => (
              <li key={folder.id} className="flex flex-col gap-2 rounded-md bg-surface-2 p-3">
                <div className="flex items-baseline gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-sm text-ink">
                    {folder.path}
                  </span>
                  <span className="flex-none text-2xs text-[var(--pa-accent-300)]">
                    {live?.folderId === folder.id
                      ? t("scan.state.scanning")
                      : folder.enabled
                        ? t("scan.state.idle")
                        : t("scan.state.disabled")}
                  </span>
                </div>

                <p className="text-2xs text-ink-muted">
                  {/* The space figure is only a fact once something measured it;
                      `free_space_bytes` defaults to zero. */}
                  {folder.last_space_checked_at === null
                    ? t("scan.spaceUnmeasured")
                    : t("scan.space", {
                        free: format.bytes(folder.free_space_bytes),
                        total:
                          folder.total_space_bytes === null
                            ? "—"
                            : format.bytes(folder.total_space_bytes),
                      })}
                  {" · "}
                  {folder.last_scanned_at === null
                    ? t("scan.neverScanned")
                    : t("scan.lastScanned", {
                        when: format.relativeDate(new Date(folder.last_scanned_at)),
                      })}
                </p>

                {folder.warning === null ? null : (
                  <p role="alert" className="text-2xs text-[var(--pa-accent-300)]">
                    {folder.warning}
                  </p>
                )}

                <div className="flex gap-2">
                  <Button
                    variant="secondary"
                    disabled={!folder.enabled || scan.isPending}
                    onClick={() => scan.mutate(folder.id)}
                  >
                    {t("scan.scanNow")}
                  </Button>
                  <Button variant="ghost" onClick={() => remove.mutate(folder.id)}>
                    {t("scan.remove")}
                  </Button>
                </div>
              </li>
            ))}
          </ul>

          <form onSubmit={submit} className="flex flex-col gap-2">
            <label htmlFor="root-folder-path" className="text-xs text-ink-muted">
              {t("scan.addPath")}
            </label>
            <Input
              id="root-folder-path"
              value={path}
              placeholder={t("scan.pathExample")}
              onChange={(event) => setPath(event.target.value)}
            />
            <Button type="submit" disabled={add.isPending || path.trim().length === 0}>
              {t("scan.add")}
            </Button>
            {add.error === null ? null : (
              <p role="alert" className="text-xs text-ink">
                {messageForError(add.error)}
              </p>
            )}
          </form>
        </section>

        {live === null ? null : (
          <section aria-labelledby="progress-heading" className="flex flex-col gap-2">
            <h2 id="progress-heading" className="text-sm text-ink">
              {t("scan.inProgress")}
            </h2>
            {/* A count, not a bar. Nothing knows the total until the walk ends,
                and a bar with a made-up denominator is worse than a number. */}
            <p className="tabular-nums text-sm text-ink">
              {t("scan.filesScanned", { count: live.filesScanned })}
            </p>
            <p className="truncate font-mono text-2xs text-ink-muted">{live.currentPath}</p>
          </section>
        )}
      </div>

      <section aria-labelledby="log-heading" className="flex min-w-0 flex-col gap-3">
        <div className="flex items-baseline justify-between gap-4">
          <h2 id="log-heading" className="text-sm text-ink">
            {t("scan.log")}
          </h2>
          <Link to="/admin/quarantine" className="text-2xs text-ink-muted underline hover:text-ink">
            {t("scan.review")}
          </Link>
        </div>
        {log.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("scan.logEmpty")}</p>
        ) : (
          <ul
            // Polite and scoped to the log: a scan emits a line per file, and
            // an assertive region would talk over everything else on the page.
            aria-live="polite"
            className="max-h-[32rem] overflow-y-auto break-all rounded-md bg-surface-2 p-3 font-mono text-2xs"
          >
            {log.map((line) => (
              <li
                key={line.id}
                className={line.warning ? "text-[var(--pa-accent-300)]" : "text-ink-muted"}
              >
                {line.text}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
