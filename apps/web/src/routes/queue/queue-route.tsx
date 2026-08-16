import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { usePageTitle } from "../../shell/page-title";

type Job = {
  id: string;
  client_name: string;
  status: string;
  priority: number;
  download_speed_bytes: number | null;
  error: string | null;
  queue_estimate: { low_seconds: number | null; high_seconds: number | null; confidence: string };
};
export function QueueRoute() {
  const { t } = useTranslation();
  usePageTitle(t("queue.title"));
  const queue = useQuery({
    queryKey: ["queue"],
    refetchInterval: 5_000,
    queryFn: async (): Promise<{ items: Job[] }> => {
      const r = await fetch("/api/queue");
      if (!r.ok) throw new Error();
      return r.json() as Promise<{ items: Job[] }>;
    },
  });
  if (queue.isPending) return <p>{t("queue.loading")}</p>;
  if (queue.isError) return <p role="alert">{t("errors.generic")}</p>;
  return (
    <section aria-label={t("queue.title")}>
      <ul className="mt-4 grid gap-3">
        {queue.data.items.map((job) => (
          <li key={job.id} className="border border-border p-4">
            <strong>{job.client_name}</strong>
            <p>
              {job.status} · {t("queue.priority", { value: job.priority })}
            </p>
            <p>
              {job.download_speed_bytes === null
                ? t("queue.speedUnknown")
                : t("queue.speed", { value: job.download_speed_bytes })}
            </p>
            <p>
              {job.queue_estimate.low_seconds === null || job.queue_estimate.high_seconds === null
                ? t("queue.estimateUnknown")
                : t("queue.estimate", {
                    low: job.queue_estimate.low_seconds,
                    high: job.queue_estimate.high_seconds,
                    confidence: job.queue_estimate.confidence,
                  })}
            </p>
            {job.error ? <p role="alert">{t("queue.failure", { error: job.error })}</p> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
