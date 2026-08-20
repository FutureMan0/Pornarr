# Screenshots

The running application, captured 2026-08-20 from the Compose stack with real indexers, a real
qBittorrent and a real SABnzbd behind it. These are photographs, not renders: everything visible
is what the product did, including the empty columns where a test fixture has no studio or
release date. [`previews/`](../previews/) holds the drawn design; this holds the built one.

Artwork is in its default state in all of them, which is blurred — see
[`art-visibility.ts`](../../../apps/web/src/lib/art-visibility.ts) for why that is the default
and why the top bar's toggle is per device. That is why every tile is a smear of colour rather
than a poster; the media behind them are FFmpeg test patterns in any case.

| File | Screen |
|---|---|
| `dashboard.png` | `/admin` — counts, recently added, library health, activity |
| `search.png` | `/search` — local matches and per-indexer results for one query |
| `library.png` | `/library` — filters, continue watching, the grid |
| `media-detail.png` | `/library/:id` — player, actions, tags, ratings, comments |
| `downloads.png` | `/downloads` — the queue and its summary |
| `quality-profiles.png` | `/settings/quality` — the profile editor |

Retake them against a stack of your own rather than editing them; nothing regenerates them.
