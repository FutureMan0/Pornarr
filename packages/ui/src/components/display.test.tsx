/**
 * The display components' accessibility guarantees, enforced rather than
 * assumed: a status is readable without colour, a skeleton is silent to
 * assistive technology, and an empty state always offers a way out.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { BADGE_STATUSES, Badge } from "./badge";
import { EmptyState } from "./empty-state";
import { SkeletonPoster, SkeletonRegion, SkeletonText } from "./skeleton";

// Vitest runs without globals here, so React Testing Library's automatic
// cleanup never registers. Without this, renders pile up in one document.
afterEach(cleanup);

describe("Badge", () => {
  test.each(BADGE_STATUSES)("%s renders a visible text label, not colour alone", (status) => {
    const { container } = render(<Badge status={status} />);
    const badge = container.firstElementChild;

    expect(badge).not.toBeNull();
    // The tint is decoration; the text is the state. Strip the class and the
    // badge must still say what it means.
    expect(badge?.textContent?.trim().length ?? 0).toBeGreaterThan(0);
    expect(screen.getByText(/\S/)).toBe(badge);
  });

  test("the label names the status rather than repeating the token", () => {
    render(<Badge status="quarantined" />);
    expect(screen.getByText("Quarantined")).toBeDefined();
  });

  test("the status selects its own class, so the tint is verifiable", () => {
    const { container } = render(<Badge status="failed" />);
    // classNameStrategy: "non-scoped" — a CSS Module class is its authored name.
    expect(container.firstElementChild?.className.split(" ")).toContain("failed");
  });

  test("every status in the vocabulary has a distinct label", () => {
    const labels = BADGE_STATUSES.map((status) => {
      const { container } = render(<Badge status={status} />);
      const text = container.firstElementChild?.textContent ?? "";
      cleanup();
      return text;
    });
    expect(new Set(labels).size).toBe(BADGE_STATUSES.length);
  });
});

describe("Skeleton", () => {
  test("a text skeleton is hidden from assistive technology", () => {
    const { container } = render(<SkeletonText lines={4} />);
    const skeleton = container.firstElementChild;

    expect(skeleton?.getAttribute("aria-hidden")).toBe("true");
    expect(skeleton?.childElementCount).toBe(4);
  });

  test("a poster skeleton is hidden from assistive technology", () => {
    const { container } = render(<SkeletonPoster />);
    expect(container.firstElementChild?.getAttribute("aria-hidden")).toBe("true");
    // It holds the 16:9 slot the library grid reserves, so it renders a box and
    // announces nothing at all.
    expect(container.textContent).toBe("");
  });

  test("the region announces the load, the shimmer stays silent", () => {
    render(
      <SkeletonRegion label="Loading library">
        <SkeletonPoster />
        <SkeletonText />
      </SkeletonRegion>,
    );

    const region = screen.getByRole("status");
    expect(region.getAttribute("aria-busy")).toBe("true");
    expect(within(region).getByText("Loading library")).toBeDefined();

    // Exactly one thing is announced: the label. Both skeletons are hidden.
    const hidden = region.querySelectorAll('[aria-hidden="true"]');
    expect(hidden.length).toBe(2);
  });
});

describe("EmptyState", () => {
  const render_ = () =>
    render(
      <EmptyState
        title="No movies yet"
        body="Pornarr scans root folders for existing files. Add one and the library fills itself."
        action={{ label: "Add a root folder", href: "/settings/media-management" }}
      />,
    );

  test("teaches the interface: title, explanation and a way forward", () => {
    render_();
    expect(screen.getByRole("heading", { name: "No movies yet" })).toBeDefined();
    expect(screen.getByText(/Add one and the library fills itself/)).toBeDefined();
  });

  test("renders the required action as a real link", () => {
    render_();
    const action = screen.getByRole("link", { name: "Add a root folder" });
    expect(action.getAttribute("href")).toBe("/settings/media-management");
  });

  test("the action is reachable by keyboard", () => {
    render_();
    const action = screen.getByRole("link", { name: "Add a root folder" });

    // Nothing has pulled it out of the tab order.
    expect(action.getAttribute("tabindex")).toBeNull();
    action.focus();
    expect(document.activeElement).toBe(action);
  });
});
