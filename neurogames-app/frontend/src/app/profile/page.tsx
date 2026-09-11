"use client";

import { useState, useEffect } from "react";
import { HistoryManager, type HistoryEntry } from "@/lib/history";
import { GamificationManager } from "@/lib/gamification";
import { NeuroAPI, type ParticipantSummary } from "@/lib/api";
import { useI18n } from "@/i18n/I18nProvider";
import { translateGameName, translateGameSkill, translatePerformanceLevel } from "@/i18n/labels";
import styles from "./page.module.css";

const GAMES = [
  { key: "gonogo",   icon: "🎯" },
  { key: "memory",   icon: "🃏" },
  { key: "tracking", icon: "👁️" },
  { key: "shapes",   icon: "🔷" },
  { key: "puzzle",   icon: "🧩" },
];

export default function ProfilePage() {
  const { t, formatNumber } = useI18n();
  const [player, setPlayer] = useState<{ id?: string; avatar?: { emoji: string }; cognitiveLevel?: string } | null>(null);
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [xp, setXp] = useState(0);
  const [streak, setStreak] = useState(0);
  const [badges, setBadges] = useState<{ icon: string; label: string }[]>([]);
  const [backendSummary, setBackendSummary] = useState<ParticipantSummary | null>(null);

  useEffect(() => {
    queueMicrotask(() => {
      // Read all localStorage-dependent data after hydration
      let p: { id?: string; avatar?: { emoji: string }; cognitiveLevel?: string } | null = null;
      try {
        p = JSON.parse(localStorage.getItem("neuro_player") || "null");
        setPlayer(p);
      } catch { /* ignore */ }
      setEntries(HistoryManager.getAll());
      setXp(GamificationManager.getTotalXP());
      setStreak(GamificationManager.getStreak());
      setBadges(GamificationManager.getBadges());

      // Fetch backend summary to get persisted XP, level and session counts
      const playerId = p?.id || "demo";
      NeuroAPI.getSummary(playerId).then(({ ok, data }) => {
        if (ok && data) {
          setBackendSummary(data);
          // Only override XP from backend if it reflects more persisted history.
          setXp((localXp) => Math.max(localXp, data.xp));
        }
      });
    });
  }, []);

  const localAvgAcc = entries.length > 0
    ? Math.round(entries.reduce((s, e) => s + e.accuracy, 0) / entries.length * 100)
    : 0;

  // Level badge: prefer backend level from persisted sessions, fallback to cognitiveLevel.
  const displayLevel = translatePerformanceLevel(
    backendSummary?.level || player?.cognitiveLevel || "Medium",
    t,
  );

  const totalSessions = Math.max(entries.length, backendSummary?.total_sessions || 0);
  const displayAvgAcc = backendSummary?.avg_accuracy || localAvgAcc;

  return (
    <div className={styles.screen}>
      <div className={styles.wrapper}>
        {/* Hero */}
        <div className={styles.hero}>
          <div className={styles.avatarLarge}>{player?.avatar?.emoji || "🦁"}</div>
          <h2 className={styles.name}>{player?.id || t("nav.profile")}</h2>
          <div className={styles.levelBadge}>★ {displayLevel}</div>
        </div>

        {/* Quick stats */}
        <div className={styles.statsRow}>
          <div className={styles.statCard}>
            <span className={styles.statVal}>{formatNumber(totalSessions)}</span>
            <span className={styles.statLbl}>{t("common.sessions")}</span>
          </div>
          <div className={styles.statCard}>
            <span className={styles.statVal}>{formatNumber(xp)}</span>
            <span className={styles.statLbl}>XP</span>
          </div>
          <div className={styles.statCard}>
            <span className={styles.statVal}>{totalSessions > 0 ? `${displayAvgAcc}%` : "—"}</span>
            <span className={styles.statLbl}>{t("metrics.accuracy")}</span>
          </div>
          <div className={styles.statCard}>
            <span className={styles.statVal}>{streak}🔥</span>
            <span className={styles.statLbl}>{t("history.streak")}</span>
          </div>
        </div>

        {/* Per-game progress */}
        <h3 className={styles.sectionTitle}>{t("profile.progressByGame")}</h3>
        <div className={styles.gameCards}>
          {GAMES.map(game => {
            const gameEntries = entries.filter(e => e.game === game.key);
            const localCount = gameEntries.length;
            // Use backend session count if it includes more persisted sessions.
            const backendStat = backendSummary?.games.find(g => g.game_id === game.key);
            const count = Math.max(localCount, backendStat?.sessions_played ?? 0);
            
            const localMaxLevel = localCount > 0 ? Math.max(...gameEntries.map(e => e.level || 1)) : 0;
            const displayMaxLevel = Math.max(localMaxLevel, backendStat?.max_level || 0);
            
            // Prefer backend accuracy (larger dataset), fallback to local best
            const localBestAcc = localCount > 0 ? Math.round(Math.max(...gameEntries.map(e => e.accuracy || 0)) * 100) : 0;
            const bestAcc = backendStat ? Math.round(Math.max(backendStat.avg_accuracy, localBestAcc)) : localBestAcc;
            return (
              <div key={game.key} className={styles.gameCard}>
                <div className={styles.gameIcon}>{game.icon}</div>
                <div className={styles.gameInfo}>
                  <div className={styles.gameName}>{translateGameName(game.key, t)}</div>
                  <div className={styles.gameSkill}>{translateGameSkill(game.key, t)}</div>
                </div>
                <div className={styles.gameStats}>
                  <div className={styles.gameStat}>
                    <span className={styles.gameStatVal}>{displayMaxLevel > 0 ? displayMaxLevel : "—"}</span>
                    <span className={styles.gameStatLbl}>{t("level.max")}</span>
                  </div>
                  <div className={styles.gameStat}>
                    <span className={styles.gameStatVal}>{count > 0 ? `${bestAcc}%` : "—"}</span>
                    <span className={styles.gameStatLbl}>{t("metrics.accuracy")}</span>
                  </div>
                  <div className={styles.gameStat}>
                    <span className={styles.gameStatVal}>{count}</span>
                    <span className={styles.gameStatLbl}>{t("common.sessions")}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* Badges */}
        <h3 className={styles.sectionTitle}>{t("profile.badges")}</h3>
        <div className={styles.badges}>
          {badges.length === 0 ? (
            <div className={styles.noBadges}>{t("profile.noBadges")}</div>
          ) : (
            badges.map((b, i) => (
              <span key={i} className={styles.badge}>
                {b.icon}
                <span className={styles.badgeLabel}>{t(b.label)}</span>
              </span>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
