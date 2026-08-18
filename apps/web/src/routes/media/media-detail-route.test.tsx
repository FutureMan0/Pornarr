import { cleanup, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test } from "vitest";
import { renderApp, server, signedIn, useMockApi } from "../../test/harness";

useMockApi();

afterEach(cleanup);

const MEDIA_ID = "00000000-0000-4000-8000-000000000000";

test("a title that does not exist says so and offers somewhere to go", async () => {
  signedIn();
  server.use(
    http.get(`/api/media/${MEDIA_ID}`, () =>
      HttpResponse.json({ code: "NOT_FOUND", status: 404, context: {} }, { status: 404 }),
    ),
  );

  renderApp(`/library/${MEDIA_ID}`);

  await screen.findByRole("heading", { name: "Not found" });
  expect(screen.getByRole("link", { name: "Go to the library" }).getAttribute("href")).toBe(
    "/library",
  );
});

test("a failed request states the cause and the next step, not a bare apology", async () => {
  signedIn();
  server.use(
    http.get(`/api/media/${MEDIA_ID}`, () =>
      HttpResponse.json({ code: "BAD_REQUEST", status: 400, context: {} }, { status: 400 }),
    ),
  );

  renderApp(`/library/${MEDIA_ID}`);

  // The point of the screen: a cause, then something to do about it.
  await screen.findByRole("heading", {
    name: "That request was not something the server could use.",
  });
  expect(screen.queryByText("Something went wrong. Try again.")).toBeNull();
});
