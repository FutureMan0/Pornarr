# Design

Visual system for Pornarr. Owned by FutureMan0, implemented in `packages/ui` and
consumed by `apps/web`. Read `PRODUCT.md` first — this file describes how it looks,
that file describes who it serves and why.

Every token below is a CSS custom property in `packages/ui/src/tokens.css`. Nothing
in `apps/web` may hardcode a colour, a radius, a duration or a spacing value.

## Theme

Dark only in v0.1. This is not a stylistic preference: the stated usage context is
evening, in a dark room, with the screen as the primary light source. A light theme
is a later addition, and the token structure is built so that adding one means
supplying a second set of values, never touching a component.

The surface is neutral at zero chroma. Posters, thumbnails and video frames are the
only saturated things on screen. A tinted surface would compete with exactly the
images the product exists to present.

## Color

Strategy: **restrained**. The accent occupies under 10% of any screen. It marks
primary actions, current selection and state — never decoration.

```css
:root {
  /* Surfaces — true neutral, zero chroma */
  --bg:            oklch(0.09 0.000 0);    /* page */
  --surface:       oklch(0.13 0.000 0);    /* cards, panels, rows */
  --surface-2:     oklch(0.17 0.000 0);    /* sidebar, toolbar, thead */
  --surface-3:     oklch(0.21 0.000 0);    /* hover on surface-2 */
  --border:        oklch(0.24 0.000 0);
  --border-strong: oklch(0.34 0.000 0);    /* focused inputs, active tabs */
  --border-control:oklch(0.52 0.000 0);    /* resting edge of a control */

  /* Ink */
  --ink:           oklch(0.97 0.000 0);    /* body, headings */
  --ink-muted:     oklch(0.72 0.000 0);    /* labels, metadata, captions */
  --ink-faint:     oklch(0.55 0.000 0);    /* disabled, placeholder-adjacent */

  /* Brand */
  --primary:       oklch(0.62 0.180 340);  /* actions, selection, focus */
  --primary-hover: oklch(0.68 0.190 340);
  --primary-ink:   oklch(0.14 0.000 0);    /* text ON primary — dark, not white */
  --primary-weak:  oklch(0.30 0.070 340);  /* selected row background */

  /* Semantic */
  --success:       oklch(0.70 0.150 150);
  --warning:       oklch(0.78 0.150  75);
  --danger:        oklch(0.62 0.200  25);
  --info:          oklch(0.70 0.120 240);
  /* Each has a -weak variant at L 0.26–0.30, C 0.05–0.07, same hue, for
     badge and banner backgrounds. */
}
```

**Contrast rules, enforced not assumed.** Body text hits 4.5:1 against whatever
surface it sits on; large text (≥18px, or bold ≥14px) hits 3:1. Placeholder text is
held to the body requirement — the muted-grey placeholder is the single most common
failure and is not acceptable here. `--ink-faint` is for disabled states only,
never for readable content.

Text on `--primary` uses `--primary-ink`. White on a rose of this lightness does not
reach 4.5:1; dark ink does. This is why primary buttons carry dark labels.

`--border` and `--border-strong` separate and decorate. Neither reaches 3:1
against any surface — they measure 1.22:1 and 1.71:1 on `--surface` — so neither
may be the only thing identifying an interactive control. A control's resting
edge is `--border-control`, the one neutral that clears 3:1 against all four
surfaces and therefore satisfies WCAG 1.4.11.

Text on a `-weak` background is `--ink`, never the matching hue: `--danger` on
`--danger-weak` reaches only 3.72:1. The hue is not what carries the meaning
anyway, per the rule below.

Never signal state by colour alone. Every status carries an icon or a text label
beside its colour, for colour-vision deficiency and for scanning at speed.

## Typography

One family. Product UI does not need a display/body pairing, and a display face in
a label is a category error.

```css
--font-sans: "Inter Variable", system-ui, sans-serif;
--font-mono: "JetBrains Mono Variable", ui-monospace, monospace;
```

Mono is not decorative. It is reserved for content where character identity matters:
file paths, hashes (oshash, phash), job identifiers, indexer GUIDs, log output.

**Fixed rem scale, not fluid.** Users view product UI at consistent DPI; a heading
that shrinks inside a sidebar looks worse, not better. Ratio ≈1.15 — there are many
type elements here and exaggerated contrast creates noise.

