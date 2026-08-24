import { expect, test } from "vitest";
import { PACKAGE_ROLE } from "./index";

test("the design system package resolves", () => {
  expect(PACKAGE_ROLE).toBe("ui");
});
