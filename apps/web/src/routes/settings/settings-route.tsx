/**
 * A5 — the settings the server actually has.
 *
 * Thirty-six runtime settings existed with no way to reach them; the only
 * screen under /settings was quality profiles. This is the rest of them,
 * grouped the way somebody looking for one would look.
 *
 * ONE KEY PER WRITE. Each control PATCHes the single setting it governs.
 * Sending the whole object back — the obvious thing, since that is what the
 * GET returns — would silently undo anything another administrator changed
 * while this screen sat open.
 *
 * WHAT THE DESIGN SHOWS THAT IS NOT HERE. "Users & access" wants a list of
 * people, roles and invite links; this server has no user-management API at
 * all, and a screen that lists one account and calls it a household would be
 * a mock-up. The bind address is a deployment concern rather than a runtime
 * setting. Both are absent rather than drawn dead.
 *
 * The sections that are here are the ones the settings support. Appearance
 * comes from `@pornarr/ui`, which owns the accent, and is per-device rather
 * than per-server — see `theme.ts` for why that is not the server's business.
 */
import {
  Input,
  Select,
  THEMES,
  type Theme,
  applyAccentFavicon,
  setTheme,
  storedTheme,
} from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { useSession } from "../../auth/session";
import { getApiClient } from "../../lib/api";
import { apiFailure, messageForError } from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";

type Settings = Record<string, unknown>;

/*
 * The two tables below use `satisfies` rather than a type annotation, so the
 * literal key strings survive into `t()`. Keys are spelled out in pairs rather
 * than derived by appending "Hint": a key built by concatenation is a plain
 * `string` to the compiler, and the point of the typed `t` is that a missing
 * translation is a build error rather than a screen reading
 * "settings.metricsHint".
 */
/** A switch and the sentence under it, keyed by the setting it writes. */
interface Toggle {
  readonly key: string;
  readonly labelKey: string;
  readonly hintKey: string;
}

/** A number field, with the bounds the API enforces so the browser says first. */
interface Numeric {
  readonly key: string;
  readonly labelKey: string;
  readonly hintKey: string;
  readonly min: number;
  readonly max: number;
  readonly nullable?: boolean;
}

const SECTIONS = ["household", "playback", "recommendations", "downloads", "housekeeping"] as const;
type Section = (typeof SECTIONS)[number];

const TOGGLES = {
  household: [
    {
      key: "private_libraries",
      labelKey: "settings.privateLibraries",
      hintKey: "settings.privateLibrariesHint",
    },
    {
      key: "pooled_search",
      labelKey: "settings.pooledSearch",
      hintKey: "settings.pooledSearchHint",
    },
    {
      key: "anonymous_social",
      labelKey: "settings.anonymousSocial",
      hintKey: "settings.anonymousSocialHint",
    },
  ],
  playback: [],
  recommendations: [
    {
      key: "recommendation_use_ratings",
      labelKey: "settings.useRatings",
      hintKey: "settings.useRatingsHint",
    },
    {
      key: "recommendation_hide_finished",
      labelKey: "settings.hideFinished",
      hintKey: "settings.hideFinishedHint",
    },
    {
      key: "recommendation_include_shorts",
      labelKey: "settings.includeShorts",
      hintKey: "settings.includeShortsHint",
    },
    {
      key: "recommendation_include_friend_picks",
      labelKey: "settings.includeFriendPicks",
      hintKey: "settings.includeFriendPicksHint",
    },
  ],
  downloads: [
    {
      key: "default_auto_downloads_enabled",
      labelKey: "settings.autoDownloads",
      hintKey: "settings.autoDownloadsHint",
    },
  ],
  housekeeping: [
    { key: "metrics_enabled", labelKey: "settings.metrics", hintKey: "settings.metricsHint" },
  ],
} as const satisfies Record<Section, readonly Toggle[]>;

