/**
 * Errors, translated once.
 *
 * docs/api-contract.md: the API answers with `{code, status, context}` and the
 * code is the stable part. The frontend never renders a server-supplied English
 * string — a string cannot be translated, and a string is not a contract. This
 * module is the only place a code becomes a sentence, and the sentence comes out
 * of the locale files.
 *
 * `i18n.t` is used directly rather than the React hook because failures are also
 * reported from `query-client.ts`, which is not a component. The instance is a
 * module singleton, so both paths get the same locale.
 */
import { isApiError } from "@pornarr/api-client";
import i18n from "../i18n";

/**
 * Every code the API is documented to produce, in one list so that a test can
 * walk it rather than a person remembering to.
 *
 * The first group is raised deliberately (`pornarr_shared.errors.PornarrError`
 * subclasses across `apps/api/pornarr_api`); the second is derived from the
 * status by `apps/api/pornarr_api/errors.py` for conditions Starlette raises
 * before our own code sees the request; the last is this client's own, for a
 * request that never got an answer in the contract's shape.
 *
 * Unknown codes are not an error here — a new backend code must never be able to
 * blank a screen, so anything absent falls through to a generic sentence that
 * still shows the code.
 */
export const ERROR_CODES = [
  "INVALID_CREDENTIALS",
  "NOT_AUTHENTICATED",
  "FORBIDDEN",
  "CSRF_FAILED",
  "LOGIN_RATE_LIMITED",
  "OIDC_DISCOVERY_FAILED",
  "WEB_ASSETS_MISSING",
  "NOT_IMPLEMENTED",
  "CONFIGURATION_INVALID",
  "DECRYPTION_FAILED",

  "BAD_REQUEST",
  "NOT_FOUND",
  "METHOD_NOT_ALLOWED",
  "CONFLICT",
  "PAYLOAD_TOO_LARGE",
  "VALIDATION_FAILED",
  "RATE_LIMITED",
  "INTERNAL_ERROR",
  "SERVICE_UNAVAILABLE",

  "NETWORK_UNREACHABLE",
] as const;

export type ErrorCode = (typeof ERROR_CODES)[number];

/** Code used when a request fails without the API answering at all. */
export const NETWORK_ERROR_CODE = "NETWORK_UNREACHABLE" satisfies ErrorCode;

export function isKnownErrorCode(code: string): code is ErrorCode {
  return (ERROR_CODES as readonly string[]).includes(code);
}

/**
 * A failed request, as an `Error` so that TanStack Query, `throw` sites and the
 * console all agree on what they are holding.
 */
export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;
  readonly context: Record<string, unknown>;

  constructor(code: string, status: number, context: Record<string, unknown> = {}) {
    // The message is for logs and stack traces only. What the user sees comes
    // from messageForError, so that it stays a code-to-sentence mapping.
    super(`${code} (${status})`);
    this.name = "ApiRequestError";
    this.code = code;
    this.status = status;
    this.context = context;
  }
}

/** Turn an openapi-fetch failure into something typed and throwable. */
export function apiFailure(error: unknown, response: Response): ApiRequestError {
  if (isApiError(error)) {
    return new ApiRequestError(error.code, error.status, error.context);
  }
  // A body that is not the contract shape still has a status worth keeping.
  return new ApiRequestError(NETWORK_ERROR_CODE, response.status, {});
}

export function errorCodeOf(value: unknown): string | null {
  if (value instanceof ApiRequestError) return value.code;
  if (isApiError(value)) return value.code;
  return null;
}

export function isClientError(value: unknown): boolean {
  const status = value instanceof ApiRequestError ? value.status : null;
  return status !== null && status >= 400 && status < 500;
}

/**
 * The sentence for a code.
 *
 * A code this build has never heard of still produces a sentence, and that
 * sentence carries the code: an operator reading a screenshot needs the token to
 * search for, and a blank alert is the one outcome that helps nobody.
 */
export function messageForErrorCode(code: string): string {
  if (isKnownErrorCode(code)) return i18n.t(`errors.${code}`);
  return i18n.t("errors.unknown", { code });
}

/** The sentence to show a user for any thrown value at all. */
export function messageForError(value: unknown): string {
  const code = errorCodeOf(value);
  return code === null ? i18n.t("errors.generic") : messageForErrorCode(code);
}
