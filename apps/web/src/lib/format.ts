/**
 * Formatting the tiles share.
 *
 * Kept out of `@pornarr/ui` on purpose: the components take already-formatted
 * strings so they know nothing about locale or units, and that boundary is what
 * lets the same tile serve the library, the feed and the shorts rail.
 */

/** `h:mm:ss`, or `m:ss` under an hour. Never `0:04:03` for a four-minute clip. */
export function formatDuration(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = whole % 60;
  const pad = (value: number): string => String(value).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(rest)}` : `${minutes}:${pad(rest)}`;
}

/**
 * A stable number for a title, from its id.
 *
 * Drives the placeholder artwork's hue. It only has to be stable and spread —
 * not uniform and certainly not secure — so the cheapest rolling hash is the
 * right one.
 */
export function seedFrom(id: string): number {
  let total = 0;
  for (const character of id) total = (total * 31 + character.charCodeAt(0)) % 100_000;
  return total;
}

/** 0 to 1, from a position and a duration either of which may be missing. */
export function progressOf(
  position: number | null | undefined,
  duration: number | null | undefined,
): number {
  if (position == null || duration == null || duration <= 0) return 0;
  return Math.min(1, Math.max(0, position / duration));
}
