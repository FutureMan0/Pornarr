/**
 * MSW handlers derived from the API contract.
 *
 * These exist so the frontend can build a screen before the endpoint behind it
 * does. Handlers are derived from the committed `openapi.json` rather than
 * written by hand: a hand-written mock encodes what we believe the API does, and
 * that belief is what drifts.
 */

import { http, type HttpHandler, HttpResponse } from "msw";
import schema from "../../../openapi.json" with { type: "json" };

type OpenApiDocument = {
  paths?: Record<string, Record<string, unknown>>;
};

const HTTP_METHODS = ["get", "put", "post", "delete", "patch", "options", "head"] as const;
type HttpMethod = (typeof HTTP_METHODS)[number];

export interface MockOverride {
  method: HttpMethod;
  /** Contract path, e.g. `/api/media/{id}`. */
  path: string;
  status?: number;
  body?: unknown;
}

/** `/api/media/{id}` in OpenAPI is `/api/media/:id` in MSW. */
export function toMswPath(contractPath: string): string {
  return contractPath.replace(/\{([^}]+)\}/g, ":$1");
}

/**
 * One handler per operation in the contract.
 *
 * Unoverridden operations answer 501 with a structured error rather than an
 * empty 200. A silent empty success is indistinguishable from a real one, and a
 * screen built against it looks finished when nothing is implemented.
 */
export function handlersFromSchema(overrides: MockOverride[] = []): HttpHandler[] {
  const document = schema as OpenApiDocument;
  const paths = document.paths ?? {};
  const handlers: HttpHandler[] = [];

  for (const [contractPath, operations] of Object.entries(paths)) {
    for (const method of HTTP_METHODS) {
      if (!(method in operations)) continue;

      const override = overrides.find(
        (candidate) => candidate.method === method && candidate.path === contractPath,
      );

      const url = toMswPath(contractPath);
      const status = override?.status ?? (override ? 200 : 501);
      const body = override?.body ?? {
        code: "NOT_IMPLEMENTED",
        status: 501,
        context: { operation: `${method} ${contractPath}` },
      };

      handlers.push(http[method](url, () => HttpResponse.json(body, { status })));
    }
  }

  return handlers;
}

/** Every operation the contract declares, as `METHOD /path`. */
export function contractOperations(): string[] {
  const document = schema as OpenApiDocument;
  return Object.entries(document.paths ?? {}).flatMap(([path, operations]) =>
    HTTP_METHODS.filter((method) => method in operations).map(
      (method) => `${method.toUpperCase()} ${path}`,
    ),
  );
}
