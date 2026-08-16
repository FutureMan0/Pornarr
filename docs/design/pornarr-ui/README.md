# Pornarr UI

The design system behind the Pornarr mockups, as React components on a Vite dev server.

## Run

```bash
cd pornarr-ui
npm install
npm run dev      # http://localhost:5180
```

`src/Showcase.jsx` is a live catalogue of every component. Delete it once you wire the real app;
nothing else imports it.

## Use in your app

```jsx
import '@phosphor-icons/web/regular';
import '@phosphor-icons/web/fill';
import 'pornarr-ui/src/styles/tokens.css';
import 'pornarr-ui/src/styles/base.css';
import { Button, MediaTile, Sidebar, Topbar, useTheme } from 'pornarr-ui';
```

## Theming

Two stylesheets' worth of tokens live in one file, scoped by attribute:

```html
<html data-theme="rose">   <!-- or data-theme="amber" -->
```

`useTheme()` writes that attribute and remembers the choice per browser.

Everything downstream follows: accent ramp, ground stack, text ramp, placeholder artwork hue,
blur strength, and the logo's hue rotation.

## The rules the components encode

- **Accent as a line, not a flood.** `--pa-accent-500` is for icons, borders, bars and glows.
  Small text uses `--pa-accent-300` — below 15px the 500 step fails contrast on this ground.
- **Ground stack, in order.** `--pa-bg-00` (video) < `-0` (page) < `-1` (frame) < `-2` (card) <
  `-3` (row) < `-4` (track). A child is never darker than its parent.
- **Scroll regions are masked.** Any list that can outgrow its box gets `className="pa-scroll"`
  (`min-height:0`, `overflow:hidden`, fade at the bottom) — never a hard clip.
- **Artwork is anchored, not rotated.** `artFor(hue)` compresses each title's hue into a ±48°
  band around `--pa-art-hue`, so tiles stay in the accent's family in both themes.
- **Blur is a token.** `--pa-art-blur` / `-md` / `-sm` plus `--pa-art-sat`, so "hide artwork"
  is one switch, not per-component CSS.

## Components

| Import | What it is |
| --- | --- |
| `Button`, `IconButton` | `primary` outline, `secondary`, `ghost`, `tonal` |
| `Tag` | `accent` / `neutral` / `outline` |
| `Card` | surface with optional title, action and masked scroll body |
| `Field`, `Input` | labelled form row |
| `Toggle`, `Segmented`, `Stars`, `ProgressBar` | the four controls the screens use |
| `Artwork`, `MediaTile`, `MediaRow`, `artFor` | placeholder media |
| `Sidebar`, `Topbar`, `Screen` | app shell; `Topbar` takes `connection="live\|reconnecting\|offline"` |
| `StatCard`, `ListRow`, `Banner`, `EmptyState` | the four repeating content blocks |
| `ThemeSwitch`, `useTheme` | accent switching |

## Wiring it to the API

The components take plain data; nothing fetches. A `MediaTile` expects
`{ title, meta, dur, res, tags, rating, comments, hue, progress }` — map `hue` from any stable
number on the record (id hash works) until real posters exist, then swap `Artwork` for an `<img>`.