const NUMBERS = {
  household: [],
  playback: [
    {
      key: "playback_completion_threshold_percent",
      labelKey: "settings.completion",
      hintKey: "settings.completionHint",
      min: 1,
      max: 100,
    },
    {
      key: "transcode_max_per_user",
      labelKey: "settings.perUser",
      hintKey: "settings.perUserHint",
      min: 1,
      max: 64,
    },
    {
      key: "transcode_max_hw_sessions",
      labelKey: "settings.hwSessions",
      hintKey: "settings.hwSessionsHint",
      min: 0,
      max: 64,
      nullable: true,
    },
    {
      key: "transcode_max_sw_sessions",
      labelKey: "settings.swSessions",
      hintKey: "settings.swSessionsHint",
      min: 0,
      max: 64,
      nullable: true,
    },
  ],
  recommendations: [],
  downloads: [
    {
      key: "default_daily_download_limit_gb",
      labelKey: "settings.dailyLimit",
      hintKey: "settings.dailyLimitHint",
      min: 0,
      max: 10_000,
    },
    {
      key: "default_max_auto_jobs",
      labelKey: "settings.maxJobs",
      hintKey: "settings.maxJobsHint",
      min: 0,
      max: 100,
    },
    {
      key: "default_max_auto_downloads_per_day",
      labelKey: "settings.maxPerDay",
      hintKey: "settings.maxPerDayHint",
      min: 0,
      max: 1_000,
    },
    {
      key: "request_search_max_age_days",
      labelKey: "settings.searchAge",
      hintKey: "settings.searchAgeHint",
      min: 1,
      max: 3_650,
    },
  ],
  housekeeping: [
    {
      key: "min_free_disk_percent",
      labelKey: "settings.minFreeDisk",
      hintKey: "settings.minFreeDiskHint",
      min: 0,
      max: 99,
    },
    {
      key: "quarantine_retention_days",
      labelKey: "settings.quarantineDays",
      hintKey: "settings.quarantineDaysHint",
      min: 1,
      max: 3_650,
    },
    {
      key: "audit_retention_days",
      labelKey: "settings.auditDays",
      hintKey: "settings.auditDaysHint",
      min: 1,
      max: 3_650,
      nullable: true,
    },
    {
      key: "user_event_retention_days",
      labelKey: "settings.eventDays",
      hintKey: "settings.eventDaysHint",
      min: 1,
      max: 3_650,
      nullable: true,
    },
  ],
} as const satisfies Record<Section, readonly Numeric[]>;

export function SettingsRoute(): JSX.Element {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const [section, setSection] = useState<Section>("household");

  const session = useSession();
  /**
   * Everything on this screen except Appearance belongs to the server.
   *
   * A guest asking for `/api/admin/settings` gets a 403 — which is what happened
   * every time one opened this screen, the same mistake `/downloads` made. The
   * accent is per-device and theirs; the thirty-six runtime settings are not, and
   * neither are the links to quality profiles, library paths or invitations.
   */
  const isAdmin = session.data?.role === "admin";

  const settings = useQuery({
    enabled: isAdmin,
    queryKey: ["admin", "settings"],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/admin/settings");
      if (!data || error) throw apiFailure(error, response);
      return data as Settings;
    },
  });

  const write = useMutation({
    mutationFn: async (patch: Settings) => {
      const { error, response } = await getApiClient().PATCH("/api/admin/settings", {
        body: patch,
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: ["admin", "settings"] }),
  });

  usePageTitle(
    t("settings.title"),
    t(`settings.section.${section}` as "settings.section.household"),
  );

  // A guest gets the one thing here that is theirs, and no navigation for the
  // sections that are not.
  if (!isAdmin) {
    return (
      <section className="flex max-w-[46rem] flex-col gap-6">
        <Appearance />
      </section>
    );
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <nav aria-label={t("settings.jumpTo")} className="flex flex-col gap-1">
        {SECTIONS.map((option) => (
          <button
            key={option}
            type="button"
            aria-current={section === option ? "page" : undefined}
            onClick={() => setSection(option)}
            className={
              section === option
                ? "rounded-md bg-[var(--primary-weak)] px-3 py-2 text-left text-sm text-[var(--pa-accent-300)]"
                : "rounded-md px-3 py-2 text-left text-sm text-ink-muted hover:bg-surface-3 hover:text-ink"
            }
          >
            {t(`settings.section.${option}` as "settings.section.household")}
          </button>
        ))}
        {/* Quality profiles, root folders, metadata and peers are their own
            screens and the tab bar above this one lists them. A second link to
            the same place here would be two section lists to keep in step. */}
        <Link
          to="/admin/scan"
          className="rounded-md px-3 py-2 text-left text-sm text-ink-muted hover:bg-surface-3 hover:text-ink"
        >
          {t("settings.section.paths")}
        </Link>
        {/* The design's "Users & access". There is still no user list — this
            server has no user-management API — but inviting somebody is the
            half of it that exists. */}
        <Link
          to="/admin/invites"
          className="rounded-md px-3 py-2 text-left text-sm text-ink-muted hover:bg-surface-3 hover:text-ink"
        >
          {t("settings.section.invites")}
        </Link>
      </nav>

      <section aria-live="polite" className="flex max-w-[46rem] flex-col gap-6">
        {settings.isError ? (
          <p role="alert" className="text-sm text-ink">
            {t("errors.generic")}
          </p>
        ) : null}
        {write.error === null ? null : (
          <p role="alert" className="text-sm text-ink">
            {messageForError(write.error)}
          </p>
        )}

        {settings.data === undefined ? (
          <p className="text-sm text-ink-muted">{t("settings.loading")}</p>
        ) : (
          <>
            {TOGGLES[section].map((toggle) => (
              <Switch
                key={toggle.key}
                id={toggle.key}
                label={t(toggle.labelKey)}
                hint={t(toggle.hintKey)}
                checked={Boolean(settings.data[toggle.key])}
                busy={write.isPending}
                onChange={(value) => write.mutate({ [toggle.key]: value })}
              />
            ))}

            {NUMBERS[section].map((field) => (
              <NumberField
                key={field.key}
                field={field}
                value={settings.data[field.key] as number | null}
                label={t(field.labelKey)}
                hint={t(field.hintKey)}
                unsetLabel={t("settings.unlimited")}
                onCommit={(value) => write.mutate({ [field.key]: value })}
              />
            ))}

            {section === "recommendations" ? (
              <p className="text-2xs text-ink-faint">{t("settings.weightsNote")}</p>
            ) : null}

            {section === "household" ? <Appearance /> : null}
          </>
        )}
      </section>
    </div>
  );
}

