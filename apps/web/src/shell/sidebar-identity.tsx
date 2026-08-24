/**
 * The two blocks above the navigation: what this server is, and who you are on
 * it.
 *
 * Separate from `sidebar.tsx` because that file is about geometry — full, rail
 * and drawer, and the focus behaviour each implies — and this is about content.
 * Mixing them is what turns a responsive shell into one component nobody wants
 * to touch.
 */
import { Logo, LogoMark, cx } from "@pornarr/ui";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";

import { useSession } from "../auth/session";

export interface SidebarIdentityProps {
  /** The rail is 56px of icons; text does not survive it. */
  readonly compact: boolean;
}

/**
 * The wordmark and the reminder that nothing here leaves the house.
 *
 * "LOCAL" is not decoration. This is a server people invite friends onto, and
 * the design puts the scope of that invitation at the top of every screen.
 */
export function SidebarBrand({ compact }: SidebarIdentityProps): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className={cx("flex items-center gap-2 px-2 py-2", compact && "justify-center")}>
      {/* The rail is 56 pixels; the name does not survive it, and the mark is
          the half that still identifies the server at that width. */}
      {compact ? <LogoMark size={22} /> : <Logo name={t("shell.brand")} size={24} />}
      {compact ? null : (
        <span className="ml-auto text-2xs tracking-[0.12em] text-ink-muted">
          {t("shell.scope")}
        </span>
      )}
    </div>
  );
}

/**
 * Which role you are signed in as, and under what name.
 *
 * The role is shown rather than inferred from which screens are reachable: a
 * guest who cannot find the admin section learns nothing about why, and on a
 * shared server "am I the admin here" is a question people actually have.
 */
export function SidebarIdentity({ compact }: SidebarIdentityProps): JSX.Element | null {
  const { t } = useTranslation();
  const session = useSession();
  const user = session.data;
  if (user === undefined || user === null) return null;

  const isAdmin = user.role === "admin";
  const roleLabel = isAdmin ? t("shell.role.admin") : t("shell.role.guest");

  return (
    <div
      className={cx(
        "flex items-center gap-2 rounded-md bg-surface-3 px-2 py-2",
        compact && "justify-center",
      )}
      // The rail drops the text, so the whole block has to say what it is.
      title={compact ? `${roleLabel} · ${user.username}` : undefined}
    >
      <RoleIcon admin={isAdmin} label={roleLabel} />
      {compact ? (
        <span className="visually-hidden">
          {roleLabel} {user.username}
        </span>
      ) : (
        <>
          <span className="text-xs text-ink">{roleLabel}</span>
          <span className="ml-auto max-w-24 truncate text-2xs text-ink-muted">{user.username}</span>
        </>
      )}
    </div>
  );
}

function RoleIcon({
  admin,
  label,
}: { readonly admin: boolean; readonly label: string }): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 16 16"
      className="size-4 flex-none text-primary"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <title>{label}</title>
      {admin ? (
        <>
          <path d="M8 1.75 13 3.5v4.25c0 3.1-2.05 5.4-5 6.5-2.95-1.1-5-3.4-5-6.5V3.5Z" />
          <path d="m5.75 7.75 1.5 1.5 3-3" />
        </>
      ) : (
        <>
          <circle cx="8" cy="5.5" r="2.5" />
          <path d="M3 13.25a5 5 0 0 1 10 0" />
        </>
      )}
    </svg>
  );
}
