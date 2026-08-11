/**
 * One error, stated the way DESIGN.md requires: the cause, then the next step.
 *
 * The two are separate props rather than one blob of prose because they are read
 * differently — the cause is scanned, the step is acted on — and because callers
 * source them from different places: an API failure takes both from the code map
 * in `lib/api-error.ts`, a render crash takes both from the locale files
 * directly. Neither may render "Something went wrong" and stop there.
 *
 * ACCESSIBILITY. This block appears without navigation, so DESIGN.md asks for a
 * polite announcement that does not steal focus. Nothing here calls `focus()`.
 * The sentence is written into the live region from an effect rather than being
 * present in the region's first render: a region that mounts with its text
 * already in it is inconsistently announced, and a region that changes after
 * mount is announced everywhere.
 */
import { Button, cx } from "@pornarr/ui";
import type { JSX } from "react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

export interface ErrorScreenProps {
  /** What went wrong, as a sentence. Never a code, never a server string. */
  readonly title: string;
  /** What to do about it. Always present — that is the point of the component. */
  readonly nextStep: string;
  /**
   * Repeats whatever failed. Omitted when repeating it cannot help;
   * `isRetryableError` is how callers decide.
   */
  readonly onRetry?: (() => void) | undefined;
  readonly className?: string | undefined;
}

export function ErrorScreen({
  title,
  nextStep,
  onRetry,
  className,
}: ErrorScreenProps): JSX.Element {
  const { t } = useTranslation();
  const [announced, setAnnounced] = useState("");

  useEffect(() => {
    setAnnounced(`${title} ${nextStep}`);
  }, [title, nextStep]);

  return (
    <section
      className={cx(
        "flex flex-col items-start gap-3 rounded-lg border border-border bg-surface p-6",
        className,
      )}
    >
      <h2 className={"text-md max-w-[70ch] text-ink"}>{title}</h2>
      <p className={"text-sm max-w-[70ch] text-ink-muted"}>{nextStep}</p>

      {onRetry === undefined ? null : (
        <Button variant="secondary" onClick={onRetry}>
          {t("errors.retry")}
        </Button>
      )}

      <p className="visually-hidden" aria-live="polite" aria-atomic="true">
        {announced}
      </p>
    </section>
  );
}
