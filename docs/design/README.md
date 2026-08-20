# Design

Visual reference for the web client, and the component library the screens were
drawn with.

| Path | What it is |
|---|---|
| [screenshots/](screenshots/) | The running application, captured from a live stack — what the product actually looks like, as opposed to what it was drawn as |
| [previews/](previews/) | Full-frame renders, one PNG per screen — the fastest way to see the design without a checkout |
| [mockups/](mockups/) | The interactive canvas, exported from the design tool |
| [pornarr-ui/](pornarr-ui/) | The same design as working React components, with the rules it encodes written down |
| [brand/](brand/) | Logo and favicons, at the sizes the client ships |

Delivered 2026-08-16. `mockups/` and `previews/` were replaced wholesale by that
delivery rather than merged, because both are generated output.

## Which of these is authoritative

Neither `DESIGN.md` nor this folder is authoritative on its own any more, and it is
worth being blunt about where they disagree rather than discovering it mid-build:

- **`DESIGN.md` says "There is no rating bar."** The delivered screens have ratings on
  the tile, the row, the detail and an entire admin screen (A6). The design moved; the
  prose did not.
- **`DESIGN.md` specifies neutral surfaces with zero chroma**, so that posters are the
  only saturated thing on screen. The delivered token set uses a tinted ground stack
  (`#14111b` … `#40374f` in rose) and drives placeholder artwork from an accent-relative
  hue.
- **The delivered set ships two accents**, rose and amber, switched by `data-theme`.
  `DESIGN.md` describes one.

Treat `pornarr-ui/` as the current intent and `DESIGN.md` as the standing constraints
it has to satisfy — chiefly the contrast floor, which is enforced by
`packages/ui/src/contrast.test.ts` and is not negotiable.

## Previews

One render per screen. The identifiers match the sections of the mockup canvas.

| Group | Screens |
|---|---|
| A1–A6 | Admin: dashboard, scan and import, queue, tags, settings, ratings and comments |
| B1–B8 | Desktop: library, search, detail and player, collections, join, shorts grid, shorts player, feed |
| C1–C9 | Mobile web: library, player, filter sheet, shorts, login, first setup, admin, queue, feed |
| D1–D4 | iOS: library, shorts, now playing, server |

## Mockups

`mockups/Ponarr.dc.html` is the full canvas: every screen side by side with its
identifier and a one-line note. The `Ponarr*.dc.html` files beside it are single
components pulled out of it.

Open any of them directly from a checkout; they need no build step. They fetch the
Phosphor icon font from unpkg.com, so icons are missing offline — nothing else is.

The export is committed **verbatim**, including its own `assets/` copy of the logo
files and the generated `_ds/nocturne-<uuid>/` directory name. Neither is tidied up on
purpose: re-exporting then stays a clean drop-in replacement instead of a merge. Treat
everything under `mockups/` as generated — change the design in the tool and
re-export, never by hand.

## Component library

`pornarr-ui/` is the design as React components on a Vite dev server, with a
`Showcase.jsx` catalogue of every one of them. Its `README.md` is the most useful
document in this folder: it states the rules the components encode, which the PNGs can
only imply.

```bash
cd docs/design/pornarr-ui && npm install && npm run dev
```

It is **reference, not a dependency**. The application's component package is
`packages/ui`, which is TypeScript with CSS modules and a contrast test; this one is
JSX with a global stylesheet. Porting happens by moving values and rules across, not by
importing it — see `packages/ui/src/tokens.css`, where the delivered token set now
lives alongside the existing one.

## Design system

`mockups/_ds/nocturne-<uuid>/` is the **Nocturne** system the mockups were built on.
It is upstream of `pornarr-ui/`, and where the two disagree, `pornarr-ui/` is the later
word.
