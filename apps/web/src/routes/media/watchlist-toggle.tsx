/**
 * Add or remove this title from the watch-later queue.
 *
 * The state and the mutation live in `useWatchlist`, because the shorts feed
 * needs the same two things in the shape of an icon. This is the labelled
 * button the detail screen wants.
 */
import { Button } from "@pornarr/ui";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";

import { useWatchlist } from "./use-watchlist";

export function WatchlistToggle({ mediaId }: { readonly mediaId: string }): JSX.Element {
  const { t } = useTranslation();
  const { saved, busy, toggle } = useWatchlist(mediaId);

  return (
    <Button
      variant="secondary"
      className="self-start"
      aria-pressed={saved}
      disabled={busy}
      onClick={toggle}
    >
      {saved ? t("watchlist.remove") : t("watchlist.add")}
    </Button>
  );
}
