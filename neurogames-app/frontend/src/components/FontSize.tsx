"use client";

import { useState, useEffect } from "react";
import { useI18n } from "@/i18n/I18nProvider";
import styles from "./FontSize.module.css";

const STORAGE_KEY = "neuro_font_size";

type SizeKey = "compact" | "normal" | "large";

const SIZES: { key: SizeKey; labelKey: string; css: string }[] = [
  { key: "compact", labelKey: "accessibility.sizeCompact", css: styles.sizeCompact },
  { key: "normal",  labelKey: "accessibility.sizeNormal",  css: styles.sizeNormal },
  { key: "large",   labelKey: "accessibility.sizeLarge",    css: styles.sizeLarge },
];

const CLASS_MAP: Record<SizeKey, string> = {
  compact: "font-size-compact",
  normal:  "",                   // default — no class needed
  large:   "font-size-large",
};

export default function FontSize() {
  const { t } = useI18n();
  const [size, setSize] = useState<SizeKey>("normal");

  // Restore preference after hydration
  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY) as SizeKey | null;
    if (saved && saved !== "normal") {
      setSize(saved);
      document.documentElement.classList.add(CLASS_MAP[saved]);
    }
  }, []);

  const pick = (next: SizeKey) => {
    // Remove previous class
    const prev = CLASS_MAP[size];
    if (prev) document.documentElement.classList.remove(prev);

    // Add new class
    const cls = CLASS_MAP[next];
    if (cls) document.documentElement.classList.add(cls);

    setSize(next);
    localStorage.setItem(STORAGE_KEY, next);
  };

  return (
    <div
      className={styles.wrapper}
      role="radiogroup"
      aria-label={t("accessibility.fontSize")}
    >
      {SIZES.map(({ key, labelKey, css }) => (
        <button
          key={key}
          className={`${styles.option} ${css} ${size === key ? styles.active : ""}`}
          onClick={() => pick(key)}
          role="radio"
          aria-checked={size === key}
          title={t(labelKey)}
        >
          A
        </button>
      ))}
    </div>
  );
}
