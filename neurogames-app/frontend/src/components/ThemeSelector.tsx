"use client";

import { useState, useEffect, useRef } from "react";
import { Palette } from "lucide-react";
import styles from "./ThemeSelector.module.css";

type Theme = "standard" | "ocean" | "forest" | "sunset";

const THEMES: { key: Theme; label: string; primary: string; bg: string }[] = [
  { key: "standard", label: "Violet",  primary: "#6D28D9", bg: "#EEE9FF" },
  { key: "ocean",    label: "Océan",   primary: "#0277BD", bg: "#DFF4F8" },
  { key: "forest",   label: "Forêt",   primary: "#2E7D32", bg: "#E3F5E5" },
  { key: "sunset",   label: "Soleil",  primary: "#D84B00", bg: "#FFF5E8" },
];

const THEME_CLASSES = ["theme-ocean", "theme-forest", "theme-sunset"];

function applyTheme(theme: Theme) {
  THEME_CLASSES.forEach(c => document.documentElement.classList.remove(c));
  if (theme !== "standard") {
    document.documentElement.classList.add(`theme-${theme}`);
  }
}

export default function ThemeSelector() {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>("standard");
  const [highContrast, setHighContrast] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const savedTheme   = (localStorage.getItem("neuro_theme") || "standard") as Theme;
    const savedHC      = localStorage.getItem("neuro_high_contrast") === "1";
    const savedRM      = localStorage.getItem("neuro_reduce_motion") === "1";

    setTheme(savedTheme);
    applyTheme(savedTheme);

    if (savedHC) {
      setHighContrast(true);
      document.documentElement.classList.add("high-contrast");
    }
    if (savedRM) {
      setReduceMotion(true);
      document.documentElement.classList.add("reduce-motion");
    }
  }, []);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    if (open) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const pickTheme = (t: Theme) => {
    setTheme(t);
    applyTheme(t);
    localStorage.setItem("neuro_theme", t);
  };

  const toggleHighContrast = () => {
    const next = !highContrast;
    setHighContrast(next);
    document.documentElement.classList.toggle("high-contrast", next);
    localStorage.setItem("neuro_high_contrast", next ? "1" : "0");
  };

  const toggleReduceMotion = () => {
    const next = !reduceMotion;
    setReduceMotion(next);
    document.documentElement.classList.toggle("reduce-motion", next);
    localStorage.setItem("neuro_reduce_motion", next ? "1" : "0");
  };

  return (
    <div ref={containerRef} className={styles.container}>
      <button
        className={`${styles.trigger} ${open ? styles.triggerOpen : ""}`}
        onClick={() => setOpen(prev => !prev)}
        aria-label="Personnaliser l'apparence"
        aria-expanded={open}
        title="Thème & Accessibilité"
      >
        <Palette size={16} aria-hidden="true" />
        <span className={styles.triggerLabel}>Thème</span>
      </button>

      {open && (
        <div className={styles.panel} role="dialog" aria-label="Personnalisation de l'apparence">
          <div className={styles.section}>
            <span className={styles.sectionTitle}>Thème de couleur</span>
            <div className={styles.themeGrid}>
              {THEMES.map(th => (
                <button
                  key={th.key}
                  className={`${styles.themeBtn} ${theme === th.key ? styles.themeActive : ""}`}
                  onClick={() => pickTheme(th.key)}
                  aria-pressed={theme === th.key}
                  title={th.label}
                >
                  <span
                    className={styles.swatch}
                    style={{
                      background: th.bg,
                      borderColor: th.primary,
                    }}
                  >
                    <span
                      className={styles.swatchDot}
                      style={{ background: th.primary }}
                    />
                  </span>
                  <span className={styles.themeLabel}>{th.label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className={styles.section}>
            <span className={styles.sectionTitle}>Accessibilité</span>

            <button
              className={`${styles.toggleBtn} ${highContrast ? styles.toggleActive : ""}`}
              onClick={toggleHighContrast}
              aria-pressed={highContrast}
            >
              <span className={styles.toggleIcon} aria-hidden="true">◑</span>
              <span>Contraste élevé</span>
              {highContrast && <span className={styles.checkmark} aria-hidden="true">✓</span>}
            </button>

            <button
              className={`${styles.toggleBtn} ${reduceMotion ? styles.toggleActive : ""}`}
              onClick={toggleReduceMotion}
              aria-pressed={reduceMotion}
            >
              <span className={styles.toggleIcon} aria-hidden="true">⏸</span>
              <span>Moins d&apos;animations</span>
              {reduceMotion && <span className={styles.checkmark} aria-hidden="true">✓</span>}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
