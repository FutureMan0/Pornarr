import { useInfiniteQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type Item = {
  id: string;
  title: string;
  studio: string | null;
  release_date: string | null;
  duration_seconds: number | null;
  quality: string | null;
  resolution: string | null;
  position_seconds: number | null;
  progress_duration_seconds: number | null;
  poster_url: string;
  sprite_url: string | null;
};
type Page = { items: Item[]; next_offset: number | null };

export function LibraryRoute() {
  const { t } = useTranslation();
  const library = useInfiniteQuery<Page, Error>({
    queryKey: ["library"],
    initialPageParam: 0,
    getNextPageParam: (page) => page.next_offset ?? undefined,
    queryFn: async ({ pageParam }): Promise<Page> => {
      const response = await fetch(`/api/library?limit=48&offset=${pageParam}`);
      if (!response.ok) throw new Error();
      return response.json() as Promise<Page>;
    },
  });
  const items = library.data?.pages.flatMap((page) => page.items) ?? [];
  if (library.isPending)
    return (
      <section aria-labelledby="library-heading">
        <h1 id="library-heading" className="text-xl text-ink">
          {t("library.title")}
        </h1>
        <p className="text-sm text-ink-muted">{t("library.loading")}</p>
      </section>
    );
  if (library.isError)
    return (
      <section aria-labelledby="library-heading">
        <h1 id="library-heading" className="text-xl text-ink">
          {t("library.title")}
        </h1>
        <p role="alert" className="text-sm text-ink">
          {t("errors.generic")}
        </p>
      </section>
    );
  return (
    <section aria-labelledby="library-heading" className="flex flex-col gap-6">
      <h1 id="library-heading" className="text-xl text-ink">
        {t("library.title")}
      </h1>
      {items.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("library.empty")}</p>
      ) : (
        <VirtualGrid
          items={items}
          onEnd={() =>
            library.hasNextPage && !library.isFetchingNextPage && void library.fetchNextPage()
          }
        />
      )}
    </section>
  );
}

function VirtualGrid({ items, onEnd }: { readonly items: Item[]; readonly onEnd: () => void }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(1);
  useEffect(() => {
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setColumns(Math.max(1, Math.floor(entry.contentRect.width / 216)));
    });
    if (parentRef.current) observer.observe(parentRef.current);
    return () => observer.disconnect();
  }, []);
  const rows = Math.ceil(items.length / columns);
  const virtualizer = useVirtualizer({
    count: rows,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 260,
    overscan: 3,
  });
  return (
    <div
      ref={parentRef}
      className="h-[calc(100vh-12rem)] overflow-auto"
      onScroll={(event) => {
        const node = event.currentTarget;
        if (node.scrollTop + node.clientHeight >= node.scrollHeight - 800) onEnd();
      }}
    >
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((row) => (
          <div
            key={row.key}
            className="absolute left-0 grid w-full grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-4"
            style={{ transform: `translateY(${row.start}px)` }}
          >
            {items.slice(row.index * columns, (row.index + 1) * columns).map((item) => (
              <MediaCard key={item.id} item={item} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function MediaCard({ item }: { readonly item: Item }) {
  const [preview, setPreview] = useState(false);
  const source = preview && item.sprite_url ? item.sprite_url : item.poster_url;
  return (
    <article
      className="overflow-hidden border border-border bg-surface"
      onPointerEnter={() => setPreview(true)}
      onPointerLeave={() => setPreview(false)}
    >
      <div className="relative aspect-video bg-surface-2">
        <img src={source} alt="" className="h-full w-full object-cover" loading="lazy" />
        {item.position_seconds !== null && item.progress_duration_seconds ? (
          <span
            className="absolute bottom-0 left-0 h-1 bg-ink"
            style={{
              width: `${Math.min(100, (item.position_seconds * 100) / item.progress_duration_seconds)}%`,
            }}
          />
        ) : null}
      </div>
      <div className="min-h-24 p-3">
        <h2 className="truncate text-sm font-medium text-ink">{item.title}</h2>
        <p className="truncate text-xs text-ink-muted">
          {item.studio ?? "—"}
          {item.release_date ? ` · ${item.release_date}` : ""}
        </p>
        <p className="text-xs text-ink-muted">{item.quality ?? item.resolution ?? "—"}</p>
      </div>
    </article>
  );
}
