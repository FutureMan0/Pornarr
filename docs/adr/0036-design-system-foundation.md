# 0036 — Tokens in CSS, components on native elements

Status: accepted (2026-08-08)

## Context
`packages/ui` had to acquire a styling mechanism and a component base before `apps/web` could be built against it, but 0022 assigns Vite, React and Tailwind to the application. Building the library on the application's Tailwind would have inverted that dependency.

## Decision
The library owns `tokens.css` and one CSS Module per component, and takes React as a peer dependency. It has no other runtime dependency and no build step. Tailwind stays in the application, mapped onto the same custom properties, and is used for layout rather than for component internals.

Components are built on the native element wherever one exists: `button`, `input`, `select`, `input[type=checkbox]` and `dialog`. Only the menu and the tooltip, which have no native equivalent, implement their ARIA pattern by hand. No unstyled component library is taken as a dependency.

## Consequences
DESIGN.md already bans reinventing a standard affordance, so this follows from it rather than adding to it; the native `dialog` element in particular supplies focus trapping, Escape and an inert background that a hand-rolled trap would only approximate. The cost is that the menu and tooltip carry keyboard logic the project now maintains itself.

Contrast is verified against the shipped token values rather than asserted in prose, which is what surfaced the gap that `--border-control` fills.
