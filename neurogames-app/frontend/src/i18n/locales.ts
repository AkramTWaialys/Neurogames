export const SUPPORTED_LOCALES = ["fr", "en", "ar"] as const;

export type Locale = (typeof SUPPORTED_LOCALES)[number];
export type Direction = "ltr" | "rtl";

export const DEFAULT_LOCALE: Locale = "fr";
export const LOCALE_STORAGE_KEY = "neuro_locale";

export const LOCALE_OPTIONS: Record<Locale, {
  nativeName: string;
  englishName: string;
  dir: Direction;
}> = {
  fr: { nativeName: "Français", englishName: "French", dir: "ltr" },
  en: { nativeName: "English", englishName: "English", dir: "ltr" },
  ar: { nativeName: "العربية", englishName: "Arabic", dir: "ltr" },
};

export function isLocale(value: string | null | undefined): value is Locale {
  return !!value && SUPPORTED_LOCALES.includes(value as Locale);
}

export function normalizeLocale(value: string | null | undefined): Locale | null {
  if (!value) return null;
  const short = value.toLowerCase().split("-")[0];
  return isLocale(short) ? short : null;
}

export function getLocaleDir(locale: Locale): Direction {
  return LOCALE_OPTIONS[locale].dir;
}
