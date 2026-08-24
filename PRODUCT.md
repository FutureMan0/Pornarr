# Product

## Register

product

## Platform

web

## Users

The primary user is the person who runs the instance: a self-hoster with existing
*arr experience who already operates Sonarr, Radarr, Prowlarr or Jellyfin on their
own hardware. They arrive fluent in the vocabulary — quality profile, indexer,
queue, grab, hardlink — and expect it to be used precisely rather than explained
away. Their context is concrete and shapes the interface: evening, a dark room,
the screen as the only significant light source, most often a desktop but
regularly a tablet on the couch.

The secondary user shares that instance without sharing that expertise. They want
to search, request something that is missing, and watch what is already there.
They should never need to understand what an indexer is to complete any of those
three tasks, and they should never be shown an administrative control they cannot
act on.

## Product Purpose

Pornarr manages a self-hosted adult media library end to end: it searches the local
library and external indexers, hands selected releases to a download client,
imports and categorizes the result, and plays it back. It exists because the
existing tools each solve one slice and force the user to operate three of them
side by side.

Success is that the core loop — search, recognize the right release, request it —
succeeds on first attempt without documentation, in under thirty seconds. That
target is a design constraint, not a marketing figure: it means a result row must
be scannable at a glance rather than legible only after effort, and it means
quality, size, age, score and estimated time must be comparable across rows
without arithmetic.

## Positioning

Pornarr is multi-user from the ground up. The *arr applications are single-operator
tools with one administrator; Pornarr gives every user their own profile, filters,
requests and recommendations, while the administrator retains full visibility and
control over the instance. Every screen reinforces that claim: what a user sees is
theirs, what an administrator sees is everyone's.

## Brand Personality

Precise, calm, competent. The interface withdraws behind the task. Nothing blinks,
nothing advertises, nothing surprises. Trust is earned through accuracy: exact
figures, honest estimates, unambiguous states.

In practice this means numbers are set in tabular figures and aligned right so
columns compare without effort. Time estimates are shown as a range with a
confidence level, because a single exact figure claims a precision the system does
not have. There are no celebratory notifications and no confetti. Errors state the
cause and the next step, never just that something failed.

## Anti-references

Not a Bootstrap administration theme. The generic management layout that Sonarr and
Radarr carry — grey panels with title bars, default blue buttons, tables without
row hierarchy, icon-font buttons with no label — is functional but visibly from
another era. Pornarr does not inherit it.

Not SaaS landing-page aesthetics. No gradient surfaces, no glass cards, no large
metric tiles with gradient numerals, no marketing language inside the product.

## Design Principles

**The image is the colour.** The surface is neutral so that posters, thumbnails and
video frames are the only saturated elements on screen. Chrome that competes with
content is chrome that is wrong.

**Density is a service, not a style.** These users manage hundreds of items and
compare releases side by side. Show the data they need to decide, in a form that
compares, rather than hiding it behind progressive disclosure to look calmer.

**State is never a question.** What is running, what failed, what is waiting for
review must be answerable without navigating. A user should never have to ask
whether something is still in progress.

**Honest over confident.** Where the system is guessing — an estimate, a metadata
match, a duplicate candidate — the interface says so and shows its confidence.
Fabricated precision is worse than an admitted range.

**Two audiences, one system.** The operator surface and the member surface share
components, spacing and vocabulary. They differ in what they expose, never in how
they behave.

## Accessibility & Inclusion

WCAG 2.2 AA throughout, verified rather than assumed. Body text at 4.5:1 minimum,
large text at 3:1, placeholder text held to the body requirement. Every function
reachable by keyboard, with a visible 2px focus ring at 3:1 against its
surroundings. `prefers-reduced-motion` is honoured by every animation, with a
crossfade or instant transition as the alternative.

The video player is the hardest surface and is treated as its own accessibility
scope: keyboard transport controls, ARIA labelling on all controls, and subtitle
support.

axe-core runs inside the Playwright suite on `develop`.
