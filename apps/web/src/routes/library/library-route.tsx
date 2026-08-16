import { useQuery } from "@tanstack/react-query";
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
};
type Page = { items: Item[]; next_offset: number | null };

export function LibraryRoute() {
  const { t } = useTranslation();
  const library = useQuery({
    queryKey: ["library"],
    queryFn: async (): Promise<Page> => {
      const response = await fetch("/api/library?limit=48&offset=0");
      if (!response.ok) throw new Error();
      return response.json() as Promise<Page>;
    },
  });
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
      {library.data.items.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("library.empty")}</p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-4">
          {library.data.items.map((item) => (
            <article key={item.id} className="overflow-hidden border border-border bg-surface">
              <div className="relative aspect-video bg-surface-2">
                <img
                  src={item.poster_url}
                  alt=""
                  className="h-full w-full object-cover"
                  loading="lazy"
                />
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
          ))}
        </div>
      )}
    </section>
  );
}
