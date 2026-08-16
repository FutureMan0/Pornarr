import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import en from "../../../i18n/en.json";
import { renderApp, server, signedIn, useMockApi } from "../../../test/harness";

useMockApi();

const FIRST_ID = "00000000-0000-4000-8000-000000000001";
const SECOND_ID = "00000000-0000-4000-8000-000000000002";
const FILTER_ID = "00000000-0000-4000-8000-000000000003";

function quarantinedItems() {
  return [
    {
      id: FIRST_ID,
      original_path: "/downloads/ambiguous-scene.mkv",
      quarantine_path: "/quarantine/ambiguous-scene.mkv",
      created_at: "2026-08-15T12:00:00Z",
      extracted_metadata: {
        title: "Ambiguous Scene",
        studio: "Example Studio",
        release_date: "2026-08-01",
        quality: "1080p",
        confidence: 0.4,
      },
      technical_details: {
        container: "matroska",
        resolution: "1080p",
        duration_seconds: 1452,
        preview_frames: ["/api/quarantine/frames/one.jpg"],
      },
      reasons: [
        {
          code: "low_confidence",
          detail: "metadata confidence 0.40 is below the required 0.80",
          evidence: { actual: 0.4, minimum: 0.8 },
        },
      ],
    },
    {
      id: SECOND_ID,
      original_path: "/downloads/other-scene.mkv",
      quarantine_path: "/quarantine/other-scene.mkv",
      created_at: "2026-08-14T12:00:00Z",
      extracted_metadata: { title: "Other Scene", confidence: 0.5 },
      technical_details: { container: "matroska", resolution: "2160p" },
      reasons: [
        {
          code: "low_confidence",
          detail: "metadata confidence 0.50 is below the required 0.80",
          evidence: { actual: 0.5, minimum: 0.8 },
        },
      ],
    },
    {
      id: FILTER_ID,
      original_path: "/downloads/blocked-scene.mkv",
      quarantine_path: "/quarantine/blocked-scene.mkv",
      created_at: "2026-08-13T12:00:00Z",
      extracted_metadata: { title: "Blocked Scene" },
      technical_details: { container: "matroska" },
      reasons: [
        {
          code: "filter_rule",
          detail: "filter rule example matched tag 'example'",
          evidence: { rule_id: "rule-12", tag: "example" },
        },
      ],
    },
  ];
}

beforeEach(() => {
  signedIn();
  server.use(http.get("/api/admin/quarantine", () => HttpResponse.json(quarantinedItems())));
});

afterEach(cleanup);

describe("QuarantineReviewRoute", () => {
  test("keeps the operator-only review screen out of a member session", async () => {
    server.use(
      http.get("/api/auth/me", () =>
        HttpResponse.json({ id: "u-2", username: "member", role: "user" }),
      ),
    );
    renderApp("/admin/quarantine");

    expect(await screen.findByRole("heading", { name: en.screen.forbiddenTitle })).toBeTruthy();
  });

  test("shows grouped evidence, the actual confidence and its failing threshold", async () => {
    const user = userEvent.setup();
    renderApp("/admin/quarantine");

    expect(await screen.findByRole("heading", { name: en.quarantine.title })).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: en.quarantine.reasonWithCount.low_confidence.replace("{{count}}", "2"),
      }),
    ).toBeTruthy();
    expect(screen.getByText(`${en.quarantine.confidenceActual}:`)).toBeTruthy();
    expect(screen.getByText("0.40")).toBeTruthy();
    expect(screen.getByText(`${en.quarantine.confidenceMinimum}:`)).toBeTruthy();
    expect(screen.getByText("0.80")).toBeTruthy();
    expect(screen.getByAltText(en.quarantine.previewFrame.replace("{{number}}", "1"))).toBeTruthy();
    await user.click(
      screen.getByRole("button", {
        name: en.quarantine.reasonWithCount.filter_rule.replace("{{count}}", "1"),
      }),
    );
    expect(screen.getByText("rule-12")).toBeTruthy();
  });

  test("sends inline metadata corrections when approving an item", async () => {
    const user = userEvent.setup();
    let approval: unknown;
    server.use(
      http.post(`/api/admin/quarantine/items/${FIRST_ID}/approve`, async ({ request }) => {
        approval = await request.json();
        return HttpResponse.json({
          media_id: "00000000-0000-4000-8000-000000000100",
          path: "/library/Correct Scene.mkv",
          placement_method: "hardlink",
        });
      }),
    );
    const { queryClient } = renderApp("/admin/quarantine");

    const title = await screen.findByLabelText(en.quarantine.fields.title);
    await user.clear(title);
    await user.type(title, "Correct Scene");
    await user.click(screen.getByRole("button", { name: en.quarantine.correctAndApprove }));

    await waitFor(() => expect(approval).toEqual({ title: "Correct Scene" }));
    await waitFor(() =>
      expect(
        queryClient
          .getQueryData<ReturnType<typeof quarantinedItems>>(["admin", "quarantine"])
          ?.some((item) => item.id === FIRST_ID),
      ).toBe(false),
    );
    expect(screen.queryByRole("button", { name: "Ambiguous Scene" })).toBeNull();
  });

  test("previews the exact bulk items and requires an explicit count-labelled confirmation", async () => {
    const user = userEvent.setup();
    let preview: unknown;
    let confirmation: unknown;
    server.use(
      http.post("/api/admin/quarantine/bulk/preview", async ({ request }) => {
        preview = await request.json();
        return HttpResponse.json({
          items: quarantinedItems().slice(0, 2),
          reason_code: "low_confidence",
          token: "a".repeat(64),
        });
      }),
      http.post("/api/admin/quarantine/bulk/approve", async ({ request }) => {
        confirmation = await request.json();
        return HttpResponse.json({ approved: [] });
      }),
    );
    renderApp("/admin/quarantine");

    const selectAll = await screen.findByLabelText(
      en.quarantine.selectReasonWithCount.replace("{{count}}", "2"),
    );
    fireEvent.click(selectAll);
    await user.click(
      screen.getByRole("button", {
        name: en.quarantine.previewBulkWithCount.replace("{{count}}", "2"),
      }),
    );

    await waitFor(() =>
      expect(preview).toEqual({ item_ids: [FIRST_ID, SECOND_ID], reason_code: "low_confidence" }),
    );
    const selectedItems = screen.getByRole("list", { name: en.quarantine.bulkItems });
    expect(within(selectedItems).getByText("Ambiguous Scene")).toBeTruthy();
    expect(within(selectedItems).getByText("Other Scene")).toBeTruthy();
    const confirm = screen.getByRole("button", {
      name: en.quarantine.confirmBulkWithCount.replace("{{count}}", "2"),
    });
    await user.click(confirm);
    await waitFor(() =>
      expect(confirmation).toEqual({
        item_ids: [FIRST_ID, SECOND_ID],
        reason_code: "low_confidence",
        token: "a".repeat(64),
      }),
    );
  });
});
