/**
 * Errors, translated once.
 *
 * docs/api-contract.md: the API answers with `{code, status, context}` and the
 * code is the stable part. The frontend never renders a server-supplied English
 * string — a string cannot be translated, and a string is not a contract. This
 * module is the only place a code becomes a sentence.
 */
import { isApiError } from "@pornarr/api-client";

/**
 * Every code the API is documented to produce today. Unknown codes are not an
 * error here — they fall back to a generic sentence, because a new backend code
 * must never be able to blank a screen.
 */
export const ERROR_MESSAGES: Readonly<Record<string, string>> = {
  INVALID_CREDENTIALS: "That username and password do not match.",
  NOT_AUTHENTICATED: "Your session has ended. Sign in again.",
  FORBIDDEN: "Your account is not allowed to do that.",
  CSRF_FAILED: "This request could not be verified. Reload the page and try again.",
  LOGIN_RATE_LIMITED: "Too many sign-in attempts. Wait a minute and try again.",
  NOT_IMPLEMENTED: "That is not built yet.",
  WEB_ASSETS_MISSING: "The interface files are missing from this installation.",
};

export const FALLBACK_ERROR_MESSAGE = "Something went wrong. Try again.";

/** Code used when a request fails without the API answering at all. */
export const NETWORK_ERROR_CODE = "NETWORK_UNREACHABLE";

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

export function messageForErrorCode(code: string): string {
  return ERROR_MESSAGES[code] ?? FALLBACK_ERROR_MESSAGE;
}

/** The sentence to show a user for any thrown value at all. */
export function messageForError(value: unknown): string {
  const code = errorCodeOf(value);
  return code === null ? FALLBACK_ERROR_MESSAGE : messageForErrorCode(code);
}
