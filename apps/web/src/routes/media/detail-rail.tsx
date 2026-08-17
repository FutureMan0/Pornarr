/**
 * The right-hand column of B3: who is in it, what it is, and what the household
 * made of it.
 *
 * A RAIL, NOT A STACK. These four are reference — you glance at them while the
 * thing plays. Putting them under the player, as this screen did before, meant
 * scrolling past the comments to reach the file's resolution, and meant the
 * related row sat a full screen below the title it relates to.
 *
 * PERFORMERS LINK INTO SEARCH. There is no performer endpoint, so there is no
 * page to send anyone to and no "41 titles" to print. What does exist is a
 * search that finds their work, which is what a reader clicking a name wants
 * anyway.
 */
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { useFormat } from "../../i18n/format";

export interface DetailRailProps {
  readonly performers: readonly string[];
  readonly studio: string | null;
  readonly releaseDate: string | null;
  readonly durationSeconds: number | null;
  readonly resolution: string | null;
  readonly sizeBytes: number;
  readonly addedAt: string;
  readonly path: string;
  readonly inMyLibrary: boolean;
}

export function DetailRail({
  performers,
  studio,
  releaseDate,
  durationSeconds,
  resolution,
  sizeBytes,
  addedAt,
  path,
  inMyLibrary,
}: DetailRailProps): JSX.Element {
  const { t } = useTranslation();
  const format = useFormat();

  return (
    <div className="flex flex-col gap-4">
      {performers.length === 0 ? null : (
        <section aria-labelledby="performers-heading" className="card">
          <h2 id="performers-heading" className="text-sm text-ink">
            {t("media.performers")}
          </h2>
          <ul className="flex flex-col gap-1">
            {performers.map((name) => (
              <li key={name}>
                <Link
                  to={`/search?q=${encodeURIComponent(name)}`}
                  className="flex items-center gap-2 rounded-lg px-1 py-1.5 text-sm text-ink transition-colors hover:bg-surface-3"
                >
                  <span
                    aria-hidden="true"
                    className="grid size-7 flex-none place-items-center rounded-full bg-surface-3 text-2xs text-ink-muted"
                  >
                    {name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{name}</span>
                  <span aria-hidden="true" className="text-ink-faint">
                    ›
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="about-heading" className="card">
        <h2 id="about-heading" className="text-sm text-ink">
          {t("media.about")}
        </h2>
        <dl className="flex flex-col gap-1.5 text-sm">
          <Fact label={t("media.studio")} value={studio ?? "—"} />
          <Fact
            label={t("media.released")}
            value={releaseDate === null ? "—" : format.date(new Date(releaseDate))}
          />
          <Fact
            label={t("media.runtime")}
            value={durationSeconds === null ? "—" : format.duration(durationSeconds)}
          />
          <Fact label={t("media.quality")} value={resolution ?? "—"} />
          <Fact label={t("media.size")} value={format.bytes(sizeBytes)} />
          <Fact label={t("media.added")} value={format.relativeDate(new Date(addedAt))} />
        </dl>

        {/* The path is administration, not description. Shown to whoever the
            title belongs to and to nobody else, because where a household keeps
            its files is not a guest's business. */}
        {inMyLibrary ? (
          <p className="break-all pt-1 font-mono text-2xs text-ink-faint">{path}</p>
        ) : (
          <p className="pt-1 text-2xs text-ink-faint">{t("media.pathHidden")}</p>
        )}
      </section>
    </div>
  );
}

function Fact({ label, value }: { readonly label: string; readonly value: string }): JSX.Element {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="m-0 min-w-0 truncate text-right text-ink">{value}</dd>
    </div>
  );
}
