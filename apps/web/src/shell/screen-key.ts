/**
 * A value that changes when the reader arrives somewhere new, and not otherwise.
 *
 * Used as a `key` on the wrapper around the routed screen, which is what lets the
 * screen animate in: `@starting-style` only fires on insertion, so the wrapper
 * has to be a new element each time.
 *
 * WHY NOT `location.pathname`. Because a changed address is not the same thing as
 * a new screen. The shorts feed *writes* the address as it scrolls, so that it
 * can be linked to — keyed on the pathname, every scroll would remount the feed,
 * restart the video and throw the reader back up the list. It would have looked
 * like a scroll bug rather than an animation decision.
 *
 * A `replace` is the distinction the router already draws: it means "same screen,
 * corrected address". A push or a back button means somebody went somewhere. So
 * the key follows pushes and pops and ignores replaces, which is both narrower
 * and more honest than a list of paths to exclude.
 */
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

export function useScreenKey(): string {
  const location = useLocation();
  const navigationType = useNavigationType();
  const [key, setKey] = useState(location.pathname);
  // Read in an effect rather than during render: two navigations can land in one
  // commit, and a ref is what keeps the comparison against what was last *shown*.
  const shown = useRef(key);

  useEffect(() => {
    if (navigationType === "REPLACE") return;
    if (shown.current === location.pathname) return;
    shown.current = location.pathname;
    setKey(location.pathname);
  }, [location.pathname, navigationType]);

  return key;
}
