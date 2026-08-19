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
 * Completeness is enforced, not remembered: `api-error.test.ts` reads every
 * `code = "..."` out of the Python sources and fails when one of them is missing
 * from this list, and `i18n.test.tsx` fails when a listed code has no sentence or
 * no next step in either locale. A backend code cannot reach a screen untranslated
 * without a red test naming it.
 *
 * The fallback below still exists, because a code from a newer server must never
 * be able to blank a screen. It is no longer silent: reaching it writes a console
 * error naming the code, so an untranslated code is visible in a browser session
 * as well as in CI.
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
  "QUALITY_CONFIGURATION_INVALID",
  "QUALITY_PROFILE_IN_USE",
  "ROOT_FOLDER_INVALID",
  "ROOT_FOLDER_HAS_MEDIA",
  "SETUP_REQUIRED",
  "SETUP_ALREADY_COMPLETED",
  "SETUP_PATH_INVALID",
  "SETUP_PASSWORD_TOO_WEAK",
  "ROOT_FOLDER_DISABLED",
  "OIDC_AUTHENTICATION_FAILED",
  "OIDC_STATE_INVALID",
  "OIDC_IDENTITY_NOT_ALLOWED",
  "OIDC_IDENTITY_ALREADY_LINKED",
  "OIDC_USERNAME_CONFLICT",
  "OIDC_UNLINK_WOULD_LOCK_ACCOUNT",
  "USER_CANNOT_DEACTIVATE_SELF",
  "INDEXER_CONNECTION_FAILED",
  "DOWNLOAD_CLIENT_CONNECTION_FAILED",
  "DOWNLOAD_CLIENT_UNAVAILABLE",
  "TORZNAB_RESPONSE_INVALID",
  "STASHDB_RESPONSE_INVALID",
  "TPDB_RESPONSE_INVALID",
  "PEER_UNAVAILABLE",
  "REQUEST_QUOTA_EXCEEDED",
  "REQUEST_ACTION_INVALID",
  "REQUEST_NOT_GRABBABLE",
  "REQUEST_CANCELLATION_FAILED",
  "REQUEST_CONTROL_FAILED",
  "REQUEST_PRIORITY_UPDATE_FAILED",
  "RELEASE_NOT_FOUND",
  "RELEASE_EXPIRED",
  "RELEASE_BLOCKED",
  "RELEASE_IN_LIBRARY",
  "RELEASE_FILTERED",
  "RELEASE_PROTOCOL_UNSUPPORTED",
  "GRAB_SUBMISSION_FAILED",
  "MONITOR_QUALITY_PROFILE_REQUIRED",
  "MONITOR_ALREADY_EXISTS",
  "QUARANTINE_REVIEW_INVALID",
  "FILTER_CONFIGURATION_INVALID",
  "COLLECTION_ALREADY_EXISTS",
  "SEND_ALREADY_EXISTS",
  "SEND_TO_SELF",
  "SHORT_ALREADY_EXISTS",
  "SHORTS_NO_MARKERS",
  "TRANSCODE_LIMIT_REACHED",
  "RANGE_NOT_SATISFIABLE",

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

/**
 * The codes whose next step is "do the same thing again".
 *
 * Membership is a claim about the *server*, not about the user: a transport
 * failure, a server that has not finished starting, a limit that expires. Every
 * other code needs the request to change first — a different password, a
 * corrected field, a smaller file — and a retry button beside one of those is an
 * invitation to press the same wall twice.
 *
 * `CSRF_FAILED` is deliberately absent even though it is transient: its next
 * step is a reload, which a retry of the same request does not perform.
 */
