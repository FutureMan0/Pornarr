/**
 * Design system. Tokens and components live here; nothing in `apps/web` may
 * hardcode a colour, spacing, radius or duration value.
 *
 * The stylesheets are imported once, at the application entry point, because
 * they define global custom properties rather than component-scoped rules:
 *
 *   import "@pornarr/ui/tokens.css";
 *   import "@pornarr/ui/utilities.css";
 *
 * DESIGN.md holds the token set and the component state requirements.
 * docs/adr/0036-design-system-foundation.md records why these components sit on
 * native elements and why the library carries no Tailwind of its own.
 */
export const PACKAGE_ROLE = "ui" as const;

export { cx } from "./lib/cx";
export {
  applyStoredTheme,
  DEFAULT_THEME,
  isTheme,
  setTheme,
  storedTheme,
  type Theme,
  THEMES,
} from "./theme";

export { Badge, BADGE_STATUSES, type BadgeProps, type BadgeStatus } from "./components/badge";
export { Button, type ButtonProps } from "./components/button";
export { Checkbox, type CheckboxProps } from "./components/checkbox";
export { Dialog, type DialogProps } from "./components/dialog";
export {
  EmptyState,
  type EmptyStateAction,
  type EmptyStateProps,
} from "./components/empty-state";
export { Input, type InputProps } from "./components/input";
export { Menu, type MenuItem, type MenuProps } from "./components/menu";
export { Select, type SelectProps } from "./components/select";
export {
  SkeletonPoster,
  type SkeletonPosterProps,
  SkeletonRegion,
  type SkeletonRegionProps,
  SkeletonText,
  type SkeletonTextProps,
} from "./components/skeleton";
export { Tooltip, type TooltipProps } from "./components/tooltip";
