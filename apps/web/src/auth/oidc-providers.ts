/**
 * The OIDC providers a signed-out visitor may choose to sign in with.
 *
 * `GET /api/auth/oidc/providers` is new and `@pornarr/api-client` is generated
 * from a contract that predates it (see `settings/peers/peers.ts` for the same
 * situation), so this one call is typed by hand rather than a generated file
 * being edited. It answers with an id and a name only -- never the issuer or
 * the client id, which belong to the administration screen and to nobody
 * without a session.
 */
import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { type ApiRequestError, apiFailure } from "../lib/api-error";

export interface OidcProviderSummary {
  readonly id: string;
  readonly name: string;
}

export const OIDC_PROVIDERS_KEY = ["auth", "oidc-providers"] as const;

/** The contract error body, when the response carries one. */
async function failureBody(response: Response): Promise<unknown> {
  try {
    return await response.clone().json();
  } catch {
    return null;
  }
}

export function useOidcProviders(): UseQueryResult<OidcProviderSummary[], ApiRequestError> {
  return useQuery<OidcProviderSummary[], ApiRequestError>({
    queryKey: OIDC_PROVIDERS_KEY,
    queryFn: async () => {
      const response = await fetch("/api/auth/oidc/providers", { credentials: "same-origin" });
      if (!response.ok) throw apiFailure(await failureBody(response), response);
      return (await response.json()) as OidcProviderSummary[];
    },
  });
}