export const RETRYABLE_ERROR_CODES = [
  "LOGIN_RATE_LIMITED",
  "OIDC_DISCOVERY_FAILED",
  "RATE_LIMITED",
  "INTERNAL_ERROR",
  "SERVICE_UNAVAILABLE",
  "NETWORK_UNREACHABLE",
  // Something downstream of the API — a download client, a peer, an indexer or a
  // metadata provider — did not answer. The request is well formed and nothing
  // about it would change on a second attempt, which is exactly the case a retry
  // is for.
  "REQUEST_CANCELLATION_FAILED",
  "REQUEST_CONTROL_FAILED",
  "REQUEST_PRIORITY_UPDATE_FAILED",
  "GRAB_SUBMISSION_FAILED",
  "PEER_UNAVAILABLE",
  "TORZNAB_RESPONSE_INVALID",
  "STASHDB_RESPONSE_INVALID",
  "TPDB_RESPONSE_INVALID",
  // A limit that expires on its own, like the two rate limits above.
  "TRANSCODE_LIMIT_REACHED",
] as const satisfies readonly ErrorCode[];

const RETRYABLE: ReadonlySet<string> = new Set(RETRYABLE_ERROR_CODES);

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

/** Codes already reported, so one broken endpoint cannot flood the console. */
const reportedUnknownCodes = new Set<string>();

/**
 * Say out loud that a code reached a screen with no sentence behind it.
 *
 * PRODUCT.md: "Errors state the cause and the next step, never just that something
 * failed." The fallback breaks that promise by construction, so it is treated as a
 * defect in this build rather than as an outcome — once per code, because the
 * point is to be noticed, not to be shouted.
 */
function reportUntranslatedCode(code: string): void {
  if (reportedUnknownCodes.has(code)) return;
  reportedUnknownCodes.add(code);
  console.error(
    `[pornarr] no translated message for error code ${code}; add it to ERROR_CODES, errors and errorSteps`,
  );
}

/**
 * The sentence for a code.
 *
 * A code this build has never heard of still produces a sentence, and that
 * sentence carries the code: an operator reading a screenshot needs the token to
 * search for, and a blank alert is the one outcome that helps nobody. Reaching
 * that fallback is a defect, so it reports itself on the way past.
 */
export function messageForErrorCode(code: string): string {
  if (isKnownErrorCode(code)) return i18n.t(`errors.${code}`);
  reportUntranslatedCode(code);
  return i18n.t("errors.unknown", { code });
}

/** The sentence to show a user for any thrown value at all. */
export function messageForError(value: unknown): string {
  const code = errorCodeOf(value);
  return code === null ? i18n.t("errors.generic") : messageForErrorCode(code);
}

/**
 * What to do about it.
 *
 * DESIGN.md: "an error states the cause and the next step". `messageForErrorCode`
 * is the cause; this is the other half, and it is a separate string rather than a
 * longer message because the two are rendered differently — the cause is the
 * heading a reader scans, the step is the sentence they act on — and because a
 * translator needs to move them independently.
 *
 * Every code in `ERROR_CODES` has one, enforced by the walk in `i18n.test.tsx`,
 * so a new backend code cannot be added with a cause and no way out. The unknown
 * branch reports itself for the same reason `messageForErrorCode` does.
 */
export function nextStepForErrorCode(code: string): string {
  if (isKnownErrorCode(code)) return i18n.t(`errorSteps.${code}`);
  reportUntranslatedCode(code);
  return i18n.t("errorSteps.unknown");
}

/** The next step for any thrown value at all. */
export function nextStepForError(value: unknown): string {
  const code = errorCodeOf(value);
  return code === null ? i18n.t("errorSteps.generic") : nextStepForErrorCode(code);
}

export function isRetryableErrorCode(code: string): boolean {
  return RETRYABLE.has(code);
}

/**
 * Whether to offer a retry for a thrown value.
 *
 * A code this build has never heard of falls back to the same rule
 * `query-client.ts` retries by: a 4xx is an answer and repeating it changes
 * nothing, anything else might still succeed. One rule, so a button that appears
 * and an automatic retry that fires cannot disagree about what is worth redoing.
 */
export function isRetryableError(value: unknown): boolean {
  const code = errorCodeOf(value);
  if (code !== null && isKnownErrorCode(code)) return isRetryableErrorCode(code);
  return !isClientError(value);
}
