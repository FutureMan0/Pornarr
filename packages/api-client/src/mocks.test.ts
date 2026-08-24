import { describe, expect, test } from "vitest";
import { contractOperations, handlersFromSchema, toMswPath } from "./mocks";

describe("contract path translation", () => {
  test.each([
    ["/api/media", "/api/media"],
    ["/api/media/{id}", "/api/media/:id"],
    ["/api/media/{id}/files/{fileId}", "/api/media/:id/files/:fileId"],
  ])("%s becomes %s", (contract, expected) => {
    expect(toMswPath(contract)).toBe(expected);
  });
});

describe("handlers derived from the contract", () => {
  test("one handler per declared operation", () => {
    // The contract currently declares no operations; endpoints arrive with
    // their own issues. This asserts the derivation agrees with the document
    // rather than asserting a fixed number, so it keeps working as routes land.
    expect(handlersFromSchema()).toHaveLength(contractOperations().length);
  });

  test("derivation is stable across calls", () => {
    expect(handlersFromSchema().length).toBe(handlersFromSchema().length);
  });

  test("an override for an operation the contract does not declare adds nothing", () => {
    // Otherwise a stale override silently mocks an endpoint that no longer
    // exists, and the screen built on it keeps working until production.
    const handlers = handlersFromSchema([
      { method: "get", path: "/api/gone", status: 200, body: {} },
    ]);

    expect(handlers).toHaveLength(contractOperations().length);
  });
});
