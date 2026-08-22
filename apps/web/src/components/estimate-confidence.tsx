/**
 * How much to trust the range beside it.
 *
 * ADR 0031: "Every estimate is returned as a range with a confidence level. A
 * single exact figure would claim precision the system does not have, and the
 * interface is built to display the range." DESIGN.md L219-221 says how it is
 * drawn: three states, told apart by a glyph and a word rather than by colour,
 * because colour alone carries nothing for a reader who cannot see it.
 *
 * Shared rather than owned by one screen. It was written for the search row and
 * the queue then rendered a bare range for the same field, which is how one
 * screen's fix stops at that screen's edge. Every surface that shows an estimate
 * shows this beside it.
 *
 * DESIGN gives the indicator exactly three states, so `unknown` is not one of
 * them and this renders nothing for it. That is not a gap: an unknown estimate
 * has no range either, so `format.estimate(null, null)` has already put the word
 * "Unknown" in the cell, and a confidence chip beside it would read "Unknown
 * Unknown". The `search.confidence.unknown` string exists because the key is
 * reachable from this component's own union type, not because a fourth state is
 * rendered.
 *
 * The keys stay under `search.confidence.*`. They were authored there, they are
 * the same four words wherever an estimate appears, and moving a key is churn in
 * two locale files for a rename nobody reads.
 */
import type { JSX } from "react";
import { useTranslation } from "react-i18next";

/** The confidence vocabulary `pornarr_core.eta.Confidence` puts on the wire. */
export type EstimateConfidenceLevel = "high" | "medium" | "low" | "unknown";

export function EstimateConfidence({
  level,
}: { readonly level: EstimateConfidenceLevel }): JSX.Element | null {
  const { t } = useTranslation();
  const glyphs: Partial<Record<EstimateConfidenceLevel, string>> = {
    high: "▮▮▮",
    medium: "▮▮▯",
    low: "▮▯▯",
  };
  const glyph = glyphs[level];
  if (glyph === undefined) return null;
  return (
    <span className="ml-2 whitespace-nowrap text-2xs text-ink-muted">
      <span aria-hidden="true">{glyph}</span> {t(`search.confidence.${level}`)}
    </span>
  );
}
