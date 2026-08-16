/**
 * The query cache and its defaults.
 *
 * Two decisions are worth naming. First, a 4xx is an answer and not a fault, so
 * retrying it only delays the message the user needs — retries apply to 5xx and
 * transport failures alone. Second, every failure is surfaced through one
 * reporter rather than being swallowed per call site: a query that fails
 * silently is the single most expensive kind of bug in a long-lived SPA.
 */
import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { isClientError, messageForError } from "./api-error";

/** Receives the translated sentence for a failure, never the raw error. */
export type ErrorReporter = (message: string, error: unknown) => void;

const MAX_RETRIES = 2;

/**
 * The default reporter. Deliberately not a toast: there is no toast component
 * yet, and inventing one here would put a second design system beside the one
 * in packages/ui. Screens surface their own errors inline; this is the net that
 * catches what nothing is watching.
 */
const reportToConsole: ErrorReporter = (message, error) => {
  console.error(`[pornarr] ${message}`, error);
};

export function createQueryClient(report: ErrorReporter = reportToConsole): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => report(messageForError(error), error),
    }),
    mutationCache: new MutationCache({
      onError: (error) => report(messageForError(error), error),
    }),
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => failureCount < MAX_RETRIES && !isClientError(error),
        // Server state arrives over SSE, so polling and focus refetching are
        // both the wrong instrument. Events invalidate; the cache stays put.
        refetchOnWindowFocus: false,
        staleTime: 30_000,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