function Switch({
  id,
  label,
  hint,
  checked,
  busy,
  onChange,
}: {
  readonly id: string;
  readonly label: string;
  readonly hint: string;
  readonly checked: boolean;
  readonly busy: boolean;
  readonly onChange: (value: boolean) => void;
}): JSX.Element {
  const labelId = `setting-${id}`;
  return (
    <div className="flex items-start gap-3">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        disabled={busy}
        onClick={() => onChange(!checked)}
        className={
          checked
            ? "mt-0.5 h-5 w-9 flex-none rounded-full bg-[var(--primary)] p-0.5 text-left"
            : "mt-0.5 h-5 w-9 flex-none rounded-full bg-surface-3 p-0.5 text-left"
        }
      >
        <span
          aria-hidden="true"
          className={
            checked
              ? "block size-4 translate-x-4 rounded-full bg-[var(--pa-bg-00)] transition-transform"
              : "block size-4 rounded-full bg-[var(--pa-text-faint)] transition-transform"
          }
        />
      </button>
      <span className="flex flex-col gap-0.5">
        <span id={labelId} className="text-sm text-ink">
          {label}
        </span>
        <span className="text-2xs text-ink-muted">{hint}</span>
      </span>
    </div>
  );
}

/**
 * A number, written when the field is left rather than on every keystroke.
 *
 * Writing per keystroke would send "1", "12", "120" for one edit of 120, and
 * the intermediate values are real settings the server would briefly hold.
 */
function NumberField({
  field,
  value,
  label,
  hint,
  unsetLabel,
  onCommit,
}: {
  readonly field: Numeric;
  readonly value: number | null;
  readonly label: string;
  readonly hint: string;
  readonly unsetLabel: string;
  readonly onCommit: (value: number | null) => void;
}): JSX.Element {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value === null ? "" : String(value));

  const commit = (): void => {
    setDraft(null);
    const trimmed = shown.trim();
    if (trimmed === "") {
      if (field.nullable === true && value !== null) onCommit(null);
      return;
    }
    const parsed = Number(trimmed);
    if (!Number.isFinite(parsed) || parsed === value) return;
    onCommit(Math.min(field.max, Math.max(field.min, Math.round(parsed))));
  };

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={`setting-${field.key}`} className="text-sm text-ink">
        {label}
      </label>
      <span className="text-2xs text-ink-muted">
        {hint}
        {field.nullable === true ? ` · ${unsetLabel}` : ""}
      </span>
      <Input
        id={`setting-${field.key}`}
        type="number"
        min={field.min}
        max={field.max}
        value={shown}
        className="max-w-[10rem]"
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
      />
    </div>
  );
}

/**
 * The accent, which is a property of this browser rather than of the server.
 * See `packages/ui/src/theme.ts`: it is applied before React so the page never
 * paints in the wrong one.
 */
function Appearance(): JSX.Element {
  const { t } = useTranslation();
  const [theme, setLocal] = useState<Theme>(() => storedTheme(globalThis.localStorage));

  return (
    <div className="flex flex-col gap-1 border-t border-border pt-6">
      <label htmlFor="setting-theme" className="text-sm text-ink">
        {t("settings.accent")}
      </label>
      <span className="text-2xs text-ink-muted">{t("settings.accentHint")}</span>
      <Select
        id="setting-theme"
        value={theme}
        className="max-w-[10rem]"
        onChange={(event) => {
          const next = event.target.value as Theme;
          setLocal(next);
          setTheme(next, document.documentElement, globalThis.localStorage);
          // The tab icon is drawn from the accent, so it has to be redrawn here
          // too — otherwise the interface changes and the browser tab does not.
          applyAccentFavicon();
        }}
      >
        {THEMES.map((option) => (
          <option key={option} value={option}>
            {t(`settings.theme.${option}` as "settings.theme.rose")}
          </option>
        ))}
      </Select>
    </div>
  );
}

export type { Section };
export { SECTIONS };

/** Re-exported so tests can name what they are asserting about. */
export const SETTING_KEYS: readonly string[] = [
  ...Object.values(TOGGLES).flatMap((group) => group.map((item) => item.key)),
  ...Object.values(NUMBERS).flatMap((group) => group.map((item) => item.key)),
];