| Token | Size | Line height | Weight | Use |
|---|---|---|---|---|
| `--text-2xs` | 0.6875rem | 1.4 | 500 | badges, table micro-labels |
| `--text-xs`  | 0.75rem   | 1.45 | 400 | metadata, captions, help text |
| `--text-sm`  | 0.8125rem | 1.5 | 400 | table cells, form labels, dense UI |
| `--text-base`| 0.9375rem | 1.55 | 400 | body, default control text |
| `--text-md`  | 1.0625rem | 1.4 | 600 | card titles, section headings |
| `--text-lg`  | 1.25rem   | 1.3 | 600 | page headings |
| `--text-xl`  | 1.5rem    | 1.25 | 650 | detail-page titles |

Nothing above 1.5rem exists in this product. There is no hero.

**Numbers are tabular everywhere.** Sizes, durations, speeds, bitrates, scores,
counts, dates. `font-variant-numeric: tabular-nums` on the numeric utility class,
right-aligned in tables. Columns must compare without the eye re-anchoring.

`text-wrap: balance` on headings, `text-wrap: pretty` on prose. Prose caps at 70ch;
tables and dense panels are exempt.

## Spacing

4px base. `--space-1: 0.25rem` through `--space-16: 4rem`.

Vary spacing for rhythm rather than applying one uniform gap. Related controls sit
at `--space-2`; groups separate at `--space-6`; sections separate at `--space-10`.

## Radius, elevation, layering

```css
--radius-sm: 4px;    /* badges, inputs, small controls */
--radius-md: 8px;    /* buttons, menu items */
--radius-lg: 12px;   /* cards, panels, dialogs */
--radius-full: 999px;/* tags, avatars, pills */
```

Cards top out at 12px. Nothing in this system is more rounded than that.

Elevation is carried by surface lightness first. Shadows appear only on genuinely
floating layers — dropdowns, dialogs, toasts — at no more than 8px blur. A 1px
border and a wide soft shadow are never applied to the same element; pick one.

```css
--z-dropdown: 100;  --z-sticky: 200;  --z-backdrop: 300;
--z-modal:    400;  --z-toast:  500;  --z-tooltip: 600;
```

No arbitrary values. Nothing is 999 or 9999.

## Layout

**App shell.** Fixed left sidebar at 240px, collapsing to a 56px icon rail below
1280px and to an overlay drawer below 768px. A top bar carries global search, the
live status cluster and the account menu. Content is capped at 1600px and centred;
tables and grids are allowed the full width.

Responsive behaviour here is structural — the sidebar collapses, the table
reflows into stacked rows, the grid changes column count. Typography does not
resize with the viewport.

**Library grid.** `repeat(auto-fill, minmax(200px, 1fr))` with a `--space-4` gap.
No breakpoint list. Poster aspect 16:9, `object-fit: cover`, skeleton placeholder
of the same aspect so the grid never reflows on load.

**Global status cluster.** Persistent in the top bar: active download count,
current aggregate speed, quarantine count when non-zero. Clicking opens a panel,
not a page. This is how "state is never a question" is delivered.

## Components

Every interactive component ships default, hover, focus-visible, active, disabled,
loading and error. Half a set is not a component.

Loading is a skeleton matching the eventual layout, never a spinner centred in a
content area. Empty states teach the interface — an empty library says how to add a
root folder and links to it; it does not say "nothing here".

### Media card

The densest component and the one the product is judged by. Anatomy:

```
┌──────────────────────────────┐
│ [16:9 poster]                │  hover: preview sprite scrubs on pointer X
│                    ┌───────┐ │  position, never autoplaying video
│                    │ 24:12 │ │  duration, bottom-right, on a scrim
│                    └───────┘ │
│ ▔▔▔▔▔▔▔▔▔▔▔▔░░░░░░░░░░░░░░░░ │  watch progress, 2px, --primary
├──────────────────────────────┤
│ Title, two lines maximum     │  --text-md, line-clamp 2
│ Studio · 2026-03-14          │  --text-xs, --ink-muted
│ [2160p] [HEVC] [8.4 GB]      │  --text-2xs badges, surface-2
└──────────────────────────────┘
```

The card is a link with one hover state that does not move layout. Secondary
actions live in a menu revealed on hover and permanently present for keyboard
users at the same tab position.

There is no rating bar. There is no coloured stripe on any edge.

### Release row (search results)

A table, not a card grid. Users compare releases, and comparison requires aligned
columns.

