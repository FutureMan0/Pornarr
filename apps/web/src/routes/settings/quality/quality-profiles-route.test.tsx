import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";
import { renderApp, server, signedIn, useMockApi } from "../../../test/harness";

useMockApi();

const definitions = [
  quality("q-720", "WEB 720p", "720p", 10),
  quality("q-1080", "WEB 1080p", "1080p", 20),
  quality("q-2160", "WEB 2160p", "2160p", 30),
];

function quality(id: string, name: string, resolution: string, weight: number) {
  return {
    id,
    name,
    resolution,
    source: "web",
    weight,
    minimum_size_mb_per_minute: 5,
    maximum_size_mb_per_minute: 35,
  };
}

afterEach(cleanup);

test("edits a quality profile with keyboard-accessible ordering and previews unsaved scores", async () => {
  signedIn();
  const saveProfile = vi.fn();
  const preview = vi.fn();
  server.use(
    http.get("/api/admin/quality/definitions", () => HttpResponse.json(definitions)),
    http.get("/api/admin/quality/profiles", () =>
      HttpResponse.json([
        {
          id: "profile-1",
          name: "Default profile",
          cutoff_quality_id: "q-2160",
          minimum_custom_format_score: 0,
          is_default: true,
          qualities: definitions,
        },
      ]),
    ),
    http.get("/api/admin/quality/custom-formats", () =>
      HttpResponse.json([
        {
          id: "format-1",
          name: "Prefer HEVC",
          score: 5,
          conditions: [
            {
              id: "condition-1",
              field: "codec",
              operator: "equals",
              value: "hevc",
              required: true,
              negate: false,
            },
          ],
        },
      ]),
    ),
    http.put("/api/admin/quality/profiles/profile-1", async ({ request }) => {
      saveProfile(await request.json());
      return HttpResponse.json({
        id: "profile-1",
        name: "Default profile",
        cutoff_quality_id: "q-2160",
        minimum_custom_format_score: 0,
        is_default: true,
        qualities: [definitions[1], definitions[0], definitions[2]],
      });
    }),
    http.post("/api/admin/quality/preview", async ({ request }) => {
      preview(await request.json());
      return HttpResponse.json({
        quality: definitions[1],
        score: 29,
        verdict: "grab",
        reason: "no_existing_file",
        matched_custom_formats: [{ name: "Prefer HEVC", score: 9 }],
        fields: { resolution: "1080p", source: "web-dl", codec: "hevc", flags: ["proper"] },
      });
    }),
  );
  const user = userEvent.setup();

  renderApp("/settings/quality");

  await screen.findByRole("heading", { name: "Quality profiles", level: 1 });
  await user.click(screen.getByRole("button", { name: "Move WEB 1080p up" }));
  await user.click(screen.getByRole("button", { name: "Save profile" }));
  await waitFor(() => expect(saveProfile).toHaveBeenCalledOnce());
  expect(saveProfile.mock.calls[0]?.[0]).toMatchObject({
    quality_definition_ids: ["q-1080", "q-720", "q-2160"],
  });

  const score = screen.getByLabelText("Score for Prefer HEVC");
  await user.clear(score);
  await user.type(score, "9");
  await user.type(screen.getByLabelText("Release name"), "Example.Scene.1080p.WEB-DL.x265.PROPER");
  await user.click(screen.getByRole("button", { name: "Preview decision" }));

  await screen.findByText("Grab this release");
  expect(
    screen.getByText("No existing file is available, so this release can be grabbed."),
  ).not.toBeNull();
  expect(preview.mock.calls[0]?.[0]).toMatchObject({
    custom_formats: [{ name: "Prefer HEVC", score: 9 }],
  });
});

test("new profile empties the editor instead of snapping back to the first profile", async () => {
  signedIn();
  server.use(
    http.get("/api/admin/quality/definitions", () => HttpResponse.json(definitions)),
    http.get("/api/admin/quality/profiles", () =>
      HttpResponse.json([
        {
          id: "profile-1",
          name: "Default profile",
          cutoff_quality_id: "q-2160",
          minimum_custom_format_score: 0,
          is_default: true,
          qualities: definitions,
        },
      ]),
    ),
    http.get("/api/admin/quality/custom-formats", () => HttpResponse.json([])),
  );

  renderApp("/settings/quality");
  const user = userEvent.setup();

  const name = await screen.findByLabelText("Profile name");
  await waitFor(() => expect((name as HTMLInputElement).value).toBe("Default profile"));

  await user.click(screen.getByRole("button", { name: "New profile" }));

  await waitFor(() =>
    expect((screen.getByLabelText("Profile name") as HTMLInputElement).value).toBe(""),
  );
});
