/**
 * Typed API client, generated from `openapi.json`.
 *
 * The contract is the boundary between backend and frontend work. It is
 * generated rather than written because a hand-maintained client drifts from the
 * server and nobody notices until runtime — CI compares the committed document
 * against a fresh export on every pull request.
 *
 * Regenerate with `make openapi`. Never edit `schema.gen.ts`.
 */

import createOpenApiClient, { type Client } from "openapi-fetch";
import type { paths } from "./schema.gen";

export type { paths } from "./schema.gen";
export type ApiClient = Client<paths>;

export interface ApiClientOptions {
  /** Where the API lives. Empty string means same origin, which is the
   * production case: FastAPI serves both the API and the frontend. */
  baseUrl?: string;
}

/**
 * Errors arrive as objects with a stable machine-readable code, never as prose.
 * The frontend maps codes to translated messages; a server-supplied English
 * string cannot be translated. See docs/api-contract.md.
 */
export interface ApiError {
  code: string;
  status: number;
  context: Record<string, unknown>;
}

export function isApiError(value: unknown): value is ApiError {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.code === "string" && typeof candidate.status === "number";
}

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  return createOpenApiClient<paths>({
    baseUrl: options.baseUrl ?? "",
    // Session cookies are HttpOnly, so the browser has to be told to send them.
    credentials: "same-origin",
  });
}
