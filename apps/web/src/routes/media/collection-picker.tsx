import type { paths } from "@pornarr/api-client";
/**
 * Put this title on one of your own shelves, or take it off again.
 *
 * `PUT /api/collections/{collection_id}/items/{media_id}` and its DELETE have
 * existed since collections did, and nothing in the application called either:
 * a shelf could be created, named, shared and deleted, and never filled, so
 * the detail screen's "Nothing on this shelf yet." was the only state a
 * collection could ever be in.
 *
 * A disclosure rather than a modal, the way the tag correction beside it works:
 * DESIGN.md puts modal last, and the list of shelves is short enough to sit
 * under the button that opens it. Only shelves the reader owns are offered -
 * `is_yours` - because a shelf somebody shared with the household is theirs to
 * arrange.
 */
import { Button } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";

type Collection =
  paths["/api/collections"]["get"]["responses"][200]["content"]["application/json"][number];

export function CollectionPicker({ mediaId }: { readonly mediaId: string }): JSX.Element {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);

  const collections = useQuery({
    queryKey: ["collections", "mine"],
    // Asked for only once the reader has said they want to file this
    // somewhere: the detail screen is the most-visited one in the product and
    // a shelf list nobody opened is a request nobody needed.
    enabled: open,
    queryFn: async (): Promise<Collection[]> => {
      const { data, error, response } = await getApiClient().GET("/api/collections");
      if (!data || error) throw apiFailure(error, response);
      return data.filter((collection) => collection.is_yours);
    },
  });

  const add = useMutation({
    mutationFn: async (collectionId: string) => {
      const { error, response } = await getApiClient().PUT(
        "/api/collections/{collection_id}/items/{media_id}",
        { params: { path: { collection_id: collectionId, media_id: mediaId } } },
      );
      if (error) throw apiFailure(error, response);
    },
    onSuccess: (_result, collectionId) => {
      setOpen(false);
      void cache.invalidateQueries({ queryKey: ["collections"] });
      void cache.invalidateQueries({ queryKey: ["collection", collectionId] });
    },
  });

  return (
    <div className="flex flex-col items-start gap-2">
      <Button
        variant="secondary"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        disabled={add.isPending}
      >
        {t("collections.addToCollection")}
      </Button>

      {open ? (
        <div className="min-w-[14rem] rounded-lg border border-border bg-surface p-2">
          {collections.isPending ? (
            <p className="px-2 py-1 text-sm text-ink-muted">{t("collections.loading")}</p>
          ) : collections.isError ? (
            <p role="alert" className="px-2 py-1 text-sm text-ink">
              {t("errors.generic")}
            </p>
          ) : (collections.data ?? []).length === 0 ? (
            <p className="px-2 py-1 text-sm text-ink-muted">{t("collections.noneOfYourOwn")}</p>
          ) : (
            // A list of actions rather than a listbox: picking a shelf files
            // the title immediately, there is nothing to submit afterwards, and
            // an `option` that acts on click is a button wearing a costume.
            <ul aria-label={t("collections.addToCollection")}>
              {(collections.data ?? []).map((collection) => (
                <li key={collection.id}>
                  <button
                    type="button"
                    className="w-full rounded-md px-2 py-1.5 text-left text-sm text-ink transition-colors hover:bg-surface-3"
                    onClick={() => add.mutate(collection.id)}
                  >
                    {collection.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {add.isError ? (
            <p role="alert" className="px-2 py-1 text-sm text-ink">
              {t("errors.generic")}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
