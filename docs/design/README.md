# Design

Visual reference for the web client. [`DESIGN.md`](../../DESIGN.md) at the repository
root is authoritative: it defines the tokens, and where anything here disagrees with
it, it wins. These files show what those tokens look like assembled into screens.

| Path | What it is |
|---|---|
| [previews/](previews/) | Full-frame renders — the fastest way to see the design without a checkout |
| [mockups/](mockups/) | The interactive mockups, exported from the design tool |
| [`../../assets/`](../../assets/) | Brand marks and favicons, the source files the client ships |

## Previews

GitHub does not render HTML from a repository, so the two renders below are the only
way to see the design in the browser tab you are already in.

| Render | Screens |
|---|---|
| [desktop-shorts.png](previews/desktop-shorts.png) | B7 — the Shorts feed on desktop: sidebar, top bar, centred vertical clip, comments and Up next |
| [mobile-screens.png](previews/mobile-screens.png) | D1–D4 — Library, Shorts, Now playing, Settings |

## Mockups

`mockups/Ponarr.dc.html` is the full canvas: every screen, laid out side by side with
its identifier and a one-line note. The four `Ponarr*.dc.html` files beside it are
single components pulled out of that canvas — sidebar, top bar, thumbnail, related row.

Open any of them directly from a checkout; they need no build step. They do fetch the
Phosphor icon font from unpkg.com, so icons are missing offline — nothing else is.

The export is committed **verbatim**, including its own `assets/` copy of the two logo
files and the generated `_ds/nocturne-<uuid>/` directory name. Neither is tidied up on
purpose: re-exporting from the design tool then stays a clean drop-in replacement
instead of a merge. Treat everything under `mockups/` as generated — change the design
in the tool and re-export, never by hand.

Two parts of the export are not committed: the canvas thumbnail, and the `uploads/`
staging folder, whose contents are either duplicates of `assets/` or intermediate crops
of the two renders above.

## Design system

`mockups/_ds/nocturne-<uuid>/` is the **Nocturne** system the mockups were built on —
one `styles.css` carrying the tokens and a component layer, with `readme.md` explaining
the intent behind them. It is a useful reference for spacing, elevation and interaction
states when implementing `packages/ui`.

It is a reference, not the contract. Two divergences to know about before copying
values out of it:

- Nocturne ships a blurple accent (`#9184d9`). The mockups override it to the Pornarr
  pink (`#d9629f`), which is what `DESIGN.md` specifies as `--primary`
  (`oklch(0.62 0.180 340)`). Take the pink.
- The brand mark runs a more saturated gradient (`#ee0083` → `#ff4fc2`) than the
  interface accent. That is deliberate — a logo is not a UI surface — and neither value
  should be pulled into the token sheet.
