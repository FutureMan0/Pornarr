/**
 * What screen you are on, said once.
 *
 * The design puts the screen's name and its count in the top bar rather than at
 * the head of the content. That is a layout decision with an accessibility
 * consequence: the heading has to move with it, or the page is left with no
 * level-one heading and a decorative line of text where the heading used to be.
 *
 * So the top bar renders the real `<h1>`, and a screen declares its title by
 * calling `usePageTitle`. The alternative — every screen keeping its own
 * heading and the top bar duplicating it — would announce the name of the
 * screen twice to anyone navigating by heading.
 *
 * A CONTEXT, NOT A STORE. Unlike artwork visibility this is per-render state
 * owned by whichever screen is mounted, and it must be cleared when that screen
 * unmounts. Context plus an effect gives exactly that lifecycle; a module-level
 * store would leave the previous screen's title on the bar for a frame after
 * navigation, which is the one thing a page title must never do.
 */
import type { JSX, ReactNode } from "react";
import { createContext, useContext, useLayoutEffect, useMemo, useState } from "react";

export interface PageTitle {
  readonly title: string;
  /** The count line beneath it, already formatted. Omitted when unknown. */
  readonly subtitle?: string | undefined;
}

interface TitleChannel {
  readonly current: PageTitle | null;
  readonly publish: (title: PageTitle | null) => void;
}

const TitleContext = createContext<TitleChannel | null>(null);

export function PageTitleProvider({ children }: { readonly children: ReactNode }): JSX.Element {
  const [current, setCurrent] = useState<PageTitle | null>(null);
  const value = useMemo<TitleChannel>(() => ({ current, publish: setCurrent }), [current]);

  return <TitleContext.Provider value={value}>{children}</TitleContext.Provider>;
}

/** What the top bar renders. Null before any screen has said anything. */
export function usePublishedTitle(): PageTitle | null {
  return useContext(TitleContext)?.current ?? null;
}

/**
 * Declare this screen's title. Safe outside a provider — the tests that render
 * a single route in isolation have no shell, and a screen should not care.
 */
export function usePageTitle(title: string, subtitle?: string | undefined): void {
  const channel = useContext(TitleContext);
  const publish = channel?.publish;

  // A layout effect, not a passive one: the screen and its name have to reach
  // the same paint. With `useEffect` the bar renders empty for one frame after
  // every navigation, and a heading that arrives late is a heading that was
  // briefly missing.
  useLayoutEffect(() => {
    if (publish === undefined) return;
    publish({ title, subtitle });
    // Cleared on unmount so a slow screen cannot inherit the last one's name.
    return () => publish(null);
  }, [publish, title, subtitle]);
}
