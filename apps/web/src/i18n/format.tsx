/**
 * Numbers, sizes, durations and dates — through the locale, never through
 * string concatenation.
 *
 * Every function here takes the locale explicitly and delegates to `Intl`, so
 * the decimal mark, the group separator, the date order and the unit name all
 * come from the platform's CLDR data rather than from a table we would have to
 * maintain per language. `useFormat()` is the same set with the current locale
 * already bound, for components.
 *
 * Unit *symbols* are deliberately not translated. `GiB`, `bit/s` and clock time
 * are notation, identical in every language this product will ship; inventing
 * localised spellings for them would make columns stop lining up and would be
 * wrong in German anyway.
 */
import { cx } from "@pornarr/ui";
import type { JSX, ReactNode } from "react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { currentLocale } from "./index";

/** IEC binary units. DESIGN.md's Size column is a disk size, not a disk label. */
const BINARY_UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] as const;
const BINARY_STEP = 1024;

/** SI decimal units: a bitrate has always been counted in thousands. */
const BITRATE_UNITS = ["bit/s", "kbit/s", "Mbit/s", "Gbit/s"] as const;
const DECIMAL_STEP = 1000;

/**
 * DESIGN.md: "Never a bare number. `~12–18 min`". The tilde is a symbol, not
 * prose — it needs no translation and reads the same in every locale.
 */
const APPROXIMATELY = "~";

const SECONDS_PER_MINUTE = 60;

/** Largest first: the first unit the gap fills is the one worth naming. */
const RELATIVE_UNITS: readonly (readonly [Intl.RelativeTimeFormatUnit, number])[] = [
  ["year", 31_536_000],
  ["month", 2_592_000],
  ["week", 604_800],
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
  ["second", 1],
];

function scale(value: number, step: number, unitCount: number): { value: number; index: number } {
  const magnitude = Math.abs(value);
  if (!Number.isFinite(magnitude) || magnitude < step) return { value, index: 0 };
  const index = Math.min(Math.floor(Math.log(magnitude) / Math.log(step)), unitCount - 1);
  return { value: value / step ** index, index };
}

/** A file size in binary units: `1.4 GiB` in English, `1,4 GiB` in German. */
export function formatBytes(bytes: number, locale: string): string {
  const { value, index } = scale(bytes, BINARY_STEP, BINARY_UNITS.length);
  const unit = BINARY_UNITS[index] ?? BINARY_UNITS[0];
  const digits = index === 0 ? 0 : 1;
  return `${formatNumber(value, locale, digits)} ${unit}`;
}

/** A transfer rate: the size formatter, per second. */
export function formatSpeed(bytesPerSecond: number, locale: string): string {
  return `${formatBytes(bytesPerSecond, locale)}/s`;
}

/** A media bitrate in SI units: `4.5 Mbit/s`. */
export function formatBitrate(bitsPerSecond: number, locale: string): string {
  const { value, index } = scale(bitsPerSecond, DECIMAL_STEP, BITRATE_UNITS.length);
  const unit = BITRATE_UNITS[index] ?? BITRATE_UNITS[0];
  const digits = index === 0 ? 0 : 1;
  return `${formatNumber(value, locale, digits)} ${unit}`;
}

/** A plain number: `1,234.5` in English, `1.234,5` in German. */
export function formatNumber(value: number, locale: string, fractionDigits = 0): string {
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(value);
}

/**
 * A running time, as a clock: `24:12`, or `1:24:12` past an hour.
 *
 * Colon-separated clock time is the same notation in English and German, so
 * this deliberately does not vary between them — only the digits themselves go
 * through `Intl`, which is what makes it correct in a locale with its own
 * numerals.
 */
export function formatDuration(seconds: number, locale: string): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  const rest = total % 60;

  const plain = new Intl.NumberFormat(locale, { useGrouping: false });
  const padded = new Intl.NumberFormat(locale, { minimumIntegerDigits: 2, useGrouping: false });

  if (hours > 0) {
    return `${plain.format(hours)}:${padded.format(minutes)}:${padded.format(rest)}`;
  }
  return `${plain.format(minutes)}:${padded.format(rest)}`;
}

/**
 * A coarse estimate over a range: `~12–18 min` in English, `~12–18 Min.` in
 * German. The dash, the order and the unit name all come from `Intl`.
 */
