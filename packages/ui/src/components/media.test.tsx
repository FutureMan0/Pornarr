/**
 * The grid atoms: placeholder artwork, the rating, and the tile that carries
 * both.
 *
 * The interesting assertions are the ones about what a screen reader gets and
 * what it does not. A rating drawn as five glyphs is decoration; the number is
 * the content, and only one of the two belongs in the accessibility tree.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { Artwork, hueOffset } from "./artwork";
import { MediaTile } from "./media-tile";
import { MAXIMUM_STARS, Stars } from "./stars";

afterEach(cleanup);

describe("placeholder artwork", () => {
  test("the same title is always the same colour", () => {
    // A library is browsed repeatedly. A tile that changes hue between loads is
    // worse than a grey box, because the eye learns it and is then misled.
    expect(hueOffset(4213)).toBe(hueOffset(4213));
  });

  test("colours stay inside a band around the theme's hue", () => {
    // Free hashing spreads tiles across the whole wheel and turns a page into a
    // rainbow arguing with the accent.
    const offsets = Array.from({ length: 500 }, (_, seed) => hueOffset(seed * 7919));

    expect(Math.min(...offsets)).toBeGreaterThanOrEqual(-50);
    expect(Math.max(...offsets)).toBeLessThanOrEqual(50);
  });

  test("the band is actually used, not collapsed to one value", () => {
    const offsets = new Set(Array.from({ length: 200 }, (_, seed) => hueOffset(seed * 31)));

    expect(offsets.size).toBeGreaterThan(20);
  });

  test("a negative or absurd seed still lands in the band", () => {
    for (const seed of [-1, -99999, 1e9, 0]) {
      expect(Math.abs(hueOffset(seed))).toBeLessThanOrEqual(50);
    }
  });

  test("the artwork itself is decoration and stays out of the reading order", () => {
    const { container } = render(<Artwork seed={1} veil={<span>hidden</span>} />);

    // Everything inside is aria-hidden; the tile supplies the name.
    expect(container.querySelectorAll("[aria-hidden='true']").length).toBeGreaterThan(0);
    expect(screen.queryByText("hidden")).not.toBeNull();
  });
});

describe("a rating", () => {
  test("reads as one sentence, not as five glyphs", () => {
    render(<Stars value={4.5} label="4.5 out of 5, from 12 ratings" />);

    const rating = screen.getByRole("img", { name: "4.5 out of 5, from 12 ratings" });

    // The glyphs are decoration. A reader hearing "star star star" learns
    // nothing it could not get from the number.
    expect(within(rating).queryAllByRole("img")).toHaveLength(0);
  });

  test("a half is drawn as half, not rounded away", () => {
    const { container } = render(<Stars value={2.5} label="2.5 out of 5" />);

    const fill = container.querySelector("[style*='--stars-fill']") as HTMLElement | null;

    expect(fill?.style.getPropertyValue("--stars-fill")).toBe("50%");
  });

  test("unrated is empty rather than zero stars pretending to be a score", () => {
    const { container } = render(<Stars value={null} label="Not rated" />);

    const fill = container.querySelector("[style*='--stars-fill']") as HTMLElement | null;

    expect(fill?.style.getPropertyValue("--stars-fill")).toBe("0%");
    expect(screen.getByRole("img", { name: "Not rated" })).not.toBeNull();
  });

  test("a value beyond the scale is clamped rather than overflowing the row", () => {
    const { container } = render(<Stars value={9} label="Broken" />);

    const fill = container.querySelector("[style*='--stars-fill']") as HTMLElement | null;

    expect(fill?.style.getPropertyValue("--stars-fill")).toBe("100%");
    expect(MAXIMUM_STARS).toBe(5);
  });
});

describe("a media tile", () => {
  const base = {
    title: "Aurora 214",
    seed: 42,
    ratingLabel: "4.5 out of 5",
  } as const;

  test("carries the title, the badges and the rating", () => {
    render(
      <MediaTile
        {...base}
        meta="Aurora Studios"
        duration="24:05"
        resolution="2160p"
        rating={4.5}
        tagCount={9}
        commentCount={12}
      />,
    );

    expect(screen.getByText("Aurora 214")).not.toBeNull();
    expect(screen.getByText("2160p")).not.toBeNull();
    expect(screen.getByText("24:05")).not.toBeNull();
    expect(screen.getByText("4.5")).not.toBeNull();
    expect(screen.getByText("9")).not.toBeNull();
    expect(screen.getByText("12")).not.toBeNull();
  });

  test("a counter at zero is absent rather than a zero to read and discard", () => {
    render(<MediaTile {...base} tagCount={0} commentCount={0} />);

    expect(screen.queryByText("0")).toBeNull();
  });

  test("an unrated title shows a dash, which is not the same as zero stars", () => {
    render(<MediaTile {...base} rating={null} />);

    expect(screen.getByText("—")).not.toBeNull();
  });

  test("a tile with no rating label leaves the rating off, dash included", () => {
    // Distinct from `rating={null}` above. Null says nobody has rated it; no
    // label says the row is not about ratings — the continue-watching row, whose
    // endpoint returns none. Five grey stars and a dash per tile would be an
    // answer to a question nobody asked.
    const { title, seed } = base;
    render(<MediaTile title={title} seed={seed} duration="24:05" />);

    expect(screen.getByText("24:05")).not.toBeNull();
    expect(screen.queryByText("—")).toBeNull();
    expect(screen.queryByRole("img", { name: /out of 5/ })).toBeNull();
  });

  test("a comment count survives a tile with no rating", () => {
    const { title, seed } = base;
    render(<MediaTile title={title} seed={seed} commentCount={3} />);

    expect(screen.getByText("3")).not.toBeNull();
  });

  test("the resume bar appears only once there is something to resume", () => {
    const { container: none } = render(<MediaTile {...base} progress={0} />);
    expect(none.querySelector("[style*='width']")).toBeNull();

    cleanup();
    const { container: part } = render(<MediaTile {...base} progress={0.42} />);
    expect((part.querySelector("[style*='width']") as HTMLElement).style.width).toBe("42%");
  });

  test("a progress value outside the range cannot overflow the bar", () => {
    const { container } = render(<MediaTile {...base} progress={4} />);

    expect((container.querySelector("[style*='width']") as HTMLElement).style.width).toBe("100%");
  });

  test("the whole tile can become one link without nesting interactive elements", () => {
    render(<MediaTile {...base} action={(content) => <a href="/library/1">{content}</a>} />);

    const link = screen.getByRole("link");

    expect(within(link).getByText("Aurora 214")).not.toBeNull();
    expect(within(link).queryAllByRole("button")).toHaveLength(0);
  });
});
