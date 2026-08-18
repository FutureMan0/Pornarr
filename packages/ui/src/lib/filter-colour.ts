/**
 * What a CSS filter does to a single colour, computed rather than guessed.
 *
 * WHY THIS EXISTS. The brand mark is one fixed pink gradient, and the token set
 * shifts it to the second accent with `--pa-logo-filter: hue-rotate(63deg)
 * saturate(1.08)`. That works wherever CSS runs. A favicon does not have that
 * luxury: it is an isolated document that never sees the host page's custom
 * properties, so the colours have to arrive already transformed.
 *
 * Rather than keeping a second hand-picked palette for the icon — which would
 * drift from the token the moment either changed — this applies the same
 * matrices the browser does, reading the token as its input. One source of truth,
 * two renderers.
 *
 * The matrices are the ones in the Filter Effects specification. `hue-rotate` is
 * *defined* as a linear matrix on sRGB, which is why it is not the same as
 * rotating a hue in HSL and why a hand-rolled HSL version would visibly disagree
 * with what the sidebar shows.
 */

/** A colour as three sRGB channels, 0–255, not necessarily whole. */
type Channels = readonly [number, number, number];

type Matrix = readonly [number, number, number, number, number, number, number, number, number];

// The luminance coefficients both matrices are built from.
const LUMA_R = 0.213;
const LUMA_G = 0.715;
const LUMA_B = 0.072;

function hueRotateMatrix(degrees: number): Matrix {
  const radians = (degrees * Math.PI) / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  return [
    LUMA_R + cos * 0.787 - sin * 0.213,
    LUMA_G - cos * 0.715 - sin * 0.715,
    LUMA_B - cos * 0.072 + sin * 0.928,
    LUMA_R - cos * 0.213 + sin * 0.143,
    LUMA_G + cos * 0.285 + sin * 0.14,
    LUMA_B - cos * 0.072 - sin * 0.283,
    LUMA_R - cos * 0.213 - sin * 0.787,
    LUMA_G - cos * 0.715 + sin * 0.715,
    LUMA_B + cos * 0.928 + sin * 0.072,
  ];
}

function saturateMatrix(amount: number): Matrix {
  return [
    LUMA_R + 0.787 * amount,
    LUMA_G - LUMA_G * amount,
    LUMA_B - LUMA_B * amount,
    LUMA_R - LUMA_R * amount,
    LUMA_G + 0.285 * amount,
    LUMA_B - LUMA_B * amount,
    LUMA_R - LUMA_R * amount,
    LUMA_G - LUMA_G * amount,
    LUMA_B + 0.928 * amount,
  ];
}

function apply(channels: Channels, matrix: Matrix): Channels {
  const [r, g, b] = channels;
  const [a, c, d, e, f, h, i, j, k] = matrix;
  return [r * a + g * c + b * d, r * e + g * f + b * h, r * i + g * j + b * k];
}

/** `#rrggbb` or `#rgb` to channels. Anything else is black, which is visible. */
export function parseHex(value: string): Channels {
  const digits = value.trim().replace(/^#/, "");
  const full =
    digits.length === 3
      ? digits
          .split("")
          .map((digit) => digit + digit)
          .join("")
      : digits;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return [0, 0, 0];
  return [
    Number.parseInt(full.slice(0, 2), 16),
    Number.parseInt(full.slice(2, 4), 16),
    Number.parseInt(full.slice(4, 6), 16),
  ];
}

function toHex(channels: Channels): string {
  const digits = channels
    .map((channel) => {
      // Clamped, because both matrices can push a channel past the ends of the
      // range — a saturated pink rotated far enough leaves the sRGB gamut, and
      // the browser clamps in exactly the same place.
      const whole = Math.round(Math.min(255, Math.max(0, channel)));
      return whole.toString(16).padStart(2, "0");
    })
    .join("");
  return `#${digits}`;
}

/**
 * Run a CSS filter list over one colour.
 *
 * Understands `hue-rotate()` and `saturate()`, which is what `--pa-logo-filter`
 * uses. Anything else in the list is ignored rather than approximated: a filter
 * this does not model would otherwise produce a colour that is confidently
 * wrong, and an untransformed colour is at least recognisably the brand.
 */
export function applyCssFilter(hex: string, filter: string): string {
  const trimmed = filter.trim();
  if (trimmed === "" || trimmed === "none") return hex;

  let channels = parseHex(hex);
  for (const [, name, argument] of trimmed.matchAll(/([a-z-]+)\(([^)]*)\)/g)) {
    if (name === undefined || argument === undefined) continue;
    const raw = argument.trim();
    if (name === "hue-rotate") {
      const degrees = Number.parseFloat(raw);
      if (Number.isFinite(degrees)) {
        channels = apply(channels, hueRotateMatrix(raw.endsWith("turn") ? degrees * 360 : degrees));
      }
      continue;
    }
    if (name === "saturate") {
      const amount = raw.endsWith("%") ? Number.parseFloat(raw) / 100 : Number.parseFloat(raw);
      if (Number.isFinite(amount)) channels = apply(channels, saturateMatrix(amount));
    }
  }
  return toHex(channels);
}
