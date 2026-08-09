import { cx } from "../lib/cx";
import css from "./badge.module.css";

/**
 * DESIGN.md defines one status vocabulary for the whole product — the request
 * and download lifecycle — and this array is it. A screen that needs a ninth
 * state has found a product question, not a styling one.
 */
export const BADGE_STATUSES = [
  "searching",
  "queued",
  "downloading",
  "importing",
  "available",
  "quarantined",
  "failed",
  "cancelled",
] as const;

export type BadgeStatus = (typeof BADGE_STATUSES)[number];

/**
 * The label is not a prop. DESIGN.md requires the same vocabulary in every
 * context, and an overridable label is how eight states quietly become twenty.
 */
const LABELS: Readonly<Record<BadgeStatus, string>> = {
  searching: "Searching",
  queued: "Queued",
  downloading: "Downloading",
  importing: "Importing",
  available: "Available",
  quarantined: "Quarantined",
  failed: "Failed",
  cancelled: "Cancelled",
};

export interface BadgeProps {
  readonly status: BadgeStatus;
  readonly className?: string;
}

/**
 * A status badge. The tint is never the signal: DESIGN.md says "Never signal
 * state by colour alone", so the label is always rendered and the badge stays
 * readable with the background ignored entirely.
 */
export const Badge = ({ status, className }: BadgeProps) => (
  <span className={cx(css.badge, css[status], className)}>{LABELS[status]}</span>
);
