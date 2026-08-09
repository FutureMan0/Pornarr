# Brand assets

Source files for the Pornarr mark. `apps/web` copies what it needs from here; nothing
regenerates these at build time, so treat the two SVGs as the masters and everything
else as output.

| File | Use |
|---|---|
| `logo.svg` | The mark on its own. Vector master — scale it, don't reach for a PNG |
| `logo-text.svg` | Mark plus wordmark, for headers and the sidebar brand slot |
| `favicon.ico` | Browser tab. Multi-resolution: 16, 32 and 64 px in one file |
| `favicon-16.png`, `favicon-32.png`, `favicon-64.png` | The individual sizes, for manifests that want PNGs |
| `logo-256.png` | Raster mark for README badges and anywhere SVG is awkward |
| `logo-text.png` | Raster lockup, 1600 px wide |
| `logo.png` | The original 1024 px render the vectors were traced from. Kept as provenance, not for use |

## Regenerating

Every raster above is rendered from `logo.svg` or `logo-text.svg`. Change a vector and
the rasters must be re-rendered from it — they are not picked up automatically.

The mark carries a gradient (`#ee0083` → `#ff4fc2`) and a soft outer glow, both defined
inside the SVG. It is deliberately more saturated than the interface accent in
[`DESIGN.md`](../DESIGN.md); do not source token values from it.

`logo.png` has an alpha channel, and the alpha is the only reliable silhouette — the
RGB values under transparent pixels are meaningless. Anything that re-traces the mark
must read alpha, not colour.
