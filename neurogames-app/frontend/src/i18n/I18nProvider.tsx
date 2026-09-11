"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { DEFAULT_LOCALE, getLocaleDir, LOCALE_STORAGE_KEY, normalizeLocale, type Direction, type Locale } from "./locales";
import { MESSAGES, type TranslationParams } from "./messages";

type I18nContextValue = {
  locale: Locale;
  dir: Direction;
  setLocale: (locale: Locale) => void;
  t: (key: string, params?: TranslationParams) => string;
  formatNumber: (value: number, options?: Intl.NumberFormatOptions) => string;
  formatDate: (value: string | number | Date, options?: Intl.DateTimeFormatOptions) => string;
  formatDuration: (seconds: number) => string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

function interpolate(template: string, params?: TranslationParams): string {
  if (!params) return template;
  return template.replace(/\{\{(\w+)\}\}/g, (_, key: string) => {
    const value = params[key];
    return value == null ? "" : String(value);
  });
}

function localeToIntl(locale: Locale): string {
  if (locale === "ar") return "ar-TN";
  if (locale === "en") return "en-US";
  return "fr-FR";
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  // Always start with DEFAULT_LOCALE so SSR and first client render match
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);

  // After hydration, sync the real locale from storage/browser
  useEffect(() => {
    const stored = normalizeLocale(localStorage.getItem(LOCALE_STORAGE_KEY));
    const browser = normalizeLocale(navigator.language);
    const resolved = stored || browser || DEFAULT_LOCALE;
    if (resolved !== DEFAULT_LOCALE) {
      setLocaleState(resolved);
    }
  }, []);

  useEffect(() => {
    const dir = getLocaleDir(locale);
    document.documentElement.lang = locale;
    document.documentElement.dir = dir;
    document.documentElement.dataset.locale = locale;
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    document.cookie = `${LOCALE_STORAGE_KEY}=${locale}; path=/; max-age=31536000; SameSite=Lax`;
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
  }, []);

  const t = useCallback((key: string, params?: TranslationParams) => {
    const template = MESSAGES[locale][key] ?? MESSAGES[DEFAULT_LOCALE][key] ?? key;
    return interpolate(template, params);
  }, [locale]);

  const formatNumber = useCallback((value: number, options?: Intl.NumberFormatOptions) => {
    return new Intl.NumberFormat(localeToIntl(locale), options).format(value);
  }, [locale]);

  const formatDate = useCallback((value: string | number | Date, options?: Intl.DateTimeFormatOptions) => {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat(localeToIntl(locale), options).format(date);
  }, [locale]);

  const formatDuration = useCallback((seconds: number) => {
    if (seconds <= 0) return "—";
    if (seconds >= 60) {
      const minutes = Math.floor(seconds / 60);
      const rest = Math.round(seconds % 60);
      return locale === "ar" ? `${minutes}د ${rest}ث` : `${minutes}m ${rest}s`;
    }
    return locale === "ar" ? `${Math.round(seconds)}ث` : `${Math.round(seconds)}s`;
  }, [locale]);

  const value = useMemo<I18nContextValue>(() => ({
    locale,
    dir: getLocaleDir(locale),
    setLocale,
    t,
    formatNumber,
    formatDate,
    formatDuration,
  }), [formatDate, formatDuration, formatNumber, locale, setLocale, t]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return context;
}
