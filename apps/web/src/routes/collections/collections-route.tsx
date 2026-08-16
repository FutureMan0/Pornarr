/**
 * B4 — collections.
 *
 * Private by default and shared only on purpose, which the screen has to make
 * visible rather than bury: the visibility of a shelf is the whole difference
 * between a private note and something the household can read.
 */
import { Button, Input, MediaTile } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FormEvent, JSX } from "react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { getApiClient } from "../../lib/api";
import { apiFailure } from "../../lib/api-error";
import { seedFrom } from "../../lib/format";

const COLLECTIONS_KEY = ["collections"] as const;

export function CollectionsRoute(): JSX.Element {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);

  const collections = useQuery({
    queryKey: [...COLLECTIONS_KEY, shared],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET("/api/collections", {
        params: { query: { shared } },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const create = useMutation({
    mutationFn: async (value: string) => {
      const { data, error, response } = await getApiClient().POST("/api/collections", {
        body: { name: value, visibility: "private" },
      });
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
    onSuccess: () => {
      setName("");
      void cache.invalidateQueries({ queryKey: COLLECTIONS_KEY });
    },
  });

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    if (name.trim().length > 0) create.mutate(name.trim());
  };

  return (
    <section aria-labelledby="collections-heading" className="flex flex-col gap-6">
      <h1 id="collections-heading" className="text-xl text-ink">
        {t("collections.title")}
      </h1>

      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        {/* Associated by id rather than by nesting. `Input` renders its own
            element, so a wrapping label leaves the connection to something a
            reader of this file cannot see. */}
        <div className="flex flex-col gap-1">
          <label htmlFor="collection-name" className="text-xs text-ink-muted">
            {t("collections.name")}
          </label>
          <Input
            id="collection-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={128}
          />
        </div>
        <Button type="submit" disabled={create.isPending || name.trim().length === 0}>
          {t("collections.create")}
        </Button>
        {create.isError ? (
          <span role="alert" className="text-xs text-ink">
            {t("collections.duplicate")}
          </span>
        ) : null}
      </form>

      <label className="flex items-center gap-2 text-sm text-ink-muted">
        <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
        {t("collections.includeShared")}
      </label>

      {collections.isPending ? (
        <p className="text-sm text-ink-muted">{t("collections.loading")}</p>
      ) : null}
      {collections.isError ? (
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      ) : null}

      {collections.data !== undefined ? (
        collections.data.length === 0 ? (
          <p className="text-sm text-ink-muted">{t("collections.empty")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {collections.data.map((collection) => (
              <li key={collection.id}>
                <Link
                  to={`/collections/${collection.id}`}
                  className="flex items-center gap-3 rounded-md bg-surface-2 px-3 py-3 hover:bg-surface-3"
                >
                  <span className="text-sm text-ink">{collection.name}</span>
                  <span className="text-2xs text-ink-muted">
                    {t("collections.items", { count: collection.item_count })}
                  </span>
                  {/* Shown, not implied. Whether a shelf is readable by the
                      household is the one fact about it that matters. */}
                  <span className="ml-auto text-2xs text-[var(--pa-accent-300)]">
                    {t(`collections.visibility.${collection.visibility}`)}
                  </span>
                  {collection.is_yours ? null : (
                    <span className="text-2xs text-ink-muted">{collection.owner}</span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </section>
  );
}

export function CollectionDetailRoute(): JSX.Element {
  const { t } = useTranslation();
  const { collectionId = "" } = useParams();
  const cache = useQueryClient();

  const collection = useQuery({
    queryKey: ["collection", collectionId],
    queryFn: async () => {
      const { data, error, response } = await getApiClient().GET(
        "/api/collections/{collection_id}",
        { params: { path: { collection_id: collectionId } } },
      );
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const visibility = useMutation({
    mutationFn: async (next: "private" | "shared") => {
      const { error, response } = await getApiClient().PATCH("/api/collections/{collection_id}", {
        params: { path: { collection_id: collectionId } },
        body: { visibility: next },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: ["collection", collectionId] }),
  });

  if (collection.isPending)
    return <p className="text-sm text-ink-muted">{t("collections.loading")}</p>;
  if (collection.isError)
    return (
      <p role="alert" className="text-sm text-ink">
        {t("errors.generic")}
      </p>
    );

  const detail = collection.data;
  return (
    <section aria-labelledby="collection-heading" className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 id="collection-heading" className="text-xl text-ink">
          {detail.name}
        </h1>
        <span className="text-xs text-ink-muted">
          {t("collections.items", { count: detail.item_count })}
        </span>
        {detail.is_yours ? (
          <Button
            variant="secondary"
            className="ml-auto"
            disabled={visibility.isPending}
            onClick={() => visibility.mutate(detail.visibility === "shared" ? "private" : "shared")}
          >
            {detail.visibility === "shared" ? t("collections.makePrivate") : t("collections.share")}
          </Button>
        ) : null}
      </div>

      {detail.items.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("collections.emptyShelf")}</p>
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-4">
          {detail.items.map((item) => (
            <li key={item.media_id}>
              <MediaTile
                title={item.media_title}
                seed={seedFrom(item.media_id)}
                rating={null}
                ratingLabel={t("library.rating.none")}
                poster={<img src={`/api/media/${item.media_id}/poster`} alt="" loading="lazy" />}
                action={(content) => (
                  <Link to={`/library/${item.media_id}`} className="block rounded-md">
                    {content}
                  </Link>
                )}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
