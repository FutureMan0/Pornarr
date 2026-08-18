import { cx } from "../lib/cx";
import css from "./empty-state.module.css";

export interface EmptyStateAction {
  readonly label: string;
  readonly href: string;
}

export interface EmptyStateProps {
  readonly title: string;
  /** What is missing and what filling it does. One or two sentences. */
  readonly body: string;
  /**
   * Required, and that is the whole design. DESIGN.md refuses "nothing here"
   * empty states, and an optional action is how they come back — so a bare one
   * is not expressible in this API.
   */
  readonly action: EmptyStateAction;
  /**
   * Heading level. Two by default, because an empty state usually sits inside
   * a screen that already has its own heading; a whole screen that is nothing
   * but an empty state - a 404, a 403 - passes one, because a page with no
   * level-one heading is a page a screen reader cannot summarise.
   */
  readonly titleLevel?: 1 | 2;
  readonly className?: string;
}

/**
 * An empty state that teaches the interface. DESIGN.md's example: an empty
 * library says how to add a root folder and links to it.
 */
export const EmptyState = ({ title, body, action, titleLevel = 2, className }: EmptyStateProps) => (
  <div className={cx(css.root, className)}>
    {titleLevel === 1 ? (
      <h1 className={css.title}>{title}</h1>
    ) : (
      <h2 className={css.title}>{title}</h2>
    )}
    <p className={css.body}>{body}</p>
    <a className={css.action} href={action.href}>
      {action.label}
    </a>
  </div>
);
