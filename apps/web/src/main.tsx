/**
 * Entry point.
 *
 * The three stylesheets are imported here and nowhere else, in this order:
 * tokens define the custom properties, utilities depend on them, and
 * `styles.css` bridges the same properties into Tailwind. Importing them from a
 * component would make their arrival depend on which screen rendered first.
 */
import "@pornarr/ui/tokens.css";
import "@pornarr/ui/utilities.css";
import "./styles.css";

import { applyAccentFavicon, applyStoredTheme } from "@pornarr/ui";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app";

// Ahead of React on purpose. The accent lives on an attribute of <html>, and
// setting it from a component would render one paint in the default accent and
// then visibly swap.
applyStoredTheme();

// After the attribute, never before: the icon is drawn from the accent's own
// logo filter, which is only readable once the theme is on the element.
applyAccentFavicon();

const container = document.getElementById("root");
if (container === null) {
  throw new Error("index.html is missing #root");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
