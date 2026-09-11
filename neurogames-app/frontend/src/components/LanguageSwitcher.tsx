"use client";

import { Languages } from "lucide-react";
import { LOCALE_OPTIONS, SUPPORTED_LOCALES, type Locale } from "@/i18n/locales";
import { useI18n } from "@/i18n/I18nProvider";
import styles from "./LanguageSwitcher.module.css";

export default function LanguageSwitcher() {
  const { locale, setLocale, t } = useI18n();

  return (
    <div className={styles.switcher} aria-label={t("language.label")}>
      <Languages size={16} aria-hidden="true" />
      <select
        className={styles.select}
        value={locale}
        onChange={(event) => setLocale(event.target.value as Locale)}
        aria-label={t("language.label")}
        title={t("language.label")}
      >
        {SUPPORTED_LOCALES.map((code) => (
          <option key={code} value={code}>
            {LOCALE_OPTIONS[code].nativeName}
          </option>
        ))}
      </select>
    </div>
  );
}