| Column | Alignment | Notes |
|---|---|---|
| Title | left | mono for the raw release name, truncated with tooltip |
| Indexer | left | text label, plus health dot when degraded |
| Quality | left | badge; upgrade candidates marked |
| Size | right | tabular |
| Age | right | relative, `title` carries the absolute date |
| Seeders | right | tabular; torrent rows only |
| Score | right | tabular, with a hover breakdown of its components |
| Time | right | range with confidence, see below |
| — | right | grab button |

Rows already in the library are dimmed and badged rather than hidden, because
knowing something is present is itself the answer.

### Estimate

Never a bare number. `~12–18 min` with a confidence indicator: three states,
distinguished by icon and label, not by colour alone. Unknown estimates say
"unknown", never "0" and never a spinner that resolves to nothing.

### Status badge

One vocabulary across the whole product for the request and download lifecycle:
searching, queued, downloading, importing, available, quarantined, failed,
cancelled. Same shape, same size, same position in every context.

## Motion

150–250ms on transitions. Users are in a task and do not want choreography.

Easing is exponential ease-out (`cubic-bezier(0.16, 1, 0.3, 1)`). No bounce, no
elastic.

Motion carries state, and it also carries craft. Those are two claims and this
section used to make only the first — it read "no orchestrated page-load
sequences, no decorative movement anywhere", which described a product that felt
correct and inert. The owner asked for the second, in as many words: light
premium movement, not overdone. So the rule is a budget rather than a
prohibition.

**What moves.** Four things, and they are named so that a fifth has to argue for
itself:

- A **floating surface** arrives from the control that opened it — a few pixels
  of travel, a hair of scale. `.pa-pop`.
- A **screen** settles in once on arrival, as one piece — it rises, it does not
  fade. `.pa-enter`.
- A **surface under the pointer** grows very slightly and gains the shadow of
  something nearer the reader. `.pa-raise`, and the media tile.
- The **current navigation entry** grows a bar on its leading edge. `.pa-nav`.

**What does not.** Elements arriving one after another inside a screen; anything
triggered by scroll position; movement that repeats while idle; anything a reader
has to wait for before they can act. A page whose parts appear in sequence is a
page that takes longer than it did.

**Only what is appearing from nothing may fade.** A floating surface has no
readable state to degrade, so it can come up from zero opacity. Content that is
already the page may only move: text at partial opacity over the page ground is
text below its contrast minimum, briefly but really. The accessibility suite
caught the first version of the screen transition doing this — a primary button
measured at 3.68:1 mid-fade against a required 4.5:1.

**Every distance is a token.** `--lift-sm`, `--lift-md`, `--hover-scale`, beside
the durations. A component naming its own is a component that cannot be turned
off, which is the whole reason for the paragraph below.

Prefer `@starting-style` and a transition over a keyframe animation: there is
nothing to re-trigger, nothing to interrupt, and no state to hold in JavaScript.
An element renders at its final values and the browser interpolates from the
starting ones exactly once, on insertion.

Progress values interpolate between server events rather than jumping, so a bar
updated every five seconds reads as continuous.

```css
@media (prefers-reduced-motion: reduce) {
  /* durations collapse to 1ms — and so do the distances */
}
```

The distances matter as much as the durations here. A 1ms transition over ten
pixels is not a reduced animation, it is a jump, which is the exact thing the
preference asks not to happen. Zeroing `--lift-*` and `--hover-scale` means the
element is simply where it belongs from the first frame.

Reduced motion is not a degraded path. It is a supported path and is tested.

## Accessibility

WCAG 2.2 AA, verified by axe-core in the Playwright suite on `develop`.

Focus is visible on every interactive element: a 2px ring in `--primary` at 3:1
against its surroundings, never removed, never replaced by a colour change alone.
Focus order follows visual order. Focus is trapped in dialogs and returned to the
trigger on close.

Live regions announce state changes that arrive over SSE — a download completing, an
import finishing, a request failing — politely, without stealing focus.

The player is its own scope: full keyboard transport, ARIA-labelled controls,
subtitle support, and no keyboard trap.

## Bans

Beyond the shared bans (gradient text, glassmorphism as default, side-stripe
borders, identical card grids, decorative grid backgrounds), this project also
refuses:

- Autoplaying video anywhere without a user action. Hover previews scrub a sprite.
- Nested carousels, and carousels for anything a grid can show.
- Hover effects that change layout or reflow the grid.
- Modal as the first answer. Exhaust inline and progressive alternatives first.
- Custom scrollbars, custom form controls, and any reinvention of a standard
  affordance for flavour.
- Marketing copy inside the product.
