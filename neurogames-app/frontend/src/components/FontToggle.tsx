"use client";

import { useState, useEffect } from "react";
import { Type } from "lucide-react";
import styles from "./FontToggle.module.css";

type FontMode = "standard" | "adhd" | "dyslexia";

const FONT_CLASSES: Record<FontMode, string> = {
  standard: "",
  adhd:     "font-adhd",
  dyslexia: "font-dyslexia",
};

const LABELS: Record<FontMode, string> = {
  standard: "Std",
  adhd:     "TDAH",
  dyslexia: "Dys",
};

const TITLES: Record<FontMode, string> = {
  standard: "Police standard (Nunito)",
  adhd:     "Police TDAH — Readex Pro (espacement renforcé)",
  dyslexia: "Police dyslexie — Lexend (fluidité de lecture)",
};

const ALL_FONT_CLASSES = ["font-adhd", "font-dyslexia", "adhd-font"];

export default function FontToggle() {
  const [mode, setMode] = useState<FontMode>("standard");

  useEffect(() => {
    // Migrate old key neuro_adhd_font → neuro_font_mode
    if (localStorage.getItem("neuro_adhd_font") === "1") {
      localStorage.setItem("neuro_font_mode", "adhd");
      localStorage.removeItem("neuro_adhd_font");
    }
    const saved = (localStorage.getItem("neuro_font_mode") || "standard") as FontMode;
    if (saved !== "standard" && FONT_CLASSES[saved]) {
      setMode(saved);
      document.documentElement.classList.add(FONT_CLASSES[saved]);
    }
  }, []);

  const pick = (next: FontMode) => {
    ALL_FONT_CLASSES.forEach(cls => document.documentElement.classList.remove(cls));
    if (next !== "standard") {
      document.documentElement.classList.add(FONT_CLASSES[next]);
    }
    setMode(next);
    localStorage.setItem("neuro_font_mode", next);
  };

  return (
    <div
      className={styles.wrapper}
      role="radiogroup"
      aria-label="Choix de la police de lecture"
    >
      <span className={styles.icon} aria-hidden="true"><Type size={14} /></span>
      {(["standard", "adhd", "dyslexia"] as FontMode[]).map(m => (
        <button
          key={m}
          className={`${styles.option} ${mode === m ? styles.active : ""}`}
          onClick={() => pick(m)}
          role="radio"
          aria-checked={mode === m}
          title={TITLES[m]}
        >
          {LABELS[m]}
        </button>
      ))}
    </div>
  );
}
