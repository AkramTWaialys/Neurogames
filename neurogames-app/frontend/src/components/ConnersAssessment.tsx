"use client";

import React, { useState, useEffect } from "react";
import { useI18n } from "@/i18n/I18nProvider";
import styles from "./ConnersAssessment.module.css";

interface ConnersAssessmentProps {
  onScoreChange: (score: number, data: Record<number, number>) => void;
}

const QUESTIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
const VALUES = [0, 1, 2, 3];
const LEGEND_EMOJIS = ["⚪", "🟡", "🟠", "🔴"];

export default function ConnersAssessment({ onScoreChange }: ConnersAssessmentProps) {
  const { t } = useI18n();
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const [legendOpen, setLegendOpen] = useState(true);

  const handleSelect = (q: number, val: number) => {
    const newAnswers = { ...answers, [q]: val };
    setAnswers(newAnswers);
  };

  useEffect(() => {
    const score = Object.values(answers).reduce((sum, val) => sum + val, 0);
    onScoreChange(score, answers);
  }, [answers, onScoreChange]);

  return (
    <div className={styles.container}>
      <header>
        <h2 className={styles.assessmentTitle}>{t("conners.title")}</h2>
        <p className={styles.assessmentSubtitle}>{t("conners.subtitle")}</p>
        <p style={{ fontSize: "13px", color: "#64748B", marginBottom: "16px" }}>
          {t("conners.instruction")}
        </p>
      </header>

      {/* Response Legend Box */}
      <div className={styles.legendBox}>
        <button
          type="button"
          className={styles.legendToggle}
          onClick={() => setLegendOpen((prev) => !prev)}
        >
          <span>{t("conners.legendTitle")}</span>
          <span
            className={`${styles.legendChevron} ${
              legendOpen ? styles.legendChevronOpen : ""
            }`}
          >
            ▼
          </span>
        </button>
        {legendOpen && (
          <div className={styles.legendContent}>
            <div className={styles.legendGrid}>
              {VALUES.map((v) => (
                <div key={v} className={styles.legendItem}>
                  <span className={styles.legendEmoji}>{LEGEND_EMOJIS[v]}</span>
                  <span className={styles.legendLabel}>{t(`conners.val${v}`)}</span>
                  <span className={styles.legendDesc}>{t(`conners.legend${v}`)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {QUESTIONS.map((q) => (
        <div key={q} className={styles.questionCard}>
          <p className={styles.questionText}>
            {q}. {t(`conners.q${q}`)}
          </p>
          <div className={styles.optionsGrid}>
            {VALUES.map((v) => (
              <button
                key={v}
                type="button"
                className={`${styles.optionButton} ${
                  answers[q] === v ? styles.optionSelected : ""
                }`}
                onClick={() => handleSelect(q, v)}
              >
                <div style={{ fontSize: "20px", marginBottom: "4px" }}>
                  {v === 0 && "⚪"}
                  {v === 1 && "🟡"}
                  {v === 2 && "🟠"}
                  {v === 3 && "🔴"}
                </div>
                <span>{t(`conners.val${v}`)}</span>
              </button>
            ))}
          </div>
        </div>
      ))}

      <div className={styles.scorePreview}>
        <p className={styles.scoreText}>
          {Object.keys(answers).length === 10 
            ? `✅ ${t("conners.completed")}` 
            : `📝 ${t("conners.progress", { answered: Object.keys(answers).length, total: 10 })}`}
        </p>
        <p style={{ fontSize: "11px", color: "#64748B", marginTop: "4px" }}>
          {t("conners.clinicalNote")}
        </p>
      </div>
    </div>
  );
}
