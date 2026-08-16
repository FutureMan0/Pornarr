/**
 * Translation, wired once.
 *
 * `en.json` is the source: every string in this application is authored there
 * and nowhere else, and `de.json` is the first translation of it. The module
 * augmentation at the bottom points i18next's key types at the English resource,
 * which is the whole reason a library is here rather than a hand-rolled record —
 * a key that does not exist, or that exists with a typo, is a compile error
 * instead of a runtime fallback that renders the key itself onto the screen.
 *
 * Resources are bundled rather than fetched. The SPA is served from the API
 * container with no CDN in front of it, so a second network round trip to
 * discover the words on the login screen would buy nothing and cost a flash of
 * untranslated interface.
 *
 * The locale files live here rather than in a root `locales/` directory: they
 * are a web-only concern and a root-level directory would land outside the
 * frontend's ownership in CODEOWNERS.
 */
import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";
import de from "./de.json";
import en from "./en.json";

/** The locales this build ships. `en` is first because it is the source. */
export const LOCALES = ["en", "de"] as const;

export type Locale = (typeof LOCALES)[number];

/**
 * Where the user's explicit choice is kept. Namespaced because the SPA can be
 * hosted on an origin it does not own alone (a reverse proxy sub-path).
 */
export const LOCALE_STORAGE_KEY = "pornarr.locale";

export function isLocale(value: string): value is Locale {
  return (LOCALES as readonly string[]).includes(value);
}

/**
 * German, checked against English at compile time. A missing key here is a type
 * error; the test suite covers the other direction (a key nobody uses).
 */
const deResource: typeof en = de;

if (!i18n.isInitialized) {
  void i18n
    .use(LanguageDetector)
    .use(initReactI18next)
    .init({
      resources: {
        en: { translation: en },
        de: { translation: deResource },
      },
      fallbackLng: "en",
      supportedLngs: [...LOCALES],
      // `de-AT` and `de-CH` are German. Without this a regional browser setting
      // silently falls back to English, which is the most common i18n bug there
      // is and the least visible one.
      load: "languageOnly",
      nonExplicitSupportedLngs: true,
      detection: {
        // The stored override wins over the browser. Detection is a default,
        // not a decision: once a user has chosen, the browser stops voting.
        order: ["localStorage", "navigator"],
        caches: ["localStorage"],
        lookupLocalStorage: LOCALE_STORAGE_KEY,
      },
      interpolation: {
        // React escapes text on render. Escaping again turns an apostrophe in a
        // German sentence into `&#39;` on screen.
        escapeValue: false,
      },
      react: {
        // Resources are already in memory, so there is nothing to suspend on.
        useSuspense: false,
      },
    });
}

/** The locale currently rendering, always one of `LOCALES`. */
export function currentLocale(): Locale {
  const resolved = i18n.resolvedLanguage ?? i18n.language;
  return resolved !== undefined && isLocale(resolved) ? resolved : "en";
}

/**
 * The user override. Persisting is the detector's job — it caches to
 * localStorage on `languageChanged` — so this stays a single call rather than a
 * write that could disagree with what detection reads back.
 */
export function setLocale(locale: Locale): Promise<unknown> {
  return i18n.changeLanguage(locale);
}

export default i18n;

/**
 * The point of the whole exercise: `t("nav.libary")` does not compile.
 *
 * Declared against the English resource, so English is the shape every other
 * locale is measured against and every call site is checked against.
 */
declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "translation";
    resources: { translation: typeof en };
  }
}
