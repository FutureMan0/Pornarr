/**
 * Peers: the other Pornarr servers this one borrows a library from.
 *
 * The federation endpoints are new and `@pornarr/api-client` is generated from
 * a contract that predates them, so these four calls are typed here by hand
 * rather than the generated file being edited — a generated file that someone
 * has written into stops being generated. When the contract is regenerated the
 * shapes below become the compiler's problem, which is the point of naming them
 * in one module instead of at four call sites.
 *
 * Two screens read the list: the settings section that edits it, and the
 * library's source picker. Sharing the query rather than the endpoint is what
 * keeps the two from disagreeing about which servers exist.
 *
 * The endpoints are administrator-only, so the query is disabled for everyone
 * else rather than fired and left to fail with a 403 each caller would have to
 * interpret. `undefined` therefore means "not asked", never "no peers".
 */
import type { UseQueryResult } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { useSession } from "../../../auth/session";
import { CSRF_HEADER, readCsrfToken } from "../../../lib/api";
import { type ApiRequestError, apiFailure } from "../../../lib/api-error";

export interface Peer {
  readonly id: string;
  readonly name: string;
  readonly base_url: string;
  readonly enabled: boolean;
  /** What the last test found. An open vocabulary — the server owns it. */
  readonly health: string;
  /** Why, when the health is not good. Diagnostic detail, not prose. */
  readonly health_reason: string | null;
  readonly last_tested_at: string | null;
  readonly media_count: number;
}

/** The key is write-only: it is sent once and never comes back. */
export interface PeerWrite {
  readonly name: string;
  readonly base_url: string;
  readonly api_key: string;
  readonly enabled: boolean;
}

export const PEERS_KEY = ["admin", "peers"] as const;

const PEERS_PATH = "/api/admin/peers";

function csrfHeaders(): HeadersInit {
  const token = readCsrfToken();
  return token === null ? {} : { [CSRF_HEADER]: token };
}

/** The contract error body, when the response carries one. */
async function failureBody(response: Response): Promise<unknown> {
  try {
    return await response.clone().json();
  } catch {
    return null;
  }
}

async function peerRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  if (!response.ok) throw apiFailure(await failureBody(response), response);
  // 204 carries nothing, and callers that expect nothing must not await a parse
  // that would throw on an empty body.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function listPeers(): Promise<Peer[]> {
  return peerRequest<Peer[]>(PEERS_PATH);
}

export function createPeer(body: PeerWrite): Promise<Peer> {
  return peerRequest<Peer>(PEERS_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(body),
  });
}

export function testPeer(peerId: string): Promise<Peer> {
  return peerRequest<Peer>(`${PEERS_PATH}/${peerId}/test`, {
    method: "POST",
    headers: csrfHeaders(),
  });
}

export function deletePeer(peerId: string): Promise<void> {
  return peerRequest<void>(`${PEERS_PATH}/${peerId}`, {
    method: "DELETE",
    headers: csrfHeaders(),
  });
}

/**
 * `enabled` is the caller's own condition, and'ed with the role check. The
 * library screen only needs the names once the reader has left their own
 * library, and a list of other people's servers fetched on every library load
 * is a request nobody asked for.
 */
export function usePeers(enabled = true): UseQueryResult<Peer[], ApiRequestError> {
  const session = useSession();

  return useQuery<Peer[], ApiRequestError>({
    queryKey: PEERS_KEY,
    enabled: enabled && session.data?.role === "admin",
    queryFn: listPeers,
  });
}
