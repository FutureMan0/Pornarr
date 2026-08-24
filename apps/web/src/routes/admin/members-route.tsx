import type { paths } from "@pornarr/api-client";
/**
 * Who has an account here, and whether they may still use it.
 *
 * The design's "Users & access" was half-built: an invitation could be issued
 * and the person who redeemed it was never seen again. `GET /api/admin/users`
 * and `PATCH /api/admin/users/{user_id}` have existed all along with no caller,
 * so an operator could not tell who had joined, could not tell an administrator
 * from a viewer, and had no way at all to stop somebody - the only remedy was a
 * row in the database.
 *
 * Suspending rather than deleting, because that is what the endpoint offers and
 * it is the right shape: ratings, comments, watch history and collections all
 * hang off a member, and removing the row would take a household's memory with
 * it. `is_active` is the door, not the record.
 */
import { Button, SkeletonRegion, SkeletonText } from "@pornarr/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { JSX } from "react";
import { useTranslation } from "react-i18next";
import { Navigate } from "react-router-dom";

import { useSession } from "../../auth/session";
import { ErrorScreen } from "../../errors/error-screen";
import { getApiClient } from "../../lib/api";
import {
  apiFailure,
  isRetryableError,
  messageForError,
  nextStepForError,
} from "../../lib/api-error";
import { usePageTitle } from "../../shell/page-title";

type Member =
  paths["/api/admin/users"]["get"]["responses"][200]["content"]["application/json"][number];

const MEMBERS_KEY = ["admin", "users"] as const;

export function MembersRoute(): JSX.Element {
  const { t } = useTranslation();
  const session = useSession();
  const cache = useQueryClient();
  usePageTitle(t("members.title"));

  const members = useQuery({
    queryKey: MEMBERS_KEY,
    enabled: session.data?.role === "admin",
    queryFn: async (): Promise<Member[]> => {
      const { data, error, response } = await getApiClient().GET("/api/admin/users");
      if (!data || error) throw apiFailure(error, response);
      return data;
    },
  });

  const setActive = useMutation({
    mutationFn: async ({ id, isActive }: { id: string; isActive: boolean }) => {
      const { error, response } = await getApiClient().PATCH("/api/admin/users/{user_id}", {
        params: { path: { user_id: id } },
        body: { is_active: isActive },
      });
      if (error) throw apiFailure(error, response);
    },
    onSuccess: () => void cache.invalidateQueries({ queryKey: MEMBERS_KEY }),
  });

  if (session.data?.role !== "admin" && session.data !== undefined)
    return <Navigate to="/forbidden" replace />;
  if (members.isError) {
    return (
      <ErrorScreen
        title={messageForError(members.error)}
        nextStep={nextStepForError(members.error)}
        onRetry={
          isRetryableError(members.error)
            ? () => void cache.invalidateQueries({ queryKey: MEMBERS_KEY })
            : undefined
        }
      />
    );
  }

  const people = members.data ?? [];

  return (
    // The heading is the top bar's, declared with `usePageTitle` above: the
    // design puts an administration screen's name there, and a second one here
    // would announce it twice to anyone navigating by heading.
    <section className="flex flex-col gap-6" aria-label={t("members.title")}>
      <header className="max-w-[70ch]">
        <p className="text-sm text-ink-muted">{t("members.intro")}</p>
      </header>

      {setActive.isError ? (
        <ErrorScreen
          title={messageForError(setActive.error)}
          nextStep={nextStepForError(setActive.error)}
        />
      ) : null}

      {members.isPending ? (
        <SkeletonRegion label={t("members.loading")}>
          <SkeletonText lines={3} />
        </SkeletonRegion>
      ) : (
        <ul className="flex flex-col gap-3">
          {people.map((member) => (
            <li
              key={member.id}
              className="flex flex-wrap items-center justify-between gap-3 border border-border bg-surface p-4"
            >
              <div className="min-w-0">
                <h2 className="text-md text-ink">{member.display_name ?? member.username}</h2>
                <p className="text-sm text-ink-muted">
                  {member.username}
                  {" · "}
                  {t(`members.roles.${member.role}` as "members.roles.admin", {
                    defaultValue: member.role,
                  })}
                  {" · "}
                  {member.is_active ? t("members.active") : t("members.suspended")}
                </p>
              </div>
              {/* An administrator cannot lock themselves out from here. The
                  server refuses it too; saying so before the click is kinder
                  than an error afterwards. */}
              {member.id === session.data?.id ? (
                <p className="text-2xs text-ink-faint">{t("members.thatIsYou")}</p>
              ) : (
                <Button
                  variant={member.is_active ? "ghost" : "secondary"}
                  loading={setActive.isPending && setActive.variables?.id === member.id}
                  aria-label={
                    member.is_active
                      ? t("members.suspendMember", { name: member.username })
                      : t("members.restoreMember", { name: member.username })
                  }
                  onClick={() => setActive.mutate({ id: member.id, isActive: !member.is_active })}
                >
                  {member.is_active ? t("members.suspend") : t("members.restore")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