export function formatEstimate(minSeconds: number, maxSeconds: number, locale: string): string {
  const low = Math.max(1, Math.round(Math.min(minSeconds, maxSeconds) / SECONDS_PER_MINUTE));
  const high = Math.max(1, Math.round(Math.max(minSeconds, maxSeconds) / SECONDS_PER_MINUTE));
  const format = new Intl.NumberFormat(locale, {
    style: "unit",
    unit: "minute",
    unitDisplay: "short",
    maximumFractionDigits: 0,
  });
  // `formatRange` inserts its own approximation sign when both ends are equal,
  // which would read as `~~12 min`.
  const range = low === high ? format.format(low) : format.formatRange(low, high);
  return `${APPROXIMATELY}${range}`;
}

/** An absolute date: `14 Mar 2025` in English, `14.03.2025` in German. */
export function formatDate(date: Date, locale: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(date);
}

/** The same instant with the time of day, for a `title` attribute. */
export function formatDateTime(date: Date, locale: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

/**
 * DESIGN.md's Age column: `3 days ago`, `vor 3 Tagen`. `numeric: "auto"` is what
 * turns `1 day ago` into `yesterday` where the locale has a word for it.
 */
export function formatRelativeDate(date: Date, locale: string, now: Date = new Date()): string {
  const seconds = (date.getTime() - now.getTime()) / 1_000;
  const format = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });

  for (const entry of RELATIVE_UNITS) {
    const [unit, size] = entry;
    if (Math.abs(seconds) >= size || size === 1) {
      return format.format(Math.round(seconds / size), unit);
    }
  }
  return format.format(0, "second");
}

/**
 * A language's name in its own language. A switcher labelled in the language
 * the reader cannot read is a switcher they cannot use.
 */
export function localeName(locale: string): string {
  return new Intl.DisplayNames([locale], { type: "language" }).of(locale) ?? locale;
}

export interface Formatters {
  readonly locale: string;
  readonly bytes: (bytes: number) => string;
  readonly speed: (bytesPerSecond: number) => string;
  readonly bitrate: (bitsPerSecond: number) => string;
  readonly number: (value: number, fractionDigits?: number) => string;
  readonly duration: (seconds: number) => string;
  /** `null` on either end means the estimate is unknown, which is said plainly. */
  readonly estimate: (minSeconds: number | null, maxSeconds: number | null) => string;
  readonly date: (date: Date) => string;
  readonly dateTime: (date: Date) => string;
  readonly relativeDate: (date: Date, now?: Date) => string;
}

/**
 * The formatters bound to whatever locale is rendering. `useTranslation` is what
 * subscribes the component to `languageChanged`, so a switch re-renders every
 * number on screen alongside every sentence.
 */
export function useFormat(): Formatters {
  // `useTranslation` is what subscribes this component to `languageChanged`;
  // the locale itself is read from the instance so it is always the resolved
  // one rather than whatever regional tag the browser handed over.
  const { t } = useTranslation();
  const locale = currentLocale();

  return useMemo(
    () => ({
      locale,
      bytes: (bytes: number) => formatBytes(bytes, locale),
      speed: (bytesPerSecond: number) => formatSpeed(bytesPerSecond, locale),
      bitrate: (bitsPerSecond: number) => formatBitrate(bitsPerSecond, locale),
      number: (value: number, fractionDigits?: number) =>
        formatNumber(value, locale, fractionDigits),
      duration: (seconds: number) => formatDuration(seconds, locale),
      estimate: (minSeconds: number | null, maxSeconds: number | null) =>
        minSeconds === null || maxSeconds === null
          ? t("format.unknown")
          : formatEstimate(minSeconds, maxSeconds, locale),
      date: (date: Date) => formatDate(date, locale),
      dateTime: (date: Date) => formatDateTime(date, locale),
      relativeDate: (date: Date, now?: Date) => formatRelativeDate(date, locale, now),
    }),
    // `locale` is the resolved language, so it changes on every switch; `t` is
    // listed because the unknown-estimate branch reads a translated sentence.
    [locale, t],
  );
}

export interface NumericProps {
  readonly children: ReactNode;
  readonly className?: string;
}

/**
 * DESIGN.md: "Numbers are tabular everywhere." `.tabular` ships in
 * `@pornarr/ui/utilities.css`; this is the one way to reach for it, so a column
 * of sizes cannot end up half proportional.
 */
export function Numeric({ children, className }: NumericProps): JSX.Element {
  return <span className={cx("tabular", className)}>{children}</span>;
}
