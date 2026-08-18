/**
 * Settings, as a place rather than a redirect.
 *
 * `/settings` used to send the reader straight to quality profiles, which made
 * every other administrative screen unreachable by navigation — the reason root
 * folders could exist in the API for a year and never appear in the client. The
 * section list is the fix, and it is a table for the same reason `NAV_ITEMS` is:
 * the id *is* the key under `settings.sections` in the locale files, so a
 * section cannot be added without a translated name.
 *
 * Radarr puts this list in a settings sidebar and Stash puts it in a row of
 * tabs. The shell here already owns the left edge, so the row is what is left.
 */
import { cx } from "@pornarr/ui";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet } from "react-router-dom";

export interface SettingsSection {
  readonly id: string;
  readonly path: string;
  /** Match the path exactly. The index section needs it; the others do not. */
  readonly end: boolean;
}

export const SETTINGS_SECTIONS = [
  // The server's own settings are the index of this section, so the tab has to
  // match the path exactly — without `end` every other tab would sit under an
  // active "general" as well.
  { id: "general", path: "/settings", end: true },
  { id: "rootFolders", path: "/settings/root-folders", end: false },
  { id: "quality", path: "/settings/quality", end: false },
  { id: "metadata", path: "/settings/metadata", end: false },
  { id: "peers", path: "/settings/peers", end: false },
] as const satisfies readonly SettingsSection[];

/** Read from the table so a link to it cannot outlive the route. */
export const ROOT_FOLDERS_PATH =
  SETTINGS_SECTIONS.find((section) => section.id === "rootFolders")?.path ??
  "/settings/root-folders";

export function SettingsLayout(): JSX.Element {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col gap-6">
      <nav aria-label={t("settings.sections.label")}>
        <ul className="flex flex-wrap gap-1 border-b border-border pb-2">
          {SETTINGS_SECTIONS.map((section) => (
            <li key={section.id}>
              <NavLink
                to={section.path}
                end={section.end}
                className={({ isActive }) =>
                  cx(
                    "text-sm",
                    "flex items-center rounded-md px-3 py-2",
                    "transition-colors duration-[var(--duration-fast)] ease-out",
                    "hover:bg-surface-3 hover:text-ink",
                    // Exclusive rather than layered: `text-ink` and
                    // `text-ink-muted` have equal specificity, so listing both
                    // leaves the winner to stylesheet order — and muted ink on
                    // --primary-weak measures 3.76:1, which axe fails.
                    isActive ? "bg-[var(--primary-weak)] text-ink" : "text-ink-muted",
                  )
                }
              >
                {t(`settings.sections.${section.id}`)}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <Outlet />
    </div>
  );
}
